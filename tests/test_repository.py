import sqlite3
from datetime import timedelta

import pytest

from job_scout.models import (
    HardFilterResult,
    MatchResult,
    NotificationTier,
    PrefilterResult,
    SourceRun,
    SourceRunStatus,
    VisaAssessment,
    VisaStatus,
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


# --- Milestone 2 Deliverable 5 step 9 (decisions.md D-038) -------------------


def test_list_provenance_returns_empty_for_unknown_job(repo: SqliteJobRepository) -> None:
    assert repo.list_provenance("does-not-exist") == []


def test_list_provenance_returns_every_observation_in_fetch_order(
    repo: SqliteJobRepository,
) -> None:
    job = make_job(source_provenance=[make_provenance(source_id="adzuna_api", external_id="1")])
    repo.save_job(job)
    repo.merge_provenance(job.job_id, make_provenance(source_id="reed_api", external_id="2"))
    # a repeat fetch of the same source/external-id pair still appends a
    # fresh observation row (Workstream F's "already an append-only log"
    # conclusion) — merge_provenance only dedupes the in-memory Job.source_provenance
    # list, never the source_provenance table itself.
    repo.merge_provenance(job.job_id, make_provenance(source_id="adzuna_api", external_id="1"))

    provenance = repo.list_provenance(job.job_id)
    assert [p.source_id for p in provenance] == ["adzuna_api", "reed_api", "adzuna_api"]
    assert [p.external_id for p in provenance] == ["1", "2", "1"]


def test_schema_creates_canonical_url_index(repo: SqliteJobRepository) -> None:
    row = repo._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        ("idx_job_fingerprints_canonical_url",),
    ).fetchone()
    assert row is not None


def test_schema_version_is_stamped_3(repo: SqliteJobRepository) -> None:
    (version,) = repo._conn.execute("PRAGMA user_version").fetchone()
    assert version == 3


def test_schema_creates_sponsor_registry_entries_table_and_index(
    repo: SqliteJobRepository,
) -> None:
    table_row = repo._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        ("sponsor_registry_entries",),
    ).fetchone()
    assert table_row is not None
    index_row = repo._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        ("idx_sponsor_registry_entries_country_normalized_name",),
    ).fetchone()
    assert index_row is not None


def test_schema_adds_visa_assessments_status_and_registry_match_columns(
    repo: SqliteJobRepository,
) -> None:
    columns = {row[1] for row in repo._conn.execute("PRAGMA table_info(visa_assessments)")}
    assert "status" in columns
    assert "employer_registry_match" in columns
    index_row = repo._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        ("idx_visa_assessments_status",),
    ).fetchone()
    assert index_row is not None


def test_save_visa_assessment_populates_status_column(repo: SqliteJobRepository) -> None:
    job = make_job()
    repo.save_job(job)
    assessment = VisaAssessment(
        job_id=job.job_id,
        status=VisaStatus.EMPLOYER_ELIGIBLE,
        confidence=0.7,
        employer_registry_match=True,
        country_work_permit_regime="uk_points_based_system",
        last_verification_date=utcnow(),
    )
    repo.save_visa_assessment(assessment)
    row = repo._conn.execute(
        "SELECT status, employer_registry_match FROM visa_assessments WHERE job_id=?",
        (job.job_id,),
    ).fetchone()
    assert row == ("employer_eligible", 1)


# The exact v1 schema this project shipped before step 9 (MILESTONE_1_1.md
# baseline) — a frozen snapshot, not imported from sqlite_repo.py, so this
# test keeps proving the *upgrade path* even if the current schema constant
# changes shape later.
_V1_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    collected_at TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_collected_at ON jobs(collected_at);

CREATE TABLE IF NOT EXISTS job_fingerprints (
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    canonical_url TEXT NOT NULL,
    external_source_id TEXT NOT NULL,
    normalized_company TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    normalized_location TEXT NOT NULL,
    description_fingerprint TEXT NOT NULL,
    posted_date TEXT,
    PRIMARY KEY (canonical_url, external_source_id)
);

CREATE TABLE IF NOT EXISTS source_provenance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    source_id TEXT NOT NULL,
    access_mode TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    raw_url TEXT NOT NULL,
    external_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_source_provenance_job_id ON source_provenance(job_id);
