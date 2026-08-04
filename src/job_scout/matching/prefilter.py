"""Stage 2 — cheap deterministic pre-filter (architecture.md section 10).

High-recall, cheap-to-evaluate gate — not the final ranking model (that's
Stage 5, scoring.py). A job reaches Stage 5 scoring if *either*:

1. **Strong title/role evidence gate**: a configured target title, title
   alias, or role family (from `CandidateProfile` *or* `SearchProfile`)
   strongly matches the job *title* — either as an exact normalised phrase,
   or via token coverage above `PrefilterWeights.strong_title_coverage`
   (structural safeguard: a multi-word configured phrase needs most/all of
   its words present, not just one common one — see `_strong_title_matches`
   docstring). A single-word configured phrase can still gate on its own;
   that's only ever a risk the candidate/search profile author chose by
   configuring a bare one-word target, never something this module invents.
2. **Weighted additive score** (the original Milestone 1 design, extended):
   keyword/phrase overlap across title_aliases, role_families,
   primary/secondary/transferable skills, included_keywords, and
   industries/sectors — from both `CandidateProfile` and `SearchProfile` —
   weighted, with title-field evidence counted more than description-only
   evidence, compared against `PrefilterWeights.threshold`.

Sector/industry terms remain a soft, non-gating signal here — Stage 5's
`sector_relevance` component is still the authoritative slot for that
(architecture.md section 10, Stage 5 table); this module only folds
industries/sectors into the weighted fallback score, never the strong gate.
"""

from __future__ import annotations

from job_scout.config import ScoringWeights
from job_scout.matching.normalize import dedupe_preserve_order, normalize_text
from job_scout.models import CandidateProfile, Job, PrefilterResult, SearchProfile


class PrefilterWeights:
    """Stage 2 category weights + gate constants. Deliberately not YAML
    config, unlike `ScoringWeights.prefilter_threshold` (which the caller
    still supplies via `from_scoring_weights` below): architecture.md never
    calls for these category weights to be an independently user-tunable
    knob, only the pass/fail threshold is — same rationale the original
    module-private `_TITLE_WEIGHT`-style constants used."""

    def __init__(
        self,
        *,
        title: float = 0.35,
        role_family: float = 0.30,
        skills: float = 0.20,
        keywords: float = 0.05,
        context: float = 0.05,
        seniority: float = 0.05,
        threshold: float = 0.15,
        desc_only_damping: float = 0.5,
        strong_title_coverage: float = 0.75,
    ) -> None:
        self.title = title
        self.role_family = role_family
        self.skills = skills
        self.keywords = keywords
        self.context = context
        self.seniority = seniority
        self.threshold = threshold
        self.desc_only_damping = desc_only_damping
        self.strong_title_coverage = strong_title_coverage

    @classmethod
    def from_scoring_weights(cls, weights: ScoringWeights) -> PrefilterWeights:
        """The only piece of `ScoringWeights` this stage reads is the
        user-tunable `prefilter_threshold`; every other field here keeps its
        fixed default."""
        return cls(threshold=weights.prefilter_threshold)


DEFAULT_PREFILTER_WEIGHTS = PrefilterWeights()


def keyword_overlap(items: list[str], text: str) -> list[str]:
    """Punctuation/case-normalised substring match of each item against
    `text` (see matching/normalize.py — ampersands, slashes, hyphens,
    underscores, other punctuation, and case are all folded before
    comparison). Returns the original (pre-normalisation) items that
    matched, so callers/evidence keep the configured spelling."""
    normalized_text = normalize_text(text)
    if not normalized_text:
        return []
    matches = []
    for item in items:
        normalized_item = normalize_text(item)
        if normalized_item and normalized_item in normalized_text:
            matches.append(item)
    return matches


def _title_and_description_matches(
    items: list[str], title_norm: str, description_norm: str
) -> tuple[list[str], list[str]]:
    """Splits `items` into (matched in the job title, matched only in the
    description) — the title/description distinction the strong gate and
    the title-weighted fallback score both need."""
    in_title: list[str] = []
    description_only: list[str] = []
    for item in items:
        normalized_item = normalize_text(item)
        if not normalized_item:
            continue
        if normalized_item in title_norm:
            in_title.append(item)
        elif description_norm and normalized_item in description_norm:
            description_only.append(item)
    return in_title, description_only


def _category_raw(
    items: list[str], title_norm: str, description_norm: str, desc_only_damping: float
) -> tuple[float, list[str], list[str]]:
    """Weighted-fallback ratio for one evidence category: a title match
    counts fully, a description-only match counts at `desc_only_damping`
    (< 1.0) — title-field evidence outweighs description-only evidence, as
    required for the strong gate's little sibling, the additive score."""
    if not items:
        return 0.0, [], []
    in_title, description_only = _title_and_description_matches(items, title_norm, description_norm)
    raw = (len(in_title) + desc_only_damping * len(description_only)) / len(items)
    return min(raw, 1.0), in_title, description_only


def _phrase_token_coverage(phrase_norm: str, title_tokens: set[str]) -> float:
    tokens = phrase_norm.split(" ")
    if not tokens:
        return 0.0
    matched = sum(1 for token in tokens if token in title_tokens)
    return matched / len(tokens)


