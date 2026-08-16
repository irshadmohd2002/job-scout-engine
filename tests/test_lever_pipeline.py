"""Lever wired through the generic pipeline's watchlist fan-out (Milestone 2
Deliverable 5 step 8; decisions.md D-040/D-041/D-047/D-048).

Confirms: watchlist selection is scoped by source_id, external_company_key
is used as the company key (never company_name), one adapter.fetch() call
happens per matching watchlisted company, zero watchlist entries produce
zero calls and no SourceRun, no keyword PlannedQuerys are ever generated
for a company_filter=True source, the compliance/approval gate remains
authoritative regardless of watchlist contents, Lever reuses the exact same
watchlist-fan-out branch Greenhouse already exercises (decisions.md D-048 —
no new execution architecture), and Adzuna/Reed/Greenhouse's own execution
paths are unaffected by this addition."""

from __future__ import annotations

from datetime import UTC, datetime

from job_scout import config
from job_scout.models import (
    AccessMode,
    ApprovalStatus,
    CompanyWatchlistEntry,
    ConfigStatus,
    RawJobRecord,
    SourceCapabilities,
    SourceRunStatus,
)
from job_scout.pipeline import _default_watchlist_adapter_factory, run_once
from job_scout.repository.sqlite_repo import SqliteJobRepository
from job_scout.sources.base import SourceRateLimitError
from job_scout.sources.lever import LeverAdapter
from tests.factories import make_candidate_profile, make_search_profile, make_source_entry

# Mirrors the packaged source_registry.example.yaml's real, verified
# lever_public_postings capabilities block (decisions.md D-048) —
# `make_source_entry`'s own default is Adzuna-shaped (keyword_search=True,
# company_filter=False) and would silently misrepresent Lever's actual
# shape in these pipeline-level tests.
LEVER_CAPABILITIES = SourceCapabilities(
    keyword_search=False,
    exact_phrase_search=False,
    location_filter=False,
    country_filter=False,
    city_filter=False,
    industry_filter=False,
    company_filter=True,
    remote_filter=False,
    salary_data=True,
    structured_description=False,
    pagination=False,
    page_size_control=False,
    posting_date_filter=False,
    stable_external_job_id=True,
    canonical_application_url=True,
    max_recommended_queries_per_request=None,
)


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


def _env(**overrides: object) -> config.EnvConfig:
    return config.EnvConfig(**overrides)


def _lever_registry_entry(**overrides: object):  # type: ignore[no-untyped-def]
    base: dict[str, object] = {
        "source_id": "lever_public_postings",
        "access_mode": AccessMode.PUBLIC_ATS_FEED,
        "approval_status": ApprovalStatus.APPROVED,
        "geographic_coverage": ["GB"],
        "role_coverage": ["general"],
        "capabilities": LEVER_CAPABILITIES,
    }
    base.update(overrides)
    return make_source_entry(**base)


def _watchlist_entry(
    *,
    company_name: str = "Example Co",
    external_company_key: str = "exampleco",
    **overrides: object,
) -> CompanyWatchlistEntry:
    base: dict[str, object] = {
        "company_name": company_name,
        "source_id": "lever_public_postings",
        "external_company_key": external_company_key,
        "priority": 50,
    }
    base.update(overrides)
    return CompanyWatchlistEntry.model_validate(base)


def _lever_payload(posting_id: str, title: str = "Strategy Manager") -> dict:
    return {
        "id": posting_id,
        "text": title,
        "categories": {"location": "London", "commitment": "Full-time"},
        "country": "GB",
        "hostedUrl": f"https://jobs.lever.co/exampleco/{posting_id}",
        "applyUrl": f"https://jobs.lever.co/exampleco/{posting_id}/apply",
        "description": f"Great strategy role #{posting_id}.",
        "workplaceType": "remote",
        "salaryRange": {"currency": "USD", "min": 90000, "max": 120000},
    }


