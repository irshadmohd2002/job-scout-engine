"""Milestone 2 Deliverable 5 step 11 — evaluation dataset loading and
metric arithmetic (architecture.md section 22; decisions.md D-043/D-051).

Metric values below are verified by hand against the two tracked fixture
datasets under tests/fixtures/evaluation/ (strategy_chief_of_staff/,
software_engineering/) -- each dataset's per-fixture score/tier is derived
directly from Stage 1/2/5's documented formulas (matching/hard_filters.py,
matching/prefilter.py, matching/scoring.py) and is independently
reproducible: see this file's own assertions for the expected score of each
labelled fixture, spelled out one at a time before any aggregate metric is
computed from them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from job_scout.config import load_candidate_profile, load_scoring_weights, load_search_profiles
from job_scout.evaluation import (
    EvaluationDatasetError,
    load_evaluation_dataset,
    run_evaluation,
)
from job_scout.models import EvaluationLabel, Location, NotificationTier

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "evaluation"


def _load_group(name: str, profile_id: str):
    base = _FIXTURES_DIR / name
    candidate = load_candidate_profile(base / "candidate_profile.yaml")
    profiles = load_search_profiles(base / "search_profiles.yaml")
    search = profiles[profile_id]
    weights = load_scoring_weights(data_dir=Path("no-such-data-dir"))
    dataset = load_evaluation_dataset(base / "dataset.yaml")
    return dataset, candidate, search, weights


# --- load_evaluation_dataset -------------------------------------------------


def test_load_evaluation_dataset_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(EvaluationDatasetError, match="not found"):
        load_evaluation_dataset(tmp_path / "does_not_exist.yaml")


def test_load_evaluation_dataset_empty_fixtures_raises(tmp_path: Path) -> None:
    path = tmp_path / "dataset.yaml"
    path.write_text("fixtures: []\n", encoding="utf-8")
    with pytest.raises(EvaluationDatasetError, match="no fixtures"):
        load_evaluation_dataset(path)


def test_load_evaluation_dataset_malformed_yaml_raises(tmp_path: Path) -> None:
    path = tmp_path / "dataset.yaml"
    path.write_text("fixtures: [unterminated\n", encoding="utf-8")
    with pytest.raises(EvaluationDatasetError, match="Malformed YAML"):
        load_evaluation_dataset(path)


def test_load_evaluation_dataset_not_a_mapping_raises(tmp_path: Path) -> None:
    path = tmp_path / "dataset.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(EvaluationDatasetError, match="top-level 'fixtures'"):
        load_evaluation_dataset(path)


def test_load_evaluation_dataset_duplicate_job_id_raises(tmp_path: Path) -> None:
    duplicate = {
        "fixtures": [
            {
                "job_id": "dup",
                "title": "Title",
                "description": "Description",
                "company": "Co",
                "location": {"country": "GB"},
                "label": "strong_match",
                "rationale": "r",
            },
            {
                "job_id": "dup",
                "title": "Title 2",
                "description": "Description 2",
                "company": "Co",
                "location": {"country": "GB"},
                "label": "weak_match",
                "rationale": "r",
            },
        ]
    }
    path = tmp_path / "dataset.yaml"
    path.write_text(yaml.safe_dump(duplicate), encoding="utf-8")
    with pytest.raises(EvaluationDatasetError, match="Duplicate fixture job_id"):
        load_evaluation_dataset(path)


def test_load_evaluation_dataset_invalid_label_raises(tmp_path: Path) -> None:
    invalid = {
        "fixtures": [
            {
                "job_id": "a",
                "title": "Title",
                "description": "Description",
                "company": "Co",
                "location": {"country": "GB"},
                "label": "not_a_real_label",
                "rationale": "r",
            }
        ]
    }
    path = tmp_path / "dataset.yaml"
    path.write_text(yaml.safe_dump(invalid), encoding="utf-8")
    with pytest.raises(EvaluationDatasetError, match="Invalid dataset"):
        load_evaluation_dataset(path)


def test_load_evaluation_dataset_loads_tracked_strategy_dataset() -> None:
    dataset = load_evaluation_dataset(
        _FIXTURES_DIR / "strategy_chief_of_staff" / "dataset.yaml"
    )
    assert len(dataset) == 18
    labels = {f.label for f in dataset}
    assert labels == set(EvaluationLabel)


def test_load_evaluation_dataset_loads_tracked_software_dataset() -> None:
    dataset = load_evaluation_dataset(
        _FIXTURES_DIR / "software_engineering" / "dataset.yaml"
    )
    assert len(dataset) == 18
    labels = {f.label for f in dataset}
    assert labels == set(EvaluationLabel)


# --- run_evaluation: per-fixture scores/tiers, hand-verified ----------------
# One assertion per fixture before any aggregate metric is trusted -- this is
# the "hand-computed" bar MILESTONE_2.md's Testing strategy section asks for.


def test_strategy_dataset_per_fixture_scores_and_tiers_match_hand_computation() -> None:
    dataset, candidate, search, weights = _load_group(
        "strategy_chief_of_staff", "strategy_chief_of_staff_eval"
    )
    report = run_evaluation(dataset, candidate, search, weights)
    by_id = {r.job_id: r for r in report.fixture_results}

    # Strong matches: exact target_title/title_alias match plus every other
    # signal present -> clamp-adjacent score, PRIORITY tier.
    assert by_id["a-strong-1"].final_score == 88.0
    assert by_id["a-strong-1"].notification_tier == NotificationTier.PRIORITY
    assert by_id["a-strong-2"].final_score == 88.0
    assert by_id["a-strong-2"].notification_tier == NotificationTier.PRIORITY
    assert by_id["a-strong-3"].final_score == 88.0
    assert by_id["a-strong-3"].notification_tier == NotificationTier.PRIORITY

    # Adjacent matches: role-family-only title credit (0.70 alone-credit,
    # matching/scoring.py::_ACTIVE_ROLE_FAMILY_ALONE_CREDIT) plus most other
    # signals -> DIGEST or just below it, always below every strong match.
    assert by_id["a-adjacent-1"].final_score == 75.5
    assert by_id["a-adjacent-1"].notification_tier == NotificationTier.DIGEST
    assert by_id["a-adjacent-2"].final_score == 71.83
    assert by_id["a-adjacent-2"].notification_tier == NotificationTier.DIGEST
    assert by_id["a-adjacent-3"].final_score == 64.33
    assert by_id["a-adjacent-3"].notification_tier == NotificationTier.STORE_ONLY
    for label in ("a-adjacent-1", "a-adjacent-2", "a-adjacent-3"):
        assert by_id[label].final_score < 88.0

    # Weak matches: too little keyword overlap to clear the Stage 2
    # pre-filter threshold (0.15) -> never reach Stage 5, final_score None.
    for job_id in ("a-weak-1", "a-weak-2", "a-weak-3"):
        assert by_id[job_id].final_score is None
        assert by_id[job_id].notification_tier == NotificationTier.STORE_ONLY
        assert by_id[job_id].hard_filter_passed is True

    # Hard-filter rejects: three independent Stage 1 rules, each correctly
    # rejecting regardless of an otherwise-strong title match.
    for job_id in ("a-reject-1", "a-reject-2", "a-reject-3"):
        assert by_id[job_id].final_score is None
        assert by_id[job_id].notification_tier == NotificationTier.REJECTED
        assert by_id[job_id].hard_filter_passed is False

    # Deceptive false positives: a-deceptive-1/3 too thin to clear Stage 2;
    # a-deceptive-2 deliberately keyword-dense enough to clear Stage 2 and
    # reach a real (low) Stage 5 score -- but still well below the digest
    # threshold (70), so it is correctly never a false positive.
    assert by_id["a-deceptive-1"].final_score is None
    assert by_id["a-deceptive-3"].final_score is None
    assert by_id["a-deceptive-2"].final_score == 42.83
    assert by_id["a-deceptive-2"].notification_tier == NotificationTier.STORE_ONLY

    # D3 implementation fixtures (decisions.md D-057 "Open follow-up"): a
    # genuine zero-title/role-family-vocabulary role equivalent
    # (a-semrescue-1), a genuinely thin borderline case that stays
    # correctly too weak for Stage 2 (a-semrescue-2), and a deceptive
    # false positive using the same zero-title/role-family-vocabulary
    # technique (a-semrescue-3) -- see MILESTONE_3.md D3.
    assert by_id["a-semrescue-1"].final_score == 52.0
    assert by_id["a-semrescue-1"].notification_tier == NotificationTier.STORE_ONLY
    assert by_id["a-semrescue-1"].final_score < 88.0
    assert by_id["a-semrescue-2"].final_score is None
    assert by_id["a-semrescue-2"].notification_tier == NotificationTier.STORE_ONLY
    assert by_id["a-semrescue-2"].hard_filter_passed is True
    assert by_id["a-semrescue-3"].final_score == 39.5
    assert by_id["a-semrescue-3"].notification_tier == NotificationTier.STORE_ONLY


def test_strategy_dataset_aggregate_metrics_match_hand_computation() -> None:
    dataset, candidate, search, weights = _load_group(
        "strategy_chief_of_staff", "strategy_chief_of_staff_eval"
    )
    report = run_evaluation(dataset, candidate, search, weights)

    assert report.dataset_size == 18
    assert report.label_counts == {
        EvaluationLabel.STRONG_MATCH: 3,
        EvaluationLabel.ADJACENT_MATCH: 4,
        EvaluationLabel.WEAK_MATCH: 4,
        EvaluationLabel.HARD_FILTER_REJECT: 3,
        EvaluationLabel.DECEPTIVE_FALSE_POSITIVE: 4,
    }

    # precision@5: top 5 by score are the 3 strong (88 each) + 2 highest
    # adjacent (75.5, 71.83) -> 5/5 relevant.
    assert report.precision_at_5 == 1.0
    # precision@10: adds the 3rd adjacent (64.33), the semrescue-1 adjacent
    # (52.0), a-deceptive-2 (42.83), and a-semrescue-3 (39.5) -> 7 relevant
    # out of the top 10.
    assert report.precision_at_10 == 0.7
    # precision@20: dataset only has 18 fixtures (min(20, 18) = 18) -> the
    # same 7 relevant fixtures out of all 18.
    assert report.precision_at_20 == pytest.approx(7 / 18)

    # All 3 strong_match fixtures land in PRIORITY (a surfaced tier) -> perfect
    # recall.
    assert report.recall_of_strong_matches == 1.0

    # No deceptive_false_positive fixture lands in PRIORITY/DIGEST -> 0 false
    # positives, even though a-deceptive-2/a-semrescue-3 do reach a real
    # Stage 5 score.
    assert report.false_positive_rate == 0.0

    # All 3 hard_filter_reject fixtures are correctly rejected at Stage 1.
    assert report.hard_filter_correctness == 1.0

    # Deliberately inserted inversions: a-deceptive-2 (42.83) and
    # a-semrescue-3 (39.5, decisions.md D-057 "Open follow-up" fixture,
    # same technique) each reach a real score while every weak_match
    # fixture (including a-semrescue-2) is None (never reaches Stage 5) --
    # a deceptive_false_positive (label rank 3) outscoring a weak_match
    # (label rank 2) is exactly what the ranking-inversions metric exists
    # to catch.
    assert report.ranking_inversions == 8
    assert set(report.ranking_inversion_pairs) == {
        ("a-weak-1", "a-deceptive-2"),
        ("a-weak-2", "a-deceptive-2"),
        ("a-weak-3", "a-deceptive-2"),
        ("a-semrescue-2", "a-deceptive-2"),
        ("a-weak-1", "a-semrescue-3"),
        ("a-weak-2", "a-semrescue-3"),
        ("a-weak-3", "a-semrescue-3"),
        ("a-semrescue-2", "a-semrescue-3"),
    }
    # Every inversion pair has the deceptive fixture as the (wrongly)
    # higher-scoring one and a weak fixture as the (wrongly) lower-scoring
    # one, i.e. ranking_inversion_pairs entries are (higher_label_job,
    # lower_label_job) tuples -- here the *label*-higher job (weak) is
    # listed first, confirming run_evaluation reports which pair was
    # inverted, not just the count.
    assert {pair[0] for pair in report.ranking_inversion_pairs} == {
        "a-weak-1",
        "a-weak-2",
        "a-weak-3",
        "a-semrescue-2",
    }
    assert {pair[1] for pair in report.ranking_inversion_pairs} == {
        "a-deceptive-2",
        "a-semrescue-3",
    }


def test_software_dataset_aggregate_metrics_match_hand_computation() -> None:
    dataset, candidate, search, weights = _load_group(
        "software_engineering", "software_engineering_eval"
    )
    report = run_evaluation(dataset, candidate, search, weights)

    assert report.dataset_size == 18
    assert report.recall_of_strong_matches == 1.0
    assert report.false_positive_rate == 0.0
    assert report.hard_filter_correctness == 1.0
    assert report.precision_at_5 == 1.0
    assert report.precision_at_10 == 0.7
    assert report.precision_at_20 == pytest.approx(7 / 18)
    # decisions.md D-057 "Open follow-up" fixtures: b-semrescue-1 (adjacent,
    # zero title/role-family vocabulary overlap) adds no inversion (it
    # scores below every weak/deceptive fixture's rank expectation is not
    # violated); b-semrescue-3 (deceptive, same zero-vocabulary technique
    # as b-deceptive-2/3) reaches a real score against every weak_match
    # fixture, same pattern as the pre-existing b-deceptive-2/3 inversions.
    assert report.ranking_inversions == 12
    assert set(report.ranking_inversion_pairs) == {
        ("b-weak-1", "b-deceptive-2"),
        ("b-weak-1", "b-deceptive-3"),
        ("b-weak-2", "b-deceptive-2"),
        ("b-weak-2", "b-deceptive-3"),
        ("b-weak-3", "b-deceptive-2"),
        ("b-weak-3", "b-deceptive-3"),
        ("b-weak-1", "b-semrescue-3"),
        ("b-weak-2", "b-semrescue-3"),
        ("b-weak-3", "b-semrescue-3"),
        ("b-semrescue-2", "b-deceptive-2"),
        ("b-semrescue-2", "b-deceptive-3"),
        ("b-semrescue-2", "b-semrescue-3"),
    }


def test_two_profession_groups_together_cover_all_five_labels() -> None:
    """MILESTONE_2.md acceptance criterion: multiple professions, all five
    EvaluationLabel values represented."""
    strategy_dataset, *_ = _load_group(
        "strategy_chief_of_staff", "strategy_chief_of_staff_eval"
    )
    software_dataset, *_ = _load_group("software_engineering", "software_engineering_eval")

    assert {f.label for f in strategy_dataset} == set(EvaluationLabel)
    assert {f.label for f in software_dataset} == set(EvaluationLabel)
    # Materially different vocabulary, not the same fixtures relabelled.
    strategy_ids = {f.job_id for f in strategy_dataset}
    software_ids = {f.job_id for f in software_dataset}
    assert strategy_ids.isdisjoint(software_ids)


# --- Ranking-inversions detector, isolated ----------------------------------


def test_ranking_inversions_detects_deliberately_inserted_pair() -> None:
    """A minimal, isolated pair (not the full fixture dataset above):
    a deceptive_false_positive fixture engineered to outscore a strong_match
    fixture must be detected and counted -- the exact scenario
    MILESTONE_2.md's Testing strategy section asks for."""
    from job_scout.config import ScoringWeights
    from job_scout.models import EvaluationJobFixture
    from tests.factories import make_candidate_profile, make_search_profile

    candidate = make_candidate_profile(
        title_aliases=["Strategy Manager"], role_families=["strategy and planning"]
    )
    search = make_search_profile(target_titles=["Strategy Manager"])
    weights = ScoringWeights(
        title_role_family=0.25,
        responsibilities=0.15,
        required_skills=0.20,
        transferable_skills=0.10,
        seniority_experience=0.10,
        sector_relevance=0.10,
        education=0.05,
        visa_relocation=0.05,
        prefilter_threshold=0.15,
    )
    weak_but_top_ranked = EvaluationJobFixture(
        job_id="weak-but-top-ranked",
        title="Strategy Manager",
        description="Strategy Manager role.",
        company="Example Co",
        location=Location(country="GB"),
        label=EvaluationLabel.STRONG_MATCH,
        rationale="Exact title match, deliberately given a thin description so its raw score "
        "is lower than the deceptive fixture below -- exercises the detector.",
    )
    deceptive_but_bottom_ranked = EvaluationJobFixture(
        job_id="deceptive-but-bottom-ranked",
        title="Strategy Manager",
        description="Strategy Manager role. Strategy Manager. Strategy Manager. Strategy and "
        "planning. Strategy and planning. Strategy and planning.",
        company="Example Co",
        location=Location(country="GB"),
        label=EvaluationLabel.DECEPTIVE_FALSE_POSITIVE,
        rationale="Deliberately keyword-stuffed to score no lower than the strong_match fixture "
        "above despite being labelled a false positive -- proves the detector fires.",
    )
    report = run_evaluation(
        [weak_but_top_ranked, deceptive_but_bottom_ranked], candidate, search, weights
    )
    by_id = {r.job_id: r for r in report.fixture_results}
    # weak-but-top-ranked (strong_match, thin description): 35.0
    # deceptive-but-bottom-ranked (deceptive_false_positive, keyword-stuffed): 36.0
    assert by_id["weak-but-top-ranked"].final_score == 35.0
    assert by_id["deceptive-but-bottom-ranked"].final_score == 36.0
    assert report.ranking_inversions == 1
    assert report.ranking_inversion_pairs == [
        ("weak-but-top-ranked", "deceptive-but-bottom-ranked")
    ]


