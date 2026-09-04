"""Unit tests for MarkdownChunker — targets all uncovered branches."""

from __future__ import annotations

import sys
import unittest.mock as mock

from obsidian_search.ingestion.chunker_markdown import (
    MarkdownChunker,
    _split_sentences,
    _tokens,
)
from obsidian_search.models import SourceType

MTIME = 1_700_000_000.0
FILE = "notes/test.md"


# ── _tokens helper ────────────────────────────────────────────────────────────


class TestTokens:
    def test_empty_string_returns_one(self) -> None:
        assert _tokens("") == 1

    def test_short_string(self) -> None:
        assert _tokens("hello") == 1  # 5 chars // 4 = 1

    def test_longer_string(self) -> None:
        text = "a" * 400
        assert _tokens(text) == 100


# ── _split_sentences ──────────────────────────────────────────────────────────


class TestSplitSentences:
    """Covers lines 37-75 (the entire function, including overlap logic)."""

    def test_short_text_returns_single_chunk(self) -> None:
        text = "Hello world. This is a test."
        result = _split_sentences(text, max_tokens=512, overlap_tokens=50)
        assert len(result) == 1
        assert "Hello" in result[0]

    def test_long_text_splits_into_multiple_chunks(self) -> None:
        # Each sentence ~25 tokens; max_tokens=30 forces splits
        sentences = [f"Sentence number {i} has some padding words here." for i in range(20)]
        text = " ".join(sentences)
        result = _split_sentences(text, max_tokens=30, overlap_tokens=10)
        assert len(result) > 1

    def test_overlap_carries_sentences_forward(self) -> None:
        # Build text long enough to force a split, then check overlap
        long_sentence = "word " * 30  # ~150 tokens
        text = f"{long_sentence.strip()}. {long_sentence.strip()}. {long_sentence.strip()}."
        result = _split_sentences(text, max_tokens=100, overlap_tokens=40)
        assert len(result) >= 2
        # Overlap: last chunk must share some words with second-to-last
        words_prev = set(result[-2].split())
        words_next = set(result[-1].split())
        assert words_prev & words_next, "Overlap expected between consecutive chunks"

    def test_empty_text_returns_original(self) -> None:
        result = _split_sentences("", max_tokens=512, overlap_tokens=50)
        assert result == [""]

    def test_nltk_import_error_falls_back_to_regex(self) -> None:
        text = "First sentence. Second sentence! Third sentence?"
        with mock.patch.dict(sys.modules, {"nltk": None}):
            result = _split_sentences(text, max_tokens=512, overlap_tokens=50)
        assert len(result) == 1
        assert "First" in result[0]

    def test_nltk_lookup_error_triggers_download(self) -> None:
        nltk_mock = mock.MagicMock()
        nltk_mock.sent_tokenize.side_effect = [LookupError, ["Sentence one.", "Sentence two."]]
        with mock.patch.dict(sys.modules, {"nltk": nltk_mock}):
            result = _split_sentences(
                "Sentence one. Sentence two.", max_tokens=512, overlap_tokens=50
            )
        nltk_mock.download.assert_called_once_with("punkt_tab", quiet=True)
        assert len(result) >= 1


# ── MarkdownChunker — fenced code blocks ─────────────────────────────────────


class TestFencedCodeBlocks:
    """Covers lines 144-154: headers inside fenced blocks must be ignored."""

    def test_header_inside_fence_not_split(self) -> None:
        content = """
# Real Header

```python
# This is not a header
def foo():
    pass
```

More content here.
"""
        chunker = MarkdownChunker(min_tokens=1)
        chunks = chunker.chunk(content, FILE, MTIME)
        # The fenced block should stay inside the Real Header section
        combined = " ".join(c.content for c in chunks)
        assert "def foo" in combined
        # There should be only one section (the Real Header)
        headers = [c.header_path for c in chunks if c.header_path]
        assert all("Real Header" in h for h in headers)

    def test_fence_open_and_close_tracked_correctly(self) -> None:
        content = """
# Section

```
inside fence
```

After fence.
"""
        chunker = MarkdownChunker(min_tokens=1)
        chunks = chunker.chunk(content, FILE, MTIME)
        combined = " ".join(c.content for c in chunks)
        assert "inside fence" in combined
        assert "After fence" in combined

    def test_nested_code_fence_language_tag(self) -> None:
        content = """
# Section

```javascript
const x = 1;
```
"""
        chunker = MarkdownChunker(min_tokens=1)
        chunks = chunker.chunk(content, FILE, MTIME)
        combined = " ".join(c.content for c in chunks)
        assert "const x" in combined


