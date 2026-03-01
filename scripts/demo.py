#!/usr/bin/env python
"""
Demo: full indexing + search pipeline using a word-hash embedder.

No model download required — uses the same deterministic embedder as the
integration tests. To use the real nomic-embed-text-v1.5 model instead,
pass --real-model (requires ~274MB download on first run).

Usage:
    uv run --project packages/backend python scripts/demo.py
    uv run --project packages/backend python scripts/demo.py --vault /path/to/vault
    uv run --project packages/backend python scripts/demo.py --real-model
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import textwrap
from pathlib import Path

import numpy as np

# ── Add src to path ───────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent / "packages/backend/src"))

from obsidian_search.config import Settings
from obsidian_search.embedding.embedder import Embedder
from obsidian_search.ingestion.pipeline import IndexingPipeline
from obsidian_search.search.searcher import Searcher
from obsidian_search.store.vector_store import VectorStore

DIMS = 768

# ── Sample vault ──────────────────────────────────────────────────────────────

SAMPLE_NOTES: dict[str, str] = {
    "Physics/Quantum Computing.md": """
---
tags: [physics, computing, quantum]
---
# Quantum Computing

## Qubits

A qubit is the fundamental unit of quantum information. Unlike a classical bit
which is either 0 or 1, a qubit can exist in a superposition of both states
simultaneously. This property, combined with entanglement and interference,
gives quantum computers their extraordinary power.

## Entanglement

Quantum entanglement occurs when two particles become correlated so that the
state of one instantly influences the other, regardless of the distance between
them. Einstein called this "spooky action at a distance". Entanglement is a key
resource for quantum teleportation and quantum cryptography.

## Quantum Gates

Quantum gates manipulate qubits analogously to classical logic gates. Common
gates include the Hadamard gate (creates superposition), the CNOT gate
(entangles qubits), and the Toffoli gate (universal reversible gate).
""",
    "Programming/Python Async.md": """
---
tags: [programming, python, async]
---
# Python Async Programming

## asyncio

Python's asyncio library provides tools for writing concurrent code using the
async/await syntax. It uses a single-threaded event loop to multiplex I/O
operations, making it ideal for network-bound workloads like web servers and
API clients.

## Coroutines

A coroutine is a function defined with `async def`. When called, it returns a
coroutine object that must be awaited. The event loop schedules coroutines and
switches between them at await points, enabling cooperative multitasking.

## Type Hints

Python's type system with mypy enables static analysis to catch bugs at
development time rather than runtime. Pydantic uses type hints to provide
runtime validation and serialisation.
""",
    "Cooking/Italian.md": """
---
tags: [cooking, food, italian]
---
# Italian Cooking

## Pasta Carbonara

Authentic carbonara uses only four ingredients: guanciale (cured pork cheek),
Pecorino Romano, eggs, and black pepper. No cream — the silky sauce comes
from emulsifying eggs with pasta cooking water and rendered fat from the
guanciale.

## Risotto

Risotto requires patient stirring and gradual addition of warm stock to
coax starch from Arborio rice, producing a creamy, velvety texture.
The final step — mantecatura — stirs in cold butter off the heat.

## Pizza Napoletana

True Neapolitan pizza uses Tipo 00 flour, San Marzano tomatoes, and fior di
latte mozzarella. It is baked at 450°C in a wood-fired oven for 60–90 seconds.
""",
    "Health/Sleep Science.md": """
---
tags: [health, neuroscience, sleep]
---
# Sleep Science

## Circadian Rhythm

The circadian rhythm is a 24-hour internal clock regulated by the
suprachiasmatic nucleus (SCN) in the hypothalamus. Light exposure through
the retina resets this clock daily, synchronising sleep-wake cycles with
the environment.

## REM Sleep

Rapid Eye Movement (REM) sleep is associated with vivid dreaming and memory
consolidation. The hippocampus replays experiences during REM, transferring
memories to the neocortex for long-term storage.

## Sleep Deprivation

Chronic sleep deprivation impairs cognitive function, immune response, and
metabolic regulation. Even modest reductions — sleeping 6 hours instead of
8 — accumulate significant cognitive debt within days.
""",
    "Finance/Investing.md": """
---
tags: [finance, investing, economics]
---
# Investing Fundamentals

## Compound Interest

Compound interest is the eighth wonder of the world. Returns earned on an
investment are reinvested, so future returns are earned on a larger base.
A 7% annual return doubles an investment in approximately 10 years (rule of 72).

## Index Funds

Passive index funds track a market index like the S&P 500. Because they
minimise trading and management costs, they consistently outperform the
majority of actively managed funds over long time horizons.

## Risk and Diversification

