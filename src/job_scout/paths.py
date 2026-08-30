"""Installed-application path resolution (architecture.md section 15.1).

`AppPaths` names every path the engine reads or writes at runtime, keeping
installed application resources (the package itself, read-only) separate
from mutable per-user data (config, database, logs, cache — all writable,
all outside the package/repository). See decisions.md D-018.

Resolution priority for the data-directory root (`application_data_dir`):
explicit `data_dir_override` argument (the CLI's `--data-dir`) >
`JOB_SCOUT_DATA_DIR` environment variable > platform user-data directory via
`platformdirs`. The current working directory plays no role here — see
decisions.md D-018. Per-file CLI overrides (`--candidate-profile`, etc.) are
resolved by callers (cli.py/config.py), not here: this module only ever
answers "where would things default to."
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

import platformdirs
from pydantic import BaseModel

APP_NAME = "job-scout"
DATA_DIR_ENV_VAR = "JOB_SCOUT_DATA_DIR"


class AppPaths(BaseModel):
    """Every path the engine reads or writes, resolved for one invocation."""

    application_data_dir: Path
    config_dir: Path
    data_dir: Path
    database_path: Path
    logs_dir: Path
    cache_dir: Path
    candidate_profile_path: Path
    search_profiles_path: Path
    source_registry_path: Path
    execution_limits_path: Path
    scoring_weights_path: Path
    source_scoring_weights_path: Path
    # Milestone 3 D3 (decisions.md D-057 point 8): same load-time
    # packaged-template-fallback treatment as scoring_weights_path/
    # execution_limits_path above.
    semantic_matching_path: Path
    # Milestone 2 Deliverable 5 step 6: user-specific like candidate_profile_path
    # (no packaged-template fallback at load time — config.py's
    # load_company_watchlist reads this path directly, MILESTONE_2.md
    # "config.py" changes).
    company_watchlist_path: Path
    # Milestone 2 Deliverable 5 step 10: same user-specific, no-fallback
    # treatment as company_watchlist_path above — metadata only (config.py's
    # load_sponsor_registries_config), never the imported register data
    # itself (that lives in the SQLite database, not YAML).
    sponsor_registries_path: Path
    # Milestone 3 D3 (decisions.md D-057 point 9): the local embedding
    # model's cache directory, under the existing per-user cache_dir —
    # never inside the repository, never fastembed's own default OS cache
    # path.
    embeddings_cache_dir: Path
    environment_file_path: Path | None = None


def resolve_app_paths(
    data_dir_override: Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> AppPaths:
    """Resolve `AppPaths` for one invocation.

    `env` defaults to the real process environment; tests that must never
    touch the real user-data directory should pass an explicit
    `data_dir_override` (and, if relevant, an explicit `env` mapping) rather
    than relying on `os.environ`.
    """
    environ = env if env is not None else os.environ

    if data_dir_override is not None:
        root = Path(data_dir_override)
    elif environ.get(DATA_DIR_ENV_VAR):
        root = Path(environ[DATA_DIR_ENV_VAR])
    else:
        root = Path(platformdirs.user_data_dir(APP_NAME, appauthor=False))

    config_dir = root / "config"
    data_dir = root / "data"

    return AppPaths(
        application_data_dir=root,
        config_dir=config_dir,
        data_dir=data_dir,
        database_path=data_dir / "job_scout.sqlite3",
        logs_dir=root / "logs",
        cache_dir=root / "cache",
        candidate_profile_path=config_dir / "candidate_profile.yaml",
        search_profiles_path=config_dir / "search_profiles.yaml",
        source_registry_path=config_dir / "source_registry.yaml",
        execution_limits_path=config_dir / "execution_limits.yaml",
        scoring_weights_path=config_dir / "scoring_weights.yaml",
        source_scoring_weights_path=config_dir / "source_scoring_weights.yaml",
        semantic_matching_path=config_dir / "semantic_matching.yaml",
        company_watchlist_path=config_dir / "company_watchlist.yaml",
        sponsor_registries_path=config_dir / "sponsor_registries.yaml",
        embeddings_cache_dir=root / "cache" / "embeddings",
        environment_file_path=root / ".env",
    )
