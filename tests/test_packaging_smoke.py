"""Opt-in packaging/clean-install smoke test (Milestone 1.1 task K).

Builds a real wheel, installs it into a throwaway virtual environment, and
runs the CLI from a directory outside this repository — proving the
package is genuinely installable and runnable without any access to the
repository (architecture.md section 15; decisions.md D-018/D-020).

Skipped by default (pyproject.toml addopts excludes the `packaging`
marker, same treatment as `integration`). Run explicitly with
`pytest -m packaging`. Slow: builds a wheel, creates a venv, and installs
real dependencies from PyPI, so it needs network access.
"""

from __future__ import annotations

import subprocess
import sys
import venv
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.packaging

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(args: list[str], *, cwd: Path, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
    )


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    dist_dir = tmp_path_factory.mktemp("dist")
    result = _run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist_dir)],
        cwd=REPO_ROOT,
        timeout=180,
    )
    assert result.returncode == 0, f"wheel build failed:\n{result.stdout}\n{result.stderr}"
    wheels = list(dist_dir.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, found {wheels}"
    return wheels[0]


EXPECTED_TEMPLATE_NAMES = {
    "candidate_profile.example.yaml",
    "search_profiles.example.yaml",
    "scoring_weights.example.yaml",
    "source_scoring_weights.example.yaml",
    "execution_limits.example.yaml",
    "source_registry.example.yaml",
    # Milestone 2 Deliverable 5 steps 6/10 (decisions.md D-047/D-049): two
    # new templates, alongside the original six, both copied by
    # `job-scout init` the same never-overwrite way as the others.
    "company_watchlist.example.yaml",
    "sponsor_registries.example.yaml",
}


def test_wheel_contains_packaged_templates(built_wheel: Path) -> None:
    with zipfile.ZipFile(built_wheel) as zf:
        names = zf.namelist()
    template_entries = [n for n in names if "resources/templates/" in n and n.endswith(".yaml")]
    template_basenames = {Path(n).name for n in template_entries}
    assert template_basenames == EXPECTED_TEMPLATE_NAMES, (
        f"expected {sorted(EXPECTED_TEMPLATE_NAMES)} templates in wheel, "
        f"found: {sorted(template_basenames)}"
    )
    assert len(template_entries) == len(EXPECTED_TEMPLATE_NAMES), (
        f"expected {len(EXPECTED_TEMPLATE_NAMES)} templates in wheel, found: {template_entries}"
    )


@pytest.fixture(scope="module")
def clean_venv_python(
    built_wheel: Path, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    venv_dir = tmp_path_factory.mktemp("venv") / "env"
    venv.EnvBuilder(with_pip=True).create(venv_dir)
    venv_python = (
        venv_dir / "Scripts" / "python.exe"
        if sys.platform == "win32"
        else venv_dir / "bin" / "python"
    )
    assert venv_python.exists()

    install = _run(
        [str(venv_python), "-m", "pip", "install", "--quiet", str(built_wheel)],
        cwd=tmp_path_factory.getbasetemp(),
        timeout=300,
    )
    assert install.returncode == 0, f"wheel install failed:\n{install.stdout}\n{install.stderr}"
    return venv_python


def test_clean_install_runs_outside_the_repository(
    clean_venv_python: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    # A working directory that shares nothing with the repository or with
    # the venv itself — proves no repo access is required.
    outside_cwd = tmp_path_factory.mktemp("outside-repo-cwd")
    data_dir = tmp_path_factory.mktemp("smoke-data-dir")

    version_result = _run([str(clean_venv_python), "-m", "job_scout", "version"], cwd=outside_cwd)
    assert version_result.returncode == 0, version_result.stderr
    assert version_result.stdout.strip()

    init_result = _run(
        [str(clean_venv_python), "-m", "job_scout", "init", "--data-dir", str(data_dir)],
        cwd=outside_cwd,
    )
    assert init_result.returncode == 0, init_result.stdout + init_result.stderr
    assert (data_dir / "config" / "candidate_profile.yaml").exists()
    assert (data_dir / "config" / "search_profiles.yaml").exists()
    assert (data_dir / "data" / "job_scout.sqlite3").exists()

    plan_result = _run(
        [
            str(clean_venv_python),
            "-m",
            "job_scout",
            "plan",
            "--profile",
            "example-profile",
            "--data-dir",
            str(data_dir),
        ],
        cwd=outside_cwd,
    )
    assert plan_result.returncode == 0, plan_result.stdout + plan_result.stderr
    assert "Search Execution Plan" in plan_result.stdout
    assert "adzuna_api" in plan_result.stdout

    # outside_cwd and data_dir are both under pytest's own tmp factory,
    # entirely separate from REPO_ROOT (only ever used above to build the
    # wheel) — every command above ran with no path anywhere near the repo,
    # which is the "no repository access required" proof this test exists
    # to give.
