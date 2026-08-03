"""CWD independence (architecture.md section 15.1; decisions.md D-018).

Config loading and the `plan` command must work identically regardless of
the process's current working directory — no default path may assume the
repository root or any other specific CWD. Complements test_paths.py
(paths.py itself) and test_config.py (individual loaders) by exercising the
CLI end to end from a directory that shares nothing with the repo or the
user's real data directory.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from job_scout.bootstrap import run_init
from job_scout.cli import app
from job_scout.paths import resolve_app_paths

runner = CliRunner()


def test_config_loading_works_outside_the_repository(
    tmp_path: Path, monkeypatch  # type: ignore[no-untyped-def]
) -> None:
    unrelated_cwd = tmp_path / "not-the-repo"
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)

    data_dir = tmp_path / "data"
    app_paths = resolve_app_paths(data_dir)
    run_init(app_paths)

    from job_scout import config

    profile = config.load_candidate_profile(data_dir=data_dir)
    assert profile.candidate_id


def test_plan_works_outside_the_repository_after_init(
    tmp_path: Path, monkeypatch  # type: ignore[no-untyped-def]
) -> None:
    unrelated_cwd = tmp_path / "not-the-repo"
    unrelated_cwd.mkdir()

    data_dir = tmp_path / "data"
    app_paths = resolve_app_paths(data_dir)
    run_init(app_paths)

    monkeypatch.chdir(unrelated_cwd)
    result = runner.invoke(
        app, ["plan", "--profile", "example-profile", "--data-dir", str(data_dir)]
    )
    assert result.exit_code == 0, result.stdout
    assert "Search Execution Plan" in result.stdout
    assert "adzuna_api" in result.stdout
