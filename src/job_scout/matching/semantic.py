"""Stage 3 semantic-similarity backend boundary (MILESTONE_3.md D3;
decisions.md D-052, D-057).

Phase 1 scope only: the `Embedder` protocol, the local `fastembed` backend,
and a lazy default-backend factory. No Stage 5 integration and no
`SemanticResult` redesign yet — decisions.md D-057's finalized evidence
schema and rescue-only scoring wiring land in a later Milestone 3 D3
implementation phase.

`fastembed`/`onnxruntime` types never leak past this module (decisions.md
D-052) — every caller, including a future Stage 3 call site, only ever sees
the `Embedder` protocol and `SemanticBackendUnavailable`. Importing this
module triggers no model load and no network call; only a backend's first
real `.embed()` call does (decisions.md D-057 point 2).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class Embedder(Protocol):
    """Narrow embedding interface Stage 3 depends on. A future milestone
    could substitute a different backend (local or API) without
    `matching/scoring.py` changing how it consumes the result (decisions.md
    D-052)."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class SemanticBackendUnavailable(Exception):
    """Raised when the local embedding backend cannot be constructed or
    used: `fastembed` is not installed, the model download failed with no
    usable local cache, or the local cache is corrupted. A future Stage 3
    call site catches this once — it must never abort `run-once`/
    `evaluate` (decisions.md D-057 point 3); that call site does not exist
    yet in this phase."""


class FastEmbedBackend:
    """`Embedder` over `fastembed`'s `TextEmbedding` (decisions.md D-057
    point 1: `BAAI/bge-small-en-v1.5`, quantized ONNX, CPU-only via ONNX
    Runtime — no PyTorch, no vector database, no API/LLM call). Construction
    stores configuration only; the real model — and its one-time download
    into `cache_dir` — is loaded lazily, inside the first `.embed()` call,
    never at import or construction time."""

    def __init__(self, model_name: str, cache_dir: Path) -> None:
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._model: Any | None = None

    def _loaded_model(self) -> Any:
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:
                raise SemanticBackendUnavailable(
                    "fastembed is not installed. Install the 'semantic' optional "
                    "extra (pip install 'job-scout-engine[semantic]') to use "
                    f"embedding-based matching: {exc}"
                ) from exc
            try:
                self._cache_dir.mkdir(parents=True, exist_ok=True)
                self._model = TextEmbedding(
                    model_name=self._model_name, cache_dir=str(self._cache_dir)
                )
            except SemanticBackendUnavailable:
                raise
            except Exception as exc:
                raise SemanticBackendUnavailable(
                    f"Could not load embedding model '{self._model_name}': {exc}"
                ) from exc
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._loaded_model()
        try:
            return [list(vector) for vector in model.embed(texts)]
        except SemanticBackendUnavailable:
            raise
        except Exception as exc:
            raise SemanticBackendUnavailable(f"Embedding computation failed: {exc}") from exc


def get_default_embedder(model_name: str, cache_dir: Path) -> Embedder:
    """Lazy default-backend factory (decisions.md D-057 point 2): returns an
    `Embedder` with no model load and no network call yet — only its first
    real `.embed()` call triggers `fastembed`'s download/load, and only
    then can `SemanticBackendUnavailable` be raised."""
    return FastEmbedBackend(model_name=model_name, cache_dir=cache_dir)
