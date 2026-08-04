import pytest

from job_scout import config
from job_scout.matching.prefilter import PrefilterWeights, run_prefilter
from job_scout.matching.scoring import (
    build_match_result,
    compute_score_components,
    determine_notification_tier,
)
from job_scout.models import (
    CandidateProfile,
    HardFilterResult,
    NotificationThresholds,
    NotificationTier,
    PrefilterResult,
    SearchProfile,
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


def test_required_skills_component_uses_candidate_primary_skills_as_capped_fallback() -> None:
    """No SearchProfile.required_skills configured -> candidate.primary_skills is
    a lower-priority fallback (Part 4), not a plain matches/vocabulary ratio."""
    candidate = make_candidate_profile(primary_skills=["strategy_development", "governance"])
    search = make_search_profile(required_skills=[])
    job = make_job(description_text="We need strategy development experience.")
    components = compute_score_components(job, candidate, search, _weights())
    required = next(c for c in components if c.name == "required_skills")
    assert required.raw_value == 0.1  # 0.2 fallback weight * (1 of 2 primary skills matched)
    assert "candidate_primary_skill:strategy_development" in required.evidence


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


def test_education_component_not_evaluable_when_nothing_configured() -> None:
    """Part 6: no automatic positive floor — nothing configured to evaluate
    against means zero, not a neutral 0.5."""
    candidate = make_candidate_profile()
    search = make_search_profile()
    job = make_job(description_text="A generic role with no education requirement mentioned.")
    components = compute_score_components(job, candidate, search, _weights())
    education = next(c for c in components if c.name == "education")
    assert education.raw_value == 0.0
    assert education.evidence == ["not_evaluable:no_education_or_qualification_configured"]


def test_education_component_mba_has_no_special_value_unless_in_profile() -> None:
    """Milestone 1.1 (decisions.md D-017): there is no universal/hard-coded
    education keyword list any more. A default candidate profile (no
    education/qualifications/certifications/licences configured) must not
    treat "MBA" as special just because it's a common credential."""
    candidate = make_candidate_profile()
    search = make_search_profile()
    job = make_job(description_text="MBA required for this role.")
    components = compute_score_components(job, candidate, search, _weights())
    education = next(c for c in components if c.name == "education")
    assert education.raw_value == 0.0
    assert education.evidence == ["not_evaluable:no_education_or_qualification_configured"]


def test_education_component_positive_when_candidates_own_qualification_mentioned() -> None:
    from job_scout.models import Education

    candidate = make_candidate_profile(
        education=[Education(level="postgraduate", degree="MBA", field="General Management")]
    )
    search = make_search_profile()
    job = make_job(description_text="MBA required for this role.")
    components = compute_score_components(job, candidate, search, _weights())
    education = next(c for c in components if c.name == "education")
    assert education.raw_value == 1.0
    assert "education_match:MBA" in education.evidence


def test_education_component_configured_but_no_match_is_not_evaluable_not_neutral() -> None:
    from job_scout.models import Education

    candidate = make_candidate_profile(
        education=[Education(level="postgraduate", degree="MBA", field="General Management")]
    )
    search = make_search_profile()
    job = make_job(description_text="No qualifications mentioned in this role at all.")
    components = compute_score_components(job, candidate, search, _weights())
    education = next(c for c in components if c.name == "education")
    assert education.raw_value == 0.0
    assert education.evidence == ["not_evaluable:no_matching_evidence_found"]


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
    assert negative == -1.0  # Part 6: explicit negative evidence, distinct from "no evidence"
    assert unknown == 0.0  # Part 6: no automatic positive floor


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


def test_search_profile_target_title_evidence_appears_in_title_role_family_component() -> None:
    """decisions.md D-029: a job that clears Stage 2 on SearchProfile.target_titles
    evidence must not have that evidence silently dropped at Stage 5."""
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    search = make_search_profile(target_titles=["Strategy Manager"], role_families=[])
    job = make_job(title="Strategy Manager", description_text="A generic role.")
    components = compute_score_components(job, candidate, search, _weights())
    title_role_family = next(c for c in components if c.name == "title_role_family")
    assert title_role_family.raw_value > 0.0
    assert any("search_target_title" in e for e in title_role_family.evidence)


def test_search_profile_role_family_evidence_appears_in_title_role_family_component() -> None:
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    search = make_search_profile(target_titles=[], role_families=["business_transformation"])
    job = make_job(
        title="Business Transformation Lead",
        description_text="A generic role.",
    )
    components = compute_score_components(job, candidate, search, _weights())
    title_role_family = next(c for c in components if c.name == "title_role_family")
    assert title_role_family.raw_value > 0.0
    assert any("search_role_family" in e for e in title_role_family.evidence)


def test_final_score_is_deterministic_across_repeated_calls() -> None:
    candidate = make_candidate_profile()
    search = make_search_profile(target_titles=["Strategy Manager"])
    job = make_job(
        title="Strategy Manager",
        description_text="Strategy and transformation focus, 4-8 years experience.",
    )
    hard_filter_result = HardFilterResult(passed=True, rejections=[])
    prefilter_result = PrefilterResult(score=0.9, passed_threshold=True)
    first = build_match_result(
        job, candidate, search, hard_filter_result, prefilter_result, _weights()
    )
    second = build_match_result(
        job, candidate, search, hard_filter_result, prefilter_result, _weights()
    )
    assert first.final_score == second.final_score


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


# --- Part 1/2 regression: title and role-family best-match stability -------


def test_exact_target_title_match_score_stable_regardless_of_vocabulary_size() -> None:
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    small_search = make_search_profile(target_titles=["Strategy Manager"], role_families=[])
    unrelated_titles = [f"Unrelated Title {i}" for i in range(30)]
    large_search = make_search_profile(
        target_titles=["Strategy Manager", *unrelated_titles], role_families=[]
    )
    job = make_job(title="Strategy Manager", description_text="A generic role.")

    small = compute_score_components(job, candidate, small_search, _weights())
    large = compute_score_components(job, candidate, large_search, _weights())
    small_title = next(c for c in small if c.name == "title_role_family")
    large_title = next(c for c in large if c.name == "title_role_family")

    assert small_title.raw_value == large_title.raw_value


def test_search_target_title_outranks_generic_candidate_historical_title() -> None:
    job = make_job(title="Strategy Manager", description_text="A generic role.")

    candidate_only = make_candidate_profile(title_aliases=["Strategy Manager"], role_families=[])
    search_no_target = make_search_profile(target_titles=[], role_families=[])
    candidate_components = compute_score_components(
        job, candidate_only, search_no_target, _weights()
    )
    candidate_title = next(c for c in candidate_components if c.name == "title_role_family")

    empty_candidate = make_candidate_profile(title_aliases=[], role_families=[])
    search_with_target = make_search_profile(target_titles=["Strategy Manager"], role_families=[])
    search_components = compute_score_components(
        job, empty_candidate, search_with_target, _weights()
    )
    search_title = next(c for c in search_components if c.name == "title_role_family")

    assert search_title.raw_value > candidate_title.raw_value


def test_stage2_strong_title_evidence_has_corresponding_stage5_credit() -> None:
    """The exact live-audit bug: 'Associate Consultant - Data & Analytics' passed
    Stage 2 via token-coverage against 'Data & Analytics Associate', but Stage
    5's old exact-substring-only matching gave it zero title credit. Part 3
    fixes this by sharing the same match_phrase() function in both stages."""
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    search = make_search_profile(target_titles=["Data & Analytics Associate"], role_families=[])
    job = make_job(
        title="Associate Consultant - Data & Analytics", description_text="A generic role."
    )

    prefilter_result = run_prefilter(
        job, candidate, search, PrefilterWeights.from_scoring_weights(_weights())
    )
    assert prefilter_result.passed_threshold is True
    assert any("strong_title_match" in e for e in prefilter_result.evidence)

    components = compute_score_components(job, candidate, search, _weights())
    title_role_family = next(c for c in components if c.name == "title_role_family")
    assert title_role_family.raw_value > 0.0


def test_ampersand_and_slash_variants_score_the_same_as_canonical_phrase() -> None:
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    search = make_search_profile(
        target_titles=["Strategy and Transformation Lead"], role_families=[]
    )
    canonical_job = make_job(
        title="Strategy and Transformation Lead", description_text="A generic role."
    )
    ampersand_job = make_job(
        title="Strategy & Transformation Lead", description_text="A generic role."
    )

    canonical = compute_score_components(canonical_job, candidate, search, _weights())
    ampersand = compute_score_components(ampersand_job, candidate, search, _weights())
    canonical_title = next(c for c in canonical if c.name == "title_role_family")
    ampersand_title = next(c for c in ampersand if c.name == "title_role_family")

    assert canonical_title.raw_value == ampersand_title.raw_value


def test_single_generic_token_from_multiword_target_does_not_score_as_title_match() -> None:
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    search = make_search_profile(target_titles=["Strategy Manager"], role_families=[])
    job = make_job(title="Business Manager", description_text="A generic role.")

    components = compute_score_components(job, candidate, search, _weights())
    title_role_family = next(c for c in components if c.name == "title_role_family")

    assert title_role_family.raw_value == 0.0


def test_search_role_family_match_stable_regardless_of_vocabulary_size() -> None:
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    unrelated_families = [f"unrelated_family_{i}" for i in range(20)]
    small_search = make_search_profile(target_titles=[], role_families=["business_transformation"])
    large_search = make_search_profile(
        target_titles=[], role_families=["business_transformation", *unrelated_families]
    )
    job = make_job(title="Business Transformation Lead", description_text="A generic role.")

    small = compute_score_components(job, candidate, small_search, _weights())
    large = compute_score_components(job, candidate, large_search, _weights())
    small_title = next(c for c in small if c.name == "title_role_family")
    large_title = next(c for c in large if c.name == "title_role_family")

    assert small_title.raw_value == large_title.raw_value


def test_search_role_family_evidence_recorded_with_provenance() -> None:
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    search = make_search_profile(target_titles=[], role_families=["business_transformation"])
    job = make_job(title="Business Transformation Lead", description_text="A generic role.")

    components = compute_score_components(job, candidate, search, _weights())
    title_role_family = next(c for c in components if c.name == "title_role_family")

    assert any("search_role_family" in e for e in title_role_family.evidence)


def test_candidate_role_family_contributes_without_diluting_search_target_evidence() -> None:
    job = make_job(title="Business Transformation Lead", description_text="A generic role.")
    search = make_search_profile(target_titles=[], role_families=["business_transformation"])

    with_unrelated_history = make_candidate_profile(
        title_aliases=[], role_families=["unrelated_history_family"]
    )
    without_history = make_candidate_profile(title_aliases=[], role_families=[])

    with_history_components = compute_score_components(
        job, with_unrelated_history, search, _weights()
    )
    without_history_components = compute_score_components(
        job, without_history, search, _weights()
    )
    with_history_title = next(c for c in with_history_components if c.name == "title_role_family")
    without_history_title = next(
        c for c in without_history_components if c.name == "title_role_family"
    )

    # unrelated candidate history doesn't change the winning (search-target) match
    assert with_history_title.raw_value == without_history_title.raw_value


# --- D-033 regression: title/role-family aggregation formula ----------------


def test_exact_target_title_alone_receives_full_title_role_family_credit() -> None:
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    search = make_search_profile(target_titles=["Strategy Manager"], role_families=[])
    job = make_job(title="Strategy Manager", description_text="A generic role.")

    components = compute_score_components(job, candidate, search, _weights())
    title_role_family = next(c for c in components if c.name == "title_role_family")

    assert title_role_family.raw_value == 1.0


def test_matching_role_family_evidence_does_not_reduce_an_exact_title_match() -> None:
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    title_only_search = make_search_profile(target_titles=["Strategy Manager"], role_families=[])
    title_and_role_search = make_search_profile(
        target_titles=["Strategy Manager"], role_families=["strategy_management"]
    )
    job = make_job(
        title="Strategy Manager",
        description_text="This is a strategy management role.",
    )

    title_only = compute_score_components(job, candidate, title_only_search, _weights())
    title_and_role = compute_score_components(job, candidate, title_and_role_search, _weights())
    title_only_component = next(c for c in title_only if c.name == "title_role_family")
    title_and_role_component = next(c for c in title_and_role if c.name == "title_role_family")

    assert title_and_role_component.raw_value >= title_only_component.raw_value


def test_role_family_only_evidence_contributes_less_than_exact_target_title() -> None:
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    role_only_search = make_search_profile(target_titles=[], role_families=["strategy_management"])
    title_search = make_search_profile(target_titles=["Strategy Manager"], role_families=[])

    role_only_job = make_job(
        title="Strategy Management Lead", description_text="A generic role."
    )
    title_job = make_job(title="Strategy Manager", description_text="A generic role.")

    role_only_components = compute_score_components(
        role_only_job, candidate, role_only_search, _weights()
    )
    title_components = compute_score_components(title_job, candidate, title_search, _weights())
    role_only_component = next(c for c in role_only_components if c.name == "title_role_family")
    title_component = next(c for c in title_components if c.name == "title_role_family")

    assert role_only_component.raw_value > 0.0
    assert role_only_component.raw_value < title_component.raw_value


def test_candidate_previous_title_does_not_outrank_exact_active_search_target() -> None:
    job = make_job(title="Strategy Manager", description_text="A generic role.")

    history_only_candidate = make_candidate_profile(
        title_aliases=[], role_families=[], previous_titles=["Strategy Manager"]
    )
    search_no_target = make_search_profile(target_titles=[], role_families=[])
    history_components = compute_score_components(
        job, history_only_candidate, search_no_target, _weights()
    )
    history_component = next(c for c in history_components if c.name == "title_role_family")

    empty_candidate = make_candidate_profile(title_aliases=[], role_families=[])
    search_with_target = make_search_profile(target_titles=["Strategy Manager"], role_families=[])
    active_components = compute_score_components(
        job, empty_candidate, search_with_target, _weights()
    )
    active_component = next(c for c in active_components if c.name == "title_role_family")

    assert active_component.raw_value > history_component.raw_value


def test_unrelated_additional_configured_titles_do_not_reduce_title_role_family_score() -> None:
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    exact_search = make_search_profile(target_titles=["Strategy Manager"], role_families=[])
    unrelated_titles = [f"Completely Unrelated Title {i}" for i in range(10)]
    padded_search = make_search_profile(
        target_titles=["Strategy Manager", *unrelated_titles], role_families=[]
    )
    job = make_job(title="Strategy Manager", description_text="A generic role.")

    exact_components = compute_score_components(job, candidate, exact_search, _weights())
    padded_components = compute_score_components(job, candidate, padded_search, _weights())
    exact_component = next(c for c in exact_components if c.name == "title_role_family")
    padded_component = next(c for c in padded_components if c.name == "title_role_family")

    assert padded_component.raw_value == exact_component.raw_value


# --- D-034 regression: active-search-intent classification and aggregation --
# Confirmed live defects from the read-only audit: "Data Business Analyst"
# (candidate-history title alias + incidental sector match, no active
# SearchProfile title/role-family evidence at all) outranking "Strategy
# Manager" (exact active target-title match, 31.25 vs 25.0); and active
# SearchProfile role-family-only evidence ("Consulting Project Team Lead -
# Corporate Strategy", two distinct active role-family matches on the
# title) scoring the same 12.5 as a single weaker active role-family match,
# and below candidate-history-only jobs once their fallback skill/
# responsibilities components stacked on top.


def test_active_role_family_alone_receives_substantially_more_than_old_half_credit() -> None:
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    search = make_search_profile(target_titles=[], role_families=["strategy_management"])
    job = make_job(title="Strategy Management Lead", description_text="A generic role.")

    components = compute_score_components(job, candidate, search, _weights())
    title_role_family = next(c for c in components if c.name == "title_role_family")

    # old formula: exactly role_score / 2 == 0.5 for a perfect match.
    assert title_role_family.raw_value > 0.5
    assert title_role_family.raw_value < 1.0
    assert any(
        "active_search_intent_tier:active_role_family" in e for e in title_role_family.evidence
    )


def test_two_distinct_active_role_family_title_matches_reinforce_above_one() -> None:
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    single_match_search = make_search_profile(target_titles=[], role_families=["strategy"])
    reinforced_search = make_search_profile(
        target_titles=[], role_families=["strategy", "corporate_strategy"]
    )
    job = make_job(
        title="Consulting Project Team Lead - Corporate Strategy",
        description_text="Leads corporate strategy consulting projects.",
    )

    single_components = compute_score_components(job, candidate, single_match_search, _weights())
    reinforced_components = compute_score_components(job, candidate, reinforced_search, _weights())
    single_component = next(c for c in single_components if c.name == "title_role_family")
    reinforced_component = next(c for c in reinforced_components if c.name == "title_role_family")

    assert reinforced_component.raw_value > single_component.raw_value
    assert reinforced_component.raw_value < 1.0


def test_candidate_only_role_family_alone_scores_less_than_active_role_family_alone() -> None:
    active_candidate = make_candidate_profile(title_aliases=[], role_families=[])
    active_search = make_search_profile(target_titles=[], role_families=["strategy_management"])
    candidate_only_candidate = make_candidate_profile(
        title_aliases=[], role_families=["strategy_management"]
    )
    candidate_only_search = make_search_profile(target_titles=[], role_families=[])
    job = make_job(title="Strategy Management Lead", description_text="A generic role.")

    active_component = next(
        c
        for c in compute_score_components(job, active_candidate, active_search, _weights())
        if c.name == "title_role_family"
    )
    candidate_only_component = next(
        c
        for c in compute_score_components(
            job, candidate_only_candidate, candidate_only_search, _weights()
        )
        if c.name == "title_role_family"
    )

    assert active_component.raw_value > candidate_only_component.raw_value
    assert any(
        "active_search_intent_tier:candidate_history_only" in e
        for e in candidate_only_component.evidence
    )


def test_candidate_title_alias_and_previous_title_are_supplemental_and_ordered() -> None:
    search = make_search_profile(target_titles=["Strategy Manager"], role_families=[])
    job = make_job(title="Strategy Manager", description_text="A generic role.")

    active_component = next(
        c
        for c in compute_score_components(
            job, make_candidate_profile(title_aliases=[], role_families=[]), search, _weights()
        )
        if c.name == "title_role_family"
    )
    alias_component = next(
        c
        for c in compute_score_components(
            job,
            make_candidate_profile(
                title_aliases=["Strategy Manager"], previous_titles=[], role_families=[]
            ),
            make_search_profile(target_titles=[], role_families=[]),
            _weights(),
        )
        if c.name == "title_role_family"
    )
    previous_title_component = next(
        c
        for c in compute_score_components(
            job,
            make_candidate_profile(
                title_aliases=[], previous_titles=["Strategy Manager"], role_families=[]
            ),
            make_search_profile(target_titles=[], role_families=[]),
            _weights(),
        )
        if c.name == "title_role_family"
    )

    # active target title > candidate title alias > candidate previous title
    assert active_component.raw_value > alias_component.raw_value
    assert alias_component.raw_value > previous_title_component.raw_value
    assert any(
        "active_search_intent_tier:active_target_title" in e for e in active_component.evidence
    )
    assert any(
        "active_search_intent_tier:candidate_history_only" in e for e in alias_component.evidence
    )
    assert any(
        "active_search_intent_tier:candidate_history_only" in e
        for e in previous_title_component.evidence
    )


def test_no_title_or_role_evidence_is_classified_and_scores_zero() -> None:
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    search = make_search_profile(target_titles=[], role_families=[])
    job = make_job(title="Completely Unrelated Role", description_text="Nothing configured.")

    components = compute_score_components(job, candidate, search, _weights())
    title_role_family = next(c for c in components if c.name == "title_role_family")

    assert title_role_family.raw_value == 0.0
    assert any(
        "active_search_intent_tier:no_title_or_role_evidence" in e
        for e in title_role_family.evidence
    )


def test_candidate_history_with_sector_match_does_not_outrank_active_target_title() -> None:
    """The confirmed live defect: a job supported only by a candidate title
    alias plus an incidental sector/industry mention (live score 31.25)
    outranked a job with an exact active SearchProfile target-title match
    and no sector evidence at all (live score 25.0)."""
    candidate = make_candidate_profile(title_aliases=["Business Analyst"], role_families=[])
    search = make_search_profile(
        target_titles=["Strategy Manager"], role_families=[], included_industries=["ai"]
    )
    hard_filter_result = HardFilterResult(passed=True, rejections=[])
    prefilter_result = PrefilterResult(score=0.9, passed_threshold=True)

    candidate_history_job = make_job(
        title="Data Business Analyst",
        description_text="A generic data role, powered by AI.",
    )
    active_target_job = make_job(title="Strategy Manager", description_text="A generic role.")

    candidate_history_result = build_match_result(
        candidate_history_job, candidate, search, hard_filter_result, prefilter_result, _weights()
    )
    active_target_result = build_match_result(
        active_target_job, candidate, search, hard_filter_result, prefilter_result, _weights()
    )

    assert candidate_history_result.final_score is not None
    assert active_target_result.final_score is not None
    assert active_target_result.final_score > candidate_history_result.final_score


# --- D-033 regression: connector-word-aware title/role matching -------------
# The confirmed post-D-032 false positives: token coverage over *raw* tokens
# let a shared connector word ("and") count toward a multi-word configured
# phrase's coverage ratio, wrongly gating jobs whose actual occupational head
# term was absent.


def test_data_and_analytics_associate_does_not_score_reporting_partner_job() -> None:
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    search = make_search_profile(target_titles=["Data & Analytics Associate"], role_families=[])
    job = make_job(
        title="People Reporting and Analytics Data Partner",
        description_text="A generic role.",
    )

    components = compute_score_components(job, candidate, search, _weights())
    title_role_family = next(c for c in components if c.name == "title_role_family")

    assert title_role_family.raw_value == 0.0


@pytest.mark.parametrize(
    "title",
    [
        "People Reporting and Analytics Data Partner",
        "Senior Manager - Model Build & Data Analytics",
        "Lead - Clinical Data Manager / Data Analytics",
    ],
)
def test_confirmed_false_positive_titles_do_not_strongly_gate_stage2(title: str) -> None:
    candidate = _empty_candidate_for_precision()
    search = _search_for_precision(target_titles=["Data & Analytics Associate"], role_families=[])
    job = make_job(title=title, description_text="A generic role.")

    result = run_prefilter(
        job, candidate, search, PrefilterWeights.from_scoring_weights(_weights())
    )

    assert not any("strong_title_match" in e for e in result.evidence), (title, result.evidence)


@pytest.mark.parametrize(
    ("title", "description"),
    [
        ("Strategy Manager", "A generic role."),
        ("Business Strategy Consultant", "Business strategy consulting engagement."),
        ("Consultant (Strategy and Transformation)", "A generic role."),
        (
            "Consulting Project Team Lead - Corporate Strategy",
            "Leads corporate strategy consulting projects.",
        ),
        ("Business Strategy Analyst/Consultant", "A generic role."),
    ],
)
def test_relevant_strategy_titles_still_strongly_gate_stage2(title: str, description: str) -> None:
    candidate = _empty_candidate_for_precision(
        title_aliases=["Strategy Consultant", "Business Strategy Consultant"]
    )
    search = _search_for_precision(
        target_titles=["Strategy Manager"],
        role_families=["corporate_strategy", "strategy_and_transformation"],
    )
    job = make_job(title=title, description_text=description)

    result = run_prefilter(
        job, candidate, search, PrefilterWeights.from_scoring_weights(_weights())
    )

    assert any("strong_title_match" in e for e in result.evidence), (title, result.evidence)


def _empty_candidate_for_precision(**overrides: object) -> CandidateProfile:
    base: dict[str, object] = {"title_aliases": [], "role_families": []}
    base.update(overrides)
    return make_candidate_profile(**base)


def _search_for_precision(**overrides: object) -> SearchProfile:
    base: dict[str, object] = {"target_titles": [], "role_families": []}
    base.update(overrides)
    return make_search_profile(**base)


# --- D-033 live-case ranking regression --------------------------------------


def _connector_fix_candidate() -> object:
    return make_candidate_profile(
        title_aliases=[
            "Business Strategy Consultant",
            "Data & Analytics Associate",
            "Business Analyst",
        ],
        role_families=["business_strategy"],
    )


def _connector_fix_search() -> object:
    return make_search_profile(
        target_titles=["Strategy Manager"],
        role_families=["business_strategy"],
        min_experience_years=5,
        max_experience_years=15,
    )


def test_strategy_manager_ranks_above_reporting_and_analytics_data_partner() -> None:
    candidate = _connector_fix_candidate()
    search = _connector_fix_search()
    hard_filter_result = HardFilterResult(passed=True, rejections=[])
    prefilter_result = PrefilterResult(score=0.9, passed_threshold=True)

    strategy_manager = build_match_result(
        make_job(title="Strategy Manager", description_text="Corporate strategy team role."),
        candidate,
        search,
        hard_filter_result,
        prefilter_result,
        _weights(),
    )
    reporting_partner = build_match_result(
        make_job(
            title="People Reporting and Analytics Data Partner",
            description_text="A generic role.",
        ),
        candidate,
        search,
        hard_filter_result,
        prefilter_result,
        _weights(),
    )

    assert strategy_manager.final_score is not None
    assert reporting_partner.final_score is not None
    assert strategy_manager.final_score > reporting_partner.final_score


def test_business_strategy_consultant_ranks_above_model_build_data_analytics_manager() -> None:
    candidate = _connector_fix_candidate()
    search = _connector_fix_search()
    hard_filter_result = HardFilterResult(passed=True, rejections=[])
    prefilter_result = PrefilterResult(score=0.9, passed_threshold=True)

    business_strategy_consultant = build_match_result(
        make_job(
            title="Business Strategy Consultant",
            description_text="Business strategy consulting engagement.",
        ),
        candidate,
        search,
        hard_filter_result,
        prefilter_result,
        _weights(),
    )
    model_build_manager = build_match_result(
        make_job(
            title="Model Build & Data Analytics Manager",
            description_text="Builds credit risk models using data analytics.",
        ),
        candidate,
        search,
        hard_filter_result,
        prefilter_result,
        _weights(),
    )

    assert business_strategy_consultant.final_score is not None
    assert model_build_manager.final_score is not None
    assert business_strategy_consultant.final_score > model_build_manager.final_score


def test_business_analyst_trainee_remains_below_standard_business_analyst_role() -> None:
    candidate = _connector_fix_candidate()
    search = _connector_fix_search()
    hard_filter_result = HardFilterResult(passed=True, rejections=[])
    prefilter_result = PrefilterResult(score=0.9, passed_threshold=True)

    standard = build_match_result(
        make_job(title="Business Analyst", description_text="Business analyst role, London."),
        candidate,
        search,
        hard_filter_result,
        prefilter_result,
        _weights(),
    )
    trainee = build_match_result(
        make_job(
            title="Business Analyst Trainee",
            description_text="Entry point into a BA career.",
        ),
        candidate,
        search,
        hard_filter_result,
        prefilter_result,
        _weights(),
    )

    assert standard.final_score is not None
    assert trainee.final_score is not None
    assert trainee.final_score < standard.final_score


# --- Part 4 regression: search-profile-aware skill scoring -----------------


def test_search_required_skill_contributes_more_than_candidate_secondary_skill() -> None:
    candidate = make_candidate_profile(
        primary_skills=[], secondary_skills=["stakeholder_management"]
    )
    search = make_search_profile(required_skills=["stakeholder_management"])
    job = make_job(description_text="This role needs stakeholder management.")

    components = compute_score_components(job, candidate, search, _weights())
    required = next(c for c in components if c.name == "required_skills")
    transferable = next(c for c in components if c.name == "transferable_skills")

    assert required.weighted_value > transferable.weighted_value


def test_search_preferred_skill_contributes_less_than_search_required_skill() -> None:
    candidate = make_candidate_profile(primary_skills=[], secondary_skills=[])
    job = make_job(description_text="This role needs stakeholder management.")

    required_search = make_search_profile(
        required_skills=["stakeholder_management"], preferred_skills=[]
    )
    preferred_search = make_search_profile(
        required_skills=[], preferred_skills=["stakeholder_management"]
    )

    required_contribution = next(
        c
        for c in compute_score_components(job, candidate, required_search, _weights())
        if c.name == "required_skills"
    ).weighted_value
    preferred_contribution = next(
        c
        for c in compute_score_components(job, candidate, preferred_search, _weights())
        if c.name == "transferable_skills"
    ).weighted_value

    assert preferred_contribution > 0.0
    assert required_contribution > preferred_contribution


def test_generic_candidate_skill_overlap_does_not_outrank_exact_title_match() -> None:
    title_match_candidate = make_candidate_profile(
        title_aliases=[], role_families=[], primary_skills=[], secondary_skills=[]
    )
    title_match_search = make_search_profile(target_titles=["Strategy Manager"], role_families=[])
    title_match_job = make_job(title="Strategy Manager", description_text="A generic role.")
    title_match_result = build_match_result(
        title_match_job,
        title_match_candidate,
        title_match_search,
        HardFilterResult(passed=True, rejections=[]),
        PrefilterResult(score=0.9, passed_threshold=True),
        _weights(),
    )

    # No title/role match at all, but the candidate's generic historical
    # skills all happen to be mentioned in an unrelated job's description.
    skill_overlap_candidate = make_candidate_profile(
        title_aliases=[],
        role_families=[],
        primary_skills=["financial_modelling", "market_sizing", "due_diligence"],
        secondary_skills=["pmo", "kpi_framework_design"],
    )
    skill_overlap_search = make_search_profile(target_titles=[], role_families=[])
    skill_overlap_job = make_job(
        title="Data Analytics Manager",
        description_text=(
            "This role covers financial modelling, market sizing, due diligence, "
            "pmo and kpi framework design."
        ),
    )
    skill_overlap_result = build_match_result(
        skill_overlap_job,
        skill_overlap_candidate,
        skill_overlap_search,
        HardFilterResult(passed=True, rejections=[]),
        PrefilterResult(score=0.9, passed_threshold=True),
        _weights(),
    )

    assert title_match_result.final_score is not None
    assert skill_overlap_result.final_score is not None
    assert title_match_result.final_score > skill_overlap_result.final_score


def test_no_configured_skill_evidence_contributes_zero_not_a_positive_floor() -> None:
    candidate = make_candidate_profile(primary_skills=[], secondary_skills=[])
    search = make_search_profile(required_skills=[], preferred_skills=[])
    job = make_job(description_text="A completely unrelated job description.")

    components = compute_score_components(job, candidate, search, _weights())
    required = next(c for c in components if c.name == "required_skills")
    transferable = next(c for c in components if c.name == "transferable_skills")

    assert required.raw_value == 0.0
    assert transferable.raw_value == 0.0


# --- Part 5 regression: sector/industry relevance ---------------------------


def test_matching_included_industry_increases_sector_component() -> None:
    candidate = make_candidate_profile(industries=["government"])
    search = make_search_profile()
    job = make_job(description_text="This role sits within the government sector.")

    components = compute_score_components(job, candidate, search, _weights())
    sector = next(c for c in components if c.name == "sector_relevance")

    assert sector.raw_value == 1.0
    assert any("included_industry_or_sector:government" in e for e in sector.evidence)


def test_excluded_industry_reduces_component_without_hard_rejection() -> None:
    candidate = make_candidate_profile(industries=["government"])
    search = make_search_profile(excluded_industries=["defence"])
    job = make_job(description_text="This role sits entirely within the defence sector.")

    components = compute_score_components(job, candidate, search, _weights())
    sector = next(c for c in components if c.name == "sector_relevance")

    assert sector.raw_value == 0.0
    assert any("excluded_industry_or_sector:defence" in e for e in sector.evidence)
    # soft signal only — never a hard rejection from this component
    result = build_match_result(
        job,
        candidate,
        search,
        HardFilterResult(passed=True, rejections=[]),
        PrefilterResult(score=0.9, passed_threshold=True),
        _weights(),
    )
    assert result.notification_tier != NotificationTier.REJECTED


def test_short_industry_token_does_not_match_inside_unrelated_word() -> None:
    """The live-audit "ai" bug: a 2-letter configured industry term must not
    match the substring "ai" inside an unrelated word like "Trainee"."""
    candidate = make_candidate_profile()
    search = make_search_profile(included_industries=["ai"])
    job = make_job(title="Business Analyst Trainee", description_text="A generic role.")

    components = compute_score_components(job, candidate, search, _weights())
    sector = next(c for c in components if c.name == "sector_relevance")

    assert not any("included_industry_or_sector:ai" in e for e in sector.evidence)
    assert sector.raw_value == 0.0


def test_no_configured_sector_preferences_gives_documented_neutral() -> None:
    candidate = make_candidate_profile(industries=[], sectors=[])
    search = make_search_profile(included_industries=[], included_sectors=[])
    job = make_job(description_text="A generic role.")

    components = compute_score_components(job, candidate, search, _weights())
    sector = next(c for c in components if c.name == "sector_relevance")

    assert sector.raw_value == 0.5
    assert sector.evidence == ["not_evaluable:no_industry_or_sector_preference_configured"]


def test_configured_preferences_with_no_detected_evidence_grant_no_points() -> None:
    candidate = make_candidate_profile(industries=["telecom"])
    search = make_search_profile()
    job = make_job(description_text="A completely unrelated job description.")

    components = compute_score_components(job, candidate, search, _weights())
    sector = next(c for c in components if c.name == "sector_relevance")

    assert sector.raw_value == 0.0
    assert sector.evidence == ["not_evaluable:no_matching_evidence_found"]


# --- Part 6 regression: no unconditional score floor ------------------------


def test_zero_evidence_job_scores_near_zero_not_fifteen() -> None:
    candidate = make_candidate_profile(
        title_aliases=[],
        role_families=[],
        primary_skills=[],
        secondary_skills=[],
        industries=[],
        sectors=[],
    )
    search = make_search_profile(
        target_titles=[],
        role_families=[],
        required_skills=[],
        preferred_skills=[],
        included_industries=[],
        included_sectors=[],
        excluded_industries=[],
        excluded_sectors=[],
    )
    job = make_job(
        title="Completely Unrelated Role",
        description_text="Nothing here matches anything configured at all.",
    )
    result = build_match_result(
        job,
        candidate,
        search,
        HardFilterResult(passed=True, rejections=[]),
        PrefilterResult(score=0.9, passed_threshold=True),
        _weights(),
    )
    assert result.final_score is not None
    # Only sector_relevance's "nothing configured" neutral (0.5 * 0.10 weight
    # = 5.0) survives; the old behaviour floored every job at 15.0.
    assert result.final_score < 10.0


def test_missing_or_truncated_description_neither_penalises_nor_rewards() -> None:
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    search = make_search_profile(target_titles=["Strategy Manager"], role_families=[])
    full_job = make_job(
        title="Strategy Manager",
        description_text="Strategy Manager role. " + ("Detail. " * 200),
    )
    truncated_job = make_job(title="Strategy Manager", description_text="Strategy Manager role.")

    full_components = compute_score_components(
        job=full_job, candidate=candidate, search=search, weights=_weights()
    )
    truncated_components = compute_score_components(
        job=truncated_job, candidate=candidate, search=search, weights=_weights()
    )
    full_title = next(c for c in full_components if c.name == "title_role_family")
    truncated_title = next(c for c in truncated_components if c.name == "title_role_family")

    # title evidence is unaffected either way — description length never
    # penalises or rewards the title/role match by itself
    assert full_title.raw_value == truncated_title.raw_value


# --- Part 7 regression: entry-level seniority safeguard ---------------------


def test_trainee_role_scores_below_standard_role_for_experienced_search() -> None:
    candidate = make_candidate_profile(title_aliases=["Business Analyst"], role_families=[])
    search = make_search_profile(
        target_titles=[], role_families=[], min_experience_years=5, max_experience_years=15
    )

    standard_job = make_job(
        title="Business Analyst", description_text="Business analyst role, London."
    )
    trainee_job = make_job(
        title="Business Analyst Trainee", description_text="Entry point into a BA career."
    )

    standard_result = build_match_result(
        standard_job,
        candidate,
        search,
        HardFilterResult(passed=True, rejections=[]),
        PrefilterResult(score=0.9, passed_threshold=True),
        _weights(),
    )
    trainee_result = build_match_result(
        trainee_job,
        candidate,
        search,
        HardFilterResult(passed=True, rejections=[]),
        PrefilterResult(score=0.9, passed_threshold=True),
        _weights(),
    )

    assert standard_result.final_score is not None
    assert trainee_result.final_score is not None
    assert trainee_result.final_score < standard_result.final_score


def test_graduate_role_receives_seniority_mismatch_signal() -> None:
    candidate = make_candidate_profile()
    search = make_search_profile(min_experience_years=5, max_experience_years=15)
    job = make_job(
        title="Graduate Strategy Analyst", description_text="A graduate programme role."
    )

    components = compute_score_components(job, candidate, search, _weights())
    seniority = next(c for c in components if c.name == "seniority_experience")

    assert any(e.startswith("seniority_mismatch:") for e in seniority.evidence)
    assert seniority.raw_value < 0.0


def test_missing_seniority_language_is_not_automatically_rewarded() -> None:
    candidate = make_candidate_profile()
    search = make_search_profile(min_experience_years=5, max_experience_years=15)
    job = make_job(title="Strategy Manager", description_text="A generic role.")

    components = compute_score_components(job, candidate, search, _weights())
    seniority = next(c for c in components if c.name == "seniority_experience")

    assert seniority.raw_value <= 0.0


# --- Live-case synthetic regression: strong group outranks weak group -------


def _live_like_candidate() -> object:
    return make_candidate_profile(
        title_aliases=["Management Consultant", "Business Analyst", "Strategy Consultant"],
        role_families=["management_consulting", "business_strategy", "data_analytics"],
        primary_skills=["business_strategy", "financial_modelling", "market_sizing"],
        secondary_skills=["stakeholder_management", "kpi_framework_design"],
        industries=["telecom_media_technology", "public_sector"],
        sectors=["government", "financial_services"],
    )


def _live_like_search() -> object:
    return make_search_profile(
        target_titles=["Strategy Manager", "Head of Strategy", "Director of Corporate Strategy"],
        role_families=["strategy", "corporate_strategy", "business_transformation"],
        # "financial_modelling" matches the real live profile's
        # preferred_skills (decisions.md D-034 audit gap: the fixture used
        # to omit it, so this fixture never actually exercised the
        # confirmed live near-miss between "Consulting Project Team Lead -
        # Corporate Strategy" and "Senior Manager - Model Build & Data
        # Analytics" below — see
        # test_reinforced_active_role_family_outranks_candidate_history_only_job.
        preferred_skills=["business_strategy", "stakeholder_management", "financial_modelling"],
        included_industries=["telecom", "government"],
        min_experience_years=5,
        max_experience_years=15,
    )


def test_strong_strategy_group_outranks_weak_data_group() -> None:
    """The data/clinical titles here are false positives on generic
    "data"/"analytics" overlap and must stay below every genuinely relevant
    strategy title, regardless of which title/role-family evidence tier won
    for each. (D-033 note: this test previously also included "Business
    Analyst Trainee" in the weak group and asserted it stayed below the
    weakest strong job here — "Consulting Project Team Lead - Corporate
    Strategy", a role-family-only match. D-033's title/role_family
    aggregation fix correctly stopped halving exact title/alias matches
    with no separate role-family evidence, which also raises the trainee
    job's own exact `candidate_title_alias:'Business Analyst'` match — so a
    trainee posting can now legitimately outscore an unrelated job that
    only ever had weak role-family-only evidence. The trainee-specific
    invariant ("a trainee posting scores below the equivalent standard
    posting") is covered on its own, not against this heterogeneous group —
    see test_trainee_role_scores_below_standard_role_for_experienced_search
    and test_business_analyst_trainee_remains_below_standard_business_analyst_role.)"""
    candidate = _live_like_candidate()
    search = _live_like_search()
    hard_filter_result = HardFilterResult(passed=True, rejections=[])
    prefilter_result = PrefilterResult(score=0.9, passed_threshold=True)

    strong_jobs = [
        make_job(title="Strategy Manager", description_text="Corporate strategy team role."),
        make_job(
            title="Business Strategy Consultant",
            description_text="Business strategy consulting engagement.",
        ),
        make_job(
            title="Consulting Project Team Lead - Corporate Strategy",
            description_text="Leads corporate strategy consulting projects.",
        ),
    ]
    weak_jobs = [
        make_job(
            title="Lead - Clinical Data Manager / Data Analytics",
            description_text="Manages clinical data analytics operations.",
        ),
        make_job(
            title="Senior Manager - Model Build & Data Analytics",
            description_text="Builds risk models using financial modelling and data analytics.",
        ),
    ]

    strong_scores = [
        build_match_result(
            job, candidate, search, hard_filter_result, prefilter_result, _weights()
        ).final_score
        for job in strong_jobs
    ]
    weak_scores = [
        build_match_result(
            job, candidate, search, hard_filter_result, prefilter_result, _weights()
        ).final_score
        for job in weak_jobs
    ]

    assert all(score is not None for score in strong_scores)
    assert all(score is not None for score in weak_scores)
    assert min(strong_scores) > max(weak_scores)


def test_reinforced_active_role_family_outranks_candidate_history_only_job() -> None:
    """The specific confirmed live regression within the group above:
    "Consulting Project Team Lead - Corporate Strategy" (two distinct
    active SearchProfile role-family matches on the title — 'strategy' and
    'corporate_strategy', no configured title match) scored 12.5 under the
    pre-D-034 `role_score / 2` formula, *below* "Senior Manager - Model
    Build & Data Analytics" at 17.29 (candidate-only role-family evidence
    plus an incidental active preferred-skill match on 'financial_modelling')
    — a pure-candidate-history job outranking a job with reinforced active
    SearchProfile evidence. D-034's role-family aggregation fix corrects
    this specific pairing, not just the group's min/max invariant above."""
    candidate = _live_like_candidate()
    search = _live_like_search()
    hard_filter_result = HardFilterResult(passed=True, rejections=[])
    prefilter_result = PrefilterResult(score=0.9, passed_threshold=True)

    reinforced_active_role_job = build_match_result(
        make_job(
            title="Consulting Project Team Lead - Corporate Strategy",
            description_text="Leads corporate strategy consulting projects.",
        ),
        candidate,
        search,
        hard_filter_result,
        prefilter_result,
        _weights(),
    )
    candidate_history_job = build_match_result(
        make_job(
            title="Senior Manager - Model Build & Data Analytics",
            description_text="Builds risk models using financial modelling and data analytics.",
        ),
        candidate,
        search,
        hard_filter_result,
        prefilter_result,
        _weights(),
    )

    assert reinforced_active_role_job.final_score is not None
    assert candidate_history_job.final_score is not None
    assert reinforced_active_role_job.final_score > candidate_history_job.final_score
