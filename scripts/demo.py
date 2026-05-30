#!/usr/bin/env python
"""
Demo and quality-comparison script for obsidian-search.

Usage:
    # Word-hash embedder (no download, instant):
    uv run --project packages/backend python scripts/demo.py

    # Real model (uses configured default — BAAI/bge-small-en-v1.5):
    uv run --project packages/backend python scripts/demo.py --model BAAI/bge-small-en-v1.5

    # Compare two models side-by-side to check quality:
    uv run --project packages/backend python scripts/demo.py \\
        --compare BAAI/bge-small-en-v1.5 nomic-ai/nomic-embed-text-v1.5

    # Use your own vault:
    uv run --project packages/backend python scripts/demo.py \\
        --vault /path/to/vault --model BAAI/bge-small-en-v1.5
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import textwrap
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "packages/backend/src"))

from obsidian_search.config import Settings
from obsidian_search.embedding.embedder import Embedder
from obsidian_search.ingestion.pipeline import IndexingPipeline
from obsidian_search.search.searcher import Searcher
from obsidian_search.store.vector_store import VectorStore

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

EVAL_QUERIES = [
    "quantum entanglement superposition",
    "async python coroutine event loop",
    "pasta recipe italian dinner",
    "sleep memory consolidation brain",
    "compound interest investing returns",
    "diet and cognitive performance",       # off-topic — good for precision check
    "machine learning neural network",      # off-topic
]

# ── ANSI helpers ──────────────────────────────────────────────────────────────

BOLD  = "\033[1m"
DIM   = "\033[2m"
GREEN = "\033[32m"
CYAN  = "\033[36m"
YELLOW = "\033[33m"
RED   = "\033[31m"
RESET = "\033[0m"
BAR   = "─" * 72


def _score_bar(score: float, width: int = 16) -> str:
    filled = round(score * width)
    return f"[{'█' * filled}{'░' * (width - filled)}] {score:.3f}"


def _print_results(results: list, query: str, label: str = "") -> None:
    header = f"{BOLD}Query:{RESET} {CYAN}{query!r}{RESET}"
    if label:
        header += f"  {DIM}({label}){RESET}"
    print(f"\n{header}")
    print(BAR)
    if not results:
        print(f"  {RED}(no results){RESET}")
        return
    for i, r in enumerate(results, 1):
        fname = Path(r.file_path).name
        hdr   = f" › {r.header_path}" if r.header_path else ""
        snip  = textwrap.shorten(r.content.replace("\n", " ").strip(), width=72, placeholder="…")
        print(
            f"  {BOLD}{i}.{RESET} {GREEN}{fname}{RESET}{DIM}{hdr}{RESET}\n"
            f"     {YELLOW}{_score_bar(r.score)}{RESET}\n"
            f"     {DIM}{snip}{RESET}\n"
        )


# ── Fake embedder (word-hash, no download) ────────────────────────────────────

_FAKE_DIMS = 384  # matches bge-small default


def _word_hash_encode(texts: list[str]) -> np.ndarray:
    vecs: list[np.ndarray] = []
    for text in texts:
        v = np.zeros(_FAKE_DIMS, dtype=np.float32)
        for word in text.lower().split():
            v[abs(hash(word)) % _FAKE_DIMS] += 1.0
        norm = float(np.linalg.norm(v))
        if norm > 0:
            v /= norm
        vecs.append(v)
    return np.array(vecs, dtype=np.float32)


def _make_fake_embedder() -> Embedder:
    import threading
    e = Embedder.__new__(Embedder)
    e.model_name = "word-hash"
    e.dims = _FAKE_DIMS
    e._model = object()      # truthy so _load() is a no-op
    e._sem = threading.Semaphore(1)
    e.encode = _word_hash_encode  # type: ignore[method-assign]
    return e


# ── Single-model run ──────────────────────────────────────────────────────────

def _build_index(
    embedder: Embedder,
    vault_path: Path,
    note_paths: list[Path],
    db_path: Path,
) -> tuple[Searcher, VectorStore]:
    settings = Settings(vault_path=str(vault_path), chunk_min_tokens=5)
    store = VectorStore(db_path)
    store.initialize(dims=embedder.dims)
    pipeline = IndexingPipeline(settings=settings, store=store, embedder=embedder)
    for path in sorted(note_paths):
        result = pipeline.index_file(path)
        rel = path.relative_to(vault_path)
        print(f"  {DIM}{rel}{RESET}  →  {result.chunks_added} chunks")
    return Searcher(settings=settings, store=store, embedder=embedder), store


# ── Compare mode ─────────────────────────────────────────────────────────────

def _run_compare(
    model_a: str,
    model_b: str,
    vault_path: Path,
    note_paths: list[Path],
) -> None:
    """Build indexes for two models, run EVAL_QUERIES, print results side by side."""
    import time

    pairs: list[tuple[str, Searcher, VectorStore, Path]] = []
    tmp_files: list[Path] = []

    for mname in (model_a, model_b):
        print(f"\n{BOLD}Loading {mname}…{RESET}")
        t0 = time.perf_counter()
        embedder = Embedder(model_name=mname)
        embedder._load()
        print(f"  model ready in {time.perf_counter()-t0:.1f}s  "
              f"({embedder.dims} dims)")

        tmp = Path(tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
        tmp_files.append(tmp)
        print(f"  indexing {len(note_paths)} notes…")
        searcher, store = _build_index(embedder, vault_path, note_paths, tmp)
        pairs.append((mname, searcher, store, tmp))

    short = [p[0].split("/")[-1] for p in pairs]

    print(f"\n{BOLD}{'═' * 72}{RESET}")
    print(f"{BOLD}  QUALITY COMPARISON: {short[0]}  vs  {short[1]}{RESET}")
    print(f"{BOLD}{'═' * 72}{RESET}")

    for query in EVAL_QUERIES:
        print(f"\n{BOLD}{CYAN}Q: {query!r}{RESET}")
        rows: list[list[str]] = []
        for mname, searcher, _, _ in pairs:
            results = searcher.search(query, top_k=3)
            col = [f"{BOLD}{mname.split('/')[-1]}{RESET}"]
            if not results:
                col.append(f"  {RED}(no results){RESET}")
            for i, r in enumerate(results, 1):
                fname = Path(r.file_path).name
                hdr   = f" › {r.header_path}" if r.header_path else ""
                col.append(
                    f"  {i}. {GREEN}{fname}{RESET}{DIM}{hdr}{RESET}  "
                    f"{YELLOW}{_score_bar(r.score)}{RESET}"
                )
            rows.append(col)

        # print columns side by side
        max_lines = max(len(c) for c in rows)
        for c in rows:
            while len(c) < max_lines:
                c.append("")
        for line_pair in zip(*rows):
            print("  |  ".join(line_pair))

    # cleanup
    for _, _, store, tmp in pairs:
        store.close()
        tmp.unlink(missing_ok=True)

    print(f"\n{DIM}Compare done. Check scores and file names above — "
          f"on-topic queries should surface matching notes with score ≥ 0.5.{RESET}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="obsidian-search demo / quality check")
    parser.add_argument("--vault", help="Path to an existing vault (indexes .md files)")
    parser.add_argument(
        "--model",
        default=None,
        metavar="MODEL",
        help="HuggingFace model name to use (default: word-hash fake embedder). "
             "Example: BAAI/bge-small-en-v1.5",
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("MODEL_A", "MODEL_B"),
        help="Compare two models side-by-side. "
             "Example: --compare BAAI/bge-small-en-v1.5 nomic-ai/nomic-embed-text-v1.5",
    )
    args = parser.parse_args()

    # ── Vault ─────────────────────────────────────────────────────────────────
    tmp_dir: tempfile.TemporaryDirectory | None = None
    if args.vault:
        vault_path = Path(args.vault)
        note_paths = list(vault_path.rglob("*.md"))
        print(f"{BOLD}Vault:{RESET} {vault_path}  ({len(note_paths)} .md files)")
    else:
        tmp_dir = tempfile.TemporaryDirectory()
        vault_path = Path(tmp_dir.name)
        for rel, content in SAMPLE_NOTES.items():
            p = vault_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content.strip())
        note_paths = list(vault_path.rglob("*.md"))
        print(f"{BOLD}Vault:{RESET} built-in sample notes ({len(note_paths)} files)")

    # ── Compare mode ──────────────────────────────────────────────────────────
    if args.compare:
        model_a, model_b = args.compare
        _run_compare(model_a, model_b, vault_path, note_paths)
        if tmp_dir:
            tmp_dir.cleanup()
        return

    # ── Single-model mode ─────────────────────────────────────────────────────
    if args.model:
        import time
        print(f"{BOLD}Embedder:{RESET} {args.model} (loading…)")
        t0 = time.perf_counter()
        embedder = Embedder(model_name=args.model)
        embedder._load()
        print(f"  ready in {time.perf_counter()-t0:.1f}s  ({embedder.dims} dims)")
    else:
        print(f"{BOLD}Embedder:{RESET} word-hash (deterministic, no download)")
        embedder = _make_fake_embedder()

    # ── Index ─────────────────────────────────────────────────────────────────
    settings = Settings(vault_path=str(vault_path), chunk_min_tokens=5)
    settings.db_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n{BOLD}Indexing {len(note_paths)} notes…{RESET}")
    searcher, store = _build_index(embedder, vault_path, note_paths, settings.db_path)
    s = store.stats()
    print(f"\n{BOLD}Index:{RESET} {s['total_chunks']} chunks / {s['total_documents']} docs")

    # ── Eval queries ──────────────────────────────────────────────────────────
    print(f"\n{BOLD}{'═' * 72}{RESET}")
    print(f"{BOLD}  EVAL QUERIES{RESET}  {DIM}(last 2 are off-topic — expect low scores){RESET}")
    print(f"{BOLD}{'═' * 72}{RESET}")
    for query in EVAL_QUERIES:
        results = searcher.search(query, top_k=3)
        _print_results(results, query)

    # ── Interactive ───────────────────────────────────────────────────────────
    print(f"\n{BOLD}{'═' * 72}{RESET}")
    print(f"{BOLD}  INTERACTIVE{RESET}  {DIM}(empty line to quit){RESET}")
    print(f"{BOLD}{'═' * 72}{RESET}\n")
    try:
        while True:
            query = input(f"{BOLD}Search:{RESET} ").strip()
            if not query:
                break
            _print_results(searcher.search(query, top_k=5), query)
    except (KeyboardInterrupt, EOFError):
        pass

    store.close()
    if tmp_dir:
        tmp_dir.cleanup()
    print(f"\n{DIM}Done.{RESET}")


if __name__ == "__main__":
    main()
