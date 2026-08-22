from datetime import UTC, datetime

from job_scout import config
from job_scout.models import (
    AccessMode,
    ApprovalStatus,
    RawJobRecord,
    SourceRunStatus,
)
from job_scout.pipeline import run_once
from job_scout.repository.sqlite_repo import SqliteJobRepository
from job_scout.sources.base import SourceAuthError
from tests.factories import make_candidate_profile, make_search_profile, make_source_entry


def _limits(**overrides: object) -> config.ExecutionLimits:
    base = {
        "max_countries_per_run": 6,
        "max_pages_per_source_country": 3,
        "results_per_page": 50,
        "request_timeout_seconds": 15,
        "max_retries": 2,
        "max_jobs_processed_per_run": None,
    }
    base.update(overrides)
    return config.ExecutionLimits.model_validate(base)


def _weights() -> config.ScoringWeights:
    return config.load_scoring_weights()


def _source_weights() -> config.SourceScoringWeights:
    return config.load_source_scoring_weights()


def _env() -> config.EnvConfig:
    return config.EnvConfig(adzuna_app_id="id", adzuna_app_key="key")


def _adzuna_job_payload(job_id: str, title: str = "Strategy Manager") -> dict:
    return {
        "id": job_id,
        "title": title,
        # each fixture job is a genuinely distinct posting (different
        # company), so accidental cross-source-duplicate matching (tier 2,
        # deduplication.py) doesn't fire between fixture records that are
        # only meant to differ by id.
        "company": {"display_name": f"Example Corp {job_id}"},
        "location": {"display_name": "London"},
        "redirect_url": f"https://example.com/jobs/{job_id}",
        "created": "2026-07-01T10:00:00Z",
        "description": (
            f"<p>Great strategy and transformation role #{job_id}, 4-8 years experience.</p>"
        ),
        "salary_min": 60000,
        "salary_max": 80000,
        "contract_time": "full_time",
        "_query_country": "GB",
    }


class _FakeAdapter:
    source_id = "adzuna_api"
    access_mode = AccessMode.PUBLIC_API

    def __init__(
        self, records: list[RawJobRecord], *, raise_error: Exception | None = None
    ) -> None:
        self._records = records
        self._raise_error = raise_error
        self.calls = 0

    def is_configured(self) -> bool:
        return True

    def fetch(self, params: object) -> list[RawJobRecord]:
        self.calls += 1
        if self._raise_error:
            raise self._raise_error
        return self._records


def _make_records(job_ids: list[str]) -> list[RawJobRecord]:
    return [
        RawJobRecord(
            source_id="adzuna_api",
            external_id=job_id,
            raw_url=f"https://example.com/jobs/{job_id}",
            raw_payload=_adzuna_job_payload(job_id),
            fetched_at=datetime.now(UTC),
        )
        for job_id in job_ids
    ]


def _adzuna_registry_entry():  # type: ignore[no-untyped-def]
    return make_source_entry(
        source_id="adzuna_api",
        access_mode=AccessMode.PUBLIC_API,
        approval_status=ApprovalStatus.APPROVED,
        geographic_coverage=["GB"],
        role_coverage=["general"],
    )


def test_run_once_persists_jobs_source_runs_and_match_results(tmp_path) -> None:  # type: ignore[no-untyped-def]
    candidate = make_candidate_profile(
        title_aliases=["Strategy Manager"], role_families=["strategy_and_planning"]
    )
    search = make_search_profile(included_countries=["GB"])
    registry = [_adzuna_registry_entry()]
    fake_records = _make_records(["1", "2"])
    fake_adapter = _FakeAdapter(fake_records)

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
            dry_run=True,
            adapter_factory=lambda source_id: fake_adapter if source_id == "adzuna_api" else None,
        )

        assert len(result.source_runs) == 1
        assert result.source_runs[0].status == SourceRunStatus.SUCCESS
        assert result.source_runs[0].jobs_new == 2
        assert len(result.results) == 2

        row_count = repo._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        assert row_count == 2
        run_count = repo._conn.execute("SELECT COUNT(*) FROM source_runs").fetchone()[0]
        assert run_count == 1
        match_count = repo._conn.execute("SELECT COUNT(*) FROM match_results").fetchone()[0]
        assert match_count == 2