# ── MarkdownChunker — special block types ────────────────────────────────────


class TestSpecialBlocks:
    """Covers mermaid (line 179), callout (185), figure (189), regular (193)."""

    def test_mermaid_block_kept_atomic(self) -> None:
        content = """
# Diagram

```mermaid
graph LR
    A --> B
    B --> C
```
"""
        chunker = MarkdownChunker(min_tokens=1)
        chunks = chunker.chunk(content, FILE, MTIME)
        mermaid_chunks = [c for c in chunks if "graph LR" in c.content]
        assert len(mermaid_chunks) == 1, "Mermaid block must be a single atomic chunk"

    def test_callout_block_kept_atomic(self) -> None:
        content = """
# Notes

> [!warning] Be careful
> This is an important warning message.
> It spans multiple lines.
"""
        chunker = MarkdownChunker(min_tokens=1)
        chunks = chunker.chunk(content, FILE, MTIME)
        callout_chunks = [c for c in chunks if "warning" in c.content.lower()]
        assert callout_chunks, "Callout block must produce a chunk"
        # All callout content in one chunk
        assert len(callout_chunks) == 1

    def test_figure_embed_kept_atomic(self) -> None:
        content = """
# Gallery

Here is a diagram: ![[architecture.png]]

With some surrounding context explaining the figure.
"""
        chunker = MarkdownChunker(min_tokens=1)
        chunks = chunker.chunk(content, FILE, MTIME)
        fig_chunks = [c for c in chunks if "architecture.png" in c.content]
        assert len(fig_chunks) == 1, "Figure embed must stay in one chunk"

    def test_regular_text_within_max_tokens_not_split(self) -> None:
        content = """
# Short Section

This is a short paragraph that fits within the token limit easily.
"""
        chunker = MarkdownChunker(min_tokens=1, max_tokens=512)
        chunks = chunker.chunk(content, FILE, MTIME)
        assert len(chunks) == 1

    def test_long_text_triggers_sentence_split(self) -> None:
        # Generate text > 512 tokens (~2048 chars)
        long_para = "This is a long sentence with many words to fill up the token budget. " * 40
        content = f"# Long Section\n\n{long_para}"
        chunker = MarkdownChunker(min_tokens=1, max_tokens=50, overlap_tokens=10)
        chunks = chunker.chunk(content, FILE, MTIME)
        assert len(chunks) > 1, "Long text must be split into multiple chunks"


# ── MarkdownChunker — table splitting ────────────────────────────────────────


class TestTableSplitting:
    """Covers _split_table (lines 199-221)."""

    def test_small_table_kept_atomic(self) -> None:
        content = """
# Data

| Name | Value |
|------|-------|
| foo  | 1     |
| bar  | 2     |
"""
        chunker = MarkdownChunker(min_tokens=1, max_tokens=512)
        chunks = chunker.chunk(content, FILE, MTIME)
        table_chunks = [c for c in chunks if "| Name |" in c.content or "Name" in c.content]
        assert table_chunks

    def test_large_table_split_with_header_repeated(self) -> None:
        header = "| Col A | Col B |\n|-------|-------|"
        # 60 rows — forces splits at max_tokens=30
        rows = "\n".join(f"| row{i:03d} | val{i:03d} |" for i in range(60))
        content = f"# Table\n\n{header}\n{rows}"
        chunker = MarkdownChunker(min_tokens=1, max_tokens=30)
        chunks = chunker.chunk(content, FILE, MTIME)
        # Every chunk after the first must repeat the header
        table_chunks = [c for c in chunks if "Col A" in c.content]
        assert len(table_chunks) > 1, "Large table must be paginated"
        for c in table_chunks:
            assert "Col A" in c.content, "Each table chunk must contain the header row"

    def test_table_no_data_rows_returned_atomic(self) -> None:
        # Table with only header + separator, no data
        content = "# T\n\n| A | B |\n|---|---|\n"
        chunker = MarkdownChunker(min_tokens=1, max_tokens=5)
        chunks = chunker.chunk(content, FILE, MTIME)
        assert len(chunks) == 1

    def test_empty_table_text_returns_original(self) -> None:
        from obsidian_search.ingestion.chunker_markdown import MarkdownChunker as MC

        c = MC(min_tokens=1, max_tokens=5)
        result = c._split_table("")
        assert result == [""]


