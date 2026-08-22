"""Stage-5-adjacent visa/sponsorship assessment (Milestone 2 Deliverable 5
step 10; MILESTONE_2.md "Sponsorship/visa enrichment design").

`assess_visa` is orchestration wiring, not a new pipeline stage
(architecture.md section 1's diagram already reserves this slot conceptually
beside, not inside, the Stage 1-5 match-scoring sequence) — it builds a
`VisaAssessment` per scored job, independent of (and never feeding back into)
Stage 5's `visa_relocation` ScoreComponent, which stays a separate ranking
signal (matching/scoring.py).

Evidence precedence (never blended/averaged — MILESTONE_2.md "Evidence
precedence"):
1. Start from VisaStatus.UNKNOWN.
2. An authoritative sponsor-registry match raises status to
   EMPLOYER_ELIGIBLE, at the registry match's own confidence (capped below
   "confirmed", ~0.7 - a match means "may be eligible to sponsor", never
   "will sponsor this vacancy", CLAUDE.md hard constraint 4).
3. Job-text evidence is applied last (most specific, run-relevant signal):
   positive wording raises status to CONFIRMED_YES; explicit negative
   wording sets CONFIRMED_NO *regardless* of a registry match (a specific
   "we cannot sponsor this role" statement overrides a general
   employer-eligibility signal).
`confidence` on the returned VisaAssessment is always the confidence of
whichever evidence source actually set the final status — never a blended
average of registry and job-text confidence.

CandidateProfile/SearchProfile work-authorisation fields are accepted for
signature parity with this module's design (MILESTONE_2.md's own
`assess_visa(job, candidate, search, registry_match, country_regime)`
signature) but deliberately never read here: they are Stage 1 hard-filter
inputs only (existing `requires_work_authorisation_support` behaviour in
hard_filters.py), not evidence about a specific job, and must never feed
`VisaAssessment.status` (MILESTONE_2.md "Evidence precedence" #4).
"""

from __future__ import annotations

from datetime import UTC, datetime

from job_scout.matching.visa_patterns import (
    VISA_NEGATIVE_PATTERNS,
    VISA_POSITIVE_PATTERNS,
    first_match,
)
from job_scout.models import (
    CandidateProfile,
    Job,
    SearchProfile,
    SponsorRegistryMatch,
    VisaAssessment,
    VisaStatus,
)


def assess_visa(
    job: Job,
    candidate: CandidateProfile,
    search: SearchProfile,
    registry_match: SponsorRegistryMatch | None,
    country_regime: str,
) -> VisaAssessment:
    _ = candidate, search  # never feeds status - see module docstring

    text = job.description_text
    positive_evidence = first_match(VISA_POSITIVE_PATTERNS, text)
    negative_evidence = first_match(VISA_NEGATIVE_PATTERNS, text)

    status = VisaStatus.UNKNOWN
    confidence = 0.0
    job_text_evidence: list[str] = []
    negative_evidence_list: list[str] = []
    employer_registry_match: bool | None = None
    employer_registry_match_confidence: float | None = None
    registry_source: str | None = None

    if registry_match is not None:
        employer_registry_match = registry_match.matched
        if registry_match.matched:
            employer_registry_match_confidence = registry_match.confidence
            registry_source = registry_match.register_name
            status = VisaStatus.EMPLOYER_ELIGIBLE
            confidence = registry_match.confidence

    if positive_evidence:
        status = VisaStatus.CONFIRMED_YES
        confidence = 1.0
        job_text_evidence.append(positive_evidence)

    if negative_evidence:
        # Explicit negative wording overrides everything above, including an
        # employer-eligible registry match (MILESTONE_2.md "Evidence
        # precedence" #2).
        status = VisaStatus.CONFIRMED_NO
        confidence = 1.0
        negative_evidence_list.append(negative_evidence)

    return VisaAssessment(
        job_id=job.job_id,
        status=status,
        confidence=confidence,
        job_text_evidence=job_text_evidence,
        negative_evidence=negative_evidence_list,
        employer_registry_match=employer_registry_match,
        employer_registry_match_confidence=employer_registry_match_confidence,
        registry_source=registry_source,
        country_work_permit_regime=country_regime,
        existing_work_authorisation_required=None,
        citizenship_restrictions=[],
        security_clearance_restrictions=[],
        relocation_support_evidence=[],
        international_candidate_evidence=[],
        last_verification_date=datetime.now(UTC),
    )
