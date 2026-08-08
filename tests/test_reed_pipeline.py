"""Reed wired through the generic pipeline (Milestone 2 Deliverable 5 step 5;
decisions.md D-040/D-046). Confirms the registered `reed_api` adapter works
through the existing generic run_once/pipeline path with no source_id-
conditional branching introduced, and that Adzuna's own behaviour is
unaffected by Reed's addition."""

from __future__ import annotations

from datetime import UTC, datetime

from job_scout import config
from job_scout.models import (
    AccessMode,
    ApprovalStatus,
    ConfigStatus,
    RawJobRecord,
    SourceCapabilities,
    SourceRunStatus,
)
from job_scout.pipeline import _default_adapter_factory, run_once
from job_scout.repository.sqlite_repo import SqliteJobRepository
from job_scout.sources.reed import ReedAdapter
from tests.factories import make_candidate_profile, make_search_profile, make_source_entry

# Mirrors the packaged source_registry.example.yaml's real, verified reed_api
# capabilities block (decisions.md D-046) — `make_source_entry`'s own default
# is Adzuna-shaped and would silently misrepresent Reed's actual contract
# (in particular exact_phrase_search) in these pipeline-level tests.
REED_CAPABILITIES = SourceCapabilities(
    keyword_search=True,
    exact_phrase_search=False,
    location_filter=True,
    country_filter=False,
    city_filter=True,
    industry_filter=False,
    company_filter=False,
    remote_filter=False,
    salary_data=True,
    structured_description=False,
    pagination=True,
    page_size_control=True,
    posting_date_filter=False,
    stable_external_job_id=True,
    canonical_application_url=False,
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
    base: dict[str, object] = {"reed_api_key": "key"}
    base.update(overrides)
    return config.EnvConfig(**base)


def _reed_registry_entry(**overrides: object):  # type: ignore[no-untyped-def]
    base: dict[str, object] = {
        "source_id": "reed_api",
        "access_mode": AccessMode.PUBLIC_API,
        "approval_status": ApprovalStatus.APPROVED,
        "geographic_coverage": ["GB"],
        "role_coverage": ["general"],
        "capabilities": REED_CAPABILITIES,
    }
    base.update(overrides)
    return make_source_entry(**base)


def _reed_payload(job_id: str, title: str = "Strategy Manager") -> dict:
    return {
        "jobId": job_id,
        "employerId": 1,
        "employerName": f"Example Corp {job_id}",
        "jobTitle": title,
        "locationName": "London",
        "description": f"Great strategy and transformation role #{job_id}, 4-8 years experience.",
        "minimumSalary": 60000,
        "maximumSalary": 80000,
        "_query_country": "GB",
    }


def _reed_record(job_id: str, title: str = "Strategy Manager") -> RawJobRecord:
    return RawJobRecord(
        source_id="reed_api",
        external_id=job_id,
        raw_url="",
        raw_payload=_reed_payload(job_id, title),
        fetched_at=datetime.now(UTC),
    )


class _FakeReedAdapter:
    source_id = "reed_api"
    access_mode = AccessMode.PUBLIC_API

    def __init__(self, records: list[RawJobRecord]) -> None:
        self._records = records
        self.calls = 0

    def is_configured(self) -> bool:
        return True

    def fetch(self, params: object) -> list[RawJobRecord]:
        self.calls += 1
        return self._records


# --- registration / adapter factory ------------------------------------------


def test_reed_registered_in_default_adapter_factory() -> None:
    factory = _default_adapter_factory(
        config.EnvConfig(reed_api_key="key"), _limits()
    )
    adapter = factory("reed_api")
    assert isinstance(adapter, ReedAdapter)
    assert adapter.is_configured() is True


def test_reed_adapter_factory_without_key_returns_unconfigured_adapter() -> None:
    factory = _default_adapter_factory(config.EnvConfig(reed_api_key=None), _limits())
    adapter = factory("reed_api")
    assert isinstance(adapter, ReedAdapter)
    assert adapter.is_configured() is False


# --- pipeline execution through run_once -------------------------------------


def test_run_once_pulls_from_reed_via_registered_adapter(tmp_path) -> None:  # type: ignore[no-untyped-def]
    candidate = make_candidate_profile(
        title_aliases=["Strategy Manager"], role_families=["strategy_and_planning"]
    )
    search = make_search_profile(included_countries=["GB"])
    registry = [_reed_registry_entry()]
    fake_adapter = _FakeReedAdapter([_reed_record("1"), _reed_record("2")])

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
            adapter_factory=lambda source_id: fake_adapter if source_id == "reed_api" else None,
        )

        assert len(result.source_runs) == 1
        assert result.source_runs[0].source_id == "reed_api"
        assert result.source_runs[0].status == SourceRunStatus.SUCCESS
        assert len(result.results) == 2
        job_count = repo._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        assert job_count == 2


