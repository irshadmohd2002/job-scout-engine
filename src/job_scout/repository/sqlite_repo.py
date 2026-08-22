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
    AccessMode,
    ApplicationStatus,
    Job,
    JobFingerprint,
    MatchResult,
    NotificationRecord,
    SourcePerformance,
    SourceProvenance,
    SourceRun,
    SponsorRegistryEntry,
    UserFeedback,
    VisaAssessment,
)

_SCHEMA_VERSION = 3


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
-- Milestone 2 Deliverable 5 step 9 (decisions.md D-038; _SCHEMA_VERSION 1->2,
-- see "Persistence implications" in MILESTONE_2.md): backs the new
-- cross-source exact-canonical-URL dedup tier's lookup. Non-unique — the
-- existing PRIMARY KEY above already covers Tier 1's own exact lookup.
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

-- Milestone 2 Deliverable 5 step 10 (_SCHEMA_VERSION 2->3): one row per
-- imported sponsor-register entry (source_intelligence/sponsor_registry.py).
-- `job-scout sponsors import` replaces (DELETE + INSERT), never appends to,
-- the rows for a given (country, register_name) pair on each import.
CREATE TABLE IF NOT EXISTS sponsor_registry_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    country TEXT NOT NULL,
    registered_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    register_name TEXT NOT NULL,
    license_status TEXT,
    imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sponsor_registry_entries_country_normalized_name
    ON sponsor_registry_entries(country, normalized_name);
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
        both stamped the current version. Milestone 2 Deliverable 5 step 9
        bumped this 1->2 (purely additive: the new canonical_url index
        only); step 10 bumps it again, 2->3 (decisions.md D-050) — the new
        sponsor_registry_entries table plus two new visa_assessments
        columns is a materially different schema shape from step 9's, so it
        gets its own version number rather than reusing 2 (MILESTONE_2.md
        "Persistence implications": never two shapes sharing one
        identifier). A database stamped 1 or 2 opens cleanly and is
        upgraded to 3 the same no-op, additive way. A database from a
        newer, unsupported schema version refuses to run.
        """
        (current_version,) = self._conn.execute("PRAGMA user_version").fetchone()
        if current_version > _SCHEMA_VERSION:
            raise SchemaVersionError(
                f"Database schema version {current_version} is newer than this build of "
                f"job-scout supports (version {_SCHEMA_VERSION}). Refusing to run to avoid "
                "corrupting data — upgrade job-scout, or point --db-path at a different "
                "database."
            )
        self._migrate_visa_assessments_columns()
        if current_version < _SCHEMA_VERSION:
            self._conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            self._conn.commit()

    def _migrate_visa_assessments_columns(self) -> None:
        """Milestone 2 Deliverable 5 step 10 (_SCHEMA_VERSION 2->3):
        `visa_assessments` predates this step as `(job_id, data)` only.
        `CREATE TABLE IF NOT EXISTS` is a no-op once the table already
        exists, so the two new indexed columns (mirroring how match_results
        already duplicates notification_tier/final_score alongside its JSON
        blob — decisions.md D-050) need an explicit, idempotent
        `ALTER TABLE ... ADD COLUMN`, checked against PRAGMA table_info so
        this is safe to call unconditionally on every open, for both a
        pre-step-10 database and a database that already has them."""
        existing_columns = {
            row[1] for row in self._conn.execute("PRAGMA table_info(visa_assessments)")
        }
        if "status" not in existing_columns:
            self._conn.execute("ALTER TABLE visa_assessments ADD COLUMN status TEXT")
        if "employer_registry_match" not in existing_columns:
            self._conn.execute(
                "ALTER TABLE visa_assessments ADD COLUMN employer_registry_match INTEGER"
            )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_visa_assessments_status ON visa_assessments(status)"
        )
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

    def list_provenance(self, job_id: str) -> list[SourceProvenance]:
        """Milestone 2 Deliverable 5 step 9 (decisions.md D-038, Workstream
        F): source_provenance is already an append-only fetch-observation
        log (merge_provenance inserts a fresh row on every call, including
        repeat fetches) — this is the missing read method, not a new model.
        """
        rows = self._conn.execute(
            """
            SELECT source_id, access_mode, fetched_at, raw_url, external_id
            FROM source_provenance
            WHERE job_id = ?
            ORDER BY id ASC
            """,
            (job_id,),
        ).fetchall()
        return [
            SourceProvenance(
                source_id=row[0],
                access_mode=AccessMode(row[1]),
                fetched_at=datetime.fromisoformat(row[2]),
                raw_url=row[3],
                external_id=row[4],
            )
            for row in rows
        ]

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
            """
            INSERT OR REPLACE INTO visa_assessments
                (job_id, status, employer_registry_match, data)
            VALUES (?, ?, ?, ?)
            """,
            (
                assessment.job_id,
                assessment.status.value,
                (
                    None
                    if assessment.employer_registry_match is None
                    else int(assessment.employer_registry_match)
                ),
                assessment.model_dump_json(),
            ),
        )
        self._conn.commit()

    # --- sponsor registry (Milestone 2 Deliverable 5 step 10) --------------

    def replace_sponsor_registry_entries(
        self, country: str, register_name: str, entries: list[SponsorRegistryEntry]
    ) -> None:
        """Replaces (never appends to) the rows for this (country,
        register_name) pair — `job-scout sponsors import` is the documented
        way to refresh a snapshot (MILESTONE_2.md "Persistence
        implications")."""
        self._conn.execute(
            "DELETE FROM sponsor_registry_entries WHERE country = ? AND register_name = ?",
            (country, register_name),
        )
        self._conn.executemany(
            """
            INSERT INTO sponsor_registry_entries
                (country, registered_name, normalized_name, register_name,
                 license_status, imported_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    entry.country,
                    entry.registered_name,
                    entry.normalized_name,
                    entry.register_name,
                    entry.license_status,
                    entry.imported_at.isoformat(),
                )
                for entry in entries
            ],
        )
        self._conn.commit()

    def find_sponsor_registry_entry(
        self, normalized_name: str, country: str
    ) -> SponsorRegistryEntry | None:
        row = self._conn.execute(
            """
            SELECT country, registered_name, normalized_name, register_name,
                   license_status, imported_at
            FROM sponsor_registry_entries
            WHERE normalized_name = ? AND country = ?
            LIMIT 1
            """,
            (normalized_name, country),
        ).fetchone()
        if row is None:
            return None
        return SponsorRegistryEntry(
            country=row[0],
            registered_name=row[1],
            normalized_name=row[2],
            register_name=row[3],
            license_status=row[4],
            imported_at=datetime.fromisoformat(row[5]),
        )

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
