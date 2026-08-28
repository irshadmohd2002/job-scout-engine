"""`job-scout init` CLI-printed output (as opposed to `run_init` itself,
already covered by test_init.py)."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from job_scout.cli import app

runner = CliRunner()


def test_init_next_steps_mentions_watchlist_requirement_for_greenhouse_lever(
    tmp_path: Path,
) -> None:
    """MILESTONE_2.md R-10: watchlist-scoped sources (Greenhouse, Lever)
    fetch nothing until company_watchlist.yaml is populated — this must be
    documented clearly in `job-scout init`'s next-steps output."""
    result = runner.invoke(app, ["init", "--data-dir", str(tmp_path / "data")])
    assert result.exit_code == 0, result.output
    assert "Next steps" in result.output
    assert "company_watchlist.yaml" in result.output
    assert "Greenhouse" in result.output
    assert "Lever" in result.output
