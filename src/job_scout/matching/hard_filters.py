"""Stage 1 — hard eligibility filters (architecture.md section 10).

Deterministic, profile-driven, evidence-producing. This is the only stage
that rejects a job outright (decisions.md D-005) — every failing rule
appends a RejectionReason with the matched evidence substring, and a job
failing any rule still gets persisted (notification_tier: rejected), never
silently dropped.

Experience-range parsing is best-effort free-text heuristics (risk R-3): a
job whose experience requirement can't be parsed is never rejected on that
basis — false negatives (wrongly rejecting a real match) are more costly
than false positives here.
"""

from __future__ import annotations

import re

from job_scout.models import CandidateProfile, HardFilterResult, Job, RejectionReason, SearchProfile

_RANGE_RE = re.compile(r"(\d{1,2})\s*(?:-|to|–)\s*(\d{1,2})\s*\+?\s*years?", re.IGNORECASE)
_PLUS_RE = re.compile(r"(\d{1,2})\s*\+\s*years?", re.IGNORECASE)
_MIN_RE = re.compile(r"(?:minimum(?: of)?|at least)\s*(\d{1,2})\s*years?", re.IGNORECASE)

_CITIZENSHIP_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"[a-z]+ citizens? only",
        r"must be an? [a-z]+ citizen",
        r"[a-z]+ nationals? only",
    ]
]

_CLEARANCE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"security clearance required",
        r"must (?:hold|have) (?:an? )?(?:active )?(?:sc|dv|security) clearance",
        r"active security clearance",
    ]
]

_EXISTING_AUTH_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"must (?:already )?have the right to work",
        r"existing right to work",
        r"must have current work authori[sz]ation",
    ]
]

_NO_SPONSORSHIP_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"(?:not able|unable) to (?:offer|provide) sponsorship",
        r"cannot sponsor",
        r"no (?:visa )?sponsorship (?:is )?(?:available|offered|provided)",
        r"does not offer (?:visa )?sponsorship",
    ]
]


def parse_experience_range(text: str) -> tuple[float, float] | None:
    """Best-effort numeric-range heuristic (risk R-3). Returns None — never
    raises — when nothing recognisable is found."""
    match = _RANGE_RE.search(text)
    if match:
        lo, hi = float(match.group(1)), float(match.group(2))
        return (min(lo, hi), max(lo, hi))
    match = _PLUS_RE.search(text)
    if match:
        lo = float(match.group(1))
        return (lo, float("inf"))
    match = _MIN_RE.search(text)
    if match:
        lo = float(match.group(1))
        return (lo, float("inf"))
    return None


