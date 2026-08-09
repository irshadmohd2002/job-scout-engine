"""AppPaths / resolve_app_paths (architecture.md section 15.1).

Every test here passes an explicit data_dir_override and/or an explicit env
mapping — never the real process environment or the real platformdirs
location — so this suite never touches the actual user-data directory on
whatever machine runs it.
"""

from __future__ import annotations

from pathlib import Path

import platformdirs

from job_scout.paths import DATA_DIR_ENV_VAR, resolve_app_paths


def test_platform_appropriate_default_when_nothing_else_set() -> None:
    expected = Path(platformdirs.user_data_dir("job-scout", appauthor=False))
    app_paths = resolve_app_paths(env={})
    assert app_paths.application_data_dir == expected
    assert app_paths.config_dir == expected / "config"
    assert app_paths.data_dir == expected / "data"
    assert app_paths.database_path == expected / "data" / "job_scout.sqlite3"
    assert app_paths.logs_dir == expected / "logs"
    assert app_paths.cache_dir == expected / "cache"
    assert app_paths.candidate_profile_path == expected / "config" / "candidate_profile.yaml"
    assert app_paths.company_watchlist_path == expected / "config" / "company_watchlist.yaml"
    assert app_paths.environment_file_path == expected / ".env"


def test_job_scout_data_dir_env_var_used_when_no_override(tmp_path: Path) -> None:
    app_paths = resolve_app_paths(env={DATA_DIR_ENV_VAR: str(tmp_path)})
    assert app_paths.application_data_dir == tmp_path


def test_data_dir_override_beats_env_var(tmp_path: Path) -> None:
    override = tmp_path / "override"
    env_dir = tmp_path / "from-env"
    app_paths = resolve_app_paths(override, env={DATA_DIR_ENV_VAR: str(env_dir)})
    assert app_paths.application_data_dir == override


def test_no_override_and_no_env_var_falls_back_to_platformdirs(tmp_path: Path) -> None:
    # An empty env mapping (not the real process environment) proves the
    # platformdirs fallback path, without ever consulting/depending on the
    # real machine's JOB_SCOUT_DATA_DIR if one happens to be set.
    app_paths = resolve_app_paths(env={})
    assert app_paths.application_data_dir == Path(
        platformdirs.user_data_dir("job-scout", appauthor=False)
    )


def test_cwd_does_not_affect_resolved_paths(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    other_dir = tmp_path / "somewhere-else"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)
    explicit = tmp_path / "explicit-data-dir"
    app_paths = resolve_app_paths(explicit, env={})
    assert app_paths.application_data_dir == explicit
    assert str(other_dir) not in str(app_paths.application_data_dir)


def test_resolve_app_paths_never_touches_real_env_when_env_mapping_given(tmp_path: Path) -> None:
    """Explicit `env={}` fully isolates resolution from the real process
    environment, even if the real machine happens to have JOB_SCOUT_DATA_DIR
    set — this is how the rest of this suite guarantees it never reads or
    writes the real user-data directory."""
    app_paths = resolve_app_paths(tmp_path, env={DATA_DIR_ENV_VAR: "/should/be/ignored"})
    assert app_paths.application_data_dir == tmp_path