# ── MarkdownChunker — header breadcrumb ──────────────────────────────────────


class TestHeaderBreadcrumb:
    def test_no_header_chunk_has_no_header_path(self) -> None:
        content = "Just some text with no headers at all."
        chunker = MarkdownChunker(min_tokens=1)
        chunks = chunker.chunk(content, FILE, MTIME)
        assert chunks
        assert chunks[0].header_path is None

    def test_nested_headers_build_breadcrumb(self) -> None:
        content = """
# Top

## Middle

### Leaf

Content here.
"""
        chunker = MarkdownChunker(min_tokens=1)
        chunks = chunker.chunk(content, FILE, MTIME)
        leaf = next(c for c in chunks if "Content here" in c.content)
        assert leaf.header_path == "Top > Middle > Leaf"

    def test_sibling_headers_reset_breadcrumb(self) -> None:
        content = """
# Section A

Content A.

# Section B

Content B.
"""
        chunker = MarkdownChunker(min_tokens=1)
        chunks = chunker.chunk(content, FILE, MTIME)
        a = next(c for c in chunks if "Content A" in c.content)
        b = next(c for c in chunks if "Content B" in c.content)
        assert a.header_path == "Section A"
        assert b.header_path == "Section B"

    def test_content_includes_header_prefix(self) -> None:
        content = "# My Section\n\nSome text."
        chunker = MarkdownChunker(min_tokens=1)
        chunks = chunker.chunk(content, FILE, MTIME)
        assert chunks[0].content.startswith("My Section\n\n")


# ── MarkdownChunker — merge small ────────────────────────────────────────────


class TestMergeSmall:
    """Covers _merge_small branches (lines 225, 231-232)."""

    def test_small_first_chunk_not_merged_into_nothing(self) -> None:
        # A tiny first chunk with no predecessor stays as-is
        content = "Tiny."  # very short, no header
        chunker = MarkdownChunker(min_tokens=100, max_tokens=512)
        chunks = chunker.chunk(content, FILE, MTIME)
        assert len(chunks) == 1  # no predecessor to merge into

    def test_small_chunk_not_merged_across_a_section_boundary(self) -> None:
        """A short section keeps its own heading rather than joining the previous one.

        This previously merged, so "Tiny." was appended to the Section A chunk
        and reported Section A's breadcrumb — attributing the text to the wrong
        heading in every search result.
        """
        content = """
# Section A

This section has enough content to exceed the minimum token threshold comfortably.
It has multiple sentences to ensure it is large enough to stand alone as a chunk.

# Section B

Tiny.
"""
        chunker = MarkdownChunker(min_tokens=50, max_tokens=512)
        chunks = chunker.chunk(content, FILE, MTIME)

        assert len(chunks) == 2
        by_header = {c.header_path: c.content for c in chunks}
        assert "Tiny" in by_header["Section B"]
        assert "Tiny" not in by_header["Section A"]

    def test_small_chunk_merged_within_the_same_section(self) -> None:
        """Merging still happens where it is safe — inside one section."""
        rows = "".join(f"| r{i} | v{i} |\n" for i in range(120))
        content = f"""
# Data

| a | b |
|---|---|
{rows}
"""
        chunker = MarkdownChunker(min_tokens=200, max_tokens=64)
        chunks = chunker.chunk(content, FILE, MTIME)
        assert all(c.header_path == "Data" for c in chunks)
        assert all(c.metadata.get("chunk_type") == "table" for c in chunks)

    def test_empty_chunk_list_returns_empty(self) -> None:
        from obsidian_search.ingestion.chunker_markdown import MarkdownChunker as MC

        c = MC()
        assert c._merge_small([]) == []


# ── MarkdownChunker — frontmatter ────────────────────────────────────────────


