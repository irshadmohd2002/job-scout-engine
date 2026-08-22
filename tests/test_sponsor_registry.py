"""Sponsor-register import/lookup (Milestone 2 Deliverable 5 step 10).

The UK CSV fixture below uses the real, verified gov.uk header for the
"Register of Worker and Temporary Worker licensed sponsors" export
(decisions.md D-050): `Organisation Name,Town/City,County,Type & Rating,
Route`. Company names are synthetic (CLAUDE.md hard constraint 8), not
copied from the live register.
"""

from __future__ import annotations

import pytest

from job_scout.repository.sqlite_repo import SqliteJobRepository
from job_scout.source_intelligence.sponsor_registry import (
    SponsorRegisterParseError,
    find_sponsor_match,
    import_sponsor_register,
    parse_uk_home_office_csv,
)

_UK_CSV = """Organisation Name,Town/City,County,Type & Rating,Route
 Acme Robotics Ltd,London,,Worker (A rating),Skilled Worker
 Bramblewood Care Ltd ,Leeds ,West Yorkshire,Worker (A rating),Skilled Worker
 CINDER FORGE LIMITED,Manchester,,Worker (A rating),Skilled Worker
"""


@pytest.fixture()
def repo(tmp_path):  # type: ignore[no-untyped-def]
    with SqliteJobRepository(tmp_path / "test.sqlite3") as r:
        yield r


def test_parse_uk_csv_extracts_normalized_entries() -> None:
    entries = parse_uk_home_office_csv(_UK_CSV, country="GB")
    assert len(entries) == 3
    acme = next(e for e in entries if e.registered_name == "Acme Robotics Ltd")
    assert acme.normalized_name == "acme robotics"
    assert acme.country == "GB"
    assert acme.register_name == "uk_home_office_sponsor_list"
    assert acme.license_status == "Worker (A rating)"


def test_parse_uk_csv_rejects_wrong_header() -> None:
    with pytest.raises(SponsorRegisterParseError):
        parse_uk_home_office_csv("Name,City\nAcme,London\n")


def test_import_then_find_exact_match(repo: SqliteJobRepository) -> None:
    count = import_sponsor_register(
        repo, _UK_CSV, country="GB", register_name="uk_home_office_sponsor_list"
    )
    assert count == 3

    match = find_sponsor_match(repo, "Acme Robotics Ltd", "GB")
    assert match is not None
    assert match.matched is True
    assert match.registered_name == "Acme Robotics Ltd"
    assert match.register_name == "uk_home_office_sponsor_list"
    assert 0.0 < match.confidence < 1.0


def test_find_match_is_case_and_punctuation_insensitive(repo: SqliteJobRepository) -> None:
    import_sponsor_register(
        repo, _UK_CSV, country="GB", register_name="uk_home_office_sponsor_list"
    )

    match = find_sponsor_match(repo, "ACME ROBOTICS, LTD.", "gb")
    assert match is not None
    assert match.registered_name == "Acme Robotics Ltd"


def test_deliberate_near_miss_does_not_match(repo: SqliteJobRepository) -> None:
    """A subsidiary/trading-name-style near miss must NOT match — M2 does
    exact normalized-name matching only (R-9, explicitly no fuzzy/alias
    matching)."""
    import_sponsor_register(
        repo, _UK_CSV, country="GB", register_name="uk_home_office_sponsor_list"
    )

    match = find_sponsor_match(repo, "Acme Robotics Europe Ltd", "GB")
    assert match is None


def test_empty_registry_returns_no_match(repo: SqliteJobRepository) -> None:
    assert find_sponsor_match(repo, "Anyone At All Ltd", "GB") is None


def test_country_mismatch_does_not_match(repo: SqliteJobRepository) -> None:
    import_sponsor_register(
        repo, _UK_CSV, country="GB", register_name="uk_home_office_sponsor_list"
    )
    assert find_sponsor_match(repo, "Acme Robotics Ltd", "NL") is None


def test_reimport_replaces_rather_than_appends(repo: SqliteJobRepository) -> None:
    import_sponsor_register(
        repo, _UK_CSV, country="GB", register_name="uk_home_office_sponsor_list"
    )
    smaller_csv = (
        "Organisation Name,Town/City,County,Type & Rating,Route\n"
        " Acme Robotics Ltd,London,,Worker (A rating),Skilled Worker\n"
    )
    count = import_sponsor_register(
        repo, smaller_csv, country="GB", register_name="uk_home_office_sponsor_list"
    )
    assert count == 1

    (row_count,) = repo._conn.execute(
        "SELECT COUNT(*) FROM sponsor_registry_entries WHERE country = 'GB'"
    ).fetchone()
    assert row_count == 1
    assert find_sponsor_match(repo, "Cinder Forge Limited", "GB") is None
    assert find_sponsor_match(repo, "Acme Robotics Ltd", "GB") is not None


def test_import_unknown_register_name_raises(repo: SqliteJobRepository) -> None:
    with pytest.raises(SponsorRegisterParseError):
        import_sponsor_register(
            repo, _UK_CSV, country="NL", register_name="ind_recognised_sponsors"
        )
