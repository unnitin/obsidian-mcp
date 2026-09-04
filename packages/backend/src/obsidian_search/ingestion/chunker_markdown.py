"""Header-hierarchy markdown chunker with special block detection."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import frontmatter

from obsidian_search.models import Chunk, ChunkId, SourceType

logger = logging.getLogger(__name__)

# Rough token estimate: 1 token ≈ 4 characters
_CHARS_PER_TOKEN = 4

_TABLE_ROW = re.compile(r"^\s*\|")
_MERMAID_OPEN = re.compile(r"^```mermaid\s*$")
_FENCE_CLOSE = re.compile(r"^```\s*$")
_CALLOUT = re.compile(r"^>\s*\[!([\w-]+)\]")
_FIGURE = re.compile(r"!\[\[([^\]]+)\]\]")
_HEADER = re.compile(r"^(#{1,6})\s+(.+)")
_FENCE_OPEN = re.compile(r"^```")
_INLINE_CODE = re.compile(r"`[^`]*`")

# Obsidian inline tags: letters, digits, _, -, / — and at least one character
# that is not a digit, so "#2024" is not a tag but "#q1-2024" is. The lookbehind
# keeps "foo#bar", "##heading" and "page#fragment" from matching.
_INLINE_TAG = re.compile(r"(?<![\w#/])#([\w/-]*[A-Za-z_/-][\w/-]*)")


def _normalize_tags(value: Any) -> list[str]:  # noqa: ANN401
    """Coerce a frontmatter tags value into a clean list of tag strings.

    YAML gives us whatever the note author wrote: ``tags: work`` is a string,
    ``tags: [a, b]`` a list, ``tags: a, b`` a single comma-joined string. The
    old code annotated the value as list[str] without converting it, so a
    scalar stayed a string and tag filtering did substring matching against it
    — filtering for "or" matched a note tagged "work".
    """
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[,\s]+", value)
    elif isinstance(value, list | tuple | set):
        parts = []
        for item in value:
            if item is None:
                continue
            parts.extend(re.split(r"[,\s]+", str(item)))
    else:
        parts = [str(value)]

    tags: list[str] = []
    seen: set[str] = set()
    for part in parts:
        tag = part.strip().lstrip("#").strip("/")
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags


def _without_code(body: str) -> str:
    """Drop fenced blocks and inline code, so they cannot yield phantom tags."""
    kept: list[str] = []
    in_fence = False
    for line in body.splitlines():
        if _FENCE_OPEN.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            kept.append(_INLINE_CODE.sub(" ", line))
    return "\n".join(kept)


def _inline_tags(body: str) -> list[str]:
    """Tags written inline in the note body, e.g. "about #machine-learning".

    This is the dominant way Obsidian notes are tagged, and it was not indexed
    at all — only frontmatter was read.
    """
    return _normalize_tags(_INLINE_TAG.findall(_without_code(body)))


@dataclass
class _Section:
    header_path: str
    lines: list[str] = field(default_factory=list)
    level: int = 0


@dataclass
class _Block:
    """A homogeneous run of lines within a section."""

    kind: str  # "mermaid" | "table" | "callout" | "prose"
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip()


def _tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _split_sentences(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    """Split text into overlapping sentence-boundary chunks."""
    sentences: list[str] | None = None
    try:
        import nltk

        try:
            sentences = nltk.sent_tokenize(text)
        except LookupError:
            # First use downloads the tokeniser. This runs inside indexing, on
            # watcher threads, so a failure here (offline, proxy, read-only
            # cache) must degrade rather than abort the whole file — the retry
            # used to be unguarded and its LookupError propagated out.
            try:
                nltk.download("punkt_tab", quiet=True)
                sentences = nltk.sent_tokenize(text)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "NLTK sentence tokeniser unavailable; falling back to "
                    "regex sentence splitting for this document"
                )
    except ImportError:
        pass

    if sentences is None:
        # Fallback: split on sentence-ending punctuation
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    overlap_buf: list[str] = []

    for sent in sentences:
        st = _tokens(sent)
        if current_tokens + st > max_tokens and current:
            chunks.append(" ".join(current))
            # Keep overlap
            overlap_buf = []
            overlap_total = 0
            for s in reversed(current):
                if overlap_total + _tokens(s) <= overlap_tokens:
                    overlap_buf.insert(0, s)
                    overlap_total += _tokens(s)
                else:
                    break
            current = overlap_buf[:]
            current_tokens = overlap_total
        current.append(sent)
        current_tokens += st

    if current:
        chunks.append(" ".join(current))

    return chunks or [text]


class MarkdownChunker:
    def __init__(
        self,
        max_tokens: int = 512,
        min_tokens: int = 64,
        overlap_tokens: int = 50,
    ) -> None:
        self.max_tokens = max_tokens
        self.min_tokens = min_tokens
        self.overlap_tokens = overlap_tokens

    def chunk(self, content: str, file_path: str, mtime: float) -> list[Chunk]:
        post = frontmatter.loads(content)
        body: str = post.content
        metadata: dict[str, Any] = dict(post.metadata)

        # Frontmatter and inline tags both count, and both need normalising.
        tags: list[str] = _normalize_tags(metadata.get("tags"))
        for tag in _inline_tags(body):
            if tag not in tags:
                tags.append(tag)

        sections = self._split_sections(body)
        chunks: list[Chunk] = []
        idx = 0

        for section in sections:
            text = "\n".join(section.lines).strip()
            if not text:
                continue

            header = section.header_path
            chunk_pairs = self._process_block(text, header)

            for ct, block_meta in chunk_pairs:
                ct = ct.strip()
                if not ct:
                    continue
                chunks.append(
                    Chunk(
                        id=ChunkId.generate(file_path, idx),
                        source_type=SourceType.MARKDOWN,
                        file_path=file_path,
                        header_path=header or None,
                        content=f"{header}\n\n{ct}" if header else ct,
                        mtime=mtime,
                        chunk_index=idx,
                        metadata={
                            "tags": tags,
                            **{k: v for k, v in metadata.items() if k != "tags"},
                            **block_meta,
                        },
                    )
                )
                idx += 1

        # Merge tiny trailing chunks into their predecessor
        return self._merge_small(chunks)

    # ── Internals ─────────────────────────────────────────────────────────────

    def _split_sections(self, body: str) -> list[_Section]:
        """Walk lines and split on ATX headers, building breadcrumb paths."""
        lines = body.splitlines()
        sections: list[_Section] = []
        current = _Section(header_path="", level=0)
        header_stack: list[tuple[int, str]] = []  # (level, title)

        in_fence = False

        for line in lines:
            # Track fenced code blocks — don't parse headers inside them
            if _FENCE_CLOSE.match(line) and in_fence:
                in_fence = False
                current.lines.append(line)
                continue
            if re.match(r"^```", line) and not in_fence:
                in_fence = True
                current.lines.append(line)
                continue
            if in_fence:
                current.lines.append(line)
                continue

            m = _HEADER.match(line)
            if m:
                if current.lines or current.header_path:
                    sections.append(current)
                level = len(m.group(1))
                title = m.group(2).strip()
                # Pop stack to current level
                header_stack = [(lvl, t) for lvl, t in header_stack if lvl < level]
                header_stack.append((level, title))
                path = " > ".join(t for _, t in header_stack)
                current = _Section(header_path=path, level=level)
            else:
                current.lines.append(line)

        if current.lines or current.header_path:
            sections.append(current)

        return sections

    def _split_blocks(self, text: str) -> list[_Block]:
        """Split a section into runs of one block type each.

        Detection used to run against the whole section, so a table or diagram
        only registered when it was the *only* thing in that section. One line
        of prose above a table meant the table fell through to sentence
        splitting and its rows were shredded across chunks.
        """
        lines = text.splitlines()
        blocks: list[_Block] = []
        i = 0
        n = len(lines)

        def _starts_special(line: str) -> bool:
            return bool(
                _MERMAID_OPEN.match(line)
                or _FENCE_OPEN.match(line)
                or _TABLE_ROW.match(line)
                or _CALLOUT.match(line)
            )

        while i < n:
            line = lines[i]

            if _MERMAID_OPEN.match(line):
                j = i + 1
                while j < n and not _FENCE_CLOSE.match(lines[j]):
                    j += 1
                blocks.append(_Block("mermaid", lines[i : min(j + 1, n)]))
                i = j + 1
                continue

            if _FENCE_OPEN.match(line):
                # A non-mermaid code fence belongs with the prose around it, and
                # must be consumed whole so a "|" inside it is not read as a table.
                j = i + 1
                while j < n and not _FENCE_CLOSE.match(lines[j]):
                    j += 1
                end = min(j + 1, n)
                while end < n and not _starts_special(lines[end]):
                    end += 1
                blocks.append(_Block("prose", lines[i:end]))
                i = end
                continue

            if _TABLE_ROW.match(line):
                j = i
                while j < n and _TABLE_ROW.match(lines[j]):
                    j += 1
                blocks.append(_Block("table", lines[i:j]))
                i = j
                continue

            if _CALLOUT.match(line):
                j = i
                while j < n and lines[j].lstrip().startswith(">"):
                    j += 1
                blocks.append(_Block("callout", lines[i:j]))
                i = j
                continue

            j = i + 1
            while j < n and not _starts_special(lines[j]):
                j += 1
            blocks.append(_Block("prose", lines[i:j]))
            i = j

        return [b for b in blocks if b.text]

    def _process_block(self, text: str, header: str) -> list[tuple[str, dict[str, Any]]]:
        """Chunk one section, handling each block inside it on its own terms.

        Returns a list of (chunk_text, extra_metadata) pairs. extra_metadata is
        merged into the chunk's metadata dict, carrying chunk_type, callout_type,
        and figure_name where applicable.
        """
        pairs: list[tuple[str, dict[str, Any]]] = []
        for block in self._split_blocks(text):
            pairs.extend(self._process_single_block(block))
        return pairs

    def _process_single_block(self, block: _Block) -> list[tuple[str, dict[str, Any]]]:
        text = block.text

        # Mermaid diagram — index DSL as atomic chunk
        if block.kind == "mermaid":
            return [(text, {"chunk_type": "mermaid"})]

        # Table — atomic; split on row boundaries if oversized
        if block.kind == "table":
            return [(t, {"chunk_type": "table"}) for t in self._split_table(text)]

        # Callout block
        if block.kind == "callout":
            m = _CALLOUT.match(block.lines[0])
            meta: dict[str, Any] = {"chunk_type": "callout"}
            if m:
                meta["callout_type"] = m.group(1).lower()
            return [(text, meta)]

        # Figure embed — keep surrounding context
        fig = _FIGURE.search(text)
        if fig:
            return [(text, {"chunk_type": "figure_context", "figure_name": fig.group(1)})]

        # Regular text — split if too long
        if _tokens(text) <= self.max_tokens:
            return [(text, {})]

        return [(t, {}) for t in _split_sentences(text, self.max_tokens, self.overlap_tokens)]

    def _split_table(self, text: str) -> list[str]:
        lines = text.splitlines()
        if not lines:
            return [text]
        header_rows = lines[:2]  # header + separator
        data_rows = lines[2:]

        if _tokens(text) <= self.max_tokens or not data_rows:
            return [text]

        # Split data rows into pages, always repeating the header
        chunks: list[str] = []
        page: list[str] = header_rows[:]
        for row in data_rows:
            page.append(row)
            if _tokens("\n".join(page)) > self.max_tokens:
                chunks.append("\n".join(page))
                page = header_rows[:]
        if len(page) > len(header_rows):
            chunks.append("\n".join(page))
        return chunks or [text]

    def _merge_small(self, chunks: list[Chunk]) -> list[Chunk]:
        """Fold undersized chunks into the previous chunk of the same section.

        Merging used to reach across section boundaries, so a short trailing
        section was appended to whatever preceded it and inherited that chunk's
        header_path and chunk_type — search then reported the wrong breadcrumb
        and attributed the text to the wrong heading.
        """
        if not chunks:
            return chunks
        merged: list[Chunk] = []
        for c in chunks:
            can_merge = (
                _tokens(c.content) < self.min_tokens
                and bool(merged)
                and _tokens(merged[-1].content) + _tokens(c.content) <= self.max_tokens
                and merged[-1].header_path == c.header_path
            )
            if not can_merge:
                merged.append(c)
                continue

            prev = merged[-1]
            update: dict[str, Any] = {"content": prev.content + "\n\n" + c.content}
            # Blocks of different kinds may merge — a two-row table does not
            # need a chunk of its own — but the result must not keep a label
            # that now describes only part of it.
            if prev.metadata.get("chunk_type") != c.metadata.get("chunk_type"):
                mixed = {k: v for k, v in prev.metadata.items() if k != "chunk_type"}
                mixed.pop("callout_type", None)
                update["metadata"] = mixed
            merged[-1] = prev.model_copy(update=update)
        return merged
