"""Core domain models for Job Scout Engine.

One flat, typed module for every Pydantic model / StrEnum in the system —
see architecture.md section 2 for the field-by-field contract and section 12
("Why models.py is one file, not a package") for why this isn't split up.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

# --- 2.9 / 2.8 / registry enums -------------------------------------------------


class AccessMode(StrEnum):
    PUBLIC_API = "public_api"
    PUBLIC_ATS_FEED = "public_ats_feed"
    RSS_OR_SITEMAP = "rss_or_sitemap"
    EMAIL_ALERT = "email_alert"
    PERMITTED_HTML = "permitted_html"
    SEARCH_DISCOVERY = "search_discovery"
    MANUAL_ONLY = "manual_only"
    DISABLED = "disabled"


class ApprovalStatus(StrEnum):
    APPROVED = "approved"
    ALERT_ONLY = "alert_only"
    REQUIRES_AUTHORISATION = "requires_authorisation"
    MANUAL_REVIEW = "manual_review"
    BLOCKED = "blocked"
    DEPRECATED = "deprecated"


class SourceType(StrEnum):
    AGGREGATOR_API = "aggregator_api"
    ATS_FEED = "ats_feed"
    GOVERNMENT = "government"
    COMPANY_CAREER_PAGE = "company_career_page"
    JOB_PORTAL = "job_portal"
    RECRUITMENT_AGENCY = "recruitment_agency"


class TermsComplianceStatus(StrEnum):
    REVIEWED_OK = "reviewed_ok"
    UNCLEAR = "unclear"
    PROHIBITED = "prohibited"


class TechnicalFeasibility(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class ExpectedValue(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ConfigStatus(StrEnum):
    CONFIGURED = "configured"
    NEEDS_CREDENTIALS = "needs_credentials"
    NEEDS_SETUP = "needs_setup"
    NOT_CONFIGURED = "not_configured"


class SeniorityLevel(StrEnum):
    ASSOCIATE = "associate"
    MANAGER = "manager"
    SENIOR_MANAGER = "senior_manager"
    ASSOCIATE_DIRECTOR = "associate_director"
    DIRECTOR = "director"


class RemoteType(StrEnum):
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class NotificationTier(StrEnum):
    PRIORITY = "priority"
    DIGEST = "digest"
    STORE_ONLY = "store_only"
    REJECTED = "rejected"


class VisaStatus(StrEnum):
    CONFIRMED_YES = "confirmed_yes"
    LIKELY = "likely"
    EMPLOYER_ELIGIBLE = "employer_eligible"
    UNKNOWN = "unknown"
    CONFIRMED_NO = "confirmed_no"
    NOT_REQUIRED = "not_required"


class SourceRunStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


# --- 2.1 Location ----------------------------------------------------------


class Location(BaseModel):
    model_config = ConfigDict(frozen=True)

    country: str
    region: str | None = None
    city: str | None = None


# --- 2.2 CandidateProfile ---------------------------------------------------


class Education(BaseModel):
    level: str
    degree: str
    field: str
    institution: str | None = None


class NotificationThresholds(BaseModel):
    priority_score: float
    digest_score: float


class CandidateProfile(BaseModel):
    candidate_id: str
    years_experience: float
    current_location: Location
    nationality: str
    # Milestone 1's fixed consulting-ladder enum, relaxed to optional in
    # Milestone 1.1 (decisions.md D-022) — a profession-agnostic profile uses
    # the free-text `seniority` field below instead. Kept for backward
    # compatibility: every valid Milestone 1 config still validates.
    seniority_level: SeniorityLevel | None = None
    education: list[Education] = []
    employment_history_summary: list[str] = []
    role_families: list[str] = []
    title_aliases: list[str] = []
    primary_skills: list[str] = []
    secondary_skills: list[str] = []
    target_countries: list[str] = []
    target_regions: list[str] = []
    requires_work_authorisation_support: bool
    open_to_relocation: bool
    excluded_industries: list[str] = []
    excluded_role_families: list[str] = []
    notification_thresholds: NotificationThresholds

    def effective_seniority_text(self) -> str | None:
        """The candidate's seniority as free text for keyword-overlap use —
        prefers the generic `seniority` field, falls back to the legacy
        `seniority_level` enum's value (decisions.md D-022), else None."""
        if self.seniority:
            return self.seniority
        if self.seniority_level:
            return self.seniority_level.value.replace("_", " ")
        return None

    # --- Milestone 1.1: generic, profession-agnostic fields -----------------
    # All optional/additive (decisions.md D-017) — no profession-specific
    # subclass or hard-coded vocabulary; every value here comes from the
    # candidate's own configuration.
    previous_titles: list[str] = []
    transferable_skills: list[str] = []
    tools_and_technologies: list[str] = []
    domain_knowledge: list[str] = []
    industries: list[str] = []
    sectors: list[str] = []
    qualifications: list[str] = []
    certifications: list[str] = []
    licences: list[str] = []
    languages: list[str] = []
    # Free-text seniority (e.g. "Senior Staff Nurse", "Senior Software
    # Engineer") — see decisions.md D-022. Scoring/hard-filter code prefers
    # this over `seniority_level` when both are set.
    seniority: str | None = None
    work_authorisation: str | None = None
    visa_requirements: list[str] = []
    relocation_preferences: list[str] = []
    employment_preferences: list[str] = []