def _lever_record(
    posting_id: str, *, title: str = "Strategy Manager", company_name: str = "Example Co"
) -> RawJobRecord:
    payload = {**_lever_payload(posting_id, title), "_company_name": company_name}
    return RawJobRecord(
        source_id="lever_public_postings",
        external_id=posting_id,
        raw_url=payload["hostedUrl"],
        raw_payload=payload,
        fetched_at=datetime.now(UTC),
    )


class _FakeLeverAdapter:
    source_id = "lever_public_postings"
    access_mode = AccessMode.PUBLIC_ATS_FEED

    def __init__(
        self, records: list[RawJobRecord] | None = None, *, fail: Exception | None = None
    ) -> None:
        self._records = records or []
        self._fail = fail
        self.calls = 0

    def is_configured(self) -> bool:
        return True

    def fetch(self, params: object) -> list[RawJobRecord]:
        self.calls += 1
        if self._fail is not None:
            raise self._fail
        return self._records


def _run(
    *,
    candidate=None,  # type: ignore[no-untyped-def]
    search=None,  # type: ignore[no-untyped-def]
    registry,  # type: ignore[no-untyped-def]
    watchlist,  # type: ignore[no-untyped-def]
    watchlist_adapter_factory,  # type: ignore[no-untyped-def]
    tmp_path,  # type: ignore[no-untyped-def]
):
    candidate = candidate or make_candidate_profile()
    search = search or make_search_profile(included_countries=["GB"])
    with SqliteJobRepository(tmp_path / "db.sqlite3") as repo:
        return run_once(
            candidate_profile=candidate,
            search_profile=search,
            registry=registry,
            execution_limits=_limits(),
            scoring_weights=_weights(),
            source_scoring_weights=_source_weights(),
            repository=repo,
            env=_env(),
            dry_run=False,
            company_watchlist=watchlist,
            watchlist_adapter_factory=watchlist_adapter_factory,
        )


# --- registration / watchlist adapter factory --------------------------------


def test_lever_registered_in_default_watchlist_adapter_factory() -> None:
    factory = _default_watchlist_adapter_factory(_limits())
    company = _watchlist_entry()
    adapter = factory("lever_public_postings", company)
    assert isinstance(adapter, LeverAdapter)
    assert adapter.company_key == "exampleco"
    assert adapter.company_name == "Example Co"


def test_default_watchlist_factory_returns_none_for_truly_unimplemented_source() -> None:
    factory = _default_watchlist_adapter_factory(_limits())
    company = _watchlist_entry(source_id="some_future_ats_feed")
    assert factory("some_future_ats_feed", company) is None


# --- watchlist selection / fan-out -------------------------------------------


