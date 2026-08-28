"""`job-scout evaluate` CLI (Milestone 2 Deliverable 5 step 11;
MILESTONE_2.md acceptance criterion: runs against a labelled fixture
dataset spanning multiple professions and all five EvaluationLabel values,
prints deterministic precision@5/@10/@20, recall, false-positive rate,
hard-filter correctness, ranking inversions, and threshold-tier
distribution -- described as relevance scores, never probabilities."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from job_scout.cli import app

runner = CliRunner()

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "evaluation"
_STRATEGY_DIR = _FIXTURES_DIR / "strategy_chief_of_staff"
_SOFTWARE_DIR = _FIXTURES_DIR / "software_engineering"


def _invoke(base: Path, profile: str, *, data_dir: Path, extra: list[str] | None = None) -> object:
    args = [
        "evaluate",
        "--profile",
        profile,
        "--dataset",
        str(base / "dataset.yaml"),
        "--candidate-profile",
        str(base / "candidate_profile.yaml"),
        "--search-profiles",
        str(base / "search_profiles.yaml"),
        "--data-dir",
        str(data_dir),
    ]
    if extra:
        args.extend(extra)
    return runner.invoke(app, args)


def test_evaluate_human_output_strategy_dataset(tmp_path: Path) -> None:
    result = _invoke(
        _STRATEGY_DIR, "strategy_chief_of_staff_eval", data_dir=tmp_path / "data"
    )
    assert result.exit_code == 0, result.output
    assert "fixtures: 15" in result.output
    assert "precision@5=1.000" in result.output
    assert "precision@10=0.600" in result.output
    assert "precision@20=0.400" in result.output
    assert "recall_of_strong_matches=1.000" in result.output
    assert "false_positive_rate=0.000" in result.output
    assert "hard_filter_correctness=1.000" in result.output
    assert "ranking_inversions=3" in result.output


def test_evaluate_human_output_software_dataset(tmp_path: Path) -> None:
    result = _invoke(_SOFTWARE_DIR, "software_engineering_eval", data_dir=tmp_path / "data")
    assert result.exit_code == 0, result.output
    assert "fixtures: 15" in result.output
    assert "ranking_inversions=6" in result.output


def test_evaluate_json_output_matches_report_shape(tmp_path: Path) -> None:
    result = _invoke(
        _STRATEGY_DIR,
        "strategy_chief_of_staff_eval",
        data_dir=tmp_path / "data",
        extra=["--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dataset_size"] == 15
    assert payload["precision_at_5"] == 1.0
    assert payload["precision_at_10"] == 0.6
    assert payload["precision_at_20"] == 0.4
    assert payload["recall_of_strong_matches"] == 1.0
    assert payload["false_positive_rate"] == 0.0
    assert payload["hard_filter_correctness"] == 1.0
    assert payload["ranking_inversions"] == 3
    assert set(payload["label_counts"].keys()) == {
        "strong_match",
        "adjacent_match",
        "weak_match",
        "hard_filter_reject",
        "deceptive_false_positive",
    }
    assert len(payload["fixture_results"]) == 15


def test_evaluate_output_never_says_probability(tmp_path: Path) -> None:
    """decisions.md D-043: final_score/ScoreComponent values are relevance
    scores, never a probability or confidence percentage -- and
    job-scout evaluate's own output must never use the word "probability"."""
    human_result = _invoke(
        _STRATEGY_DIR, "strategy_chief_of_staff_eval", data_dir=tmp_path / "data"
    )
    assert human_result.exit_code == 0, human_result.output
    assert "probability" not in human_result.output.lower()
    assert "relevance score" in human_result.output.lower()

    json_result = _invoke(
        _STRATEGY_DIR,
        "strategy_chief_of_staff_eval",
        data_dir=tmp_path / "data2",
        extra=["--json"],
    )
    assert json_result.exit_code == 0, json_result.output
    assert "probability" not in json_result.output.lower()


def test_evaluate_unknown_profile_errors(tmp_path: Path) -> None:
    result = _invoke(_STRATEGY_DIR, "no-such-profile", data_dir=tmp_path / "data")
    assert result.exit_code == 1
    assert "unknown search profile" in result.output.lower()


def test_evaluate_missing_dataset_file_errors(tmp_path: Path) -> None:
    args = [
        "evaluate",
        "--profile",
        "strategy_chief_of_staff_eval",
        "--dataset",
        str(tmp_path / "does_not_exist.yaml"),
        "--candidate-profile",
        str(_STRATEGY_DIR / "candidate_profile.yaml"),
        "--search-profiles",
        str(_STRATEGY_DIR / "search_profiles.yaml"),
        "--data-dir",
        str(tmp_path / "data"),
    ]
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_evaluate_never_calls_a_source_adapter_or_touches_the_network(
    tmp_path: Path, monkeypatch
) -> None:
    """Read-only calibration report: no adapter import/call anywhere in the
    evaluate command path."""
    import httpx

    def _forbidden_request(*args: object, **kwargs: object) -> None:
        raise AssertionError("job-scout evaluate must never perform an HTTP request")

    monkeypatch.setattr(httpx.Client, "send", _forbidden_request)
    result = _invoke(
        _STRATEGY_DIR, "strategy_chief_of_staff_eval", data_dir=tmp_path / "data"
    )
    assert result.exit_code == 0, result.output
