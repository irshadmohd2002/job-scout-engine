"""Milestone 2 Deliverable 5 step 4: pipeline.py::run_once executes
SelectedSource.planned_queries (one adapter.fetch() call per PlannedQuery),
instead of the M1/1.1 single-query-per-source call pattern.

AdzunaAdapter itself is not touched by this step (MILESTONE_2.md Deliverable
5 step 4) — these tests use a fake adapter that records the
SourceSearchParams it was called with, so the assertions are about pipeline
call cardinality/ordering/content, not about real Adzuna request encoding."""

from __future__ import annotations

from datetime import UTC, datetime

from job_scout import config
from job_scout.models import (
    AccessMode,
    ApprovalStatus,
    ConfigStatus,
    RawJobRecord,
    SourceRunStatus,
    SourceSearchParams,
)
from job_scout.pipeline import run_once
from job_scout.repository.sqlite_repo import SqliteJobRepository
from job_scout.sources.base import SourceRateLimitError
from tests.factories import make_candidate_profile, make_search_profile, make_source_entry


def _limits(**overrides: object) -> config.ExecutionLimits:
    base: dict[str, object] = {
        "max_countries_per_run": 6,
        "max_pages_per_source_country": 3,
        "results_per_page": 50,
        "request_timeout_seconds": 15,
        "max_retries": 2,
        "max_jobs_processed_per_run": None,
        "max_queries_per_source_country": 3,
    }
    base.update(overrides)
    return config.ExecutionLimits.model_validate(base)


def _weights() -> config.ScoringWeights:
    return config.load_scoring_weights()


def _source_weights() -> config.SourceScoringWeights:
    return config.load_source_scoring_weights()


def _env() -> config.EnvConfig:
    return config.EnvConfig(adzuna_app_id="id", adzuna_app_key="key")


def _adzuna_registry_entry(**overrides: object):  # type: ignore[no-untyped-def]
    base: dict[str, object] = {
        "source_id": "adzuna_api",
        "access_mode": AccessMode.PUBLIC_API,
        "approval_status": ApprovalStatus.APPROVED,
        "geographic_coverage": ["GB"],
        "role_coverage": ["general"],
    }
    base.update(overrides)
    return make_source_entry(**base)


def _payload(job_id: str, title: str = "Strategy Manager") -> dict:
    return {
        "id": job_id,
        "title": title,
        "company": {"display_name": f"Example Corp {job_id}"},
        "location": {"display_name": "London"},
        "redirect_url": f"https://example.com/jobs/{job_id}",
        "created": "2026-07-01T10:00:00Z",
        "description": f"<p>Great strategy and transformation role #{job_id}.</p>",
        "salary_min": 60000,
        "salary_max": 80000,
        "contract_time": "full_time",
        "_query_country": "GB",
    }


def _record(job_id: str, title: str = "Strategy Manager") -> RawJobRecord:
    return RawJobRecord(
        source_id="adzuna_api",
        external_id=job_id,
        raw_url=f"https://example.com/jobs/{job_id}",
        raw_payload=_payload(job_id, title),
        fetched_at=datetime.now(UTC),
    )


class _RecordingAdapter:
    """Records the SourceSearchParams of every fetch() call, in order, and
    can be configured to return a fixed record set per call index or raise on
    a specific call."""

    source_id = "adzuna_api"
    access_mode = AccessMode.PUBLIC_API

    def __init__(
        self,
        records_per_call: dict[int, list[RawJobRecord]] | None = None,
        *,
        fail_on_call: int | None = None,
        fail_error: Exception | None = None,
    ) -> None:
        self._records_per_call = records_per_call or {}
        self._fail_on_call = fail_on_call
        self._fail_error = fail_error
        self.calls: list[SourceSearchParams] = []

    def is_configured(self) -> bool:
        return True

    def fetch(self, params: SourceSearchParams) -> list[RawJobRecord]:
        self.calls.append(params)
        call_index = len(self.calls)
        if self._fail_on_call == call_index:
            assert self._fail_error is not None
            raise self._fail_error
        return self._records_per_call.get(call_index, [])