def test_ranking_inversions_zero_when_labels_score_in_order() -> None:
    from job_scout.config import ScoringWeights
    from job_scout.models import EvaluationJobFixture
    from tests.factories import make_candidate_profile, make_search_profile

    candidate = make_candidate_profile()
    search = make_search_profile()
    weights = ScoringWeights(
        title_role_family=0.25,
        responsibilities=0.15,
        required_skills=0.20,
        transferable_skills=0.10,
        seniority_experience=0.10,
        sector_relevance=0.10,
        education=0.05,
        visa_relocation=0.05,
        prefilter_threshold=0.15,
    )
    strong = EvaluationJobFixture(
        job_id="strong",
        title="Strategy Manager",
        description="Strategy Manager, transformation manager role.",
        company="Example Co",
        location=Location(country="GB"),
        label=EvaluationLabel.STRONG_MATCH,
        rationale="r",
    )
    unrelated = EvaluationJobFixture(
        job_id="unrelated",
        title="Warehouse Operative",
        description="Loads and unloads delivery trucks.",
        company="Example Co",
        location=Location(country="GB"),
        label=EvaluationLabel.WEAK_MATCH,
        rationale="r",
    )
    report = run_evaluation([strong, unrelated], candidate, search, weights)
    assert report.ranking_inversions == 0
    assert report.ranking_inversion_pairs == []


