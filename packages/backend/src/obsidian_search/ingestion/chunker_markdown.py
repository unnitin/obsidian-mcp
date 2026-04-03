"""Header-hierarchy markdown chunker with special block detection."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import frontmatter

from obsidian_search.models import Chunk, ChunkId, SourceType

# Rough token estimate: 1 token ≈ 4 characters
_CHARS_PER_TOKEN = 4

_TABLE_ROW = re.compile(r"^\s*\|")
_MERMAID_OPEN = re.compile(r"^```mermaid\s*$")
_FENCE_CLOSE = re.compile(r"^```\s*$")
_CALLOUT = re.compile(r"^>\s*\[!([\w-]+)\]")
_FIGURE = re.compile(r"!\[\[([^\]]+)\]\]")
_HEADER = re.compile(r"^(#{1,6})\s+(.+)")


@dataclass
class _Section:
    header_path: str
    lines: list[str] = field(default_factory=list)
    level: int = 0


def _tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _split_sentences(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    """Split text into overlapping sentence-boundary chunks."""
    try:
        import nltk

        try:
            sentences = nltk.sent_tokenize(text)
        except LookupError:
            nltk.download("punkt_tab", quiet=True)
            sentences = nltk.sent_tokenize(text)
    except ImportError:
        # Fallback: split on period-newline boundaries
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
        tags: list[str] = metadata.get("tags", [])

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

    def _process_block(self, text: str, header: str) -> list[tuple[str, dict[str, Any]]]:
        """Detect special blocks; fall back to sentence splitting for long text.

        Returns a list of (chunk_text, extra_metadata) pairs. extra_metadata is
        merged into the chunk's metadata dict, carrying chunk_type, callout_type,
        and figure_name where applicable.
        """
        lines = text.splitlines()
        first = lines[0] if lines else ""

        # Mermaid diagram — index DSL as atomic chunk
        if _MERMAID_OPEN.match(first):
            return [(text, {"chunk_type": "mermaid"})]

        # Table — atomic; split on row boundaries if oversized
        if all(_TABLE_ROW.match(row) or not row.strip() for row in lines if row.strip()):
            return [(t, {"chunk_type": "table"}) for t in self._split_table(text)]

        # Callout block
        m = _CALLOUT.match(first)
        if m:
            return [(text, {"chunk_type": "callout", "callout_type": m.group(1).lower()})]

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
        if not chunks:
            return chunks
        merged: list[Chunk] = []
        i = 0
        while i < len(chunks):
            c = chunks[i]
            if _tokens(c.content) < self.min_tokens and merged:
                prev = merged[-1]
                merged[-1] = prev.model_copy(update={"content": prev.content + "\n\n" + c.content})
            else:
                merged.append(c)
            i += 1
        return merged