def _run(
    *,
    candidate=None,  # type: ignore[no-untyped-def]
    search=None,  # type: ignore[no-untyped-def]
    registry,  # type: ignore[no-untyped-def]
    adapter,  # type: ignore[no-untyped-def]
    tmp_path,  # type: ignore[no-untyped-def]
    limits=None,  # type: ignore[no-untyped-def]
):
    candidate = candidate or make_candidate_profile()
    search = search or make_search_profile(included_countries=["GB"])
    with SqliteJobRepository(tmp_path / "db.sqlite3") as repo:
        result = run_once(
            candidate_profile=candidate,
            search_profile=search,
            registry=registry,
            execution_limits=limits or _limits(),
            scoring_weights=_weights(),
            source_scoring_weights=_source_weights(),
            repository=repo,
            env=_env(),
            dry_run=False,
            adapter_factory=lambda source_id: adapter if source_id == "adzuna_api" else None,
        )
        return result


# --- call cardinality / ordering -----------------------------------------


def test_one_fetch_call_per_planned_query_in_deterministic_order(tmp_path) -> None:  # type: ignore[no-untyped-def]
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    search = make_search_profile(
        included_countries=["GB"], target_titles=["Chief of Staff", "Transformation Lead"]
    )
    registry = [_adzuna_registry_entry()]
    adapter = _RecordingAdapter({1: [_record("1")], 2: [_record("2")]})

    result = _run(
        candidate=candidate, search=search, registry=registry, adapter=adapter, tmp_path=tmp_path
    )

    planned = next(
        s for s in result.plan.selected_sources if s.source_id == "adzuna_api"
    ).planned_queries
    assert [q.label for q in planned] == ["Chief of Staff", "Transformation Lead"]
    assert len(adapter.calls) == len(planned) == 2
    # deterministic order, keywords translated per-query, not merged into one OR
    assert adapter.calls[0].keywords == ["Chief of Staff"]
    assert adapter.calls[1].keywords == ["Transformation Lead"]
    assert result.source_runs[0].jobs_fetched == 2


def test_query_count_never_exceeds_planned_queries(tmp_path) -> None:  # type: ignore[no-untyped-def]
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    search = make_search_profile(
        included_countries=["GB"],
        target_titles=["Role A", "Role B", "Role C", "Role D", "Role E"],
    )
    registry = [_adzuna_registry_entry()]
    adapter = _RecordingAdapter()

    result = _run(
        candidate=candidate,
        search=search,
        registry=registry,
        adapter=adapter,
        tmp_path=tmp_path,
        limits=_limits(max_queries_per_source_country=3),
    )

    planned = next(
        s for s in result.plan.selected_sources if s.source_id == "adzuna_api"
    ).planned_queries
    assert len(planned) == 3  # bounded by max_queries_per_source_country (Task 3)
    assert len(adapter.calls) == 3  # execution never exceeds the plan


# --- exact_phrase / any_of_words translation ------------------------------


def test_exact_phrase_query_passes_single_keyword_through(tmp_path) -> None:  # type: ignore[no-untyped-def]
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    search = make_search_profile(included_countries=["GB"], target_titles=["Chief of Staff"])
    registry = [_adzuna_registry_entry()]  # default capabilities: exact_phrase_search=True
    adapter = _RecordingAdapter()

    result = _run(
        candidate=candidate, search=search, registry=registry, adapter=adapter, tmp_path=tmp_path
    )

    planned = next(
        s for s in result.plan.selected_sources if s.source_id == "adzuna_api"
    ).planned_queries
    assert planned[0].mode == "exact_phrase"
    assert len(adapter.calls) == 1
    assert adapter.calls[0].keywords == ["Chief of Staff"]
    # PlannedQuery.mode threads through to the adapter-facing request model —
    # generic pipeline code, no source_id-conditional branching.
    assert adapter.calls[0].keyword_mode == "exact_phrase"


def test_any_of_words_query_passes_full_keyword_group(tmp_path) -> None:  # type: ignore[no-untyped-def]
    candidate = make_candidate_profile(
        title_aliases=["Strategy Manager", "Transformation Manager"],
        role_families=[],
    )
    search = make_search_profile(included_countries=["GB"])  # no active intent -> fallback
    registry = [_adzuna_registry_entry()]
    adapter = _RecordingAdapter()

    result = _run(
        candidate=candidate, search=search, registry=registry, adapter=adapter, tmp_path=tmp_path
    )

    planned = next(
        s for s in result.plan.selected_sources if s.source_id == "adzuna_api"
    ).planned_queries
    assert len(planned) == 1
    assert planned[0].mode == "any_of_words"
    assert len(adapter.calls) == 1
    assert adapter.calls[0].keywords == planned[0].keywords
    assert adapter.calls[0].keywords == ["Strategy Manager", "Transformation Manager"]
    assert adapter.calls[0].keyword_mode == "any_of_words"