class TestFrontmatter:
    def test_tags_extracted_from_frontmatter(self) -> None:
        content = "---\ntags: [python, testing]\n---\n# Section\n\nContent."
        chunker = MarkdownChunker(min_tokens=1)
        chunks = chunker.chunk(content, FILE, MTIME)
        assert chunks[0].metadata["tags"] == ["python", "testing"]

    def test_extra_frontmatter_fields_in_metadata(self) -> None:
        content = "---\nauthor: Alice\nstatus: draft\n---\n# Section\n\nContent."
        chunker = MarkdownChunker(min_tokens=1)
        chunks = chunker.chunk(content, FILE, MTIME)
        assert chunks[0].metadata["author"] == "Alice"
        assert chunks[0].metadata["status"] == "draft"

    def test_no_frontmatter_produces_empty_tags(self) -> None:
        content = "# Section\n\nPlain content without frontmatter."
        chunker = MarkdownChunker(min_tokens=1)
        chunks = chunker.chunk(content, FILE, MTIME)
        assert chunks[0].metadata["tags"] == []

    def test_source_type_is_markdown(self) -> None:
        content = "# Section\n\nContent."
        chunker = MarkdownChunker(min_tokens=1)
        chunks = chunker.chunk(content, FILE, MTIME)
        assert all(c.source_type == SourceType.MARKDOWN for c in chunks)

    def test_chunk_indices_are_sequential(self) -> None:
        content = "# A\n\nText A.\n\n# B\n\nText B.\n\n# C\n\nText C."
        chunker = MarkdownChunker(min_tokens=1)
        chunks = chunker.chunk(content, FILE, MTIME)
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))

    def test_empty_sections_skipped(self) -> None:
        content = "# Header\n\n\n\n# Non-empty\n\nContent."
        chunker = MarkdownChunker(min_tokens=1)
        chunks = chunker.chunk(content, FILE, MTIME)
        # Only "Non-empty" section produces a chunk; empty header section is skipped
        assert all("Content" in c.content for c in chunks)

    def test_whitespace_only_chunk_text_skipped(self) -> None:
        """Covers line 110: chunk text that is empty after strip() is skipped."""
        # A section whose lines are all whitespace/blank produces no chunk
        content = "# Section\n\n   \n\n# Real\n\nActual content."
        chunker = MarkdownChunker(min_tokens=1)
        chunks = chunker.chunk(content, FILE, MTIME)
        assert all(c.content.strip() for c in chunks)


class TestSplitTableEdgeCases:
    def test_split_table_all_rows_fit_returns_single_chunk(self) -> None:
        """Covers line 220: `chunks or [text]` fallback when no chunk was appended."""
        # A table where each individual row pushes over max_tokens but there's
        # only one data row — the final page never gets appended in the loop,
        # so we rely on the `if len(page) > len(header_rows)` branch.
        from obsidian_search.ingestion.chunker_markdown import MarkdownChunker as MC

        c = MC(min_tokens=1, max_tokens=5)
        # One data row — small enough that `_tokens(page) > max_tokens` never
        # triggers during the loop, so only the post-loop append fires.
        table = "| A | B |\n|---|---|\n| x | y |"
        result = c._split_table(table)
        assert len(result) >= 1
        assert all("A" in r or "x" in r for r in result)


# ── Tag normalisation ─────────────────────────────────────────────────────────


class TestTagNormalisation:
    """Frontmatter tags arrive in whatever shape the note author wrote."""

    def _tags(self, content: str) -> list[str]:
        return MarkdownChunker(min_tokens=1).chunk(content, FILE, MTIME)[0].metadata["tags"]

    def test_scalar_tag_becomes_a_list(self) -> None:
        """Regression: `tags: work` stayed a string, so filters matched substrings."""
        assert self._tags("---\ntags: work\n---\n\nBody text here.") == ["work"]

    def test_scalar_tag_does_not_match_a_substring(self) -> None:
        """Filtering for "or" must not match a note tagged "work"."""
        tags = self._tags("---\ntags: work\n---\n\nBody text here.")
        assert not any(t in tags for t in ["or"])

    def test_comma_separated_string_splits(self) -> None:
        assert self._tags("---\ntags: work, urgent\n---\n\nBody.") == ["work", "urgent"]

    def test_list_is_preserved(self) -> None:
        assert self._tags("---\ntags: [alpha, beta]\n---\n\nBody.") == ["alpha", "beta"]

    def test_leading_hash_is_stripped(self) -> None:
        assert self._tags("---\ntags: ['#alpha']\n---\n\nBody.") == ["alpha"]

    def test_nested_tag_kept_whole(self) -> None:
        assert self._tags("---\ntags: [project/alpha]\n---\n\nBody.") == ["project/alpha"]

    def test_missing_tags_gives_empty_list(self) -> None:
        assert self._tags("---\ntitle: Note\n---\n\nBody.") == []

    def test_numeric_tag_becomes_a_string(self) -> None:
        assert self._tags("---\ntags: 2024\n---\n\nBody.") == ["2024"]

    def test_duplicates_removed(self) -> None:
        assert self._tags("---\ntags: [a, a, b]\n---\n\nBody.") == ["a", "b"]