def test_multi_source_run_pulls_from_both_adzuna_and_reed_fixtures(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Acceptance criterion (MILESTONE_2.md Deliverable 5 step 5): a
    fixture-driven run-once pulls from both Adzuna and Reed fixtures in one
    run, through the same generic pipeline, no source_id branching visible
    at the call site beyond the existing adapter_factory dict lookup.

    Adzuna is scoped to DE (not GB) here so the two sources' supported-
    country sets don't overlap — both are pre-existing generic aggregators
    (`role_coverage=["general"]`), and planner.py's own (pre-existing,
    out-of-scope-for-this-task) diversity rule keeps only the higher-scoring
    survivor when two generic aggregators' supported countries *do* overlap
    (architecture.md section 6 step 4). GB+DE is exactly the complementary,
    non-redundant multi-source shape this rule is designed to keep."""
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
    reed_entry = _reed_registry_entry()

    adzuna_payload = {
        "id": "a1",
        "title": "Strategy Manager",
        "company": {"display_name": "Adzuna Co"},
        "location": {"display_name": "London"},
        "redirect_url": "https://example.com/jobs/a1",
        "created": "2026-07-01T10:00:00Z",
        "description": "<p>Adzuna role.</p>",
        "salary_min": 50000,
        "salary_max": 70000,
        "contract_time": "full_time",
        "_query_country": "GB",
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
    fake_reed = _FakeReedAdapter([_reed_record("r1")])

    def factory(source_id: str):  # type: ignore[no-untyped-def]
        return {"adzuna_api": fake_adzuna, "reed_api": fake_reed}.get(source_id)

    with SqliteJobRepository(tmp_path / "db.sqlite3") as repo:
        result = run_once(
            candidate_profile=candidate,
            search_profile=search,
            registry=[adzuna_entry, reed_entry],
            execution_limits=_limits(),
            scoring_weights=_weights(),
            source_scoring_weights=_source_weights(),
            repository=repo,
            env=config.EnvConfig(adzuna_app_id="id", adzuna_app_key="key", reed_api_key="key"),
            dry_run=False,
            adapter_factory=factory,
        )

        source_ids = {run.source_id for run in result.source_runs}
        assert source_ids == {"adzuna_api", "reed_api"}
        assert all(run.status == SourceRunStatus.SUCCESS for run in result.source_runs)
        assert len(result.results) == 2
        job_count = repo._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        assert job_count == 2


def test_reed_normalization_dedup_scoring_receive_canonical_job_same_as_adzuna(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    """No stage after normalisation may branch on source_id (D-040) — a Reed
    job reaches Stage 1/Stage 5 exactly like an Adzuna job."""
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    search = make_search_profile(included_countries=["GB"], target_titles=["Strategy Manager"])
    registry = [_reed_registry_entry()]
    fake_adapter = _FakeReedAdapter([_reed_record("1", title="Strategy Manager")])

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
            adapter_factory=lambda source_id: fake_adapter if source_id == "reed_api" else None,
        )
        job, match = result.results[0]
        assert job.source_provenance[0].source_id == "reed_api"
        assert match.hard_filter_result.passed is True
        assert match.final_score is not None


def test_multiple_planned_queries_use_existing_task4_execution_path(tmp_path) -> None:  # type: ignore[no-untyped-def]
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    search = make_search_profile(
        included_countries=["GB"], target_titles=["Chief of Staff", "Transformation Lead"]
    )
    registry = [_reed_registry_entry()]
    fake_adapter = _FakeReedAdapter([_reed_record("1")])

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
            adapter_factory=lambda source_id: fake_adapter if source_id == "reed_api" else None,
        )
        # capability exact_phrase_search=False degrades both target-title
        # queries to any_of_words — still two distinct PlannedQuery calls.
        planned = next(
            s for s in result.plan.selected_sources if s.source_id == "reed_api"
        ).planned_queries
        assert len(planned) == 2
        assert all(q.mode == "any_of_words" for q in planned)
        assert fake_adapter.calls == 2


# --- geography / compliance ---------------------------------------------------


def test_reed_selected_for_supported_gb_geography(tmp_path) -> None:  # type: ignore[no-untyped-def]
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["GB"])
    registry = [_reed_registry_entry()]
    fake_adapter = _FakeReedAdapter([_reed_record("1")])

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
            adapter_factory=lambda source_id: fake_adapter if source_id == "reed_api" else None,
        )
        reed_selected = next(s for s in result.plan.selected_sources if s.source_id == "reed_api")
        assert reed_selected.executable is True
        assert reed_selected.supported_countries == ["GB"]


def test_reed_not_executed_for_unsupported_country(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """reed_api's geographic_coverage is GB-only (per the packaged
    registry) — a DE-only search profile must never select/execute it."""
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["DE"])
    registry = [_reed_registry_entry(geographic_coverage=["GB"])]
    fake_adapter = _FakeReedAdapter([_reed_record("1")])

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
            adapter_factory=lambda source_id: fake_adapter if source_id == "reed_api" else None,
        )
        assert result.source_runs == []
        assert fake_adapter.calls == 0
        excluded_ids = {s.source_id for s in result.plan.excluded_sources}
        assert "reed_api" in excluded_ids


def test_manual_review_reed_never_fetches_even_with_credentials(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The packaged registry template ships reed_api as manual_review — the
    compliance gate must remain authoritative regardless of credentials."""
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["GB"])
    registry = [
        _reed_registry_entry(
            approval_status=ApprovalStatus.MANUAL_REVIEW,
            config_status=ConfigStatus.NEEDS_CREDENTIALS,
        )
    ]
    fake_adapter = _FakeReedAdapter([_reed_record("1")])

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
            adapter_factory=lambda source_id: fake_adapter if source_id == "reed_api" else None,
        )
        selected = next(s for s in result.plan.selected_sources if s.source_id == "reed_api")
        assert selected.executable is False
        assert fake_adapter.calls == 0
        assert result.source_runs == []