def test_mixed_target_titles_and_fallback_never_render_identical_modes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A profile with one target_title (exact_phrase) and budget left over
    for the grouped any_of_words fallback (search.role_families gives the
    fallback its own query intent, since target_titles alone would leave the
    fallback with nothing to build from) must send two distinguishable
    per-query modes to the adapter, not the same mode twice."""
    candidate = make_candidate_profile()
    search = make_search_profile(
        included_countries=["GB"],
        target_titles=["Chief of Staff"],
        role_families=["strategy_and_planning"],
    )
    registry = [_adzuna_registry_entry()]
    adapter = _RecordingAdapter()

    _run(candidate=candidate, search=search, registry=registry, adapter=adapter, tmp_path=tmp_path)

    assert len(adapter.calls) == 2
    modes = [call.keyword_mode for call in adapter.calls]
    assert modes == ["exact_phrase", "any_of_words"]
    assert len(set(modes)) == 2  # the two calls are not identical requests


# --- zero planned queries --------------------------------------------------


def test_zero_planned_queries_means_no_fetch_and_no_source_run(tmp_path) -> None:  # type: ignore[no-untyped-def]
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    search = make_search_profile(included_countries=["GB"])  # no intent anywhere
    registry = [_adzuna_registry_entry()]
    adapter = _RecordingAdapter()

    result = _run(
        candidate=candidate, search=search, registry=registry, adapter=adapter, tmp_path=tmp_path
    )

    planned = next(
        s for s in result.plan.selected_sources if s.source_id == "adzuna_api"
    ).planned_queries
    assert planned == []
    assert adapter.calls == []
    assert result.source_runs == []
    assert result.results == []


# --- compliance / countries -------------------------------------------------


def test_manual_review_source_never_fetches_even_with_planned_queries(tmp_path) -> None:  # type: ignore[no-untyped-def]
    registry = [
        _adzuna_registry_entry(
            approval_status=ApprovalStatus.MANUAL_REVIEW,
            config_status=ConfigStatus.NOT_CONFIGURED,
        )
    ]
    adapter = _RecordingAdapter()

    result = _run(registry=registry, adapter=adapter, tmp_path=tmp_path)

    selected = next(s for s in result.plan.selected_sources if s.source_id == "adzuna_api")
    assert selected.executable is False
    assert len(selected.planned_queries) >= 1  # planning still happens; execution must not
    assert adapter.calls == []
    assert result.source_runs == []


def test_unsupported_country_never_reaches_any_per_query_fetch_call(tmp_path) -> None:  # type: ignore[no-untyped-def]
    search = make_search_profile(included_countries=["GB", "IE"])
    registry = [_adzuna_registry_entry(geographic_coverage=["GB"])]  # IE unsupported
    adapter = _RecordingAdapter()

    _run(search=search, registry=registry, adapter=adapter, tmp_path=tmp_path)

    assert len(adapter.calls) >= 1
    for call in adapter.calls:
        assert call.countries == ["GB"]
        assert "IE" not in call.countries


def test_page_limit_is_identical_across_every_planned_query_call(tmp_path) -> None:  # type: ignore[no-untyped-def]
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    search = make_search_profile(
        included_countries=["GB"], target_titles=["Role A", "Role B"]
    )
    registry = [_adzuna_registry_entry()]
    adapter = _RecordingAdapter()

    _run(
        candidate=candidate,
        search=search,
        registry=registry,
        adapter=adapter,
        tmp_path=tmp_path,
        limits=_limits(max_pages_per_source_country=7),
    )

    assert len(adapter.calls) == 2
    assert all(call.max_pages == 7 for call in adapter.calls)


# --- overlapping results / dedup -------------------------------------------


def test_overlapping_query_results_do_not_duplicate_persisted_jobs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    search = make_search_profile(
        included_countries=["GB"], target_titles=["Strategy Manager", "Corporate Strategy"]
    )
    registry = [_adzuna_registry_entry()]
    # Both planned queries "find" the exact same Adzuna vacancy (id "1"), plus
    # the second query also finds a genuinely new one (id "2").
    adapter = _RecordingAdapter({1: [_record("1")], 2: [_record("1"), _record("2")]})

    with SqliteJobRepository(tmp_path / "db.sqlite3") as repo:
        result = run_once(
            candidate_profile=candidate,
            search_profile=search,
            registry=registry,
            execution_limits=_limits(),
            scoring_weights=_weights(),
            source_scoring_weights=_source_weights(),
            repository=repo,
            env=_env(),
            dry_run=False,
            adapter_factory=lambda source_id: adapter if source_id == "adzuna_api" else None,
        )
        job_count = repo._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        assert job_count == 2  # not 3 — the repeated "1" observation is deduped

    run = result.source_runs[0]
    assert run.jobs_fetched == 3  # raw observations across both queries
    assert run.jobs_new == 2
    assert run.jobs_duplicate == 1
    # Existing M1 convention (test_pipeline.py's
    # test_second_run_same_jobs_does_not_duplicate_job_rows): a MatchResult
    # is appended per raw observation, not deduped — only the persisted Job
    # row count (asserted above) reflects the dedup outcome.
    assert len(result.results) == 3


# --- partial failure ---------------------------------------------------


def test_one_query_failure_stops_later_queries_but_keeps_earlier_results(tmp_path) -> None:  # type: ignore[no-untyped-def]
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    search = make_search_profile(
        included_countries=["GB"], target_titles=["Role A", "Role B", "Role C"]
    )
    registry = [_adzuna_registry_entry()]
    adapter = _RecordingAdapter(
        {1: [_record("1")]},
        fail_on_call=2,
        fail_error=SourceRateLimitError("rate limited"),
    )

    result = _run(
        candidate=candidate, search=search, registry=registry, adapter=adapter, tmp_path=tmp_path
    )

    assert len(adapter.calls) == 2  # query 3 never attempted after query 2 failed
    run = result.source_runs[0]
    assert run.status == SourceRunStatus.PARTIAL
    assert run.errors  # the rate-limit error was recorded
    assert run.jobs_fetched == 1
    assert len(result.results) == 1


def test_all_queries_failing_produces_isolated_failed_run_zero_jobs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    search = make_search_profile(included_countries=["GB"], target_titles=["Role A"])
    registry = [_adzuna_registry_entry()]
    adapter = _RecordingAdapter(fail_on_call=1, fail_error=SourceRateLimitError("rate limited"))

    result = _run(
        candidate=candidate, search=search, registry=registry, adapter=adapter, tmp_path=tmp_path
    )

    assert len(adapter.calls) == 1
    run = result.source_runs[0]
    assert run.status == SourceRunStatus.FAILED
    assert run.jobs_fetched == 0
    assert result.results == []


# --- plan/execution consistency --------------------------------------------


def test_run_once_fetch_keywords_match_plan_command_planned_queries(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Acceptance criterion (MILESTONE_2.md Deliverable 5 step 4 / task
    section 13): what `job-scout plan` would show for a profile is exactly
    what `run-once` executes — build_plan() is a pure function of the same
    inputs, so the plan.selected_sources[*].planned_queries produced here are
    byte-identical to a standalone `job-scout plan` call, and this test
    confirms pipeline.py's fetch() calls carry exactly those keywords, in the
    same order."""
    candidate = make_candidate_profile(
        title_aliases=["Should Not Appear"], role_families=["should_not_appear"]
    )
    search = make_search_profile(
        included_countries=["GB"], target_titles=["Chief of Staff", "Transformation Lead"]
    )
    registry = [_adzuna_registry_entry()]
    adapter = _RecordingAdapter()

    result = _run(
        candidate=candidate, search=search, registry=registry, adapter=adapter, tmp_path=tmp_path
    )

    planned = next(
        s for s in result.plan.selected_sources if s.source_id == "adzuna_api"
    ).planned_queries
    assert [c.keywords for c in adapter.calls] == [q.keywords for q in planned]
    # candidate_profile.title_aliases must never leak into execution once
    # planned_queries (driven by the active SearchProfile here) exist.
    for call in adapter.calls:
        assert "Should Not Appear" not in call.keywords