Diversification across uncorrelated assets reduces portfolio volatility
without sacrificing expected return. Modern Portfolio Theory, developed by
Harry Markowitz, formalises this with the efficient frontier.
""",
}


# ── Fake embedder (word-hash, no download) ────────────────────────────────────


def _word_hash_encode(texts: list[str]) -> np.ndarray:
    vecs: list[np.ndarray] = []
    for text in texts:
        v = np.zeros(DIMS, dtype=np.float32)
        for word in text.lower().split():
            v[abs(hash(word)) % DIMS] += 1.0
        norm = float(np.linalg.norm(v))
        if norm > 0:
            v /= norm
        vecs.append(v)
    return np.array(vecs, dtype=np.float32)


def _make_fake_embedder() -> Embedder:
    e = Embedder.__new__(Embedder)
    e.encode = _word_hash_encode  # type: ignore[method-assign]
    e.dims = DIMS
    return e


# ── Formatting helpers ────────────────────────────────────────────────────────

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
RESET = "\033[0m"
BAR = "─" * 72


def _score_bar(score: float, width: int = 20) -> str:
    filled = round(score * width)
    return f"[{'█' * filled}{'░' * (width - filled)}] {score:.2f}"


def _print_results(results: list, query: str) -> None:
    print(f"\n{BOLD}Query:{RESET} {CYAN}{query!r}{RESET}")
    print(BAR)
    if not results:
        print("  (no results)")
        return
    for i, r in enumerate(results, 1):
        path = Path(r.file_path).name
        header = f" › {r.header_path}" if r.header_path else ""
        excerpt = r.content.replace("\n", " ").strip()
        excerpt = textwrap.shorten(excerpt, width=80, placeholder="…")
        print(
            f"  {BOLD}{i}.{RESET} {GREEN}{path}{RESET}{DIM}{header}{RESET}\n"
            f"     {YELLOW}{_score_bar(r.score)}{RESET}\n"
            f"     {DIM}{excerpt}{RESET}\n"
        )


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="obsidian-search demo")
    parser.add_argument("--vault", help="Path to an existing vault (indexes .md files)")
    parser.add_argument(
        "--real-model",
        action="store_true",
        help="Use nomic-embed-text-v1.5 instead of the word-hash embedder",
    )
    args = parser.parse_args()

    # ── Build vault ───────────────────────────────────────────────────────────
    tmp_dir: tempfile.TemporaryDirectory | None = None
    if args.vault:
        vault_path = Path(args.vault)
        note_paths = list(vault_path.rglob("*.md"))
        print(f"{BOLD}Vault:{RESET} {vault_path}  ({len(note_paths)} .md files found)")
    else:
        tmp_dir = tempfile.TemporaryDirectory()
        vault_path = Path(tmp_dir.name)
        for rel, content in SAMPLE_NOTES.items():
            p = vault_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content.strip())
        note_paths = list(vault_path.rglob("*.md"))
        print(f"{BOLD}Vault:{RESET} sample notes ({len(note_paths)} files)")

    # ── Embedder ──────────────────────────────────────────────────────────────
    if args.real_model:
        print(f"{BOLD}Embedder:{RESET} nomic-embed-text-v1.5 (loading…)")
        embedder = Embedder()
        embedder._load()  # trigger download now so timing is visible
    else:
        print(f"{BOLD}Embedder:{RESET} word-hash (deterministic, no download)")
        embedder = _make_fake_embedder()

    # ── Store + settings ──────────────────────────────────────────────────────
    settings = Settings(vault_path=str(vault_path), chunk_min_tokens=5)
    settings.db_dir.mkdir(parents=True, exist_ok=True)

    store = VectorStore(settings.db_path)
    store.initialize(dims=DIMS)

    # ── Index ─────────────────────────────────────────────────────────────────
    pipeline = IndexingPipeline(settings=settings, store=store, embedder=embedder)
    print(f"\n{BOLD}Indexing {len(note_paths)} notes…{RESET}")
    total_chunks = 0
    for path in sorted(note_paths):
        result = pipeline.index_file(path)
        rel = path.relative_to(vault_path)
        print(f"  {DIM}{rel}{RESET}  →  {result.chunks_added} chunks")
        total_chunks += result.chunks_added

    stats = store.stats()
    print(f"\n{BOLD}Index ready:{RESET} {stats['total_chunks']} chunks "
          f"across {stats['total_documents']} documents")

    # ── Search demo ───────────────────────────────────────────────────────────
    searcher = Searcher(settings=settings, store=store, embedder=embedder)

    queries = [
        ("quantum entanglement superposition", None, None),
        ("async python coroutine event loop", None, None),
        ("pasta recipe italian dinner", None, None),
        ("sleep memory consolidation brain", None, None),
        ("compound interest investing returns", None, None),
        ("quantum computing", None, ["physics"]),        # tag filter
        ("programming language", ["markdown"], None),   # source_type filter
    ]

    print(f"\n{BOLD}{'═' * 72}{RESET}")
    print(f"{BOLD}  SEARCH DEMO{RESET}")
    print(f"{BOLD}{'═' * 72}{RESET}")

    for query, source_types, tags in queries:
        label = query
        if tags:
            label += f"  {DIM}[tag filter: {tags}]{RESET}"
        if source_types:
            label += f"  {DIM}[source: {source_types}]{RESET}"
        results = searcher.search(query, top_k=3, source_types=source_types, tags=tags)
        _print_results(results, label)

    # ── Interactive mode ──────────────────────────────────────────────────────
    print(f"\n{BOLD}{'═' * 72}{RESET}")
    print(f"{BOLD}  INTERACTIVE SEARCH{RESET}  {DIM}(Ctrl-C or empty query to quit){RESET}")
    print(f"{BOLD}{'═' * 72}{RESET}\n")

    try:
        while True:
            query = input(f"{BOLD}Search:{RESET} ").strip()
            if not query:
                break
            results = searcher.search(query, top_k=5)
            _print_results(results, query)
    except (KeyboardInterrupt, EOFError):
        pass

    if tmp_dir:
        tmp_dir.cleanup()
    store.close()
    print(f"\n{DIM}Done.{RESET}")


if __name__ == "__main__":
    main()
