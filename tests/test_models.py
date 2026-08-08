"""SourceCapabilities (Milestone 2 Deliverable 5 step 1; decisions.md
D-040/D-041) — typed capability metadata on SourceRegistryEntry.

Task 1 scope only: default values, explicit configuration, backward
compatibility, round-tripping, and invalid-value rejection. No query-planner
or dedup consumption exists yet (later M2 steps)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from job_scout.models import SourceCapabilities
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