def test_dry_run_still_persists_everything_not_read_only(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Dry-run is not a read-only mode (architecture.md section 11) —
    fetching/normalising/dedup/scoring/local persistence all happen."""
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["GB"])
    registry = [_adzuna_registry_entry()]
    fake_adapter = _FakeAdapter(_make_records(["1"]))

    with SqliteJobRepository(tmp_path / "db.sqlite3") as repo:
        run_once(
            candidate_profile=candidate,
            search_profile=search,
            registry=registry,
            execution_limits=_limits(),
            scoring_weights=_weights(),
            source_scoring_weights=_source_weights(),
            repository=repo,
            env=_env(),
            dry_run=True,
            adapter_factory=lambda source_id: fake_adapter,
        )
        assert repo._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_second_run_same_jobs_does_not_duplicate_job_rows(tmp_path) -> None:  # type: ignore[no-untyped-def]
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["GB"])
    registry = [_adzuna_registry_entry()]

    with SqliteJobRepository(tmp_path / "db.sqlite3") as repo:
        for _ in range(2):
            fake_adapter = _FakeAdapter(_make_records(["1", "2"]))
            run_once(
                candidate_profile=candidate,
                search_profile=search,
                registry=registry,
                execution_limits=_limits(),
                scoring_weights=_weights(),
                source_scoring_weights=_source_weights(),
                repository=repo,
                env=_env(),
                dry_run=False,
                adapter_factory=lambda source_id, _adapter=fake_adapter: _adapter,
            )

        job_count = repo._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        assert job_count == 2  # not duplicated on the second run
        provenance_count = repo._conn.execute("SELECT COUNT(*) FROM source_provenance").fetchone()[
            0
        ]
        assert provenance_count == 4  # 2 jobs x 2 runs, each run merges provenance
        match_count = repo._conn.execute("SELECT COUNT(*) FROM match_results").fetchone()[0]
        assert match_count == 4  # match_results accumulate per run, not deduped


def test_source_isolation_one_failing_source_does_not_abort_run(tmp_path) -> None:  # type: ignore[no-untyped-def]
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["GB", "DE"])
    good_entry = _adzuna_registry_entry()
    bad_entry = make_source_entry(
        source_id="broken_source",
        access_mode=AccessMode.PUBLIC_API,
        approval_status=ApprovalStatus.APPROVED,
        geographic_coverage=["DE"],
        role_coverage=["general"],
    )
    good_adapter = _FakeAdapter(_make_records(["1"]))
    bad_adapter = _FakeAdapter([], raise_error=SourceAuthError("bad credentials"))

    def factory(source_id: str):  # type: ignore[no-untyped-def]
        return {"adzuna_api": good_adapter, "broken_source": bad_adapter}.get(source_id)

    with SqliteJobRepository(tmp_path / "db.sqlite3") as repo:
        result = run_once(
            candidate_profile=candidate,
            search_profile=search,
            registry=[good_entry, bad_entry],
            execution_limits=_limits(),
            scoring_weights=_weights(),
            source_scoring_weights=_source_weights(),
            repository=repo,
            env=_env(),
            dry_run=False,
            adapter_factory=factory,
        )
        statuses = {run.source_id: run.status for run in result.source_runs}
        assert statuses["adzuna_api"] == SourceRunStatus.SUCCESS
        assert statuses["broken_source"] == SourceRunStatus.FAILED
        assert len(result.results) == 1  # the good source's job still made it through


def test_max_jobs_processed_per_run_guardrail_caps_pipeline_processing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["GB"])
    registry = [_adzuna_registry_entry()]
    fake_adapter = _FakeAdapter(_make_records(["1", "2", "3", "4", "5"]))

    with SqliteJobRepository(tmp_path / "db.sqlite3") as repo:
        result = run_once(
            candidate_profile=candidate,
            search_profile=search,
            registry=registry,
            execution_limits=_limits(max_jobs_processed_per_run=2),
            scoring_weights=_weights(),
            source_scoring_weights=_source_weights(),
            repository=repo,
            env=_env(),
            dry_run=False,
            adapter_factory=lambda source_id: fake_adapter,
        )
        assert len(result.results) == 2
        job_count = repo._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        assert job_count == 2


def test_cli_limit_further_lowers_but_never_raises_the_configured_ceiling(tmp_path) -> None:  # type: ignore[no-untyped-def]
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["GB"])
    registry = [_adzuna_registry_entry()]
    fake_adapter = _FakeAdapter(_make_records(["1", "2", "3", "4", "5"]))

    with SqliteJobRepository(tmp_path / "db.sqlite3") as repo:
        result = run_once(
            candidate_profile=candidate,
            search_profile=search,
            registry=registry,
            execution_limits=_limits(max_jobs_processed_per_run=4),
            scoring_weights=_weights(),
            source_scoring_weights=_source_weights(),
            repository=repo,
            env=_env(),
            dry_run=False,
            limit=1,
            adapter_factory=lambda source_id: fake_adapter,
        )
        assert len(result.results) == 1

    with SqliteJobRepository(tmp_path / "db2.sqlite3") as repo2:
        fake_adapter2 = _FakeAdapter(_make_records(["1", "2", "3", "4", "5"]))
        result2 = run_once(
            candidate_profile=candidate,
            search_profile=search,
            registry=registry,
            execution_limits=_limits(max_jobs_processed_per_run=2),
            scoring_weights=_weights(),
            source_scoring_weights=_source_weights(),
            repository=repo2,
            env=_env(),
            dry_run=False,
            limit=100,  # a CLI limit higher than the configured ceiling never raises it
            adapter_factory=lambda source_id: fake_adapter2,
        )
        assert len(result2.results) == 2


def test_gb_ie_profile_executes_gb_via_adzuna_without_ie_in_params(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Regression for the live D-028 finding: a search profile that still
    requests GB+IE must still run Adzuna successfully for GB — the planner
    already narrows search_params.countries to the registry-supported
    subset, so IE (unsupported for adzuna_api per the fixed registry
    coverage) never reaches the adapter and never causes a 404."""
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["GB", "IE"])
    registry = [_adzuna_registry_entry()]  # geographic_coverage=["GB"] only
    fake_adapter = _FakeAdapter(_make_records(["1"]))

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
            adapter_factory=lambda source_id: fake_adapter if source_id == "adzuna_api" else None,
        )

        assert fake_adapter.calls == 1
        assert len(result.source_runs) == 1
        assert result.source_runs[0].source_id == "adzuna_api"
        assert result.source_runs[0].status == SourceRunStatus.SUCCESS
        assert result.source_runs[0].errors == []
        assert len(result.results) == 1

        adzuna_selected = next(
            s for s in result.plan.selected_sources if s.source_id == "adzuna_api"
        )
        assert adzuna_selected.executable is True
        assert adzuna_selected.supported_countries == ["GB"]
        assert [c.country for c in adzuna_selected.unsupported_countries] == ["IE"]
        assert [c.reason for c in adzuna_selected.unsupported_countries] == [
            "not_in_geographic_coverage"
        ]


