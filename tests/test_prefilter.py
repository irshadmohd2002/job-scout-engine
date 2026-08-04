"""Stage 2 pre-filter tests (architecture.md section 10; decisions.md D-029).

Fixtures here are synthetic, not the user's real profile/database — see
CLAUDE.md hard constraint 8 and this session's explicit instruction not to
depend on the live SQLite database. Titles mirror the real audit findings
(both the relevant titles that were wrongly left `unscored` and the
irrelevant "consultant" titles that must stay excluded) without using any
real personal data.
"""

from __future__ import annotations

import pytest

from job_scout.matching.prefilter import PrefilterWeights, run_prefilter
from job_scout.models import CandidateProfile, SearchProfile
from tests.factories import make_candidate_profile, make_job, make_search_profile


def _weights(**overrides: float) -> PrefilterWeights:
    return PrefilterWeights(**overrides)


def _empty_candidate(**overrides: object) -> CandidateProfile:
    base: dict[str, object] = {
        "title_aliases": [],
        "role_families": [],
        "primary_skills": [],
        "secondary_skills": [],
        "transferable_skills": [],
        "industries": [],
        "sectors": [],
        # Avoid the factory default seniority_level=MANAGER coincidentally
        # matching "Manager" in job titles under test — seniority is
        # deliberately out of scope for these prefilter fixtures.
        "seniority_level": None,
    }
    base.update(overrides)
    return make_candidate_profile(**base)


def _empty_search(**overrides: object) -> SearchProfile:
    base: dict[str, object] = {
        "target_titles": [],
        "title_aliases": [],
        "role_families": [],
        "required_skills": [],
        "preferred_skills": [],
        "transferable_skills": [],
        "included_keywords": [],
        "included_industries": [],
        "included_sectors": [],
    }
    base.update(overrides)
    return make_search_profile(**base)


# --- Search-profile matching -------------------------------------------------


def test_search_profile_target_titles_alone_passes() -> None:
    """Candidate has no matching fields configured at all; the search
    profile's target_titles alone is enough (the exact live-audit bug: real
    target titles like 'Strategy Manager' were never read)."""
    candidate = _empty_candidate()
    search = _empty_search(target_titles=["Strategy Manager"])
    job = make_job(title="Strategy Manager", description_text="A generic role.")

    result = run_prefilter(job, candidate, search, _weights())

    assert result.passed_threshold is True
    assert any("search_target_title" in e for e in result.evidence)


def test_search_profile_title_aliases_alone_passes() -> None:
    candidate = _empty_candidate()
    search = _empty_search(title_aliases=["Strategy Consultant"])
    job = make_job(title="Strategy Consultant", description_text="A generic role.")

    result = run_prefilter(job, candidate, search, _weights())

    assert result.passed_threshold is True
    assert any("search_title_alias" in e for e in result.evidence)


def test_search_profile_role_families_alone_passes() -> None:
    candidate = _empty_candidate()
    search = _empty_search(role_families=["business_transformation"])
    job = make_job(title="Business Transformation Lead", description_text="A generic role.")

    result = run_prefilter(job, candidate, search, _weights())

    assert result.passed_threshold is True
    assert any("search_role_family" in e for e in result.evidence)
    assert "business_transformation" in result.role_family_hints


def test_search_profile_required_and_preferred_skills_contribute_to_score() -> None:
    candidate = _empty_candidate()
    search = _empty_search(
        required_skills=["stakeholder_management"], preferred_skills=["financial_modelling"]
    )
    job = make_job(
        title="Generic Role",
        description_text="This role needs stakeholder management and financial modelling.",
    )

    result = run_prefilter(job, candidate, search, _weights())

    assert result.score > 0.0
    assert any(e.startswith("skill:stakeholder_management") for e in result.evidence)
    assert any(e.startswith("skill:financial_modelling") for e in result.evidence)


def test_search_profile_included_keywords_contribute_to_score() -> None:
    candidate = _empty_candidate()
    search = _empty_search(included_keywords=["chief of staff"])
    job = make_job(
        title="Generic Role", description_text="Acting as chief of staff to the CEO."
    )

    result = run_prefilter(job, candidate, search, _weights())

    assert result.score > 0.0
    assert any(e.startswith("keyword:chief of staff") for e in result.evidence)


# --- Strong-signal behaviour --------------------------------------------------


def test_exact_target_title_match_passes_alone() -> None:
    candidate = _empty_candidate()
    search = _empty_search(target_titles=["Strategy Manager"])
    job = make_job(title="Strategy Manager", description_text="Nothing else configured matches.")

    result = run_prefilter(job, candidate, search, _weights())

    assert result.passed_threshold is True
    assert any("method=exact_phrase" in e for e in result.evidence)


def test_consultant_strategy_and_transformation_matches_configured_multiword_signal() -> None:
    candidate = _empty_candidate()
    search = _empty_search(role_families=["strategy_and_transformation"])
    job = make_job(
        title="Consultant (Strategy and Transformation)", description_text="A generic role."
    )

    result = run_prefilter(job, candidate, search, _weights())

    assert result.passed_threshold is True