class TestInlineTags:
    """Inline #tags are the dominant Obsidian convention and were not indexed."""

    def _tags(self, content: str) -> list[str]:
        return MarkdownChunker(min_tokens=1).chunk(content, FILE, MTIME)[0].metadata["tags"]

    def test_inline_tags_indexed(self) -> None:
        tags = self._tags("Notes on #machine-learning and #physics today.")
        assert tags == ["machine-learning", "physics"]

    def test_nested_inline_tag(self) -> None:
        assert self._tags("Filed under #project/alpha here.") == ["project/alpha"]

    def test_merged_with_frontmatter_tags(self) -> None:
        tags = self._tags("---\ntags: [work]\n---\n\nAbout #physics and #work.")
        assert tags == ["work", "physics"]

    def test_headings_are_not_tags(self) -> None:
        assert self._tags("# Heading\n\n## Subheading\n\nBody #real here.") == ["real"]

    def test_all_numeric_is_not_a_tag(self) -> None:
        assert self._tags("Released in #2024 sometime.") == []

    def test_url_fragment_is_not_a_tag(self) -> None:
        assert self._tags("See https://example.com/page#section for detail.") == []

    def test_fenced_code_yields_no_tags(self) -> None:
        content = "Body text.\n\n```python\n# comment\n#neither\n```\n"
        assert self._tags(content) == []

    def test_inline_code_yields_no_tags(self) -> None:
        assert self._tags("Use the `#nope` directive here.") == []


# ── Block detection within a section ─────────────────────────────────────────


