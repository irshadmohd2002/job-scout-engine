"""`job-scout version`, `python -m job_scout`, and module entry point
(architecture.md section 15; decisions.md D-023)."""

from __future__ import annotations

import subprocess
import sys
from importlib.metadata import version as package_version

from typer.testing import CliRunner

from job_scout.cli import PACKAGE_DISTRIBUTION_NAME, app

runner = CliRunner()


def test_version_command_returns_installed_package_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == package_version(PACKAGE_DISTRIBUTION_NAME)


def test_python_dash_m_job_scout_help() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "job_scout", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "run-once" in completed.stdout
    assert "plan" in completed.stdout
    assert "init" in completed.stdout
    assert "version" in completed.stdout


def test_python_dash_m_job_scout_version() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "job_scout", "version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == package_version(PACKAGE_DISTRIBUTION_NAME)


def test_plan_and_run_once_commands_still_registered() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "plan" in result.stdout
    assert "run-once" in result.stdout
    assert "init" in result.stdout
    assert "version" in result.stdout
