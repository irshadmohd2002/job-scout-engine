from job_scout.matching.hard_filters import evaluate_hard_filters, parse_experience_range
from job_scout.models import Location
from tests.factories import make_candidate_profile, make_job, make_search_profile


def _passes(candidate=None, search=None, **job_overrides):  # type: ignore[no-untyped-def]
    candidate = candidate or make_candidate_profile()
    search = search or make_search_profile(included_countries=["GB"])
    job = make_job(location=Location(country="GB", city="London"), **job_overrides)
    return evaluate_hard_filters(job, candidate, search)


def test_baseline_job_passes() -> None:
    result = _passes(description_text="A great strategy role, 4-8 years experience.")
    assert result.passed is True
    assert result.rejections == []


def test_excluded_country_rejects_with_evidence() -> None:
    search = make_search_profile(included_countries=["GB", "DE"], excluded_countries=["DE"])
    job = make_job(location=Location(country="DE"))
    result = evaluate_hard_filters(job, make_candidate_profile(), search)
    assert result.passed is False
    rule_names = {r.rule for r in result.rejections}
    assert "excluded_country" in rule_names
    rejection = next(r for r in result.rejections if r.rule == "excluded_country")
    assert rejection.evidence == "DE"


def test_country_not_included_rejects() -> None:
    search = make_search_profile(included_countries=["GB"])
    job = make_job(location=Location(country="US"))
    result = evaluate_hard_filters(job, make_candidate_profile(), search)
    assert result.passed is False
    assert any(r.rule == "country_not_included" for r in result.rejections)


def test_citizenship_restriction_rejects_with_evidence() -> None:
    result = _passes(description_text="This role requires UK citizens only to apply.")
    assert result.passed is False
    rejection = next(r for r in result.rejections if r.rule == "citizenship_restriction")
    assert "citizens only" in rejection.evidence.lower()


def test_security_clearance_rejects_when_not_allowed() -> None:
    search = make_search_profile(included_countries=["GB"], security_clearance_allowed=False)
    job = make_job(
        location=Location(country="GB"),
        description_text="Candidates must hold active security clearance (SC).",
    )
    result = evaluate_hard_filters(job, make_candidate_profile(), search)
    assert result.passed is False
    assert any(r.rule == "security_clearance_required" for r in result.rejections)


def test_existing_work_authorisation_required_rejects_when_candidate_needs_sponsorship() -> None:
    candidate = make_candidate_profile(requires_work_authorisation_support=True)
    result = _passes(
        candidate=candidate,
        description_text="Applicants must already have the right to work in the UK.",
    )
    assert result.passed is False
    assert any(r.rule == "existing_work_authorisation_required" for r in result.rejections)


def test_explicit_no_sponsorship_rejects_with_evidence() -> None:
    search = make_search_profile(included_countries=["GB"], reject_on_explicit_no_sponsorship=True)
    job = make_job(
        location=Location(country="GB"),
        description_text="We are unable to offer sponsorship for this position.",
    )
    result = evaluate_hard_filters(job, make_candidate_profile(), search)
    assert result.passed is False
    rejection = next(r for r in result.rejections if r.rule == "explicit_no_sponsorship")
    assert "sponsorship" in rejection.evidence.lower()


def test_missing_mandatory_qualification_rejects() -> None:
    search = make_search_profile(
        included_countries=["GB"], mandatory_qualifications=["CFA charter"]
    )
    job = make_job(location=Location(country="GB"), description_text="A generic strategy role.")
    result = evaluate_hard_filters(job, make_candidate_profile(), search)
    assert result.passed is False
    assert any(r.rule == "missing_mandatory_qualification" for r in result.rejections)


def test_missing_required_language_rejects() -> None:
    search = make_search_profile(included_countries=["GB"], required_languages=["German"])
    job = make_job(location=Location(country="GB"), description_text="A generic strategy role.")
    result = evaluate_hard_filters(job, make_candidate_profile(), search)
    assert result.passed is False
    assert any(r.rule == "missing_required_language" for r in result.rejections)


def test_excluded_industry_rejects() -> None:
    candidate = make_candidate_profile(excluded_industries=["gambling"])
    result = _passes(candidate=candidate, description_text="Join our leading gambling platform.")
    assert result.passed is False
    assert any(r.rule == "excluded_industry" for r in result.rejections)


def test_excluded_role_family_rejects() -> None:
    candidate = make_candidate_profile(excluded_role_families=["sales_strategy"])
    result = _passes(candidate=candidate, description_text="We need a sales strategy leader.")
    assert result.passed is False
    assert any(r.rule == "excluded_role_family" for r in result.rejections)


def test_experience_out_of_range_rejects() -> None:
    search = make_search_profile(
        included_countries=["GB"], min_experience_years=10, max_experience_years=15
    )
    job = make_job(
        location=Location(country="GB"), description_text="Looking for 1-2 years experience."
    )
    result = evaluate_hard_filters(job, make_candidate_profile(), search)
    assert result.passed is False
    assert any(r.rule == "experience_out_of_range" for r in result.rejections)


def test_unparseable_experience_text_fails_open_r3() -> None:
    """R-3: unparseable experience text must never cause a rejection."""
    search = make_search_profile(
        included_countries=["GB"], min_experience_years=10, max_experience_years=15
    )
    job = make_job(
        location=Location(country="GB"),
        description_text="Experience requirements vary depending on the team.",
    )
    result = evaluate_hard_filters(job, make_candidate_profile(), search)
    assert not any(r.rule == "experience_out_of_range" for r in result.rejections)


def test_missing_secondary_skill_never_rejects_anywhere() -> None:
    """Per D-005/architecture.md: secondary skills are never a Stage 1
    gating condition — Stage 1 has no rule referencing secondary_skills at all."""
    candidate = make_candidate_profile(secondary_skills=["financial_modelling", "pnl_analysis"])
    result = _passes(candidate=candidate, description_text="A strategy role with no PnL exposure.")
    assert result.passed is True


def test_parse_experience_range_variants() -> None:
    assert parse_experience_range("Looking for 4-6 years of experience.") == (4.0, 6.0)
    assert parse_experience_range("At least 8 years required.") == (8.0, float("inf"))
    assert parse_experience_range("5+ years needed.") == (5.0, float("inf"))
    assert parse_experience_range("No specific requirement mentioned.") is None
