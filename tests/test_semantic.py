"""Stage 3 semantic-similarity backend boundary (MILESTONE_3.md D3;
decisions.md D-052/D-057), Phase 1 only.

Never imports fastembed and never downloads a model — the default suite
must run with no `[semantic]` extra installed (decisions.md D-057 point
10). The real-model opt-in suite is deferred to a later Milestone 3 D3
phase, per the Phase 1 instructions this test file implements.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from job_scout.matching.semantic import (
    Embedder,
    FastEmbedBackend,
    SemanticBackendUnavailable,
    get_default_embedder,
)


class StubEmbedder:
    """Deterministic `Embedder` stand-in for tests — a fixed-length vector
    per input text, no real computation, no import of fastembed."""

    def __init__(self, dimension: int = 4) -> None:
        self.dimension = dimension
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [[float(len(text))] * self.dimension for text in texts]


def test_stub_embedder_satisfies_embedder_protocol() -> None:
    embedder: Embedder = StubEmbedder()
    vectors = embedder.embed(["strategy manager", "software engineer"])
    assert len(vectors) == 2
    assert all(len(vector) == 4 for vector in vectors)


def test_stub_embedder_records_calls_deterministically() -> None:
    embedder = StubEmbedder(dimension=2)
    vectors = embedder.embed(["ab", "abcd"])
    assert vectors == [[2.0, 2.0], [4.0, 4.0]]
    assert embedder.calls == [["ab", "abcd"]]


def test_get_default_embedder_returns_fast_embed_backend(tmp_path: Path) -> None:
    embedder = get_default_embedder("BAAI/bge-small-en-v1.5", tmp_path / "embeddings")
    assert isinstance(embedder, FastEmbedBackend)


def test_constructing_default_embedder_triggers_no_model_load_or_import(
    tmp_path: Path,
) -> None:
    """Constructing the backend (or the factory) must not create the cache
    directory or import fastembed — only a real .embed() call may do
    either (decisions.md D-057 point 2)."""
    cache_dir = tmp_path / "embeddings"
    get_default_embedder("BAAI/bge-small-en-v1.5", cache_dir)
    assert not cache_dir.exists()
    assert "fastembed" not in sys.modules


def test_embed_raises_semantic_backend_unavailable_when_fastembed_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Forces `from fastembed import TextEmbedding` to raise ImportError
    # regardless of whether the real optional dependency happens to be
    # installed in this environment — Python treats a `None` entry in
    # sys.modules as "this import must fail."
    monkeypatch.setitem(sys.modules, "fastembed", None)
    backend = FastEmbedBackend("BAAI/bge-small-en-v1.5", tmp_path / "embeddings")

    with pytest.raises(SemanticBackendUnavailable) as excinfo:
        backend.embed(["strategy manager"])

    assert "semantic" in str(excinfo.value)


def test_embed_failure_does_not_create_cache_dir_when_import_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "fastembed", None)
    cache_dir = tmp_path / "embeddings"
    backend = FastEmbedBackend("BAAI/bge-small-en-v1.5", cache_dir)

    with pytest.raises(SemanticBackendUnavailable):
        backend.embed(["strategy manager"])

    assert not cache_dir.exists()