def _first_match(patterns: list[re.Pattern[str]], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def evaluate_hard_filters(
    job: Job, candidate: CandidateProfile, search: SearchProfile
) -> HardFilterResult:
    rejections: list[RejectionReason] = []
    text = job.description_text

    country = job.location.country.upper() if job.location.country else None
    if search.included_countries and country not in {c.upper() for c in search.included_countries}:
        rejections.append(
            RejectionReason(
                rule="country_not_included",
                detail=(
                    f"Job country '{country}' is not in the search profile's included_countries."
                ),
                evidence=country,
            )
        )
    if country and country in {c.upper() for c in search.excluded_countries}:
        rejections.append(
            RejectionReason(
                rule="excluded_country",
                detail=f"Job country '{country}' is explicitly excluded.",
                evidence=country,
            )
        )

    city = job.location.city
    if city:
        if search.included_cities and city.lower() not in {
            c.lower() for c in search.included_cities
        }:
            rejections.append(
                RejectionReason(
                    rule="city_not_included",
                    detail=f"Job city '{city}' is not in included_cities.",
                    evidence=city,
                )
            )
        if city.lower() in {c.lower() for c in search.excluded_cities}:
            rejections.append(
                RejectionReason(
                    rule="excluded_city",
                    detail=f"Job city '{city}' is explicitly excluded.",
                    evidence=city,
                )
            )

    if (
        job.employment_type
        and search.employment_types
        and job.employment_type not in search.employment_types
    ):
        rejections.append(
            RejectionReason(
                rule="employment_type_mismatch",
                detail=(
                    f"Job employment_type '{job.employment_type}' not in "
                    f"{search.employment_types}."
                ),
                evidence=job.employment_type,
            )
        )

    parsed_range = parse_experience_range(text)
    if (
        parsed_range is not None
        and search.min_experience_years is not None
        and search.max_experience_years is not None
    ):
        job_min, job_max = parsed_range
        if job_min > search.max_experience_years or job_max < search.min_experience_years:
            rejections.append(
                RejectionReason(
                    rule="experience_out_of_range",
                    detail=(
                        f"Parsed job experience range {job_min:g}-{job_max:g} years does not "
                        f"overlap the search profile's target range "
                        f"{search.min_experience_years:g}-{search.max_experience_years:g} years."
                    ),
                    evidence=f"{job_min:g}-{job_max:g} years",
                )
            )

    citizenship_evidence = _first_match(_CITIZENSHIP_PATTERNS, text)
    if citizenship_evidence:
        rejections.append(
            RejectionReason(
                rule="citizenship_restriction",
                detail="Job text restricts eligibility by citizenship/nationality.",
                evidence=citizenship_evidence,
            )
        )

    if not search.security_clearance_allowed:
        clearance_evidence = _first_match(_CLEARANCE_PATTERNS, text)
        if clearance_evidence:
            rejections.append(
                RejectionReason(
                    rule="security_clearance_required",
                    detail="Job requires a security clearance the candidate is not eligible for.",
                    evidence=clearance_evidence,
                )
            )

    if candidate.requires_work_authorisation_support:
        auth_evidence = _first_match(_EXISTING_AUTH_PATTERNS, text)
        if auth_evidence:
            rejections.append(
                RejectionReason(
                    rule="existing_work_authorisation_required",
                    detail=(
                        "Job requires existing right to work; candidate needs sponsorship support."
                    ),
                    evidence=auth_evidence,
                )
            )

    if search.reject_on_explicit_no_sponsorship:
        sponsorship_evidence = _first_match(_NO_SPONSORSHIP_PATTERNS, text)
        if sponsorship_evidence:
            rejections.append(
                RejectionReason(
                    rule="explicit_no_sponsorship",
                    detail="Job text explicitly states no visa sponsorship is available.",
                    evidence=sponsorship_evidence,
                )
            )

    for qualification in search.mandatory_qualifications:
        if qualification.lower() not in text.lower():
            rejections.append(
                RejectionReason(
                    rule="missing_mandatory_qualification",
                    detail=(
                        f"Mandatory qualification '{qualification}' not found in job description."
                    ),
                    evidence=None,
                )
            )

    for language in search.required_languages:
        if language.lower() not in text.lower():
            rejections.append(
                RejectionReason(
                    rule="missing_required_language",
                    detail=f"Required language '{language}' not found in job description.",
                    evidence=None,
                )
            )

    combined_text = f"{job.title} {text}".lower()
    for industry in candidate.excluded_industries:
        if industry.lower() in combined_text:
            rejections.append(
                RejectionReason(
                    rule="excluded_industry",
                    detail=f"Job matches excluded industry '{industry}'.",
                    evidence=industry,
                )
            )
    for role_family in candidate.excluded_role_families:
        readable = role_family.replace("_", " ")
        if readable.lower() in combined_text:
            rejections.append(
                RejectionReason(
                    rule="excluded_role_family",
                    detail=f"Job matches excluded role family '{role_family}'.",
                    evidence=readable,
                )
            )

    rejections.extend(_evaluate_opt_in_hard_filters(job, search, combined_text))

    return HardFilterResult(passed=not rejections, rejections=rejections)


def _missing_items_rejections(
    *, rule: str, label: str, required: list[str], text: str
) -> list[RejectionReason]:
    return [
        RejectionReason(
            rule=rule,
            detail=f"Required {label} '{item}' not found in job description.",
            evidence=None,
        )
        for item in required
        if item.lower() not in text
    ]


def _evaluate_opt_in_hard_filters(
    job: Job, search: SearchProfile, combined_text: str
) -> list[RejectionReason]:
    """Milestone 1.1's new generic hard filters — every one of these only
    rejects a job when the matching `SearchProfile.hard_filters` toggle is
    explicitly True (decisions.md D-025). Milestone 1's original filters
    above are unaffected and keep rejecting unconditionally when non-empty."""
    toggles = search.hard_filters
    rejections: list[RejectionReason] = []

    if toggles.enforce_required_skills:
        rejections.extend(
            _missing_items_rejections(
                rule="missing_required_skill",
                label="skill",
                required=search.required_skills,
                text=combined_text,
            )
        )
    if toggles.enforce_required_qualifications:
        rejections.extend(
            _missing_items_rejections(
                rule="missing_required_qualification_search_profile",
                label="qualification",
                required=search.required_qualifications,
                text=combined_text,
            )
        )
    if toggles.enforce_required_certifications:
        rejections.extend(
            _missing_items_rejections(
                rule="missing_required_certification",
                label="certification",
                required=search.required_certifications,
                text=combined_text,
            )
        )
    if toggles.enforce_required_licences:
        rejections.extend(
            _missing_items_rejections(
                rule="missing_required_licence",
                label="licence",
                required=search.required_licences,
                text=combined_text,
            )
        )

    if toggles.enforce_included_industries and search.included_industries:
        if not any(industry.lower() in combined_text for industry in search.included_industries):
            rejections.append(
                RejectionReason(
                    rule="not_in_included_industries",
                    detail=(
                        "Job does not match any of the search profile's included_industries "
                        f"{search.included_industries}."
                    ),
                    evidence=None,
                )
            )
    if toggles.enforce_excluded_industries:
        for industry in search.excluded_industries:
            if industry.lower() in combined_text:
                rejections.append(
                    RejectionReason(
                        rule="excluded_industry_search_profile",
                        detail=f"Job matches search profile's excluded industry '{industry}'.",
                        evidence=industry,
                    )
                )

    if toggles.enforce_included_sectors and search.included_sectors:
        if not any(sector.lower() in combined_text for sector in search.included_sectors):
            rejections.append(
                RejectionReason(
                    rule="not_in_included_sectors",
                    detail=(
                        "Job does not match any of the search profile's included_sectors "
                        f"{search.included_sectors}."
                    ),
                    evidence=None,
                )
            )
    if toggles.enforce_excluded_sectors:
        for sector in search.excluded_sectors:
            if sector.lower() in combined_text:
                rejections.append(
                    RejectionReason(
                        rule="excluded_sector",
                        detail=f"Job matches search profile's excluded sector '{sector}'.",
                        evidence=sector,
                    )
                )

    if toggles.enforce_included_keywords and search.included_keywords:
        if not any(kw.lower() in combined_text for kw in search.included_keywords):
            rejections.append(
                RejectionReason(
                    rule="missing_included_keyword",
                    detail=(
                        "Job does not contain any of the search profile's included_keywords "
                        f"{search.included_keywords}."
                    ),
                    evidence=None,
                )
            )
    if toggles.enforce_excluded_keywords:
        for keyword in search.excluded_keywords:
            if keyword.lower() in combined_text:
                rejections.append(
                    RejectionReason(
                        rule="excluded_keyword",
                        detail=f"Job matches search profile's excluded keyword '{keyword}'.",
                        evidence=keyword,
                    )
                )

    if toggles.enforce_minimum_salary and search.minimum_salary is not None:
        currency_mismatch = (
            search.minimum_salary_currency is not None
            and job.salary_currency is not None
            and job.salary_currency != search.minimum_salary_currency
        )
        # Fail open (R-3 stance) when there's no comparable salary data at
        # all, or the currencies don't match and can't be compared directly.
        if job.salary_max is not None and not currency_mismatch:
            if job.salary_max < search.minimum_salary:
                rejections.append(
                    RejectionReason(
                        rule="below_minimum_salary",
                        detail=(
                            f"Job's salary_max {job.salary_max:g} is below the search "
                            f"profile's minimum_salary {search.minimum_salary:g}."
                        ),
                        evidence=f"salary_max={job.salary_max:g}",
                    )
                )

    return rejections
