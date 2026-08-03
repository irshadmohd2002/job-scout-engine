from job_scout import config
from job_scout.matching.prefilter import run_prefilter
from tests.factories import make_candidate_profile, make_job


def _weights() -> config.ScoringWeights:
    return config.load_scoring_weights()


def test_title_alias_match_scores_higher_than_no_match() -> None:
    candidate = make_candidate_profile(title_aliases=["Strategy Manager"])
    matching_job = make_job(
        title="Strategy Manager", description_text="Lead strategic initiatives."
    )
    non_matching_job = make_job(title="Software Engineer", description_text="Write backend code.")

    matching = run_prefilter(matching_job, candidate, _weights())
    non_matching = run_prefilter(non_matching_job, candidate, _weights())

    assert matching.score > non_matching.score


def test_role_family_overlap_contributes_to_score_and_hints() -> None:
    candidate = make_candidate_profile(
        title_aliases=["Strategy Manager"], role_families=["transformation", "chief_of_staff"]
    )
    job = make_job(
        title="Business Lead",
        description_text="This role focuses on transformation and chief of staff duties.",
    )
    result = run_prefilter(job, candidate, _weights())
    assert "transformation" in result.role_family_hints
    assert "chief_of_staff" in result.role_family_hints
    assert result.score > 0


def test_below_threshold_jobs_still_produce_a_result_not_scored_further() -> None:
    candidate = make_candidate_profile(
        title_aliases=["Strategy Manager"],
        role_families=["transformation"],
        primary_skills=["strategy_development"],
        secondary_skills=["financial_modelling"],
    )
    job = make_job(title="Warehouse Associate", description_text="Pack boxes and load trucks.")
    result = run_prefilter(job, candidate, _weights())
    assert result.passed_threshold is False
    assert result.score < _weights().prefilter_threshold