def test_business_strategy_analyst_consultant_survives_slash_normalisation() -> None:
    candidate = _empty_candidate(title_aliases=["Strategy Consultant"])
    search = _empty_search()
    job = make_job(
        title="Business Strategy Analyst/Consultant", description_text="A generic role."
    )

    result = run_prefilter(job, candidate, search, _weights())

    assert result.passed_threshold is True
    assert any("method=token_coverage" in e for e in result.evidence)


def test_strategy_and_transformation_lead_survives_ampersand_normalisation() -> None:
    candidate = _empty_candidate()
    search = _empty_search(target_titles=["Strategy and Transformation Lead"])
    job = make_job(title="Strategy & Transformation Lead", description_text="A generic role.")

    result = run_prefilter(job, candidate, search, _weights())

    assert result.passed_threshold is True
    assert any("method=exact_phrase" in e for e in result.evidence)


def test_one_exact_candidate_title_alias_alone_passes() -> None:
    candidate = _empty_candidate(title_aliases=["Strategy Manager"])
    search = _empty_search()
    job = make_job(title="Strategy Manager", description_text="A generic role.")

    result = run_prefilter(job, candidate, search, _weights())

    assert result.passed_threshold is True
    assert any("candidate_title_alias" in e for e in result.evidence)


def test_one_generic_token_from_multiword_target_does_not_pass_alone() -> None:
    candidate = _empty_candidate()
    search = _empty_search(target_titles=["Strategy Manager"])
    job = make_job(title="Business Manager", description_text="A generic role.")

    result = run_prefilter(job, candidate, search, _weights())

    assert result.passed_threshold is False
    assert not any("strong_title_match" in e for e in result.evidence)


# --- Precision controls: still-irrelevant titles must stay excluded ---------

_CANDIDATE_FOR_PRECISION_CONTROLS: dict[str, object] = {
    "title_aliases": ["Strategy Consultant", "Management Consultant"],
    "role_families": ["business_strategy", "management_consulting"],
}
_SEARCH_FOR_PRECISION_CONTROLS: dict[str, object] = {
    "target_titles": ["Strategy Manager", "Chief of Staff", "Head of Strategy"],
    "role_families": ["corporate_strategy", "business_transformation"],
}

_IRRELEVANT_TITLES = [
    ("Recruitment Consultant", "Assist clients with recruitment across sectors."),
    ("Sales Consultant", "Sell products to retail customers and manage accounts."),
    ("Business Travel Consultant", "Arrange corporate travel itineraries for clients."),
    ("Asbestos Consultant", "Survey buildings for asbestos and issue reports."),
    ("Legionella Consultant", "Carry out legionella risk assessments on site."),
    ("ICT Consultant", "Support desktop and network infrastructure for clients."),
    ("Business Development Consultant", "Generate new leads and grow the client base."),
]


@pytest.mark.parametrize(("title", "description"), _IRRELEVANT_TITLES)
def test_irrelevant_consultant_titles_stay_unscored(title: str, description: str) -> None:
    candidate = _empty_candidate(**_CANDIDATE_FOR_PRECISION_CONTROLS)
    search = _empty_search(**_SEARCH_FOR_PRECISION_CONTROLS)
    job = make_job(title=title, description_text=description)

    result = run_prefilter(job, candidate, search, _weights())

    assert result.passed_threshold is False, (title, result.evidence)
    assert not any("strong_title_match" in e for e in result.evidence)


# --- Regression coverage for the pre-Milestone-1.1 behaviour ----------------


def test_title_alias_match_scores_higher_than_no_match() -> None:
    candidate = _empty_candidate(title_aliases=["Strategy Manager"])
    search = _empty_search()
    matching_job = make_job(
        title="Strategy Manager", description_text="Lead strategic initiatives."
    )
    non_matching_job = make_job(title="Software Engineer", description_text="Write backend code.")

    matching = run_prefilter(matching_job, candidate, search, _weights())
    non_matching = run_prefilter(non_matching_job, candidate, search, _weights())

    assert matching.score > non_matching.score


def test_role_family_overlap_contributes_to_score_and_hints() -> None:
    candidate = _empty_candidate(
        title_aliases=["Strategy Manager"], role_families=["transformation", "chief_of_staff"]
    )
    search = _empty_search()
    job = make_job(
        title="Business Lead",
        description_text="This role focuses on transformation and chief of staff duties.",
    )
    result = run_prefilter(job, candidate, search, _weights())
    assert "transformation" in result.role_family_hints
    assert "chief_of_staff" in result.role_family_hints
    assert result.score > 0


def test_below_threshold_jobs_still_produce_a_result_not_scored_further() -> None:
    candidate = _empty_candidate(
        title_aliases=["Strategy Manager"],
        role_families=["transformation"],
        primary_skills=["strategy_development"],
        secondary_skills=["financial_modelling"],
    )
    search = _empty_search()
    job = make_job(title="Warehouse Associate", description_text="Pack boxes and load trucks.")
    result = run_prefilter(job, candidate, search, _weights())
    assert result.passed_threshold is False
    assert result.score < _weights().threshold
