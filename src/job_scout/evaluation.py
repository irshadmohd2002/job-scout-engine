"""Milestone 2 Deliverable 5 step 11 — offline score-calibration tool
(architecture.md section 22; decisions.md D-043/D-051; MILESTONE_2.md
"Evaluation dataset and calibration design").

`run_evaluation` is a pure function over the existing Stage 1/2/5 matching
functions (`matching.hard_filters.evaluate_hard_filters`,
`matching.prefilter.run_prefilter`, `matching.scoring.build_match_result`) —
no pipeline, network, or persistence involvement, exactly like every other
Stage 1/2/5 caller. `load_evaluation_dataset` is the one I/O boundary,
kept separate so `run_evaluation` itself stays trivially testable in memory.

`final_score`/`ScoreComponent` values are **relevance scores** — a
deterministic, weighted-sum ranking signal — never a probability or
confidence percentage. Every string this module produces (report fields,
`job-scout evaluate` output) must keep calling them that.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from job_scout.config import ScoringWeights
from job_scout.deduplication import compute_fingerprint, normalize_company, normalize_title
from job_scout.matching.hard_filters import evaluate_hard_filters
from job_scout.matching.prefilter import PrefilterWeights, run_prefilter
from job_scout.matching.scoring import build_match_result
from job_scout.models import (
    CandidateProfile,
    EvaluationJobFixture,
    EvaluationLabel,
    Job,
    NotificationTier,
    SearchProfile,
)

# Fixed, deterministic fallback for Job.collected_at when a fixture supplies
# no posted_at — an evaluation fixture is offline test data, never a real
# fetch, so a real "now" timestamp would make report output non-reproducible
# from one run to the next for no benefit.
_FIXTURE_COLLECTED_AT = datetime(2026, 1, 1, tzinfo=UTC)

# Label precedence for the ranking-inversions metric (decisions.md D-043):
# strong_match > adjacent_match > weak_match > deceptive_false_positive /
# hard_filter_reject — the last two are equally "should rank lowest", not
# ordered relative to each other.
_LABEL_RANK: dict[EvaluationLabel, int] = {
    EvaluationLabel.STRONG_MATCH: 0,
    EvaluationLabel.ADJACENT_MATCH: 1,
    EvaluationLabel.WEAK_MATCH: 2,
    EvaluationLabel.HARD_FILTER_REJECT: 3,
    EvaluationLabel.DECEPTIVE_FALSE_POSITIVE: 3,
}

_SURFACED_TIERS = (NotificationTier.PRIORITY, NotificationTier.DIGEST)


class EvaluationDatasetError(Exception):
    """Raised for any problem loading/parsing an evaluation dataset file.
    Carries enough detail for the CLI to print a concise, actionable
    message without a traceback (same shape as config.py's ConfigError)."""

    def __init__(self, message: str, *, file: Path | str | None = None) -> None:
        self.file = str(file) if file is not None else None
        parts = [message]
        if file is not None:
            parts.append(f"File: {file}")
        super().__init__(" | ".join(parts))


class _EvaluationDatasetFile(BaseModel):
    fixtures: list[EvaluationJobFixture] = []


def load_evaluation_dataset(path: Path) -> list[EvaluationJobFixture]:
    """Parses a `--dataset` YAML file (`{fixtures: [...]}`, one entry per
    `EvaluationJobFixture`) into a list of fixtures. Never touches the
    network or a database — a labelled dataset is local, offline test data
    (MILESTONE_2.md "Configuration changes": supplied via `--dataset`, not a
    runtime YAML config surface)."""
    if not path.exists():
        raise EvaluationDatasetError("Evaluation dataset file not found.", file=path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvaluationDatasetError(f"Could not read dataset file: {exc}", file=path) from exc
    try:
        data: Any = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise EvaluationDatasetError(f"Malformed YAML: {exc}", file=path) from exc
    if not isinstance(data, dict):
        raise EvaluationDatasetError(
            "Dataset file must contain a YAML mapping with a top-level 'fixtures' list.",
            file=path,
        )
    try:
        parsed = _EvaluationDatasetFile.model_validate(data)
    except ValidationError as exc:
        raise EvaluationDatasetError(f"Invalid dataset: {exc}", file=path) from exc

    seen: set[str] = set()
    for fixture in parsed.fixtures:
        if fixture.job_id in seen:
            raise EvaluationDatasetError(
                f"Duplicate fixture job_id '{fixture.job_id}'.", file=path
            )
        seen.add(fixture.job_id)
    if not parsed.fixtures:
        raise EvaluationDatasetError("Dataset file contains no fixtures.", file=path)
    return parsed.fixtures


def _fixture_to_job(fixture: EvaluationJobFixture) -> Job:
    """Builds the canonical `Job` model (decisions.md D-040) an
    `EvaluationJobFixture` represents, reusing the exact same normalisation
    helpers (`deduplication.normalize_title`/`normalize_company`/
    `compute_fingerprint`) every real adapter's normalizer uses — an
    evaluation fixture is a synthetic job posting, not a second normalized
    representation."""
    fingerprint = compute_fingerprint(
        source_id="evaluation_fixture",
        external_id=fixture.job_id,
        raw_url=f"https://evaluation.local/fixtures/{fixture.job_id}",
        company=fixture.company,
        title=fixture.title,
        location=fixture.location,
        description_text=fixture.description,
        posted_at=fixture.posted_at,
    )
    return Job(
        job_id=fixture.job_id,
        title=fixture.title,
        normalized_title=normalize_title(fixture.title),
        company=fixture.company,
        normalized_company=normalize_company(fixture.company),
        location=fixture.location,
        employment_type=fixture.employment_type,
        description_raw=fixture.description,
        description_text=fixture.description,
        posted_at=fixture.posted_at,
        collected_at=fixture.posted_at or _FIXTURE_COLLECTED_AT,
        fingerprint=fingerprint,
    )


class EvaluationFixtureResult(BaseModel):
    """Per-fixture outcome — the transparency/evidence trail behind the
    aggregate metrics below (CLAUDE.md hard constraint 5: never collapse to
    a bare number)."""

    job_id: str
    label: EvaluationLabel
    rationale: str
    hard_filter_passed: bool
    final_score: float | None
    notification_tier: NotificationTier


class EvaluationReport(BaseModel):
    """Return value of `run_evaluation` (architecture.md section 22).
    `final_score`/component values folded into these metrics are relevance
    scores, not probabilities — see this module's docstring."""

    dataset_size: int
    label_counts: dict[EvaluationLabel, int]
    precision_at_5: float
    precision_at_10: float
    precision_at_20: float
    recall_of_strong_matches: float
    false_positive_rate: float
    hard_filter_correctness: float
    ranking_inversions: int
    ranking_inversion_pairs: list[tuple[str, str]]
    tier_distribution: dict[EvaluationLabel, dict[NotificationTier, int]]
    fixture_results: list[EvaluationFixtureResult]


def _effective_score(result: EvaluationFixtureResult) -> float:
    """Ranking key for precision@k / ranking-inversions (decisions.md
    D-051): a fixture whose `final_score` is None (rejected at Stage 1, or
    filtered out at Stage 2) never outranks any fixture that actually
    reached Stage 5 scoring — the same job a real run would never surface
    above a scored one either, since it lands in REJECTED/STORE_ONLY
    regardless of any hypothetical score. -1.0 sorts below the clamped
    [0, 100] range every real `final_score` lives in."""
    return result.final_score if result.final_score is not None else -1.0


def _rank_sort_key(result: EvaluationFixtureResult) -> tuple[float, str]:
    # Descending by effective score, ascending by job_id as a deterministic
    # tie-break (two fixtures never compare equal-and-unordered).
    return (-_effective_score(result), result.job_id)


def run_evaluation(
    dataset: list[EvaluationJobFixture],
    candidate: CandidateProfile,
    search: SearchProfile,
    weights: ScoringWeights,
) -> EvaluationReport:
    prefilter_weights = PrefilterWeights.from_scoring_weights(weights)
    fixture_results: list[EvaluationFixtureResult] = []
    for fixture in dataset:
        job = _fixture_to_job(fixture)
        hard_filter_result = evaluate_hard_filters(job, candidate, search)
        prefilter_result = run_prefilter(job, candidate, search, prefilter_weights)
        match_result = build_match_result(
            job, candidate, search, hard_filter_result, prefilter_result, weights
        )
        fixture_results.append(
            EvaluationFixtureResult(
                job_id=fixture.job_id,
                label=fixture.label,
                rationale=fixture.rationale,
                hard_filter_passed=hard_filter_result.passed,
                final_score=match_result.final_score,
                notification_tier=match_result.notification_tier,
            )
        )

    ranked = sorted(fixture_results, key=_rank_sort_key)

    def _precision_at(k: int) -> float:
        top_k = ranked[: min(k, len(ranked))]
        if not top_k:
            return 0.0
        relevant = sum(
            1
            for r in top_k
            if r.label in (EvaluationLabel.STRONG_MATCH, EvaluationLabel.ADJACENT_MATCH)
        )
        return relevant / len(top_k)

    strong = [r for r in fixture_results if r.label == EvaluationLabel.STRONG_MATCH]
    recalled = sum(1 for r in strong if r.notification_tier in _SURFACED_TIERS)
    recall_of_strong_matches = (recalled / len(strong)) if strong else 1.0

    deceptive = [
        r for r in fixture_results if r.label == EvaluationLabel.DECEPTIVE_FALSE_POSITIVE
    ]
    false_positives = sum(1 for r in deceptive if r.notification_tier in _SURFACED_TIERS)
    false_positive_rate = (false_positives / len(deceptive)) if deceptive else 0.0

    hard_reject = [r for r in fixture_results if r.label == EvaluationLabel.HARD_FILTER_REJECT]
    correctly_rejected = sum(1 for r in hard_reject if not r.hard_filter_passed)
    hard_filter_correctness = (correctly_rejected / len(hard_reject)) if hard_reject else 1.0

    ranking_inversion_pairs: list[tuple[str, str]] = []
    for i, a in enumerate(fixture_results):
        for b in fixture_results[i + 1 :]:
            rank_a, rank_b = _LABEL_RANK[a.label], _LABEL_RANK[b.label]
            if rank_a == rank_b:
                continue
            higher, lower = (a, b) if rank_a < rank_b else (b, a)
            if _effective_score(higher) < _effective_score(lower):
                ranking_inversion_pairs.append((higher.job_id, lower.job_id))

    tier_distribution: dict[EvaluationLabel, dict[NotificationTier, int]] = {
        label: {tier: 0 for tier in NotificationTier} for label in EvaluationLabel
    }
    label_counts: dict[EvaluationLabel, int] = dict.fromkeys(EvaluationLabel, 0)
    for r in fixture_results:
        tier_distribution[r.label][r.notification_tier] += 1
        label_counts[r.label] += 1

    return EvaluationReport(
        dataset_size=len(dataset),
        label_counts=label_counts,
        precision_at_5=_precision_at(5),
        precision_at_10=_precision_at(10),
        precision_at_20=_precision_at(20),
        recall_of_strong_matches=recall_of_strong_matches,
        false_positive_rate=false_positive_rate,
        hard_filter_correctness=hard_filter_correctness,
        ranking_inversions=len(ranking_inversion_pairs),
        ranking_inversion_pairs=ranking_inversion_pairs,
        tier_distribution=tier_distribution,
        fixture_results=fixture_results,
    )