def _custom_payload(job_id: str, title: str, description: str) -> dict:
    return {
        "id": job_id,
        "title": title,
        "company": {"display_name": f"Example Corp {job_id}"},
        "location": {"display_name": "London"},
        "redirect_url": f"https://example.com/jobs/{job_id}",
        "created": "2026-07-01T10:00:00Z",
        "description": f"<p>{description}</p>",
        "salary_min": 60000,
        "salary_max": 80000,
        "contract_time": "full_time",
        "_query_country": "GB",
    }


def test_search_profile_target_title_reaches_scoring_while_irrelevant_and_hard_filtered_jobs_do_not(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    """decisions.md D-029/pipeline wiring: SearchProfile is threaded into
    run_prefilter, so a job matching only SearchProfile.target_titles (the
    candidate profile has no matching fields at all) still reaches Stage 5
    scoring; an unrelated job stays store_only/unscored; a job that fails
    Stage 1 hard filters is rejected separately and never reaches
    prefilter/scoring at all. All three are persisted regardless."""
    candidate = make_candidate_profile(
        title_aliases=[], role_families=[], primary_skills=[], secondary_skills=[]
    )
    search = make_search_profile(
        included_countries=["GB"], target_titles=["Chief of Staff"], role_families=[]
    )
    registry = [_adzuna_registry_entry()]

    records = [
        RawJobRecord(
            source_id="adzuna_api",
            external_id="relevant",
            raw_url="https://example.com/jobs/relevant",
            raw_payload=_custom_payload(
                "relevant", "Chief of Staff", "Support the CEO office, 4-8 years experience."
            ),
            fetched_at=datetime.now(UTC),
        ),
        RawJobRecord(
            source_id="adzuna_api",
            external_id="irrelevant",
            raw_url="https://example.com/jobs/irrelevant",
            raw_payload=_custom_payload(
                "irrelevant",
                "Recruitment Consultant",
                "Assist clients with recruitment across sectors, 4-8 years experience.",
            ),
            fetched_at=datetime.now(UTC),
        ),
        RawJobRecord(
            source_id="adzuna_api",
            external_id="hard_filtered",
            raw_payload=_custom_payload(
                "hard_filtered",
                "Chief of Staff",
                "We are unable to offer sponsorship for this role, 4-8 years experience.",
            ),
            raw_url="https://example.com/jobs/hard_filtered",
            fetched_at=datetime.now(UTC),
        ),
    ]
    fake_adapter = _FakeAdapter(records)

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
            adapter_factory=lambda source_id: fake_adapter,
        )

        by_id = {job.external_ids[0].external_id: match for job, match in result.results}
        assert by_id["relevant"].final_score is not None
        assert by_id["relevant"].notification_tier.value != "rejected"
        assert by_id["irrelevant"].final_score is None
        assert by_id["irrelevant"].notification_tier.value == "store_only"
        assert by_id["hard_filtered"].final_score is None
        assert by_id["hard_filtered"].notification_tier.value == "rejected"

        job_count = repo._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        assert job_count == 3  # all three persisted regardless of outcome


