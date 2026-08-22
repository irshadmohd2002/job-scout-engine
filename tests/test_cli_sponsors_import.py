"""`job-scout sponsors import` CLI round-trip (Milestone 2 Deliverable 5 step
10; MILESTONE_2.md acceptance criterion: "round-trips a fixture CSV into
sponsor_registry_entries and is queryable via sponsor_registry.find_sponsor_match").
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from job_scout.cli import app
from job_scout.repository.sqlite_repo import SqliteJobRepository
from job_scout.source_intelligence.sponsor_registry import find_sponsor_match

runner = CliRunner()

_UK_CSV = """Organisation Name,Town/City,County,Type & Rating,Route
Acme Robotics Ltd,London,,Worker (A rating),Skilled Worker
Bramblewood Care Ltd,Leeds,West Yorkshire,Worker (A rating),Skilled Worker
"""


def test_sponsors_import_round_trips_csv_and_is_queryable(tmp_path: Path) -> None:
    csv_path = tmp_path / "uk_sponsors.csv"
    csv_path.write_text(_UK_CSV, encoding="utf-8")
    db_path = tmp_path / "job_scout.sqlite3"

    result = runner.invoke(
        app,
        [
            "sponsors",
            "import",
            str(csv_path),
            "--country",
            "GB",
            "--register",
            "uk_home_office_sponsor_list",
            "--db-path",
            str(db_path),
            "--data-dir",
            str(tmp_path / "data"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Imported 2 sponsor register entries" in result.output

    with SqliteJobRepository(db_path) as repo:
        row_count = repo._conn.execute("SELECT COUNT(*) FROM sponsor_registry_entries").fetchone()[
            0
        ]
        assert row_count == 2
        match = find_sponsor_match(repo, "Acme Robotics Ltd", "GB")
        assert match is not None
        assert match.registered_name == "Acme Robotics Ltd"


def test_sponsors_import_missing_file_errors(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "sponsors",
            "import",
            str(tmp_path / "does_not_exist.csv"),
            "--country",
            "GB",
            "--register",
            "uk_home_office_sponsor_list",
            "--db-path",
            str(tmp_path / "job_scout.sqlite3"),
            "--data-dir",
            str(tmp_path / "data"),
        ],
    )
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_sponsors_import_unknown_register_errors(tmp_path: Path) -> None:
    csv_path = tmp_path / "uk_sponsors.csv"
    csv_path.write_text(_UK_CSV, encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "sponsors",
            "import",
            str(csv_path),
            "--country",
            "NL",
            "--register",
            "ind_recognised_sponsors",
            "--db-path",
            str(tmp_path / "job_scout.sqlite3"),
            "--data-dir",
            str(tmp_path / "data"),
        ],
    )
    assert result.exit_code == 1
    assert "unknown register_name" in result.output.lower()


def test_sponsors_import_replaces_on_reimport(tmp_path: Path) -> None:
    csv_path = tmp_path / "uk_sponsors.csv"
    csv_path.write_text(_UK_CSV, encoding="utf-8")
    db_path = tmp_path / "job_scout.sqlite3"
    data_dir = str(tmp_path / "data")

    def _import() -> None:
        result = runner.invoke(
            app,
            [
                "sponsors",
                "import",
                str(csv_path),
                "--country",
                "GB",
                "--register",
                "uk_home_office_sponsor_list",
                "--db-path",
                str(db_path),
                "--data-dir",
                data_dir,
            ],
        )
        assert result.exit_code == 0, result.output

    _import()
    _import()

    with SqliteJobRepository(db_path) as repo:
        row_count = repo._conn.execute(
            "SELECT COUNT(*) FROM sponsor_registry_entries WHERE country = 'GB'"
        ).fetchone()[0]
        assert row_count == 2  # re-import replaces, never appends
