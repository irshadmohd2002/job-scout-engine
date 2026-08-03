"""Stage 2 — cheap deterministic pre-filter (architecture.md section 10).

Keyword/phrase overlap scoring against title_aliases, role_families,
primary_skills, secondary_skills, and seniority language. Produces a
prefilter score and role_family_hints (cached on Job downstream by the
pipeline). Sector terms are not scored here — Milestone 1 has no sector
field on CandidateProfile/SearchProfile, so sector relevance stays a neutral
Stage 5 component instead (architecture.md section 10, Stage 5 table).
"""

from __future__ import annotations

from job_scout.config import ScoringWeights
from job_scout.models import CandidateProfile, Job, PrefilterResult

# Internal sub-weights for combining the four overlap ratios + seniority
# match into one prefilter score. Not externalised to config: unlike Stage
# 5's component weights and the prefilter_threshold itself, architecture.md
# doesn't call for these to be independently tunable — they're an
# implementation detail of "keyword/phrase overlap scoring", not a
# documented, user-facing knob.
_TITLE_WEIGHT = 0.40
_ROLE_WEIGHT = 0.30
_PRIMARY_SKILL_WEIGHT = 0.20
_SECONDARY_SKILL_WEIGHT = 0.05
_SENIORITY_WEIGHT = 0.05


def keyword_overlap(items: list[str], text: str) -> list[str]:
    """Case-insensitive substring match of each item (with underscores
    treated as spaces, so role-family ids like "chief_of_staff" match
    "Chief of Staff" in free text) against `text`."""
    lowered = text.lower()
    return [item for item in items if item.replace("_", " ").lower() in lowered]


def _ratio(matches: list[str], universe: list[str]) -> float:
    return len(matches) / len(universe) if universe else 0.0


def run_prefilter(
    job: Job, candidate: CandidateProfile, weights: ScoringWeights
) -> PrefilterResult:
    combined_text = f"{job.title} {job.description_text}"

    title_matches = keyword_overlap(candidate.title_aliases, combined_text)
    role_matches = keyword_overlap(candidate.role_families, combined_text)
    primary_matches = keyword_overlap(candidate.primary_skills, combined_text)
    secondary_matches = keyword_overlap(candidate.secondary_skills, combined_text)

    seniority_term = candidate.seniority_level.value.replace("_", " ")
    seniority_matched = seniority_term in combined_text.lower()

    score = (
        _TITLE_WEIGHT * _ratio(title_matches, candidate.title_aliases)
        + _ROLE_WEIGHT * _ratio(role_matches, candidate.role_families)
        + _PRIMARY_SKILL_WEIGHT * _ratio(primary_matches, candidate.primary_skills)
        + _SECONDARY_SKILL_WEIGHT * _ratio(secondary_matches, candidate.secondary_skills)
        + _SENIORITY_WEIGHT * (1.0 if seniority_matched else 0.0)
    )

    evidence = (
        [f"title_alias:{m}" for m in title_matches]
        + [f"role_family:{m}" for m in role_matches]
        + [f"primary_skill:{m}" for m in primary_matches]
        + [f"secondary_skill:{m}" for m in secondary_matches]
    )
    if seniority_matched:
        evidence.append(f"seniority:{seniority_term}")

    return PrefilterResult(
        score=score,
        passed_threshold=score >= weights.prefilter_threshold,
        role_family_hints=role_matches,
        evidence=evidence,
    )
