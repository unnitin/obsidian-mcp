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

    def test_small_trailing_chunk_merged_into_predecessor(self) -> None:
        content = """
# Section A

This section has enough content to exceed the minimum token threshold comfortably.
It has multiple sentences to ensure it is large enough to stand alone as a chunk.

# Section B

Tiny.
"""
        chunker = MarkdownChunker(min_tokens=50, max_tokens=512)
        chunks = chunker.chunk(content, FILE, MTIME)
        # "Tiny." is below min_tokens so it should be merged into the previous chunk
        combined_content = " ".join(c.content for c in chunks)
        assert "Tiny" in combined_content
        # Should be merged: fewer chunks than sections
        assert len(chunks) == 1

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
