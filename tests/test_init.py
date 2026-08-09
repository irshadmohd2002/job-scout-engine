"""`job-scout init` (architecture.md section 15.3; decisions.md D-019/D-020).

Every test uses an explicit tmp_path-derived data directory — never the
real user-data directory."""

from __future__ import annotations

from pathlib import Path

import pytest

from job_scout import config
from job_scout.bootstrap import run_init
from job_scout.paths import resolve_app_paths


def test_first_run_creates_expected_directories_and_files(tmp_path: Path) -> None:
    app_paths = resolve_app_paths(tmp_path / "data")
    result = run_init(app_paths)

    assert app_paths.config_dir.is_dir()
    assert app_paths.data_dir.is_dir()
    assert app_paths.logs_dir.is_dir()
    assert app_paths.cache_dir.is_dir()

    assert app_paths.candidate_profile_path.exists()
    assert app_paths.search_profiles_path.exists()
    assert app_paths.source_registry_path.exists()
    assert app_paths.execution_limits_path.exists()
    assert app_paths.scoring_weights_path.exists()
    assert app_paths.source_scoring_weights_path.exists()
    assert app_paths.company_watchlist_path.exists()
    assert app_paths.database_path.exists()

    assert len(result.created_dirs) >= 4
    assert len(result.created_files) == 7
    assert result.skipped_files == []
    assert result.database_created is True


def test_repeated_run_is_idempotent(tmp_path: Path) -> None:
    app_paths = resolve_app_paths(tmp_path / "data")
    first = run_init(app_paths)
    second = run_init(app_paths)

    assert second.created_dirs == []
    assert second.created_files == []
    assert set(second.skipped_files) == set(first.created_files)
    assert second.database_created is False


def test_existing_config_file_is_never_overwritten(tmp_path: Path) -> None:
    app_paths = resolve_app_paths(tmp_path / "data")
    app_paths.config_dir.mkdir(parents=True)
    custom_content = "candidate_id: my-real-profile\n"
    app_paths.candidate_profile_path.write_text(custom_content, encoding="utf-8")

    result = run_init(app_paths)

    assert app_paths.candidate_profile_path.read_text(encoding="utf-8") == custom_content
    assert app_paths.candidate_profile_path in result.skipped_files
    assert app_paths.candidate_profile_path not in result.created_files


def test_existing_company_watchlist_is_never_overwritten(tmp_path: Path) -> None:
    app_paths = resolve_app_paths(tmp_path / "data")
    app_paths.config_dir.mkdir(parents=True)
    custom_content = "companies:\n  - company_name: my-real-company\n"
    app_paths.company_watchlist_path.write_text(custom_content, encoding="utf-8")

    result = run_init(app_paths)

    assert app_paths.company_watchlist_path.read_text(encoding="utf-8") == custom_content
    assert app_paths.company_watchlist_path in result.skipped_files
    assert app_paths.company_watchlist_path not in result.created_files


def test_partial_installation_is_completed_safely(tmp_path: Path) -> None:
    """Simulates an interrupted first run: some dirs/files exist, others
    don't. A second run must fill in exactly what's missing, without
    touching what's already there."""
    app_paths = resolve_app_paths(tmp_path / "data")
    app_paths.config_dir.mkdir(parents=True)
    custom_content = "candidate_id: partial-run\n"
    app_paths.candidate_profile_path.write_text(custom_content, encoding="utf-8")
    # search_profiles.yaml, source_registry.yaml, etc. and data/logs/cache
    # dirs deliberately not created yet.

    result = run_init(app_paths)

    assert app_paths.candidate_profile_path.read_text(encoding="utf-8") == custom_content
    assert app_paths.search_profiles_path.exists()
    assert app_paths.source_registry_path.exists()
    assert app_paths.data_dir.is_dir()
    assert app_paths.logs_dir.is_dir()
    assert app_paths.cache_dir.is_dir()
    assert app_paths.database_path.exists()
    assert app_paths.candidate_profile_path in result.skipped_files
    assert app_paths.search_profiles_path in result.created_files


def test_data_dir_override_is_honoured(tmp_path: Path) -> None:
    custom_root = tmp_path / "custom-root"
    app_paths = resolve_app_paths(custom_root)
    run_init(app_paths)
    assert app_paths.application_data_dir == custom_root
    assert app_paths.database_path.is_relative_to(custom_root)


@pytest.mark.skipif(
    __import__("sys").platform == "win32",
    reason="chmod-based unwritable-directory simulation is unreliable on Windows",
)
def test_unwritable_destination_fails_cleanly(tmp_path: Path) -> None:
    root = tmp_path / "readonly-root"
    root.mkdir()
    root.chmod(0o500)
    app_paths = resolve_app_paths(root / "data")
    try:
        with pytest.raises(OSError):
            run_init(app_paths)
    finally:
        root.chmod(0o700)


def test_sqlite_database_resides_under_selected_data_directory(tmp_path: Path) -> None:
    app_paths = resolve_app_paths(tmp_path / "data")
    run_init(app_paths)
    assert app_paths.database_path.is_relative_to(app_paths.data_dir)
    assert app_paths.database_path.is_relative_to(tmp_path / "data")


def test_init_never_creates_a_populated_env_file(tmp_path: Path) -> None:
    app_paths = resolve_app_paths(tmp_path / "data")
    run_init(app_paths)
    assert app_paths.environment_file_path is not None
    assert not app_paths.environment_file_path.exists()


def test_generated_configuration_loads_successfully(tmp_path: Path) -> None:
    app_paths = resolve_app_paths(tmp_path / "data")
    run_init(app_paths)

    candidate_profile = config.load_candidate_profile(app_paths.candidate_profile_path)
    search_profiles = config.load_search_profiles(app_paths.search_profiles_path)
    registry = config.load_source_registry(app_paths.source_registry_path)
    execution_limits = config.load_execution_limits(app_paths.execution_limits_path)
    scoring_weights = config.load_scoring_weights(app_paths.scoring_weights_path)
    source_scoring_weights = config.load_source_scoring_weights(
        app_paths.source_scoring_weights_path
    )
    watchlist = config.load_company_watchlist(app_paths.company_watchlist_path)

    assert candidate_profile.candidate_id
    assert "example-profile" in search_profiles
    assert len(registry) > 0
    assert execution_limits.max_countries_per_run > 0
    assert abs(sum(scoring_weights.component_weights().values()) - 1.0) < 1e-6
    assert abs(sum(source_scoring_weights.component_weights().values()) - 1.0) < 1e-6
    assert len(watchlist) > 0
    assert all(entry.external_company_key for entry in watchlist)
