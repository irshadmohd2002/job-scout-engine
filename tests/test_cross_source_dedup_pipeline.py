"""Milestone 2 Deliverable 5 step 9: cross-source deduplication wired through
the generic pipeline (deduplication.py's new tiers, sqlite_repo.py's
list_provenance()). Confirms the acceptance criterion in MILESTONE_2.md's
Deliverable 5 step 9: a fixture-driven multi-source run collapses a genuine
cross-source duplicate into one canonical `Job` with multiple provenance
rows, rather than persisting the same vacancy twice.
"""

from __future__ import annotations

from datetime import UTC, datetime

from job_scout import config
from job_scout.models import (
    AccessMode,
    ApprovalStatus,
    CompanyWatchlistEntry,
    RawJobRecord,
    SourceCapabilities,
    SourceRunStatus,
)
from job_scout.pipeline import run_once
from job_scout.repository.sqlite_repo import SqliteJobRepository
from tests.factories import make_candidate_profile, make_search_profile, make_source_entry

# Same shape as test_greenhouse_pipeline.py's GREENHOUSE_CAPABILITIES — the
# packaged registry template's real, verified greenhouse_public_feeds block
# (canonical_application_url: true).
_GREENHOUSE_CAPABILITIES = SourceCapabilities(
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
)

# The one canonical apply URL both sources happen to report for the same
# underlying vacancy — the exact scenario MILESTONE_2.md's "Deduplication
# implications" describes ("some aggregators... resolve directly to the same
# application URL an ATS feed would also report").
_SHARED_CANONICAL_URL = "https://jobs.example.com/apply/12345"


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


def _adzuna_record() -> RawJobRecord:
    payload = {
        "id": "a1",
        "title": "Strategy Manager",
        "company": {"display_name": "Example Co"},
        "location": {"display_name": "Berlin"},
        "redirect_url": _SHARED_CANONICAL_URL,
        "created": "2026-07-01T10:00:00Z",
        "description": "<p>Great strategy role, discovered via Adzuna.</p>",
        "salary_min": 60000,
        "salary_max": 80000,
        "contract_time": "full_time",
        "_query_country": "DE",
    }
    return RawJobRecord(
        source_id="adzuna_api",
        external_id="a1",
        raw_url=_SHARED_CANONICAL_URL,
        raw_payload=payload,
        fetched_at=datetime.now(UTC),
    )


def _greenhouse_record() -> RawJobRecord:
    payload = {
        "id": "g1",
        "title": "Strategy Manager (ATS listing)",
        "location": {"name": "Berlin, Germany"},
        "absolute_url": _SHARED_CANONICAL_URL,
        "content": "Same underlying vacancy, re-hosted on the employer's own ATS board.",
        "_company_name": "Example Co",
    }
    return RawJobRecord(
        source_id="greenhouse_public_feeds",
        external_id="g1",
        raw_url=_SHARED_CANONICAL_URL,
        raw_payload=payload,
        fetched_at=datetime.now(UTC),
    )


class _FakeAdapter:
    def __init__(
        self, source_id: str, access_mode: AccessMode, records: list[RawJobRecord]
    ) -> None:
        self.source_id = source_id
        self.access_mode = access_mode
        self._records = records
        self.calls = 0

    def is_configured(self) -> bool:
        return True

    def fetch(self, params: object) -> list[RawJobRecord]:
        self.calls += 1
        return self._records


def test_exact_canonical_url_match_merges_adzuna_and_greenhouse_into_one_job(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    search = make_search_profile(
        included_countries=["DE", "GB"], target_titles=["Strategy Manager"]
    )
    adzuna_entry = make_source_entry(
        source_id="adzuna_api",
        access_mode=AccessMode.PUBLIC_API,
        approval_status=ApprovalStatus.APPROVED,
        geographic_coverage=["DE"],
        role_coverage=["general"],
    )
    greenhouse_entry = make_source_entry(
        source_id="greenhouse_public_feeds",
        access_mode=AccessMode.PUBLIC_ATS_FEED,
        approval_status=ApprovalStatus.APPROVED,
        geographic_coverage=["GB"],
        role_coverage=["general"],
        capabilities=_GREENHOUSE_CAPABILITIES,
    )

    fake_adzuna = _FakeAdapter("adzuna_api", AccessMode.PUBLIC_API, [_adzuna_record()])
    fake_greenhouse = _FakeAdapter(
        "greenhouse_public_feeds", AccessMode.PUBLIC_ATS_FEED, [_greenhouse_record()]
    )
    watchlist = [
        CompanyWatchlistEntry(
            company_name="Example Co",
            source_id="greenhouse_public_feeds",
            external_company_key="exampleco",
            priority=50,
        )
    ]

    with SqliteJobRepository(tmp_path / "db.sqlite3") as repo:
        result = run_once(
            candidate_profile=candidate,
            search_profile=search,
            registry=[adzuna_entry, greenhouse_entry],
            execution_limits=_limits(),
            scoring_weights=config.load_scoring_weights(),
            source_scoring_weights=config.load_source_scoring_weights(),
            repository=repo,
            env=config.EnvConfig(adzuna_app_id="id", adzuna_app_key="key"),
            dry_run=False,
            adapter_factory=lambda sid: fake_adzuna if sid == "adzuna_api" else None,
            company_watchlist=watchlist,
            watchlist_adapter_factory=lambda sid, company: fake_greenhouse,
        )

        # Both source runs executed and fetched one record each...
        source_ids = {run.source_id for run in result.source_runs}
        assert source_ids == {"adzuna_api", "greenhouse_public_feeds"}
        assert all(run.status == SourceRunStatus.SUCCESS for run in result.source_runs)

        # ...but they collapse into a single canonical Job, not two.
        job_count = repo._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        assert job_count == 1

        # One run reports it as new, the other as a merged duplicate.
        jobs_new_total = sum(run.jobs_new for run in result.source_runs)
        jobs_duplicate_total = sum(run.jobs_duplicate for run in result.source_runs)
        assert jobs_new_total == 1
        assert jobs_duplicate_total == 1

        # The canonical job carries provenance from both sources.
        canonical_job_id = repo._conn.execute("SELECT job_id FROM jobs").fetchone()[0]
        provenance = repo.list_provenance(canonical_job_id)
        assert {p.source_id for p in provenance} == {"adzuna_api", "greenhouse_public_feeds"}
        assert len(provenance) == 2


def test_list_provenance_returns_empty_for_unknown_job(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with SqliteJobRepository(tmp_path / "db.sqlite3") as repo:
        assert repo.list_provenance("does-not-exist") == []