# --- 2.3 SearchProfile -------------------------------------------------------


class HardFilterToggles(BaseModel):
    """Opt-in gates for Milestone 1.1's new generic hard filters
    (architecture.md section 15.5; decisions.md D-025). Milestone 1's
    pre-existing filters (mandatory_qualifications, required_languages,
    excluded_industries, excluded_role_families, explicit-no-sponsorship)
    are unaffected — they keep rejecting unconditionally whenever their list
    is non-empty, exactly as in Milestone 1. Every field here defaults to
    `False`: a new hard filter never rejects a job unless the search profile
    explicitly turns it on."""

    enforce_required_skills: bool = False
    enforce_required_qualifications: bool = False
    enforce_required_certifications: bool = False
    enforce_required_licences: bool = False
    enforce_included_industries: bool = False
    enforce_excluded_industries: bool = False
    enforce_included_sectors: bool = False
    enforce_excluded_sectors: bool = False
    enforce_included_keywords: bool = False
    enforce_excluded_keywords: bool = False
    enforce_minimum_salary: bool = False


class SearchProfile(BaseModel):
    profile_id: str
    candidate_profile_ref: str
    description: str = ""
    included_countries: list[str] = []
    excluded_countries: list[str] = []
    included_cities: list[str] = []
    excluded_cities: list[str] = []
    role_families: list[str] = []
    employment_types: list[str] = []
    min_experience_years: float | None = None
    max_experience_years: float | None = None
    required_languages: list[str] = []
    mandatory_qualifications: list[str] = []
    security_clearance_allowed: bool
    reject_on_explicit_no_sponsorship: bool
    notification_thresholds: NotificationThresholds
    polling_frequency_minutes: int

    # --- Milestone 1.1: generic, profession-agnostic fields -----------------
    # All optional/additive. The new *_skills/*_qualifications/etc. filters
    # only reject a job when the matching HardFilterToggles flag is True
    # (decisions.md D-025) — unlike the legacy fields above, which are
    # unconditional whenever non-empty.
    target_titles: list[str] = []
    title_aliases: list[str] = []
    required_skills: list[str] = []
    preferred_skills: list[str] = []
    transferable_skills: list[str] = []
    included_industries: list[str] = []
    excluded_industries: list[str] = []
    included_sectors: list[str] = []
    excluded_sectors: list[str] = []
    required_qualifications: list[str] = []
    preferred_qualifications: list[str] = []
    required_certifications: list[str] = []
    preferred_certifications: list[str] = []
    required_licences: list[str] = []
    preferred_licences: list[str] = []
    preferred_languages: list[str] = []
    seniority_levels: list[str] = []
    work_arrangements: list[str] = []
    minimum_salary: float | None = None
    minimum_salary_currency: str | None = None
    visa_relocation_requirements: list[str] = []
    included_keywords: list[str] = []
    excluded_keywords: list[str] = []
    hard_filters: HardFilterToggles = HardFilterToggles()


