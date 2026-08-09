"""SourceCapabilities (Milestone 2 Deliverable 5 step 1; decisions.md
D-040/D-041) — typed capability metadata on SourceRegistryEntry.

Task 1 scope only: default values, explicit configuration, backward
compatibility, round-tripping, and invalid-value rejection. No query-planner
or dedup consumption exists yet (later M2 steps)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from job_scout.models import CompanyWatchlistEntry, SourceCapabilities
from tests.factories import make_source_entry


def test_source_capabilities_defaults_match_adzuna_verified_contract() -> None:
    caps = SourceCapabilities()
    assert caps.keyword_search is True
    assert caps.exact_phrase_search is True
    assert caps.location_filter is True
    assert caps.country_filter is True
    assert caps.city_filter is True
    assert caps.industry_filter is False
    assert caps.company_filter is False
    assert caps.remote_filter is False
    assert caps.salary_data is True
    assert caps.structured_description is False
    assert caps.pagination is True
    assert caps.page_size_control is True
    assert caps.posting_date_filter is False
    assert caps.stable_external_job_id is True
    assert caps.canonical_application_url is True
    assert caps.max_recommended_queries_per_request is None


def test_source_registry_entry_defaults_capabilities_when_omitted() -> None:
    entry = make_source_entry()
    assert entry.capabilities == SourceCapabilities()


def test_explicit_capabilities_configuration_parses_correctly() -> None:
    caps = SourceCapabilities(
        keyword_search=False,
        exact_phrase_search=False,
        company_filter=True,
        salary_data=False,
        max_recommended_queries_per_request=2,
    )
    entry = make_source_entry(
        source_id="greenhouse_public_feeds",
        capabilities=caps,
    )
    assert entry.capabilities.keyword_search is False
    assert entry.capabilities.company_filter is True
    assert entry.capabilities.max_recommended_queries_per_request == 2
    # Untouched fields keep their own defaults.
    assert entry.capabilities.location_filter is True


def test_source_capabilities_round_trip_via_dump_and_validate() -> None:
    caps = SourceCapabilities(company_filter=True, keyword_search=False)
    dumped = caps.model_dump()
    restored = SourceCapabilities.model_validate(dumped)
    assert restored == caps


def test_source_registry_entry_round_trip_preserves_capabilities() -> None:
    entry = make_source_entry(capabilities=SourceCapabilities(industry_filter=True))
    restored = type(entry).model_validate(entry.model_dump())
    assert restored.capabilities.industry_filter is True


def test_invalid_capability_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SourceCapabilities.model_validate({"keyword_search": "not-a-boolean"})


def test_invalid_max_queries_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SourceCapabilities.model_validate({"max_recommended_queries_per_request": "abc"})


def test_unknown_capability_field_is_ignored_not_rejected() -> None:
    # Matches this project's existing config-validation philosophy: every
    # model in models.py uses Pydantic's default `extra="ignore"` behaviour
    # (no model in the codebase sets extra="forbid"), so an unrecognised key
    # is silently dropped rather than raising — consistent with every other
    # config surface (candidate_profile.yaml, search_profiles.yaml, etc.).
    caps = SourceCapabilities.model_validate({"totally_unknown_field": True})
    assert caps == SourceCapabilities()


# --- CompanyWatchlistEntry (Milestone 2 Deliverable 5 step 6) ---------------
# Config-model tests only: no adapter/network/query-planner consumption
# exists yet (Greenhouse/Lever adapters are steps 7/8).


def test_company_watchlist_entry_parses_greenhouse_shaped() -> None:
    entry = CompanyWatchlistEntry(
        company_name="Example Co",
        source_id="greenhouse_public_feeds",
        external_company_key="examplecoboardtoken",
        priority=50,
    )
    assert entry.source_id == "greenhouse_public_feeds"
    assert entry.external_company_key == "examplecoboardtoken"
    assert entry.company_name == "Example Co"
    assert entry.notes is None


def test_company_watchlist_entry_parses_lever_shaped() -> None:
    entry = CompanyWatchlistEntry(
        company_name="Another Co",
        source_id="lever_public_postings",
        external_company_key="anothercoslug",
        priority=40,
        notes="illustrative",
    )
    assert entry.source_id == "lever_public_postings"
    assert entry.external_company_key == "anothercoslug"
    assert entry.notes == "illustrative"


def test_company_watchlist_entry_company_name_and_external_key_are_distinct() -> None:
    """The human-readable identity field must remain independent of the
    ATS routing key — a display name is never used for API routing, and
    the routing key is never shown as the company's human-readable name."""
    entry = CompanyWatchlistEntry(
        company_name="Human-Readable Display Name Ltd",
        source_id="greenhouse_public_feeds",
        external_company_key="raw-routing-token-123",
        priority=1,
    )
    assert entry.company_name != entry.external_company_key
    assert entry.company_name == "Human-Readable Display Name Ltd"
    assert entry.external_company_key == "raw-routing-token-123"


def test_company_watchlist_entry_missing_source_id_fails() -> None:
    with pytest.raises(ValidationError):
        CompanyWatchlistEntry.model_validate(
            {
                "company_name": "Example Co",
                "external_company_key": "examplecoboardtoken",
                "priority": 50,
            }
        )


def test_company_watchlist_entry_missing_external_company_key_fails() -> None:
    with pytest.raises(ValidationError):
        CompanyWatchlistEntry.model_validate(
            {
                "company_name": "Example Co",
                "source_id": "greenhouse_public_feeds",
                "priority": 50,
            }
        )


def test_company_watchlist_entry_notes_defaults_to_none() -> None:
    entry = CompanyWatchlistEntry(
        company_name="Example Co",
        source_id="greenhouse_public_feeds",
        external_company_key="examplecoboardtoken",
        priority=50,
    )
    assert entry.notes is None


def test_company_watchlist_entry_round_trip_via_dump_and_validate() -> None:
    entry = CompanyWatchlistEntry(
        company_name="Example Co",
        source_id="lever_public_postings",
        external_company_key="examplecoslug",
        priority=50,
        notes="note",
    )
    restored = CompanyWatchlistEntry.model_validate(entry.model_dump())
    assert restored == entry
