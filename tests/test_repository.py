from datetime import timedelta

import pytest

from job_scout.models import (
    HardFilterResult,
    MatchResult,
    NotificationTier,
    PrefilterResult,
    SourceRun,
    SourceRunStatus,
)
from job_scout.repository.sqlite_repo import SqliteJobRepository
from tests.factories import make_fingerprint, make_job, make_provenance, utcnow


@pytest.fixture()
def repo(tmp_path):  # type: ignore[no-untyped-def]
    with SqliteJobRepository(tmp_path / "test.sqlite3") as r:
        yield r


def test_find_by_fingerprint_returns_none_for_novel(repo: SqliteJobRepository) -> None:
    assert repo.find_by_fingerprint(make_fingerprint()) is None


def test_save_and_find_job_round_trip(repo: SqliteJobRepository) -> None:
    job = make_job()
    repo.save_job(job)
    found = repo.find_by_fingerprint(job.fingerprint)
    assert found is not None
    assert found.job_id == job.job_id
    assert found.title == job.title


def test_merge_provenance_appends_and_is_idempotent(repo: SqliteJobRepository) -> None:
    job = make_job(source_provenance=[make_provenance(source_id="adzuna_api")])
    repo.save_job(job)

    second = make_provenance(source_id="other_source", external_id="xyz")
    repo.merge_provenance(job.job_id, second)

    found = repo.find_by_fingerprint(job.fingerprint)
    assert found is not None
    assert {p.source_id for p in found.source_provenance} == {"adzuna_api", "other_source"}

    # calling again with the same provenance does not duplicate it
    repo.merge_provenance(job.job_id, second)
    found_again = repo.find_by_fingerprint(job.fingerprint)
    assert found_again is not None
    assert len(found_again.source_provenance) == 2


def test_merge_provenance_unknown_job_raises(repo: SqliteJobRepository) -> None:
    with pytest.raises(KeyError):
        repo.merge_provenance("does-not-exist", make_provenance())


def test_list_recent_jobs_filters_by_since_and_orders_desc(repo: SqliteJobRepository) -> None:
    now = utcnow()
    old_job = make_job(
        job_id="00000000-0000-0000-0000-000000000001",
        collected_at=now - timedelta(days=10),
        fingerprint=make_fingerprint(canonical_url="https://example.com/jobs/old"),
    )
    new_job = make_job(
        job_id="00000000-0000-0000-0000-000000000002",
        collected_at=now,
        fingerprint=make_fingerprint(canonical_url="https://example.com/jobs/new"),
    )
    repo.save_job(old_job)
    repo.save_job(new_job)

    recent = repo.list_recent_jobs(since=now - timedelta(days=1))
    assert [j.job_id for j in recent] == [new_job.job_id]


def test_save_source_run_round_trip(repo: SqliteJobRepository) -> None:
    run = SourceRun(
        run_id="run-1",
        source_id="adzuna_api",
        search_profile_ref="strategy-global",
        started_at=utcnow(),
        completed_at=utcnow(),
        status=SourceRunStatus.SUCCESS,
        jobs_fetched=10,
        jobs_new=8,
        jobs_duplicate=2,
        errors=[],
    )
    repo.save_source_run(run)
    row = repo._conn.execute(
        "SELECT source_id, jobs_fetched FROM source_runs WHERE run_id=?", ("run-1",)
    ).fetchone()
    assert row == ("adzuna_api", 10)


def test_save_match_result_round_trip(repo: SqliteJobRepository) -> None:
    job = make_job()
    repo.save_job(job)
    result = MatchResult(
        job_id=job.job_id,
        search_profile_ref="strategy-global",
        hard_filter_result=HardFilterResult(passed=True, rejections=[]),
        prefilter_result=PrefilterResult(score=0.8, passed_threshold=True),
        final_score=90.0,
        score_components=[],
        notification_tier=NotificationTier.PRIORITY,
    )
    repo.save_match_result(result)
    row = repo._conn.execute(
        "SELECT job_id, notification_tier, final_score FROM match_results WHERE job_id=?",
        (job.job_id,),
    ).fetchone()
    assert row == (job.job_id, "priority", 90.0)