def test_reed_without_credentials_reports_needs_credentials(tmp_path) -> None:  # type: ignore[no-untyped-def]
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["GB"])
    registry = [_reed_registry_entry()]

    with SqliteJobRepository(tmp_path / "db.sqlite3") as repo:
        result = run_once(
            candidate_profile=candidate,
            search_profile=search,
            registry=registry,
            execution_limits=_limits(),
            scoring_weights=_weights(),
            source_scoring_weights=_source_weights(),
            repository=repo,
            env=config.EnvConfig(reed_api_key=None),
            dry_run=False,
            adapter_factory=None,  # use the real, unconfigured ReedAdapter
        )
        reed_selected = next(s for s in result.plan.selected_sources if s.source_id == "reed_api")
        assert reed_selected.effective_config_status == ConfigStatus.NEEDS_CREDENTIALS
        assert result.source_runs[0].status == SourceRunStatus.FAILED
        assert "not configured" in result.source_runs[0].errors[0].lower()


def test_reed_with_credentials_becomes_effectively_configured(tmp_path) -> None:  # type: ignore[no-untyped-def]
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["GB"])
    registry = [_reed_registry_entry()]
    fake_adapter = _FakeReedAdapter([_reed_record("1")])

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
            adapter_factory=lambda source_id: fake_adapter if source_id == "reed_api" else None,
        )
        reed_selected = next(s for s in result.plan.selected_sources if s.source_id == "reed_api")
        assert reed_selected.effective_config_status == ConfigStatus.CONFIGURED


def test_adzuna_behaviour_unchanged_by_reed_addition(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Regression: adding reed_api's credential rule to
    _effective_config_status must not alter adzuna_api's own rule."""
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["GB"])
    adzuna_entry = make_source_entry(
        source_id="adzuna_api",
        access_mode=AccessMode.PUBLIC_API,
        approval_status=ApprovalStatus.APPROVED,
        geographic_coverage=["GB"],
        role_coverage=["general"],
    )

    with SqliteJobRepository(tmp_path / "db.sqlite3") as repo:
        result = run_once(
            candidate_profile=candidate,
            search_profile=search,
            registry=[adzuna_entry],
            execution_limits=_limits(),
            scoring_weights=_weights(),
            source_scoring_weights=_source_weights(),
            repository=repo,
            env=config.EnvConfig(adzuna_app_id=None, adzuna_app_key=None, reed_api_key="key"),
            dry_run=False,
            adapter_factory=None,
        )
        adzuna_selected = next(
            s for s in result.plan.selected_sources if s.source_id == "adzuna_api"
        )
        assert adzuna_selected.effective_config_status == ConfigStatus.NEEDS_CREDENTIALS