# --- 2.5 / 2.6 / 2.4 Job and supporting models ------------------------------


class SourceExternalId(BaseModel):
    source_id: str
    external_id: str


class SourceProvenance(BaseModel):
    source_id: str
    access_mode: AccessMode
    fetched_at: datetime
    raw_url: str
    external_id: str


class JobFingerprint(BaseModel):
    model_config = ConfigDict(frozen=True)

    canonical_url: str
    external_source_id: str
    normalized_company: str
    normalized_title: str
    normalized_location: str
    description_fingerprint: str
    posted_date: date | None = None


class Job(BaseModel):
    job_id: str
    external_ids: list[SourceExternalId] = []
    title: str
    normalized_title: str
    company: str
    normalized_company: str
    location: Location
    remote_type: RemoteType = RemoteType.UNKNOWN
    employment_type: str | None = None
    description_raw: str
    description_text: str
    posted_at: datetime | None = None
    collected_at: datetime
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    source_provenance: list[SourceProvenance] = []
    fingerprint: JobFingerprint
    role_family_hints: list[str] = []
    previous_job_id: str | None = None


# --- 2.7 SourceRegistryEntry -------------------------------------------------


class SourceRegistryEntry(BaseModel):
    source_id: str
    name: str
    source_type: SourceType
    geographic_coverage: list[str]
    role_coverage: list[str]
    # Milestone 1.1 (architecture.md section 15.7; decisions.md D-024):
    # empty list = unrestricted (matches every entry), same convention as
    # `role_coverage`'s "general" sentinel but simpler since these three are
    # new fields with no existing registry data to stay compatible with.
    industry_coverage: list[str] = []
    sector_coverage: list[str] = []
    seniority_coverage: list[str] = []
    access_mode: AccessMode
    approval_status: ApprovalStatus
    terms_compliance_status: TermsComplianceStatus
    auth_required: bool
    technical_feasibility: TechnicalFeasibility
    expected_value: ExpectedValue
    priority: int
    polling_frequency_minutes: int | None = None
    config_status: ConfigStatus
    required_setup_actions: list[str] = []
    adapter_ref: str | None = None
    reliability_score: float | None = None
    historical_match_count: int | None = None
    duplicate_rate: float | None = None
    last_successful_run: datetime | None = None


# --- 3. Source-adapter contract models --------------------------------------


class SourceSearchParams(BaseModel):
    countries: list[str]
    keywords: list[str]
    role_family_hints: list[str]
    employment_types: list[str]
    min_experience_years: float | None
    max_experience_years: float | None
    page_size: int
    max_pages: int


class RawJobRecord(BaseModel):
    """Source-native shape, pre-normalisation. Adapters return this, never Job."""

    source_id: str
    external_id: str
    raw_url: str
    raw_payload: dict[str, Any]
    fetched_at: datetime


# --- 2.10 SearchExecutionPlan -----------------------------------------------


class CountryExclusion(BaseModel):
    country: str
    reason: str


class SelectedSource(BaseModel):
    source_id: str
    score: float
    score_breakdown: dict[str, float]
    reasons_selected: list[str]
    access_mode: AccessMode
    approval_status: ApprovalStatus
    search_params: SourceSearchParams | None
    search_queries: list[str]
    polling_frequency_minutes: int | None
    config_status: ConfigStatus  # declared: static registry metadata, user-maintained
    effective_config_status: ConfigStatus  # runtime: derived from live credential checks
    required_setup_actions: list[str]
    region_country_coverage: list[str]
    priority: int
    executable: bool
    supported_countries: list[str]
    unsupported_countries: list[CountryExclusion]