def test_run_once_persists_visa_assessment_for_every_stage5_job(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """MILESTONE_2.md Deliverable 5 step 10 acceptance criterion:
    VisaAssessment is constructed and persisted (repository.save_visa_assessment)
    for every job that reaches Stage 5 scoring."""
    candidate = make_candidate_profile(
        title_aliases=["Strategy Manager"], role_families=["strategy_and_planning"]
    )
    search = make_search_profile(included_countries=["GB"])
    registry = [_adzuna_registry_entry()]
    fake_adapter = _FakeAdapter(_make_records(["1", "2"]))

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
            adapter_factory=lambda source_id: fake_adapter,
        )
        assert len(result.results) == 2
        assert all(match.final_score is not None for _job, match in result.results)

        visa_count = repo._conn.execute("SELECT COUNT(*) FROM visa_assessments").fetchone()[0]
        assert visa_count == 2
        job_ids = {job.job_id for job, _match in result.results}
        persisted_ids = {
            row[0] for row in repo._conn.execute("SELECT job_id FROM visa_assessments")
        }
        assert persisted_ids == job_ids


def test_run_once_does_not_persist_visa_assessment_for_non_stage5_job(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    """A job that never reaches Stage 5 (store_only/rejected) gets no
    VisaAssessment either — persisted 1:1 with match_results.final_score,
    reusing the target-title fixture from
    test_search_profile_target_title_reaches_scoring_while_irrelevant_and_hard_filtered_jobs_do_not."""
    candidate = make_candidate_profile(
        title_aliases=[], role_families=[], primary_skills=[], secondary_skills=[]
    )
    search = make_search_profile(
        included_countries=["GB"], target_titles=["Chief of Staff"], role_families=[]
    )
    registry = [_adzuna_registry_entry()]

    records = [
        RawJobRecord(
            source_id="adzuna_api",
            external_id="relevant",
            raw_url="https://example.com/jobs/relevant",
            raw_payload=_custom_payload(
                "relevant", "Chief of Staff", "Support the CEO office, 4-8 years experience."
            ),
            fetched_at=datetime.now(UTC),
        ),
        RawJobRecord(
            source_id="adzuna_api",
            external_id="irrelevant",
            raw_url="https://example.com/jobs/irrelevant",
            raw_payload=_custom_payload(
                "irrelevant",
                "Recruitment Consultant",
                "Assist clients with recruitment across sectors, 4-8 years experience.",
            ),
            fetched_at=datetime.now(UTC),
        ),
        RawJobRecord(
            source_id="adzuna_api",
            external_id="hard_filtered",
            raw_payload=_custom_payload(
                "hard_filtered",
                "Chief of Staff",
                "We are unable to offer sponsorship for this role, 4-8 years experience.",
            ),
            raw_url="https://example.com/jobs/hard_filtered",
            fetched_at=datetime.now(UTC),
        ),
    ]
    fake_adapter = _FakeAdapter(records)

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
            adapter_factory=lambda source_id: fake_adapter,
        )

        by_id = {job.external_ids[0].external_id: job for job, _match in result.results}
        visa_job_ids = {
            row[0] for row in repo._conn.execute("SELECT job_id FROM visa_assessments")
        }
        assert visa_job_ids == {by_id["relevant"].job_id}


def test_unconfigured_adapter_produces_isolated_failed_run_not_a_crash(tmp_path) -> None:  # type: ignore[no-untyped-def]
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["GB"])
    registry = [_adzuna_registry_entry()]

    with SqliteJobRepository(tmp_path / "db.sqlite3") as repo:
        result = run_once(
            candidate_profile=candidate,
            search_profile=search,
            registry=registry,
            execution_limits=_limits(),
            scoring_weights=_weights(),
            source_scoring_weights=_source_weights(),
            repository=repo,
            env=config.EnvConfig(adzuna_app_id=None, adzuna_app_key=None),
            dry_run=False,
            adapter_factory=None,  # use the real AdzunaAdapter, unconfigured
        )
        assert result.source_runs[0].status == SourceRunStatus.FAILED
        assert "not configured" in result.source_runs[0].errors[0].lower()
        assert result.results == []
