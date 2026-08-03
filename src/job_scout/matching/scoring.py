"""Stage 5 — transparent final scoring (architecture.md section 10).

Only Stage 1 rejects (decisions.md D-005); this stage can score a job
arbitrarily low but never sets notification_tier: rejected. A job below the
Stage 2 pre-filter threshold is persisted with store_only treatment and
never reaches Stage 5 scoring at all — see build_match_result.

Deterministic keyword/phrase-overlap proxies stand in for the Stage 3
(semantic) / Stage 4 (LLM) components this table will eventually use
(decisions.md D-007); the component *slots* and weights already match the
final shape so a later milestone upgrades a computation, not the schema.
"""

from __future__ import annotations

import re

from job_scout.config import ScoringWeights
from job_scout.matching.hard_filters import parse_experience_range
from job_scout.matching.prefilter import keyword_overlap
from job_scout.models import (
    CandidateProfile,
    HardFilterResult,
    Job,
    MatchResult,
    NotificationThresholds,
    NotificationTier,
    PrefilterResult,
    ScoreComponent,
    SearchProfile,
)

_EDUCATION_KEYWORDS = ["mba", "master", "bachelor", "postgraduate", "degree", "phd", "doctorate"]

_VISA_POSITIVE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"visa sponsorship (?:is )?(?:available|provided|offered)",
        r"relocation (?:assistance|support|package)",
        r"we (?:will |can )?sponsor",
        r"work permit sponsorship",
    ]
]

_VISA_NEGATIVE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"(?:not able|unable) to (?:offer|provide) sponsorship",
        r"cannot sponsor",
        r"no (?:visa )?sponsorship (?:is )?(?:available|offered|provided)",
        r"does not offer (?:visa )?sponsorship",
    ]
]