class ExcludedSource(BaseModel):
    source_id: str
    reasons_excluded: list[str]


class SearchExecutionPlan(BaseModel):
    plan_id: str
    search_profile_ref: str
    generated_at: datetime
    selected_sources: list[SelectedSource]
    excluded_sources: list[ExcludedSource]
    region_country_coverage: dict[str, list[str]]
    diversity_notes: list[str] = []


# --- 2.11 MatchResult and Stage results --------------------------------------


class RejectionReason(BaseModel):
    rule: str
    detail: str
    evidence: str | None = None


class HardFilterResult(BaseModel):
    passed: bool
    rejections: list[RejectionReason] = []


class PrefilterResult(BaseModel):
    score: float
    passed_threshold: bool
    role_family_hints: list[str] = []
    evidence: list[str] = []


class ScoreComponent(BaseModel):
    name: str
    weight: float
    raw_value: float
    weighted_value: float
    evidence: list[str] = []


class SemanticResult(BaseModel):
    """Reserved for Milestone 3 (Stage 3, embedding-based similarity). Not
    populated in M1."""

    similarity_score: float | None = None
    evidence: list[str] = []


class LlmExtraction(BaseModel):
    """Reserved for Milestone 4 (Stage 4, optional Anthropic extraction). Not
    populated in M1 — never sets the final score even when populated."""

    required_skills: list[str] = []
    preferred_skills: list[str] = []
    responsibilities: list[str] = []
    seniority: str | None = None
    experience_range_years: str | None = None
    qualifications: list[str] = []
    visa_relocation_evidence: list[str] = []
    citizenship_restrictions: list[str] = []
    match_reasons: list[str] = []
    critical_gaps: list[str] = []
    ambiguities: list[str] = []


class MatchResult(BaseModel):
    job_id: str
    search_profile_ref: str
    hard_filter_result: HardFilterResult
    prefilter_result: PrefilterResult
    semantic_result: SemanticResult | None = None
    llm_extraction: LlmExtraction | None = None
    final_score: float | None = None
    score_components: list[ScoreComponent] = []
    notification_tier: NotificationTier


# --- 2.12 VisaAssessment -----------------------------------------------------


class VisaAssessment(BaseModel):
    job_id: str
    status: VisaStatus
    confidence: float
    job_text_evidence: list[str] = []
    negative_evidence: list[str] = []
    employer_registry_match: bool | None = None
    employer_registry_match_confidence: float | None = None
    registry_source: str | None = None
    country_work_permit_regime: str
    existing_work_authorisation_required: bool | None = None
    citizenship_restrictions: list[str] = []
    security_clearance_restrictions: list[str] = []
    relocation_support_evidence: list[str] = []
    international_candidate_evidence: list[str] = []
    last_verification_date: datetime


# --- 2.13 SourceRun -----------------------------------------------------------


class SourceRun(BaseModel):
    run_id: str
    source_id: str
    search_profile_ref: str
    started_at: datetime
    completed_at: datetime | None = None
    status: SourceRunStatus
    jobs_fetched: int = 0
    jobs_new: int = 0
    jobs_duplicate: int = 0
    errors: list[str] = []


# --- 2.14 Reserved for later milestones --------------------------------------
# Schema defined now so JobRepository (architecture.md section 4) does not need
# a breaking change later; none of these are written to in Milestone 1.


class NotificationRecord(BaseModel):
    notification_id: str
    job_id: str
    tier: NotificationTier
    channel: str
    sent_at: datetime


class UserFeedback(BaseModel):
    feedback_id: str
    job_id: str
    rating: str
    notes: str | None = None
    created_at: datetime


class ApplicationStatus(BaseModel):
    job_id: str
    status: str
    updated_at: datetime


class SourcePerformance(BaseModel):
    source_id: str
    period_start: datetime
    period_end: datetime
    jobs_fetched: int
    jobs_matched: int


class CompanyWatchlistEntry(BaseModel):
    company_name: str
    priority: int
    notes: str | None = None
