"""`python -m job_scout` — the documented fallback entry point when the
`job-scout` console script isn't usable (decisions.md D-023). Fully
equivalent to the `job-scout` script: same Typer app, every command."""

from __future__ import annotations

from job_scout.cli import app

if __name__ == "__main__":
    app()
