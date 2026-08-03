"""SqliteJobRepository — the only JobRepository implementation in Milestone 1
(decisions.md D-001: stdlib sqlite3, no ORM, behind the Protocol).

Schema is created with a single `CREATE TABLE IF NOT EXISTS` set run at
startup — no migration framework (architecture.md section 12, "What
Milestone 1 deliberately does not add").
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from job_scout.models import (
    ApplicationStatus,
    Job,
    JobFingerprint,
    MatchResult,
    NotificationRecord,
    SourcePerformance,
    SourceProvenance,
    SourceRun,
    UserFeedback,
    VisaAssessment,
)

_SCHEMA_VERSION = 1


class SchemaVersionError(Exception):
    """Raised when an existing database's PRAGMA user_version is newer than
    this build understands (architecture.md section 15.6; decisions.md
    D-026). Milestone 1.1 adds no migration framework by design — refusing
    to run is the safe response to a schema this build has never seen,
    rather than risking silent corruption."""


_SCHEMA = """
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

CREATE TABLE IF NOT EXISTS source_runs (
    run_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    search_profile_ref TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    jobs_fetched INTEGER NOT NULL,
    jobs_new INTEGER NOT NULL,
    jobs_duplicate INTEGER NOT NULL,
    errors TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS match_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    search_profile_ref TEXT NOT NULL,
    notification_tier TEXT NOT NULL,
    final_score REAL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_match_results_job_id ON match_results(job_id);

-- Reserved for later milestones (architecture.md section 2.14 / section 4).
-- Real tables exist now so the Protocol's write methods aren't no-ops, even
-- though nothing in M1's pipeline calls them yet.
CREATE TABLE IF NOT EXISTS visa_assessments (
    job_id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
    notification_id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_feedback (
    feedback_id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS application_status (
    job_id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    data TEXT NOT NULL
);
"""


class SqliteJobRepository:
    """Implements JobRepository (repository/base.py) over stdlib sqlite3."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._ensure_schema_version()

    def _ensure_schema_version(self) -> None:
        """PRAGMA user_version check (architecture.md section 15.6;
        decisions.md D-026). A brand-new database and a pre-Milestone-1.1
        database (schema-identical, but never stamped, so it reads 0) are
        both stamped the current version — 1.1 introduces no schema change,
        so this is purely the versioning mechanism itself, not a migration.
        A database from a newer, unsupported schema version refuses to run.
        """
        (current_version,) = self._conn.execute("PRAGMA user_version").fetchone()
        if current_version > _SCHEMA_VERSION:
            raise SchemaVersionError(
                f"Database schema version {current_version} is newer than this build of "
                f"job-scout supports (version {_SCHEMA_VERSION}). Refusing to run to avoid "
                "corrupting data — upgrade job-scout, or point --db-path at a different "
                "database."
            )
        if current_version < _SCHEMA_VERSION:
            self._conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SqliteJobRepository:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --- jobs / fingerprints / provenance -----------------------------------

    def find_by_fingerprint(self, fingerprint: JobFingerprint) -> Job | None:
        row = self._conn.execute(
            """
            SELECT jobs.data FROM job_fingerprints
            JOIN jobs ON jobs.job_id = job_fingerprints.job_id
            WHERE job_fingerprints.canonical_url = ?
              AND job_fingerprints.external_source_id = ?
            """,
            (fingerprint.canonical_url, fingerprint.external_source_id),
        ).fetchone()
        if row is None:
            return None
        return Job.model_validate(json.loads(row[0]))

    def save_job(self, job: Job) -> None:
        self._conn.execute(
            "INSERT INTO jobs (job_id, collected_at, data) VALUES (?, ?, ?)",
            (job.job_id, job.collected_at.isoformat(), job.model_dump_json()),
        )
        fp = job.fingerprint
        self._conn.execute(
            """
            INSERT INTO job_fingerprints
                (job_id, canonical_url, external_source_id, normalized_company,
                 normalized_title, normalized_location, description_fingerprint, posted_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.job_id,
                fp.canonical_url,
                fp.external_source_id,
                fp.normalized_company,
                fp.normalized_title,
                fp.normalized_location,
                fp.description_fingerprint,
                fp.posted_date.isoformat() if fp.posted_date else None,
            ),
        )
        for provenance in job.source_provenance:
            self._insert_provenance_row(job.job_id, provenance)
        self._conn.commit()

    def _insert_provenance_row(self, job_id: str, provenance: SourceProvenance) -> None:
        self._conn.execute(
            """
            INSERT INTO source_provenance
                (job_id, source_id, access_mode, fetched_at, raw_url, external_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                provenance.source_id,
                provenance.access_mode.value,
                provenance.fetched_at.isoformat(),
                provenance.raw_url,
                provenance.external_id,
            ),
        )

    def merge_provenance(self, job_id: str, provenance: SourceProvenance) -> None:
        row = self._conn.execute("SELECT data FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(
                f"No job with job_id '{job_id}' — merge_provenance requires an existing job"
            )
        job = Job.model_validate(json.loads(row[0]))
        already_present = any(
            p.source_id == provenance.source_id and p.external_id == provenance.external_id
            for p in job.source_provenance
        )
        if not already_present:
            job = job.model_copy(update={"source_provenance": [*job.source_provenance, provenance]})
            self._conn.execute(
                "UPDATE jobs SET data = ? WHERE job_id = ?", (job.model_dump_json(), job_id)
            )
        self._insert_provenance_row(job_id, provenance)
        self._conn.commit()

    def list_recent_jobs(self, since: datetime, limit: int = 200) -> list[Job]:
        rows = self._conn.execute(
            "SELECT data FROM jobs WHERE collected_at >= ? ORDER BY collected_at DESC LIMIT ?",
            (since.isoformat(), limit),
        ).fetchall()
        return [Job.model_validate(json.loads(row[0])) for row in rows]

    # --- source runs / match results ----------------------------------------

    def save_source_run(self, run: SourceRun) -> None:
        self._conn.execute(
            """
            INSERT INTO source_runs
                (run_id, source_id, search_profile_ref, started_at, completed_at,
                 status, jobs_fetched, jobs_new, jobs_duplicate, errors)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.run_id,
                run.source_id,
                run.search_profile_ref,
                run.started_at.isoformat(),
                run.completed_at.isoformat() if run.completed_at else None,
                run.status.value,
                run.jobs_fetched,
                run.jobs_new,
                run.jobs_duplicate,
                json.dumps(run.errors),
            ),
        )
        self._conn.commit()

    def save_match_result(self, result: MatchResult) -> None:
        self._conn.execute(
            """
            INSERT INTO match_results
                (job_id, search_profile_ref, notification_tier, final_score, data)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                result.job_id,
                result.search_profile_ref,
                result.notification_tier.value,
                result.final_score,
                result.model_dump_json(),
            ),
        )
        self._conn.commit()

    # --- reserved for later milestones (architecture.md section 2.14) -------

    def save_visa_assessment(self, assessment: VisaAssessment) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO visa_assessments (job_id, data) VALUES (?, ?)",
            (assessment.job_id, assessment.model_dump_json()),
        )
        self._conn.commit()

    def save_notification(self, record: NotificationRecord) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO notifications (notification_id, data) VALUES (?, ?)",
            (record.notification_id, record.model_dump_json()),
        )
        self._conn.commit()

    def save_feedback(self, feedback: UserFeedback) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO user_feedback (feedback_id, data) VALUES (?, ?)",
            (feedback.feedback_id, feedback.model_dump_json()),
        )
        self._conn.commit()

    def save_application_status(self, status: ApplicationStatus) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO application_status (job_id, data) VALUES (?, ?)",
            (status.job_id, status.model_dump_json()),
        )
        self._conn.commit()

    def save_source_performance(self, perf: SourcePerformance) -> None:
        self._conn.execute(
            "INSERT INTO source_performance (source_id, data) VALUES (?, ?)",
            (perf.source_id, perf.model_dump_json()),
        )
        self._conn.commit()