def _first_match(patterns: list[re.Pattern[str]], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def _title_role_family_component(
    job: Job, candidate: CandidateProfile, weight: float
) -> ScoreComponent:
    combined_text = f"{job.title} {job.description_text}"
    title_matches = keyword_overlap(candidate.title_aliases, combined_text)
    role_matches = keyword_overlap(candidate.role_families, combined_text)
    title_ratio = (
        len(title_matches) / len(candidate.title_aliases) if candidate.title_aliases else 0.0
    )
    role_ratio = (
        len(role_matches) / len(candidate.role_families) if candidate.role_families else 0.0
    )
    raw = (title_ratio + role_ratio) / 2
    evidence = [f"title_alias:{m}" for m in title_matches] + [
        f"role_family:{m}" for m in role_matches
    ]
    return ScoreComponent(
        name="title_role_family",
        weight=weight,
        raw_value=raw,
        weighted_value=weight * raw,
        evidence=evidence,
    )


def _responsibilities_component(
    job: Job, candidate: CandidateProfile, weight: float
) -> ScoreComponent:
    # Proxy corpus: role_families + primary_skills overlap against the
    # description body only (not the title) — a deliberately different
    # signal from the title/role component above (architecture.md section
    # 10, Stage 5, "Responsibilities match ... proxy until Stage 3/4 exist").
    vocabulary = [*candidate.role_families, *candidate.primary_skills]
    matches = keyword_overlap(vocabulary, job.description_text)
    raw = len(matches) / len(vocabulary) if vocabulary else 0.0
    return ScoreComponent(
        name="responsibilities",
        weight=weight,
        raw_value=raw,
        weighted_value=weight * raw,
        evidence=[f"responsibility_term:{m}" for m in matches],
    )


def _required_skills_component(
    job: Job, candidate: CandidateProfile, weight: float
) -> ScoreComponent:
    combined_text = f"{job.title} {job.description_text}"
    matches = keyword_overlap(candidate.primary_skills, combined_text)
    raw = len(matches) / len(candidate.primary_skills) if candidate.primary_skills else 0.0
    return ScoreComponent(
        name="required_skills",
        weight=weight,
        raw_value=raw,
        weighted_value=weight * raw,
        evidence=[f"primary_skill:{m}" for m in matches],
    )


def _transferable_skills_component(
    job: Job, candidate: CandidateProfile, weight: float
) -> ScoreComponent:
    combined_text = f"{job.title} {job.description_text}"
    matches = keyword_overlap(candidate.secondary_skills, combined_text)
    raw = len(matches) / len(candidate.secondary_skills) if candidate.secondary_skills else 0.0
    # Soft overlap only — per D-005/architecture.md, a missing secondary
    # skill can never by itself drop a job below a rejection line; this
    # component only ever adds to the score, it is not a gate.
    return ScoreComponent(
        name="transferable_skills",
        weight=weight,
        raw_value=raw,
        weighted_value=weight * raw,
        evidence=[f"secondary_skill:{m}" for m in matches],
    )


def _seniority_experience_component(
    job: Job, candidate: CandidateProfile, search: SearchProfile, weight: float
) -> ScoreComponent:
    seniority_term = candidate.seniority_level.value.replace("_", " ")
    seniority_matched = seniority_term in job.description_text.lower()
    seniority_raw = 1.0 if seniority_matched else 0.5

    parsed_range = parse_experience_range(job.description_text)
    evidence = [f"seniority:{seniority_term}"] if seniority_matched else []
    if (
        parsed_range is not None
        and search.min_experience_years is not None
        and search.max_experience_years is not None
    ):
        job_min, job_max = parsed_range
        overlap = job_min <= search.max_experience_years and job_max >= search.min_experience_years
        experience_raw = 1.0 if overlap else 0.0
        evidence.append(f"experience_range:{job_min:g}-{job_max:g} years")
    else:
        experience_raw = 0.5  # unparseable -> neutral, consistent with R-3's fail-open stance

    raw = (seniority_raw + experience_raw) / 2
    return ScoreComponent(
        name="seniority_experience",
        weight=weight,
        raw_value=raw,
        weighted_value=weight * raw,
        evidence=evidence,
    )


def _sector_relevance_component(weight: float) -> ScoreComponent:
    # Always neutral in M1: no sector field exists on CandidateProfile or
    # SearchProfile yet (architecture.md section 6 cold-start default).
    return ScoreComponent(
        name="sector_relevance",
        weight=weight,
        raw_value=0.5,
        weighted_value=weight * 0.5,
        evidence=[],
    )


def _education_component(job: Job, weight: float) -> ScoreComponent:
    text = job.description_text.lower()
    matched = [kw for kw in _EDUCATION_KEYWORDS if kw in text]
    raw = 1.0 if matched else 0.5  # "no education requirement stated" -> neutral
    evidence = [f"education_keyword:{m}" for m in matched]
    return ScoreComponent(
        name="education",
        weight=weight,
        raw_value=raw,
        weighted_value=weight * raw,
        evidence=evidence,
    )


def _visa_relocation_component(job: Job, weight: float) -> ScoreComponent:
    text = job.description_text
    positive = _first_match(_VISA_POSITIVE_PATTERNS, text)
    if positive:
        return ScoreComponent(
            name="visa_relocation",
            weight=weight,
            raw_value=1.0,
            weighted_value=weight * 1.0,
            evidence=[positive],
        )
    negative = _first_match(_VISA_NEGATIVE_PATTERNS, text)
    if negative:
        return ScoreComponent(
            name="visa_relocation",
            weight=weight,
            raw_value=0.0,
            weighted_value=0.0,
            evidence=[negative],
        )
    # No evidence either way -> unknown (risk R-4: expect a lot of "unknown"
    # without sponsor-registry enrichment, which is out of scope for M1).
    return ScoreComponent(
        name="visa_relocation",
        weight=weight,
        raw_value=0.5,
        weighted_value=weight * 0.5,
        evidence=[],
    )


def compute_score_components(
    job: Job, candidate: CandidateProfile, search: SearchProfile, weights: ScoringWeights
) -> list[ScoreComponent]:
    w = weights
    return [
        _title_role_family_component(job, candidate, w.title_role_family),
        _responsibilities_component(job, candidate, w.responsibilities),
        _required_skills_component(job, candidate, w.required_skills),
        _transferable_skills_component(job, candidate, w.transferable_skills),
        _seniority_experience_component(job, candidate, search, w.seniority_experience),
        _sector_relevance_component(w.sector_relevance),
        _education_component(job, w.education),
        _visa_relocation_component(job, w.visa_relocation),
    ]


def determine_notification_tier(
    final_score: float, thresholds: NotificationThresholds
) -> NotificationTier:
    if final_score >= thresholds.priority_score:
        return NotificationTier.PRIORITY
    if final_score >= thresholds.digest_score:
        return NotificationTier.DIGEST
    return NotificationTier.STORE_ONLY


def build_match_result(
    job: Job,
    candidate: CandidateProfile,
    search: SearchProfile,
    hard_filter_result: HardFilterResult,
    prefilter_result: PrefilterResult,
    weights: ScoringWeights,
) -> MatchResult:
    if not hard_filter_result.passed:
        return MatchResult(
            job_id=job.job_id,
            search_profile_ref=search.profile_id,
            hard_filter_result=hard_filter_result,
            prefilter_result=prefilter_result,
            final_score=None,
            score_components=[],
            notification_tier=NotificationTier.REJECTED,
        )

    if not prefilter_result.passed_threshold:
        return MatchResult(
            job_id=job.job_id,
            search_profile_ref=search.profile_id,
            hard_filter_result=hard_filter_result,
            prefilter_result=prefilter_result,
            final_score=None,
            score_components=[],
            notification_tier=NotificationTier.STORE_ONLY,
        )

    components = compute_score_components(job, candidate, search, weights)
    final_score = round(100 * sum(c.weighted_value for c in components), 2)
    tier = determine_notification_tier(final_score, search.notification_thresholds)
    return MatchResult(
        job_id=job.job_id,
        search_profile_ref=search.profile_id,
        hard_filter_result=hard_filter_result,
        prefilter_result=prefilter_result,
        final_score=final_score,
        score_components=components,
        notification_tier=tier,
    )
