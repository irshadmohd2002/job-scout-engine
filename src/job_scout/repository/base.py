"""JobRepository Protocol (architecture.md section 4).

M1 ships SqliteJobRepository implementing every method with real schema
(jobs, job_fingerprints, source_provenance, source_runs, match_results,
plus the reserved tables for the five later-milestone entities) — see
sqlite_repo.py.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

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


class JobRepository(Protocol):
    def find_by_fingerprint(self, fingerprint: JobFingerprint) -> Job | None: ...
    def save_job(self, job: Job) -> None: ...
    def merge_provenance(self, job_id: str, provenance: SourceProvenance) -> None: ...
    def save_source_run(self, run: SourceRun) -> None: ...
    def save_match_result(self, result: MatchResult) -> None: ...
    def list_recent_jobs(self, since: datetime, limit: int = 200) -> list[Job]: ...
    # Milestone 2 Deliverable 5 step 9 (decisions.md D-038): exposes the
    # per-job fetch-observation history source_provenance already stores
    # (Workstream F's "no new SourceObservation model" conclusion).
    def list_provenance(self, job_id: str) -> list[SourceProvenance]: ...

    # Defined for interface stability; schema reserved now so a later
    # milestone's write-path is additive, not a breaking Protocol change.
    def save_visa_assessment(self, assessment: VisaAssessment) -> None: ...
    def save_notification(self, record: NotificationRecord) -> None: ...
    def save_feedback(self, feedback: UserFeedback) -> None: ...
    def save_application_status(self, status: ApplicationStatus) -> None: ...
    def save_source_performance(self, perf: SourcePerformance) -> None: ...
