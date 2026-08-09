"""`job-scout init` (architecture.md section 15.3; decisions.md D-019/D-020).

Idempotent, non-interactive bootstrap of a user's data directory: creates
config/data/logs/cache, copies the packaged templates to their real
filenames (never overwriting an existing file), and initialises the SQLite
database. Never creates `.env` or any credential value — every user
supplies their own (decisions.md D-019). This is a distribution
*foundation*, not an installer (decisions.md D-020): it never produces a
standalone executable or package-manager artifact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from job_scout.paths import AppPaths
from job_scout.repository.sqlite_repo import SqliteJobRepository
from job_scout.resources import template_text

# (packaged template name, AppPaths attribute it's copied to)
_TEMPLATE_TARGETS: tuple[tuple[str, str], ...] = (
    ("candidate_profile.example.yaml", "candidate_profile_path"),
    ("search_profiles.example.yaml", "search_profiles_path"),
    ("source_registry.example.yaml", "source_registry_path"),
    ("execution_limits.example.yaml", "execution_limits_path"),
    ("scoring_weights.example.yaml", "scoring_weights_path"),
    ("source_scoring_weights.example.yaml", "source_scoring_weights_path"),
    ("company_watchlist.example.yaml", "company_watchlist_path"),
)


@dataclass
class InitResult:
    created_dirs: list[Path] = field(default_factory=list)
    created_files: list[Path] = field(default_factory=list)
    skipped_files: list[Path] = field(default_factory=list)
    database_path: Path = Path()
    database_created: bool = False


def run_init(app_paths: AppPaths) -> InitResult:
    """Create/verify everything `AppPaths` names, without ever overwriting
    an existing config file or database. Safe to call repeatedly — a
    second call reports everything as already present. Raises OSError (not
    caught here) if the destination is unwritable; the CLI layer turns that
    into a clean, non-traceback exit."""
    result = InitResult(database_path=app_paths.database_path)

    for directory in (
        app_paths.application_data_dir,
        app_paths.config_dir,
        app_paths.data_dir,
        app_paths.logs_dir,
        app_paths.cache_dir,
    ):
        already_existed = directory.exists()
        directory.mkdir(parents=True, exist_ok=True)
        if not already_existed:
            result.created_dirs.append(directory)

    for template_name, target_attr in _TEMPLATE_TARGETS:
        target: Path = getattr(app_paths, target_attr)
        if target.exists():
            result.skipped_files.append(target)
            continue
        target.write_text(template_text(template_name), encoding="utf-8")
        result.created_files.append(target)

    database_already_existed = app_paths.database_path.exists()
    # Opening (and closing) the repository is enough to create the schema
    # and stamp/verify its version (repository/sqlite_repo.py) without
    # touching anything else.
    with SqliteJobRepository(app_paths.database_path):
        pass
    result.database_created = not database_already_existed

    return result
