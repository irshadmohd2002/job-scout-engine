"""assess_visa evidence precedence (Milestone 2 Deliverable 5 step 10;
MILESTONE_2.md "Evidence precedence"): registry match sets employer_eligible;
job-text evidence is applied last and can raise to confirmed_yes or override
down to confirmed_no regardless of a registry match; neither -> unknown.
Registry and job-text evidence are never blended/averaged.
"""

from __future__ import annotations

from job_scout.matching.visa import assess_visa
from job_scout.models import SponsorRegistryMatch, VisaStatus
from tests.factories import make_candidate_profile, make_job, make_search_profile

_REGISTRY_MATCH = SponsorRegistryMatch(
    matched=True,
    registered_name="Acme Robotics Ltd",
    register_name="uk_home_office_sponsor_list",
    confidence=0.7,
)


def _assess(*, description_text: str, registry_match: SponsorRegistryMatch | None):  # type: ignore[no-untyped-def]
    job = make_job(description_text=description_text)
    candidate = make_candidate_profile()
    search = make_search_profile()
    return assess_visa(job, candidate, search, registry_match, "uk_points_based_system")


def test_registry_only_yields_employer_eligible() -> None:
    result = _assess(description_text="Great strategy role.", registry_match=_REGISTRY_MATCH)
    assert result.status == VisaStatus.EMPLOYER_ELIGIBLE
    assert result.confidence == 0.7
    assert result.employer_registry_match is True
    assert result.employer_registry_match_confidence == 0.7
    assert result.registry_source == "uk_home_office_sponsor_list"


def test_positive_text_only_yields_confirmed_yes() -> None:
    result = _assess(
        description_text="Visa sponsorship is available for the right candidate.",
        registry_match=None,
    )
    assert result.status == VisaStatus.CONFIRMED_YES
    assert result.confidence == 1.0
    assert result.job_text_evidence


def test_negative_text_only_yields_confirmed_no() -> None:
    result = _assess(
        description_text="We are unable to offer sponsorship for this role.",
        registry_match=None,
    )
    assert result.status == VisaStatus.CONFIRMED_NO
    assert result.confidence == 1.0
    assert result.negative_evidence


def test_registry_plus_positive_text_stays_confirmed_yes() -> None:
    result = _assess(
        description_text="We will sponsor the right candidate.",
        registry_match=_REGISTRY_MATCH,
    )
    assert result.status == VisaStatus.CONFIRMED_YES
    # Registry evidence is still recorded even though job-text evidence won
    # on status (evidence sources are never blended into one confidence).
    assert result.employer_registry_match is True


def test_registry_plus_negative_text_yields_confirmed_no_negative_wins() -> None:
    """The explicit case this milestone calls out by name: negative job
    text must override an employer-eligible registry match."""
    result = _assess(
        description_text="Unfortunately we cannot sponsor visas for this position.",
        registry_match=_REGISTRY_MATCH,
    )
    assert result.status == VisaStatus.CONFIRMED_NO
    assert result.confidence == 1.0
    assert result.employer_registry_match is True  # evidence preserved, not erased
    assert result.negative_evidence


def test_neither_registry_nor_text_evidence_stays_unknown() -> None:
    result = _assess(
        description_text="Great strategy role, no visa language here.", registry_match=None
    )
    assert result.status == VisaStatus.UNKNOWN
    assert result.confidence == 0.0
    assert result.employer_registry_match is None


def test_no_registry_match_records_false_not_none() -> None:
    no_match = SponsorRegistryMatch(matched=False, confidence=0.0)
    result = _assess(description_text="Great strategy role.", registry_match=no_match)
    assert result.status == VisaStatus.UNKNOWN
    assert result.employer_registry_match is False


def test_country_work_permit_regime_is_populated() -> None:
    result = _assess(description_text="Great strategy role.", registry_match=None)
    assert result.country_work_permit_regime == "uk_points_based_system"