def test_watchlist_selection_is_scoped_by_source_id(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A watchlist entry for a different source_id (e.g.
    greenhouse_public_feeds) must never produce a Lever fetch."""
    registry = [_lever_registry_entry()]
    watchlist = [
        _watchlist_entry(company_name="Wrong Source Co", source_id="greenhouse_public_feeds"),
    ]
    fake = _FakeLeverAdapter([_lever_record("1")])

    result = _run(
        registry=registry,
        watchlist=watchlist,
        watchlist_adapter_factory=lambda sid, company: fake,
        tmp_path=tmp_path,
    )
    assert fake.calls == 0
    assert result.source_runs == []


def test_external_company_key_used_as_company_key_not_company_name(tmp_path) -> None:  # type: ignore[no-untyped-def]
    registry = [_lever_registry_entry()]
    watchlist = [
        _watchlist_entry(company_name="Pretty Display Name", external_company_key="raw-slug")
    ]
    seen_keys: list[str] = []

    def factory(source_id: str, company: CompanyWatchlistEntry):  # type: ignore[no-untyped-def]
        seen_keys.append(company.external_company_key)
        return _FakeLeverAdapter([_lever_record("1")])

    _run(
        registry=registry, watchlist=watchlist, watchlist_adapter_factory=factory, tmp_path=tmp_path
    )
    assert seen_keys == ["raw-slug"]


def test_one_fetch_call_per_watchlisted_company(tmp_path) -> None:  # type: ignore[no-untyped-def]
    registry = [_lever_registry_entry()]
    watchlist = [
        _watchlist_entry(company_name="Alpha Co", external_company_key="alpha"),
        _watchlist_entry(company_name="Beta Co", external_company_key="beta"),
        _watchlist_entry(company_name="Gamma Co", external_company_key="gamma"),
    ]
    adapters_by_key: dict[str, _FakeLeverAdapter] = {}

    def factory(source_id: str, company: CompanyWatchlistEntry):  # type: ignore[no-untyped-def]
        adapter = _FakeLeverAdapter([_lever_record(company.external_company_key)])
        adapters_by_key[company.external_company_key] = adapter
        return adapter

    result = _run(
        registry=registry, watchlist=watchlist, watchlist_adapter_factory=factory, tmp_path=tmp_path
    )
    assert len(adapters_by_key) == 3  # one fresh adapter instance per company
    assert all(a.calls == 1 for a in adapters_by_key.values())  # exactly one fetch() each
    assert len(result.source_runs) == 1  # aggregated into a single SourceRun for the source
    assert result.source_runs[0].jobs_fetched == 3


def test_zero_watchlist_entries_means_no_fetch_and_no_source_run(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """R-10 (MILESTONE_2.md): zero watchlist entries -> zero calls -> zero
    jobs, no SourceRun row at all — mirrors the existing zero-planned-
    queries invariant for keyword sources."""
    registry = [_lever_registry_entry()]
    fake = _FakeLeverAdapter([_lever_record("1")])

    result = _run(
        registry=registry,
        watchlist=[],
        watchlist_adapter_factory=lambda sid, company: fake,
        tmp_path=tmp_path,
    )
    assert fake.calls == 0
    assert result.source_runs == []
    assert result.results == []


# --- query planner interaction ------------------------------------------------


def test_no_keyword_planned_queries_generated_for_lever(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """decisions.md D-041: company_filter=True must never produce keyword
    PlannedQuerys, even with an active SearchProfile.target_titles and a
    populated watchlist — query_planner.py is untouched by this task."""
    search = make_search_profile(included_countries=["GB"], target_titles=["Chief of Staff"])
    registry = [_lever_registry_entry()]
    watchlist = [_watchlist_entry()]
    fake = _FakeLeverAdapter([_lever_record("1")])

    result = _run(
        search=search,
        registry=registry,
        watchlist=watchlist,
        watchlist_adapter_factory=lambda sid, company: fake,
        tmp_path=tmp_path,
    )
    selected = next(
        s for s in result.plan.selected_sources if s.source_id == "lever_public_postings"
    )
    assert selected.planned_queries == []
    assert fake.calls == 1  # execution still happened, via watchlist fan-out, not queries


# --- pipeline integration / normalization ------------------------------------


def test_run_once_pulls_from_lever_via_watchlist_fan_out(tmp_path) -> None:  # type: ignore[no-untyped-def]
    registry = [_lever_registry_entry()]
    watchlist = [_watchlist_entry()]
    # distinct titles (not just a differing numeric id in otherwise-identical
    # text), so Milestone 2 Deliverable 5 step 9's bounded-Jaccard
    # probable-duplicate tier (deduplication.py) doesn't fire between fixture
    # records that are only meant to differ by id — same discipline
    # test_pipeline.py's Adzuna fixtures already document.
    fake = _FakeLeverAdapter(
        [_lever_record("1"), _lever_record("2", title="Finance Analyst")]
    )

    with SqliteJobRepository(tmp_path / "db.sqlite3") as repo:
        result = run_once(
            candidate_profile=make_candidate_profile(),
            search_profile=make_search_profile(included_countries=["GB"]),
            registry=registry,
            execution_limits=_limits(),
            scoring_weights=_weights(),
            source_scoring_weights=_source_weights(),
            repository=repo,
            env=_env(),
            dry_run=False,
            company_watchlist=watchlist,
            watchlist_adapter_factory=lambda sid, company: fake,
        )
        assert result.source_runs[0].source_id == "lever_public_postings"
        assert result.source_runs[0].status == SourceRunStatus.SUCCESS
        assert len(result.results) == 2
        job_count = repo._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        assert job_count == 2
        job, _match = result.results[0]
        assert job.source_provenance[0].source_id == "lever_public_postings"
        assert job.company == "Example Co"


def test_lever_job_passes_country_hard_filter_via_structured_country(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    """Direct positive counterpart to Greenhouse's documented
    always-rejected consequence (decisions.md D-047): Lever's real,
    structured country field lets a country-scoped search profile actually
    match the job, rather than always rejecting it."""
    registry = [_lever_registry_entry()]
    watchlist = [_watchlist_entry()]
    fake = _FakeLeverAdapter([_lever_record("1")])

    with SqliteJobRepository(tmp_path / "db.sqlite3") as repo:
        result = run_once(
            candidate_profile=make_candidate_profile(),
            # employment_types=[] isolates the country check from the
            # unrelated employment_type_mismatch rule ("Full-time" from
            # Lever's categories.commitment vs. make_search_profile's
            # default "full_time").
            search_profile=make_search_profile(included_countries=["GB"], employment_types=[]),
            registry=registry,
            execution_limits=_limits(),
            scoring_weights=_weights(),
            source_scoring_weights=_source_weights(),
            repository=repo,
            env=_env(),
            dry_run=False,
            company_watchlist=watchlist,
            watchlist_adapter_factory=lambda sid, company: fake,
        )
        assert result.source_runs[0].status == SourceRunStatus.SUCCESS
        job, match = result.results[0]
        assert match.hard_filter_result.passed is True
        assert job.location.country == "GB"


def test_multi_source_run_pulls_from_both_adzuna_keywords_and_lever_watchlist(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    """Acceptance criterion (mirroring step 5/7's acceptance shape): a
    fixture-driven run-once pulls from both a keyword-query source and a
    watchlist-scoped source in one run, through the same generic pipeline,
    with no source_id branching visible beyond the existing
    adapter_factory/watchlist_adapter_factory dict lookups."""
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    search = make_search_profile(
        included_countries=["GB", "DE"], target_titles=["Strategy Manager"]
    )
    adzuna_entry = make_source_entry(
        source_id="adzuna_api",
        access_mode=AccessMode.PUBLIC_API,
        approval_status=ApprovalStatus.APPROVED,
        geographic_coverage=["DE"],
        role_coverage=["general"],
    )
    lever_entry = _lever_registry_entry(geographic_coverage=["GB"])

    adzuna_payload = {
        "id": "a1",
        "title": "Strategy Manager",
        "company": {"display_name": "Adzuna Co"},
        "location": {"display_name": "Berlin"},
        "redirect_url": "https://example.com/jobs/a1",
        "created": "2026-07-01T10:00:00Z",
        "description": "<p>Adzuna role.</p>",
        "salary_min": 50000,
        "salary_max": 70000,
        "contract_time": "full_time",
        "_query_country": "DE",
    }
    adzuna_record = RawJobRecord(
        source_id="adzuna_api",
        external_id="a1",
        raw_url="https://example.com/jobs/a1",
        raw_payload=adzuna_payload,
        fetched_at=datetime.now(UTC),
    )

    class _FakeAdzunaAdapter:
        source_id = "adzuna_api"
        access_mode = AccessMode.PUBLIC_API

        def is_configured(self) -> bool:
            return True

        def fetch(self, params: object) -> list[RawJobRecord]:
            return [adzuna_record]

    fake_adzuna = _FakeAdzunaAdapter()
    fake_lever = _FakeLeverAdapter([_lever_record("l1")])
    watchlist = [_watchlist_entry()]

    with SqliteJobRepository(tmp_path / "db.sqlite3") as repo:
        result = run_once(
            candidate_profile=candidate,
            search_profile=search,
            registry=[adzuna_entry, lever_entry],
            execution_limits=_limits(),
            scoring_weights=_weights(),
            source_scoring_weights=_source_weights(),
            repository=repo,
            env=config.EnvConfig(adzuna_app_id="id", adzuna_app_key="key"),
            dry_run=False,
            adapter_factory=lambda source_id: fake_adzuna if source_id == "adzuna_api" else None,
            company_watchlist=watchlist,
            watchlist_adapter_factory=lambda sid, company: fake_lever,
        )
        source_ids = {run.source_id for run in result.source_runs}
        assert source_ids == {"adzuna_api", "lever_public_postings"}
        assert all(run.status == SourceRunStatus.SUCCESS for run in result.source_runs)
        assert len(result.results) == 2
        job_count = repo._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        assert job_count == 2


# --- error handling ------------------------------------------------------


def test_one_company_failure_stops_remaining_companies_but_keeps_earlier_results(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    registry = [_lever_registry_entry()]
    watchlist = [
        _watchlist_entry(company_name="Alpha Co", external_company_key="alpha"),
        _watchlist_entry(company_name="Beta Co", external_company_key="beta"),
        _watchlist_entry(company_name="Gamma Co", external_company_key="gamma"),
    ]
    calls: list[str] = []

    def factory(source_id: str, company: CompanyWatchlistEntry):  # type: ignore[no-untyped-def]
        calls.append(company.external_company_key)
        if company.external_company_key == "beta":
            return _FakeLeverAdapter(fail=SourceRateLimitError("rate limited"))
        return _FakeLeverAdapter([_lever_record(company.external_company_key)])

    result = _run(
        registry=registry, watchlist=watchlist, watchlist_adapter_factory=factory, tmp_path=tmp_path
    )
    # fail-fast: gamma's adapter is never constructed once beta's fetch() raises
    assert calls == ["alpha", "beta"]
    run = result.source_runs[0]
    assert run.status == SourceRunStatus.PARTIAL
    assert run.errors
    assert run.jobs_fetched == 1  # only alpha's record survived


def test_all_companies_failing_produces_isolated_failed_run_zero_jobs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    registry = [_lever_registry_entry()]
    watchlist = [_watchlist_entry()]

    def factory(source_id: str, company: CompanyWatchlistEntry):  # type: ignore[no-untyped-def]
        return _FakeLeverAdapter(fail=SourceRateLimitError("rate limited"))

    result = _run(
        registry=registry, watchlist=watchlist, watchlist_adapter_factory=factory, tmp_path=tmp_path
    )
    run = result.source_runs[0]
    assert run.status == SourceRunStatus.FAILED
    assert run.jobs_fetched == 0
    assert result.results == []


# --- geography / compliance ---------------------------------------------------


def test_lever_selected_for_supported_gb_geography(tmp_path) -> None:  # type: ignore[no-untyped-def]
    registry = [_lever_registry_entry()]
    watchlist = [_watchlist_entry()]
    fake = _FakeLeverAdapter([_lever_record("1")])

    result = _run(
        registry=registry,
        watchlist=watchlist,
        watchlist_adapter_factory=lambda sid, company: fake,
        tmp_path=tmp_path,
    )
    selected = next(
        s for s in result.plan.selected_sources if s.source_id == "lever_public_postings"
    )
    assert selected.executable is True
    assert selected.supported_countries == ["GB"]


def test_lever_not_selected_for_unsupported_country(tmp_path) -> None:  # type: ignore[no-untyped-def]
    search = make_search_profile(included_countries=["DE"])
    registry = [_lever_registry_entry(geographic_coverage=["GB"])]
    watchlist = [_watchlist_entry()]
    fake = _FakeLeverAdapter([_lever_record("1")])

    result = _run(
        search=search,
        registry=registry,
        watchlist=watchlist,
        watchlist_adapter_factory=lambda sid, company: fake,
        tmp_path=tmp_path,
    )
    assert result.source_runs == []
    assert fake.calls == 0
    excluded_ids = {s.source_id for s in result.plan.excluded_sources}
    assert "lever_public_postings" in excluded_ids


def test_manual_review_lever_never_fetches_even_with_populated_watchlist(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    """The packaged registry template ships lever_public_postings as
    manual_review — the compliance gate must remain authoritative regardless
    of watchlist contents (CLAUDE.md hard constraint 1: the watchlist
    identifies companies, it does not itself make a source executable)."""
    registry = [
        _lever_registry_entry(
            approval_status=ApprovalStatus.MANUAL_REVIEW,
            config_status=ConfigStatus.NEEDS_SETUP,
        )
    ]
    watchlist = [_watchlist_entry()]
    fake = _FakeLeverAdapter([_lever_record("1")])

    result = _run(
        registry=registry,
        watchlist=watchlist,
        watchlist_adapter_factory=lambda sid, company: fake,
        tmp_path=tmp_path,
    )
    selected = next(
        s for s in result.plan.selected_sources if s.source_id == "lever_public_postings"
    )
    assert selected.executable is False
    assert fake.calls == 0
    assert result.source_runs == []


# --- regression: Adzuna/Reed/Greenhouse unaffected --------------------------


def test_adzuna_planned_query_path_unaffected_by_lever_watchlist_branch(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Regression: a plain keyword-search source (company_filter=False, the
    make_source_entry default) must keep using the existing step-4
    planned-query execution path unchanged, even when a (non-matching)
    company_watchlist is supplied."""
    search = make_search_profile(included_countries=["GB"], target_titles=["Strategy Manager"])
    adzuna_entry = make_source_entry(
        source_id="adzuna_api",
        access_mode=AccessMode.PUBLIC_API,
        approval_status=ApprovalStatus.APPROVED,
        geographic_coverage=["GB"],
        role_coverage=["general"],
    )
    adzuna_record = RawJobRecord(
        source_id="adzuna_api",
        external_id="1",
        raw_url="https://example.com/jobs/1",
        raw_payload={
            "id": "1",
            "title": "Strategy Manager",
            "company": {"display_name": "Adzuna Co"},
            "location": {"display_name": "London"},
            "redirect_url": "https://example.com/jobs/1",
            "created": "2026-07-01T10:00:00Z",
            "description": "<p>Adzuna role.</p>",
            "salary_min": 50000,
            "salary_max": 70000,
            "contract_time": "full_time",
            "_query_country": "GB",
        },
        fetched_at=datetime.now(UTC),
    )

    class _FakeAdzunaAdapter:
        source_id = "adzuna_api"
        access_mode = AccessMode.PUBLIC_API

        def is_configured(self) -> bool:
            return True

        def fetch(self, params: object) -> list[RawJobRecord]:
            return [adzuna_record]

    fake_adzuna = _FakeAdzunaAdapter()
    # A watchlist entry for a different source_id must never affect Adzuna.
    watchlist = [_watchlist_entry(source_id="lever_public_postings")]

    with SqliteJobRepository(tmp_path / "db.sqlite3") as repo:
        result = run_once(
            candidate_profile=make_candidate_profile(title_aliases=[], role_families=[]),
            search_profile=search,
            registry=[adzuna_entry],
            execution_limits=_limits(),
            scoring_weights=_weights(),
            source_scoring_weights=_source_weights(),
            repository=repo,
            env=config.EnvConfig(adzuna_app_id="id", adzuna_app_key="key"),
            dry_run=False,
            adapter_factory=lambda source_id: fake_adzuna if source_id == "adzuna_api" else None,
            company_watchlist=watchlist,
        )
        assert len(result.source_runs) == 1
        assert result.source_runs[0].source_id == "adzuna_api"
        assert result.source_runs[0].status == SourceRunStatus.SUCCESS


def test_greenhouse_and_lever_watchlist_entries_coexist_without_cross_talk(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    """Both watchlist-scoped ATS sources share the exact same generic
    is_watchlist_source branch (decisions.md D-047/D-048) — a watchlist
    containing entries for both source_ids must route each to only its own
    source's fetch calls, never cross-contaminating. Deliberately different
    geographic_coverage per source (GB vs. DE): two ATS sources covering
    identical geography/role_coverage/priority are correctly treated as
    redundant by the planner's own diversity rule (source_intelligence/
    planner.py::_apply_diversity_rule) and only one would be selected —
    that rule is pre-existing, unrelated to this task, and not what this
    test is checking."""
    greenhouse_entry = make_source_entry(
        source_id="greenhouse_public_feeds",
        access_mode=AccessMode.PUBLIC_ATS_FEED,
        approval_status=ApprovalStatus.APPROVED,
        geographic_coverage=["GB"],
        role_coverage=["general"],
        capabilities=SourceCapabilities(
            keyword_search=False,
            exact_phrase_search=False,
            location_filter=False,
            country_filter=False,
            city_filter=False,
            industry_filter=False,
            company_filter=True,
            remote_filter=False,
            salary_data=False,
            structured_description=False,
            pagination=False,
            page_size_control=False,
            posting_date_filter=False,
            stable_external_job_id=True,
            canonical_application_url=True,
            max_recommended_queries_per_request=None,
        ),
    )
    lever_entry = _lever_registry_entry(geographic_coverage=["DE"])
    watchlist = [
        _watchlist_entry(
            company_name="GH Co", source_id="greenhouse_public_feeds", external_company_key="ghco"
        ),
        _watchlist_entry(
            company_name="Lever Co", source_id="lever_public_postings", external_company_key="lvco"
        ),
    ]

    class _FakeGreenhouseAdapter:
        source_id = "greenhouse_public_feeds"
        access_mode = AccessMode.PUBLIC_ATS_FEED

        def __init__(self) -> None:
            self.calls = 0

        def is_configured(self) -> bool:
            return True

        def fetch(self, params: object) -> list[RawJobRecord]:
            self.calls += 1
            return [
                RawJobRecord(
                    source_id="greenhouse_public_feeds",
                    external_id="g1",
                    raw_url="https://boards.greenhouse.io/ghco/jobs/g1",
                    raw_payload={
                        "id": "g1",
                        "title": "Strategy Manager",
                        "location": {"name": "London"},
                        "absolute_url": "https://boards.greenhouse.io/ghco/jobs/g1",
                        "content": "GH role.",
                        "_company_name": "GH Co",
                    },
                    fetched_at=datetime.now(UTC),
                )
            ]

    fake_greenhouse = _FakeGreenhouseAdapter()
    fake_lever = _FakeLeverAdapter([_lever_record("l1")])

    def factory(source_id: str, company: CompanyWatchlistEntry):  # type: ignore[no-untyped-def]
        if source_id == "greenhouse_public_feeds":
            return fake_greenhouse
        if source_id == "lever_public_postings":
            return fake_lever
        return None

    result = _run(
        search=make_search_profile(included_countries=["GB", "DE"]),
        registry=[greenhouse_entry, lever_entry],
        watchlist=watchlist,
        watchlist_adapter_factory=factory,
        tmp_path=tmp_path,
    )
    assert fake_greenhouse.calls == 1
    assert fake_lever.calls == 1
    source_ids = {run.source_id for run in result.source_runs}
    assert source_ids == {"greenhouse_public_feeds", "lever_public_postings"}