"""


def test_opening_a_v1_stamped_database_upgrades_to_v3_without_data_loss(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_V1_SCHEMA)
    conn.execute(
        "INSERT INTO jobs (job_id, collected_at, data) VALUES (?, ?, ?)",
        ("legacy-job", "2026-01-01T00:00:00+00:00", '{"job_id": "legacy-job"}'),
    )
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()

    with SqliteJobRepository(db_path) as repo:
        (version,) = repo._conn.execute("PRAGMA user_version").fetchone()
        assert version == 3
        index_row = repo._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
            ("idx_job_fingerprints_canonical_url",),
        ).fetchone()
        assert index_row is not None
        table_row = repo._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            ("sponsor_registry_entries",),
        ).fetchone()
        assert table_row is not None
        visa_columns = {row[1] for row in repo._conn.execute("PRAGMA table_info(visa_assessments)")}
        assert "status" in visa_columns
        assert "employer_registry_match" in visa_columns
        data_row = repo._conn.execute(
            "SELECT job_id FROM jobs WHERE job_id = ?", ("legacy-job",)
        ).fetchone()
        assert data_row == ("legacy-job",)


# The exact v2 schema this project shipped after step 9 (MILESTONE_1_1.md
# baseline + step 9's canonical_url index only) — a frozen snapshot, so this
# test keeps proving the v2->v3 upgrade path even if the current schema
# constant changes shape later. visa_assessments is still (job_id, data)
# only at this point — step 10 is what adds status/employer_registry_match.
_V2_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    collected_at TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_collected_at ON jobs(collected_at);

CREATE TABLE IF NOT EXISTS job_fingerprints (
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    canonical_url TEXT NOT NULL,
    external_source_id TEXT NOT NULL,
    normalized_company TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    normalized_location TEXT NOT NULL,
    description_fingerprint TEXT NOT NULL,
    posted_date TEXT,
    PRIMARY KEY (canonical_url, external_source_id)
);
CREATE INDEX IF NOT EXISTS idx_job_fingerprints_canonical_url
    ON job_fingerprints(canonical_url);

CREATE TABLE IF NOT EXISTS source_provenance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    source_id TEXT NOT NULL,
    access_mode TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    raw_url TEXT NOT NULL,
    external_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_source_provenance_job_id ON source_provenance(job_id);

CREATE TABLE IF NOT EXISTS visa_assessments (
    job_id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
"""


def test_opening_a_v2_stamped_database_upgrades_to_v3_without_data_loss(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    db_path = tmp_path / "v2.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_V2_SCHEMA)
    conn.execute(
        "INSERT INTO jobs (job_id, collected_at, data) VALUES (?, ?, ?)",
        ("v2-job", "2026-01-01T00:00:00+00:00", '{"job_id": "v2-job"}'),
    )
    conn.execute(
        "INSERT INTO visa_assessments (job_id, data) VALUES (?, ?)",
        ("v2-job", '{"job_id": "v2-job", "status": "unknown"}'),
    )
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()

    with SqliteJobRepository(db_path) as repo:
        (version,) = repo._conn.execute("PRAGMA user_version").fetchone()
        assert version == 3

        table_row = repo._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            ("sponsor_registry_entries",),
        ).fetchone()
        assert table_row is not None
        index_row = repo._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
            ("idx_sponsor_registry_entries_country_normalized_name",),
        ).fetchone()
        assert index_row is not None

        visa_columns = {row[1] for row in repo._conn.execute("PRAGMA table_info(visa_assessments)")}
        assert "status" in visa_columns
        assert "employer_registry_match" in visa_columns

        # pre-existing v2 data survives the ALTER TABLE ADD COLUMN untouched
        # (the new columns are simply NULL for a row written before step 10).
        preserved = repo._conn.execute(
            "SELECT job_id, status FROM visa_assessments WHERE job_id = ?", ("v2-job",)
        ).fetchone()
        assert preserved == ("v2-job", None)
        job_row = repo._conn.execute(
            "SELECT job_id FROM jobs WHERE job_id = ?", ("v2-job",)
        ).fetchone()
        assert job_row == ("v2-job",)