# --- None-final_score ranking treatment (decisions.md D-051) ----------------


def test_none_scored_fixture_never_outranks_a_scored_fixture() -> None:
    """A fixture whose final_score is None (Stage 1/2 rejected) must never
    count toward precision@k ahead of a fixture that reached a real score,
    regardless of dataset order."""
    from job_scout.config import ScoringWeights
    from job_scout.models import EvaluationJobFixture
    from tests.factories import make_candidate_profile, make_search_profile

    candidate = make_candidate_profile()
    search = make_search_profile()
    weights = ScoringWeights(
        title_role_family=0.25,
        responsibilities=0.15,
        required_skills=0.20,
        transferable_skills=0.10,
        seniority_experience=0.10,
        sector_relevance=0.10,
        education=0.05,
        visa_relocation=0.05,
        prefilter_threshold=0.15,
    )
    rejected = EvaluationJobFixture(
        job_id="a-rejected-first-in-file",
        title="Strategy Manager",
        description="US citizens only. Strategy Manager role.",
        company="Example Co",
        location=Location(country="GB"),
        label=EvaluationLabel.HARD_FILTER_REJECT,
        rationale="r",
    )
    scored = EvaluationJobFixture(
        job_id="z-scored-last-in-file",
        title="Strategy Manager",
        description="Strategy Manager, transformation manager role.",
        company="Example Co",
        location=Location(country="GB"),
        label=EvaluationLabel.STRONG_MATCH,
        rationale="r",
    )
    report = run_evaluation([rejected, scored], candidate, search, weights)
    assert report.precision_at_5 == 0.5  # only the scored fixture is strong/adjacent
    top1 = sorted(
        report.fixture_results,
        key=lambda r: (
            -(r.final_score if r.final_score is not None else -1.0),
            r.job_id,
        ),
    )[0]
    assert top1.job_id == "z-scored-last-in-file"