def _strong_title_matches(
    labeled_phrases: list[tuple[str, str]],
    title_norm: str,
    title_tokens: set[str],
    coverage_threshold: float,
) -> list[str]:
    """Evidence strings for every configured phrase that strongly matches
    the job *title* — never the description; per the strong-gate design,
    only title-field evidence is allowed to pass a job on a single signal.

    A phrase counts as a strong match if either:
      - its full normalised form is a contiguous substring of the
        normalised title ("exact_phrase"), or
      - the fraction of its own tokens present anywhere in the title's
        token set is >= `coverage_threshold` ("token_coverage").

    The token-coverage ratio is the structural safeguard against a single
    generic word completing a multi-word phrase: a 2-word configured phrase
    needs *both* words (1/2 = 0.5 is below any reasonable threshold), a
    3-word phrase needs all 3, a 4-word phrase tolerates at most one missing
    word — all without a hand-maintained stopword list. A deliberately
    single-word configured phrase (1 token) still matches on its own; that
    is the phrase author's explicit choice, not a generic-token loophole.
    """
    evidence: list[str] = []
    for label, phrase in labeled_phrases:
        phrase_norm = normalize_text(phrase)
        if not phrase_norm:
            continue
        if phrase_norm in title_norm:
            evidence.append(
                f"strong_title_match:{label}:'{phrase}' field=title method=exact_phrase"
            )
            continue
        coverage = _phrase_token_coverage(phrase_norm, title_tokens)
        if coverage >= coverage_threshold:
            evidence.append(
                f"strong_title_match:{label}:'{phrase}' field=title "
                f"method=token_coverage coverage={coverage:.2f}"
            )
    return evidence


def run_prefilter(
    job: Job,
    candidate: CandidateProfile,
    search: SearchProfile,
    weights: PrefilterWeights,
) -> PrefilterResult:
    title_norm = normalize_text(job.title)
    description_norm = normalize_text(job.description_text)
    title_tokens = set(title_norm.split(" ")) if title_norm else set()

    # --- strong title/role evidence gate (Stage 2 principle: a strong
    # title-field signal alone should normally be enough) -----------------
    gate_phrases: list[tuple[str, str]] = (
        [("candidate_title_alias", phrase) for phrase in candidate.title_aliases]
        + [("candidate_role_family", phrase) for phrase in candidate.role_families]
        + [("search_target_title", phrase) for phrase in search.target_titles]
        + [("search_title_alias", phrase) for phrase in search.title_aliases]
        + [("search_role_family", phrase) for phrase in search.role_families]
    )
    strong_evidence = _strong_title_matches(
        gate_phrases, title_norm, title_tokens, weights.strong_title_coverage
    )
    strong_pass = bool(strong_evidence)

    # --- weighted additive fallback score ---------------------------------
    title_universe = dedupe_preserve_order(
        [*candidate.title_aliases, *search.target_titles, *search.title_aliases]
    )
    role_universe = dedupe_preserve_order([*candidate.role_families, *search.role_families])
    skill_universe = dedupe_preserve_order(
        [
            *candidate.primary_skills,
            *candidate.secondary_skills,
            *candidate.transferable_skills,
            *search.required_skills,
            *search.preferred_skills,
            *search.transferable_skills,
        ]
    )
    keyword_universe = dedupe_preserve_order(list(search.included_keywords))
    context_universe = dedupe_preserve_order(
        [
            *candidate.industries,
            *candidate.sectors,
            *search.included_industries,
            *search.included_sectors,
        ]
    )

    title_raw, title_in_title, title_desc_only = _category_raw(
        title_universe, title_norm, description_norm, weights.desc_only_damping
    )
    role_raw, role_in_title, role_desc_only = _category_raw(
        role_universe, title_norm, description_norm, weights.desc_only_damping
    )
    skill_raw, skill_in_title, skill_desc_only = _category_raw(
        skill_universe, title_norm, description_norm, weights.desc_only_damping
    )
    keyword_raw, keyword_in_title, keyword_desc_only = _category_raw(
        keyword_universe, title_norm, description_norm, weights.desc_only_damping
    )
    context_raw, context_in_title, context_desc_only = _category_raw(
        context_universe, title_norm, description_norm, weights.desc_only_damping
    )

    seniority_term = candidate.effective_seniority_text()
    seniority_norm = normalize_text(seniority_term) if seniority_term else ""
    combined_norm = f"{title_norm} {description_norm}".strip()
    seniority_matched = bool(seniority_norm) and seniority_norm in combined_norm

    score = round(
        weights.title * title_raw
        + weights.role_family * role_raw
        + weights.skills * skill_raw
        + weights.keywords * keyword_raw
        + weights.context * context_raw
        + weights.seniority * (1.0 if seniority_matched else 0.0),
        6,
    )

    evidence: list[str] = list(strong_evidence)
    evidence += [f"title:{m} (title)" for m in title_in_title]
    evidence += [f"title:{m} (description)" for m in title_desc_only]
    evidence += [f"role_family:{m} (title)" for m in role_in_title]
    evidence += [f"role_family:{m} (description)" for m in role_desc_only]
    evidence += [f"skill:{m} (title)" for m in skill_in_title]
    evidence += [f"skill:{m} (description)" for m in skill_desc_only]
    evidence += [f"keyword:{m} (title)" for m in keyword_in_title]
    evidence += [f"keyword:{m} (description)" for m in keyword_desc_only]
    evidence += [f"context:{m} (title)" for m in context_in_title]
    evidence += [f"context:{m} (description)" for m in context_desc_only]
    if seniority_matched:
        evidence.append(f"seniority:{seniority_term}")

    passed = strong_pass or score >= weights.threshold
    if strong_pass:
        evidence.append("decision:passed via strong_title_match")
    elif passed:
        evidence.append(
            f"decision:passed via weighted_score={score:.3f} >= threshold={weights.threshold:.3f}"
        )
    else:
        evidence.append(
            f"decision:failed weighted_score={score:.3f} < threshold={weights.threshold:.3f} "
            "and no strong title match"
        )

    role_family_hints = dedupe_preserve_order([*role_in_title, *role_desc_only])

    return PrefilterResult(
        score=score,
        passed_threshold=passed,
        role_family_hints=role_family_hints,
        evidence=evidence,
    )
