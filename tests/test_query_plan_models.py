"""PlannedQuery / SelectedSource query-plan capacity (Milestone 2
Deliverable 5 step 2; domain model only).

Task 2 scope: typed model validation, serialization round-trips, and proof
that `SearchExecutionPlan` can structurally represent more than one planned
query per source without any query-generation logic existing yet (that's
step 3, `source_intelligence/query_planner.py`). No test here asserts
anything about *how many* queries get generated — nothing generates them in
this step."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from job_scout.models import PlannedQuery, SelectedSource
from tests.factories import make_source_entry


def _make_selected_source(**overrides: object) -> SelectedSource:
    entry = make_source_entry()
    data: dict[str, object] = {
        "source_id": entry.source_id,
        "score": 1.0,
        "score_breakdown": {},
        "reasons_selected": ["score=1.000"],
        "access_mode": entry.access_mode,
        "approval_status": entry.approval_status,
        "search_params": None,
        "search_queries": ["Strategy Manager"],
        "polling_frequency_minutes": entry.polling_frequency_minutes,
        "config_status": entry.config_status,
        "effective_config_status": entry.config_status,
        "required_setup_actions": [],
        "region_country_coverage": ["GB"],
        "priority": entry.priority,
        "executable": True,
        "supported_countries": ["GB"],
        "unsupported_countries": [],
    }
    data.update(overrides)
    return SelectedSource.model_validate(data)


def test_planned_query_requires_all_fields() -> None:
    query = PlannedQuery(
        label="Strategy Manager",
        keywords=["Strategy Manager"],
        mode="exact_phrase",
        provenance=["search.target_titles"],
    )
    assert query.label == "Strategy Manager"
    assert query.keywords == ["Strategy Manager"]
    assert query.mode == "exact_phrase"
    assert query.provenance == ["search.target_titles"]


def test_planned_query_rejects_invalid_mode() -> None:
    with pytest.raises(ValidationError):
        PlannedQuery.model_validate(
            {
                "label": "Strategy Manager",
                "keywords": ["Strategy Manager"],
                "mode": "fuzzy_match",
                "provenance": [],
            }
        )


def test_planned_query_rejects_missing_field() -> None:
    with pytest.raises(ValidationError):
        PlannedQuery.model_validate(
            {
                "label": "Strategy Manager",
                "keywords": ["Strategy Manager"],
                "mode": "exact_phrase",
                # provenance omitted
            }
        )


def test_planned_query_round_trip_via_dump_and_validate() -> None:
    query = PlannedQuery(
        label="fallback",
        keywords=["Strategy Manager", "Transformation Manager"],
        mode="any_of_words",
        provenance=["search.title_aliases", "candidate.title_aliases"],
    )
    restored = PlannedQuery.model_validate(query.model_dump())
    assert restored == query


def test_selected_source_defaults_no_planned_queries() -> None:
    """Backward compatibility: a SelectedSource built the M1/1.1 way (no
    planned_queries/estimated_request_count supplied, exactly what
    `build_plan()` still does in this step) keeps validating unchanged."""
    source = _make_selected_source()
    assert source.planned_queries == []
    assert source.estimated_request_count == 0


def test_selected_source_can_carry_multiple_planned_queries() -> None:
    """Structural proof only: the domain model can represent >1 planned
    query per selected source. No planner logic produces this yet."""
    queries = [
        PlannedQuery(
            label="Strategy Manager",
            keywords=["Strategy Manager"],
            mode="exact_phrase",
            provenance=["search.target_titles"],
        ),
        PlannedQuery(
            label="Transformation Manager",
            keywords=["Transformation Manager"],
            mode="exact_phrase",
            provenance=["search.target_titles"],
        ),
    ]
    source = _make_selected_source(planned_queries=queries, estimated_request_count=6)
    assert len(source.planned_queries) == 2
    assert source.estimated_request_count == 6


def test_selected_source_round_trip_preserves_planned_queries() -> None:
    query = PlannedQuery(
        label="Strategy Manager",
        keywords=["Strategy Manager"],
        mode="exact_phrase",
        provenance=["search.target_titles"],
    )
    source = _make_selected_source(planned_queries=[query], estimated_request_count=3)
    restored = SelectedSource.model_validate(source.model_dump())
    assert restored.planned_queries == [query]
    assert restored.estimated_request_count == 3
