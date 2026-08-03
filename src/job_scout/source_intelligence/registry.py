"""Source registry loading/indexing for the planner.

YAML parsing and validation itself lives in config.py (config.py's stated
role per architecture.md section 12 is "env + YAML loading for all config,
... source registry"). This module adds the registry-specific structuring
the planner needs on top of an already-loaded list[SourceRegistryEntry]:
lookup by id and duplicate-id detection.
"""

from __future__ import annotations

from pathlib import Path

from job_scout.config import ConfigError, load_source_registry
from job_scout.models import SourceRegistryEntry

__all__ = ["load_registry", "index_by_id"]


def load_registry(path: Path | None = None) -> list[SourceRegistryEntry]:
    return load_source_registry(path)


def index_by_id(entries: list[SourceRegistryEntry]) -> dict[str, SourceRegistryEntry]:
    by_id: dict[str, SourceRegistryEntry] = {}
    for entry in entries:
        if entry.source_id in by_id:
            raise ConfigError(f"Duplicate source_id '{entry.source_id}' in registry.")
        by_id[entry.source_id] = entry
    return by_id
