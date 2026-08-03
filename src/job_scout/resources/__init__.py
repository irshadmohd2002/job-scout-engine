"""Packaged, profession-agnostic config templates (architecture.md section
15.2; decisions.md D-021).

The six files under `templates/` are the single canonical copy of every
example config file — `job-scout init` copies them into a user's data
directory, and `config.py`'s fallback for `execution_limits`/
`scoring_weights`/`source_scoring_weights` reads them directly (no
filesystem path required, so this works identically whether the package is
installed as a wheel, editable install, or from an sdist). Never write back
into these — they are read-only package data.
"""

from __future__ import annotations

import importlib.resources
from importlib.resources.abc import Traversable

TEMPLATE_NAMES: tuple[str, ...] = (
    "candidate_profile.example.yaml",
    "search_profiles.example.yaml",
    "source_registry.example.yaml",
    "execution_limits.example.yaml",
    "scoring_weights.example.yaml",
    "source_scoring_weights.example.yaml",
)


def templates_root() -> Traversable:
    return importlib.resources.files("job_scout.resources") / "templates"


def template_text(name: str) -> str:
    if name not in TEMPLATE_NAMES:
        raise ValueError(
            f"Unknown template '{name}'. Known templates: {', '.join(TEMPLATE_NAMES)}."
        )
    return (templates_root() / name).read_text(encoding="utf-8")
