from job_scout import config
from job_scout.matching.scoring import (
    build_match_result,
    compute_score_components,
    determine_notification_tier,
)
from job_scout.models import (
    HardFilterResult,
    NotificationThresholds,
    NotificationTier,
    PrefilterResult,
)
from tests.factories import make_candidate_profile, make_job, make_search_profile


def _weights() -> config.ScoringWeights:
    return config.load_scoring_weights()


def test_score_components_sum_of_weights_matches_config_total() -> None:
    candidate = make_candidate_profile()
    search = make_search_profile()
    job = make_job(description_text="A strategy and transformation role, 4-8 years experience.")
    components = compute_score_components(job, candidate, search, _weights())
    assert {c.name for c in components} == {
        "title_role_family",
        "responsibilities",
        "required_skills",
        "transferable_skills",
        "seniority_experience",
        "sector_relevance",
        "education",
        "visa_relocation",
    }
    total_weight = sum(c.weight for c in components)
    assert abs(total_weight - 1.0) < 1e-6


def test_required_skills_component_matches_primary_skills_overlap() -> None:
    candidate = make_candidate_profile(primary_skills=["strategy_development", "governance"])
    search = make_search_profile()
    job = make_job(description_text="We need strategy development experience.")
    components = compute_score_components(job, candidate, search, _weights())
    required = next(c for c in components if c.name == "required_skills")
    assert required.raw_value == 0.5  # 1 of 2 primary skills matched
    assert "primary_skill:strategy_development" in required.evidence


def test_transferable_skill_never_zeroes_overall_score() -> None:
    candidate = make_candidate_profile(
        title_aliases=["Strategy Manager"],
        role_families=["strategy_and_planning"],
        primary_skills=["strategy_development"],
        secondary_skills=["financial_modelling"],
    )
    search = make_search_profile()
    job = make_job(
        title="Strategy Manager",
        description_text=(
            "Strategy and planning role requiring strategy development, 5 years experience."
        ),
    )
    components = compute_score_components(job, candidate, search, _weights())
    transferable = next(c for c in components if c.name == "transferable_skills")
    assert transferable.raw_value == 0.0  # missing secondary skill
    # the job still scores positively overall from other components
    total = sum(c.weighted_value for c in components)
    assert total > 0.0


def test_education_component_neutral_when_no_requirement_stated() -> None:
    candidate = make_candidate_profile()
    search = make_search_profile()
    job = make_job(description_text="A generic role with no education requirement mentioned.")
    components = compute_score_components(job, candidate, search, _weights())
    education = next(c for c in components if c.name == "education")
    assert education.raw_value == 0.5


def test_education_component_positive_when_mba_mentioned() -> None:
    candidate = make_candidate_profile()
    search = make_search_profile()
    job = make_job(description_text="MBA required for this role.")
    components = compute_score_components(job, candidate, search, _weights())
    education = next(c for c in components if c.name == "education")
    assert education.raw_value == 1.0


def test_visa_relocation_component_positive_negative_unknown() -> None:
    candidate = make_candidate_profile()
    search = make_search_profile()

    positive_job = make_job(description_text="Visa sponsorship is available for this role.")
    negative_job = make_job(description_text="We are unable to offer sponsorship.")
    unknown_job = make_job(description_text="A generic role description.")

    positive = next(
        c.raw_value
        for c in compute_score_components(positive_job, candidate, search, _weights())
        if c.name == "visa_relocation"
    )
    negative = next(
        c.raw_value
        for c in compute_score_components(negative_job, candidate, search, _weights())
        if c.name == "visa_relocation"
    )
    unknown = next(
        c.raw_value
        for c in compute_score_components(unknown_job, candidate, search, _weights())
        if c.name == "visa_relocation"
    )
    assert positive == 1.0
    assert negative == 0.0
    assert unknown == 0.5


def test_notification_tier_boundaries_exact() -> None:
    thresholds = NotificationThresholds(priority_score=85, digest_score=70)
    assert determine_notification_tier(85.0, thresholds) == NotificationTier.PRIORITY
    assert determine_notification_tier(84.99, thresholds) == NotificationTier.DIGEST
    assert determine_notification_tier(70.0, thresholds) == NotificationTier.DIGEST
    assert determine_notification_tier(69.99, thresholds) == NotificationTier.STORE_ONLY


def test_build_match_result_rejected_when_hard_filter_fails() -> None:
    candidate = make_candidate_profile()
    search = make_search_profile()
    job = make_job()
    hard_filter_result = HardFilterResult(passed=False, rejections=[])
    prefilter_result = PrefilterResult(score=0.9, passed_threshold=True)
    result = build_match_result(
        job, candidate, search, hard_filter_result, prefilter_result, _weights()
    )
    assert result.notification_tier == NotificationTier.REJECTED
    assert result.final_score is None
    assert result.score_components == []


def test_build_match_result_store_only_when_below_prefilter_threshold() -> None:
    candidate = make_candidate_profile()
    search = make_search_profile()
    job = make_job()
    hard_filter_result = HardFilterResult(passed=True, rejections=[])
    prefilter_result = PrefilterResult(score=0.01, passed_threshold=False)
    result = build_match_result(
        job, candidate, search, hard_filter_result, prefilter_result, _weights()
    )
    assert result.notification_tier == NotificationTier.STORE_ONLY
    assert result.final_score is None
    assert result.score_components == []  # not scored further, per Stage 2 gating


def test_build_match_result_scores_when_both_stages_pass() -> None:
    candidate = make_candidate_profile()
    search = make_search_profile()
    job = make_job(
        title="Strategy Manager",
        description_text=(
            "MBA preferred. Strategy and transformation focus, 4-8 years experience. "
            "Visa sponsorship available."
        ),
    )
    hard_filter_result = HardFilterResult(passed=True, rejections=[])
    prefilter_result = PrefilterResult(score=0.9, passed_threshold=True)
    result = build_match_result(
        job, candidate, search, hard_filter_result, prefilter_result, _weights()
    )
    assert result.final_score is not None
    assert 0 <= result.final_score <= 100
    assert len(result.score_components) == 8
