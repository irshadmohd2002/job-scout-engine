"""Shared test-fixture builders — not personal data, pure test scaffolding."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from job_scout.models import (
    AccessMode,
    ApprovalStatus,
    CandidateProfile,
    ConfigStatus,
    ExpectedValue,
    Job,
    JobFingerprint,
    Location,
    NotificationThresholds,
    RemoteType,
    SearchProfile,
    SeniorityLevel,
    SourceProvenance,
    SourceRegistryEntry,
    SourceType,
    TechnicalFeasibility,
    TermsComplianceStatus,
)


def make_source_entry(
    *,
    source_id: str = "test_source",
    access_mode: AccessMode = AccessMode.PUBLIC_API,
    approval_status: ApprovalStatus = ApprovalStatus.APPROVED,
    geographic_coverage: list[str] | None = None,
    role_coverage: list[str] | None = None,
    config_status: ConfigStatus = ConfigStatus.CONFIGURED,
    priority: int = 50,
    **overrides: Any,
) -> SourceRegistryEntry:
    data: dict[str, Any] = {
        "source_id": source_id,
        "name": source_id,
        "source_type": SourceType.AGGREGATOR_API,
        "geographic_coverage": geographic_coverage if geographic_coverage is not None else ["GB"],
        "role_coverage": role_coverage if role_coverage is not None else ["general"],
        "access_mode": access_mode,
        "approval_status": approval_status,
        "terms_compliance_status": TermsComplianceStatus.REVIEWED_OK,
        "auth_required": False,
        "technical_feasibility": TechnicalFeasibility.HIGH,
        "expected_value": ExpectedValue.MEDIUM,
        "priority": priority,
        "polling_frequency_minutes": 720,
        "config_status": config_status,
        "required_setup_actions": [],
        "adapter_ref": None,
        "reliability_score": 0.5,
        "historical_match_count": None,
        "duplicate_rate": None,
    }
    data.update(overrides)
    return SourceRegistryEntry.model_validate(data)


def make_candidate_profile(**overrides: Any) -> CandidateProfile:
    data: dict[str, Any] = {
        "candidate_id": "default-candidate",
        "years_experience": 7.0,
        "current_location": Location(country="IN"),
        "nationality": "IN",
        "seniority_level": SeniorityLevel.MANAGER,
        "education": [],
        "employment_history_summary": [],
        "role_families": ["strategy_and_planning", "transformation"],
        "title_aliases": ["Strategy Manager", "Transformation Manager"],
        "primary_skills": ["strategy_development", "transformation"],
        "secondary_skills": ["financial_modelling"],
        "target_countries": [],
        "target_regions": [],
        "requires_work_authorisation_support": True,
        "open_to_relocation": True,
        "excluded_industries": [],
        "excluded_role_families": [],
        "notification_thresholds": NotificationThresholds(priority_score=85, digest_score=70),
    }
    data.update(overrides)
    return CandidateProfile.model_validate(data)


def make_search_profile(**overrides: Any) -> SearchProfile:
    data: dict[str, Any] = {
        "profile_id": "strategy-global",
        "candidate_profile_ref": "default-candidate",
        "description": "test profile",
        "included_countries": ["GB", "DE"],
        "excluded_countries": [],
        "included_cities": [],
        "excluded_cities": [],
        "role_families": [],
        "employment_types": ["full_time"],
        "min_experience_years": 4,
        "max_experience_years": 12,
        "required_languages": [],
        "mandatory_qualifications": [],
        "security_clearance_allowed": False,
        "reject_on_explicit_no_sponsorship": True,
        "notification_thresholds": NotificationThresholds(priority_score=85, digest_score=70),
        "polling_frequency_minutes": 720,
    }
    data.update(overrides)
    return SearchProfile.model_validate(data)


def utcnow() -> datetime:
    return datetime.now(UTC)


def make_fingerprint(**overrides: Any) -> JobFingerprint:
    data: dict[str, Any] = {
        "canonical_url": "https://example.com/jobs/1234",
        "external_source_id": "adzuna_api:1234",
        "normalized_company": "example corp",
        "normalized_title": "strategy manager",
        "normalized_location": "london gb",
        "description_fingerprint": "a" * 64,
        "posted_date": None,
    }
    data.update(overrides)
    return JobFingerprint.model_validate(data)


def make_provenance(**overrides: Any) -> SourceProvenance:
    data: dict[str, Any] = {
        "source_id": "adzuna_api",
        "access_mode": AccessMode.PUBLIC_API,
        "fetched_at": utcnow(),
        "raw_url": "https://example.com/jobs/1234",
        "external_id": "1234",
    }
    data.update(overrides)
    return SourceProvenance.model_validate(data)


def make_job(**overrides: Any) -> Job:
    fingerprint = overrides.pop("fingerprint", make_fingerprint())
    provenance = overrides.pop("source_provenance", [make_provenance()])
    data: dict[str, Any] = {
        "job_id": "11111111-1111-1111-1111-111111111111",
        "external_ids": [],
        "title": "Strategy Manager",
        "normalized_title": "strategy manager",
        "company": "Example Corp",
        "normalized_company": "example corp",
        "location": Location(country="GB", city="London"),
        "remote_type": RemoteType.HYBRID,
        "employment_type": "full_time",
        "description_raw": "<p>Great strategy role.</p>",
        "description_text": "Great strategy role.",
        "posted_at": utcnow(),
        "collected_at": utcnow(),
        "salary_min": None,
        "salary_max": None,
        "salary_currency": None,
        "source_provenance": provenance,
        "fingerprint": fingerprint,
        "role_family_hints": ["strategy_and_planning"],
    }
    data.update(overrides)
    return Job.model_validate(data)