class TestBlocksWithinSection:
    """Special blocks used to register only when alone in their section."""

    def _chunk(self, content: str, **kw: int) -> list:
        return MarkdownChunker(min_tokens=1, **kw).chunk(content, FILE, MTIME)

    def test_table_after_prose_is_still_a_table(self) -> None:
        """Regression: one line of prose above a table shredded its rows."""
        rows = "".join(f"| r{i} | v{i} |\n" for i in range(200))
        chunks = self._chunk(f"# Data\n\nHere are the results.\n\n| a | b |\n|---|---|\n{rows}")
        kinds = [c.metadata.get("chunk_type") for c in chunks]
        assert None in kinds  # the prose
        assert "table" in kinds  # and the table, detected

    def test_table_after_prose_keeps_every_row(self) -> None:
        rows = "".join(f"| r{i} | v{i} |\n" for i in range(200))
        chunks = self._chunk(f"# Data\n\nIntro line.\n\n| a | b |\n|---|---|\n{rows}")
        seen = {
            line.strip()
            for c in chunks
            if c.metadata.get("chunk_type") == "table"
            for line in c.content.splitlines()
            if line.startswith("| r")
        }
        assert len(seen) == 200

    def test_table_after_prose_repeats_the_header(self) -> None:
        rows = "".join(f"| r{i} | v{i} |\n" for i in range(200))
        chunks = self._chunk(f"# Data\n\nIntro.\n\n| a | b |\n|---|---|\n{rows}")
        tables = [c for c in chunks if c.metadata.get("chunk_type") == "table"]
        assert len(tables) > 1
        assert all("| a | b |" in t.content for t in tables)

    def test_prose_is_separated_from_the_table(self) -> None:
        chunks = self._chunk("# Data\n\nIntro line.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
        prose = [c for c in chunks if c.metadata.get("chunk_type") is None]
        assert len(prose) == 1
        assert "Intro line." in prose[0].content
        assert "| 1 | 2 |" not in prose[0].content

    def test_mermaid_after_prose_is_detected(self) -> None:
        content = "# Arch\n\nThe system looks like this.\n\n```mermaid\ngraph LR\n  A-->B\n```\n"
        chunks = self._chunk(content)
        kinds = [c.metadata.get("chunk_type") for c in chunks]
        assert "mermaid" in kinds
        diagram = next(c for c in chunks if c.metadata.get("chunk_type") == "mermaid")
        assert "graph LR" in diagram.content and "A-->B" in diagram.content

    def test_callout_after_prose_is_detected(self) -> None:
        content = "# Notes\n\nSome prose first.\n\n> [!warning]\n> Be careful here.\n"
        chunks = self._chunk(content)
        callouts = [c for c in chunks if c.metadata.get("chunk_type") == "callout"]
        assert len(callouts) == 1
        assert callouts[0].metadata["callout_type"] == "warning"

    def test_code_fence_is_not_mistaken_for_a_table(self) -> None:
        content = "# Code\n\n```text\n| not | a | table |\n```\n\nTrailing prose.\n"
        chunks = self._chunk(content)
        assert all(c.metadata.get("chunk_type") != "table" for c in chunks)

    def test_multiple_blocks_in_one_section(self) -> None:
        content = (
            "# Mixed\n\nIntro.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n"
            "More prose.\n\n> [!note]\n> A note.\n"
        )
        kinds = [c.metadata.get("chunk_type") for c in self._chunk(content)]
        assert "table" in kinds
        assert "callout" in kinds
        assert None in kinds

    def test_all_blocks_keep_the_section_breadcrumb(self) -> None:
        content = "# Mixed\n\nIntro.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
        assert all(c.header_path == "Mixed" for c in self._chunk(content))


class TestFragmentation:
    """Block detection must not shatter a note into meaningless fragments.

    Requiring an equal chunk_type to merge meant alternating prose and small
    tables never coalesced: a 15-entry log went from one chunk to thirty, with
    a median of 36 characters. Embedding fragments that small is worse than not
    splitting at all.
    """

    def _log_note(self, entries: int = 15) -> str:
        body = "\n".join(
            f"Point {i} about the thing.\n| k | v |\n|---|---|\n| a{i} | b{i} |\n"
            for i in range(entries)
        )
        return f"# Log\n\n{body}"

    def test_alternating_blocks_do_not_fragment(self) -> None:
        chunks = MarkdownChunker(max_tokens=512, min_tokens=64).chunk(self._log_note(), FILE, MTIME)
        assert len(chunks) <= 3
        assert min(_tokens(c.content) for c in chunks) >= 64 or len(chunks) == 1

    def test_merged_chunk_drops_a_label_it_no_longer_fits(self) -> None:
        """A prose+table chunk must not claim to be a table."""
        chunks = MarkdownChunker(max_tokens=512, min_tokens=64).chunk(
            "# Log\n\nA line.\n\n| k | v |\n|---|---|\n| a | b |\n", FILE, MTIME
        )
        assert len(chunks) == 1
        assert "chunk_type" not in chunks[0].metadata

    def test_merging_respects_max_tokens(self) -> None:
        """Coalescing must not build a chunk the model would truncate."""
        chunks = MarkdownChunker(max_tokens=40, min_tokens=30).chunk(
            self._log_note(entries=20), FILE, MTIME
        )
        assert all(_tokens(c.content) <= 40 * 2 for c in chunks)
        assert len(chunks) > 1

    def test_large_table_still_splits_on_row_boundaries(self) -> None:
        """The #37 behaviour must survive: prose separated, rows intact."""
        rows = "".join(f"| r{i} | v{i} |\n" for i in range(200))
        chunks = MarkdownChunker(max_tokens=512, min_tokens=64).chunk(
            f"# Data\n\nIntro.\n\n| a | b |\n|---|---|\n{rows}", FILE, MTIME
        )
        tables = [c for c in chunks if c.metadata.get("chunk_type") == "table"]
        assert len(tables) > 1
        assert all("| a | b |" in t.content for t in tables)

    def test_cross_section_merging_still_refused(self) -> None:
        """And so must the header-attribution fix."""
        content = "## Introduction\n\n" + ("Intro sentence. " * 60) + "\n\n## Conclusions\n\nShort."
        chunks = MarkdownChunker(max_tokens=512, min_tokens=64).chunk(content, FILE, MTIME)
        by_header = {c.header_path: c.content for c in chunks}
        assert "Short." in by_header["Conclusions"]
        assert "Short." not in by_header["Introduction"]
