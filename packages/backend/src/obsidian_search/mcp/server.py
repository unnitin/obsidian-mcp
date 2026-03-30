"""FastMCP server — exposes obsidian-search tools to Claude Desktop."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from obsidian_search.config import Settings
from obsidian_search.embedding.embedder import Embedder
from obsidian_search.ingestion.pipeline import IndexingPipeline
from obsidian_search.store.vector_store import VectorStore


def _build_mcp_server(settings: Settings, store: VectorStore, embedder: Embedder) -> Any:  # noqa: ANN401
    """Construct and return the FastMCP server instance."""
    from fastmcp import FastMCP

    from obsidian_search.search.reranker import Reranker
    from obsidian_search.search.searcher import Searcher

    pipeline = IndexingPipeline(settings=settings, store=store, embedder=embedder)
    reranker = Reranker()
    searcher = Searcher(settings=settings, store=store, embedder=embedder, reranker=reranker)

    mcp = FastMCP("obsidian-search")

    # ── Tools ──────────────────────────────────────────────────────────────

    @mcp.tool()
    def search_notes(
        query: str,
        top_k: int = 10,
        source_types: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Search across all indexed Obsidian notes, PDFs, and web pages.

        Args:
            query: Natural-language search query.
            top_k: Maximum number of results to return (default 10).
            source_types: Filter by source type: "markdown", "pdf", or "web".
            tags: Filter to chunks whose frontmatter tags contain any of these.

        Returns:
            List of matching chunks with score, file path, header breadcrumb,
            and a content excerpt.
        """
        results = searcher.search(query=query, top_k=top_k, source_types=source_types, tags=tags)
        return [
            {
                "score": r.score,
                "file_path": r.file_path,
                "header_path": r.header_path,
                "source_type": r.source_type,
                "url": r.url,
                "content": r.content,
            }
            for r in results
        ]

    @mcp.tool()
    def get_note_content(file_path: str) -> str:
        """Read the full text of a vault note by its vault-relative or absolute path.

        Args:
            file_path: Path to the markdown or PDF file.

        Returns:
            The full text content of the file, or an error message.
        """
        path = Path(file_path)
        if not path.is_absolute():
            path = settings.vault_path / file_path
        if not path.exists():
            return f"Error: file not found: {file_path!r}"
        try:
            return path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return f"Error reading file: {exc}"

    @mcp.tool()
    def index_url(url: str, tags: list[str] | None = None) -> dict[str, Any]:
        """Fetch a web page, extract its content, and add it to the search index.

        Args:
            url: The URL to fetch and index.
            tags: Optional list of tags to associate with the indexed content.

        Returns:
            Ingest result with chunks_added and status.
        """
        result = pipeline.index_url(url, tags=tags)
        return result.model_dump()

    @mcp.tool()
    def index_pdf(file_path: str) -> dict[str, Any]:
        """Index a PDF file at the given absolute path.

        Args:
            file_path: Absolute path to the PDF file.

        Returns:
            Ingest result with chunks_added and status.
        """
        result = pipeline.index_file(Path(file_path))
        return result.model_dump()

    @mcp.tool()
    def get_index_status() -> dict[str, Any]:
        """Return current index statistics.

        Returns:
            Dict with total_chunks, total_documents, last_indexed_at,
            and index_size_bytes.
        """
        return store.stats()

    @mcp.tool()
    def list_indexed_files(source_type: str | None = None) -> list[dict[str, Any]]:
        """List all indexed documents with their chunk counts.

        Args:
            source_type: Optionally filter by "markdown", "pdf", or "web".

        Returns:
            List of dicts with file_path, source_type, and chunk_count.
        """
        conn = store._conn_()
        if source_type:
            rows = conn.execute(
                """
                SELECT file_path, source_type, COUNT(*) AS chunk_count,
                       MAX(mtime) AS last_mtime
                FROM chunks
                WHERE source_type = ?
                GROUP BY file_path, source_type
                ORDER BY file_path
                """,
                (source_type,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT file_path, source_type, COUNT(*) AS chunk_count,
                       MAX(mtime) AS last_mtime
                FROM chunks
                GROUP BY file_path, source_type
                ORDER BY file_path
                """
            ).fetchall()
        return [
            {
                "file_path": row["file_path"],
                "source_type": row["source_type"],
                "chunk_count": row["chunk_count"],
                "last_mtime": row["last_mtime"],
            }
            for row in rows
        ]

    @mcp.tool()
    def remove_from_index(file_path: str) -> dict[str, Any]:
        """Remove a document and all its chunks from the search index.

        Args:
            file_path: Vault-relative or absolute path (or URL for web content).

        Returns:
            Dict with chunks_removed.
        """
        removed = store.delete_by_file(file_path)
        return {"file_path": file_path, "chunks_removed": removed}

    return mcp


# ── CLI entry point ───────────────────────────────────────────────────────────


def main() -> None:
    """Start the MCP server over stdio (used by obsidian-search-mcp script)."""
    settings = Settings()  # type: ignore[call-arg]  # vault_path read from env
    settings.db_dir.mkdir(parents=True, exist_ok=True)

    store = VectorStore(settings.db_path)
    store.initialize(dims=768)

    embedder = Embedder(model_name=settings.embedding_model)

    mcp = _build_mcp_server(settings=settings, store=store, embedder=embedder)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
