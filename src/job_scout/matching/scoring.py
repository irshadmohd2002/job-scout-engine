"""Stage 5 — transparent final scoring (architecture.md section 10).

Only Stage 1 rejects (decisions.md D-005); this stage can score a job
arbitrarily low but never sets notification_tier: rejected. A job below the
Stage 2 pre-filter threshold is persisted with store_only treatment and
never reaches Stage 5 scoring at all — see build_match_result.

Deterministic keyword/phrase-overlap proxies stand in for the Stage 3
(semantic) / Stage 4 (LLM) components this table will eventually use
(decisions.md D-007); the component *slots* and weights already match the
final shape so a later milestone upgrades a computation, not the schema.

Scoring calibration fix (decisions.md D-032): the formulas below replaced an
earlier version that divided every match by the *entire* configured
vocabulary size, which diluted an exact target-title match in proportion to
how many other titles happened to be configured, and defaulted four
components (seniority, sector, education, visa) to a neutral 0.5 whenever no
evidence existed at all — producing a flat, unconditional 15-point score
floor for every job that cleared Stage 2, regardless of relevance. See the
per-component docstrings below for the replacement formula; architecture.md
section 10 documents the same contract at the table level.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from job_scout.config import ScoringWeights
from job_scout.matching.hard_filters import parse_experience_range
from job_scout.matching.normalize import (
    contains_phrase_tokens,
    dedupe_preserve_order,
    match_phrase,
    normalize_text,
    normalize_tokens,
)
from job_scout.matching.prefilter import PrefilterWeights, keyword_overlap
from job_scout.matching.visa_patterns import VISA_NEGATIVE_PATTERNS, VISA_POSITIVE_PATTERNS
from job_scout.models import (
    CandidateProfile,
    HardFilterResult,
    Job,
    MatchResult,
    NotificationThresholds,
    NotificationTier,
    PrefilterResult,
    ScoreComponent,
    SearchProfile,
)


def _first_match(patterns: list[re.Pattern[str]], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


# --- Part 1/2: best-match title and role-family scoring --------------------
#
# Replaces the earlier "matches / total configured vocabulary" ratio, which
# mechanically punished a job for an exact title match by however many
# *other* target titles the profile author happened to configure. The raw
# score is now the single strongest configured phrase match (never diluted
# by unrelated additional phrases), weighted by:
#   - field: a title-field match always outweighs a description-only match
#     (_DESCRIPTION_FIELD_DAMPING), consistent with the Stage 2 pre-filter's
#     own title-vs-description weighting and with the truncated-description
#     reality documented in architecture.md section 3.
#   - provenance tier: an active SearchProfile signal (this run's actual
#     ask) outranks a CandidateProfile signal of the same match quality,
#     and a candidate's *historical* previous_titles rank lowest of all —
#     "generic historical titles must not automatically outrank active
#     search-profile targets."
# Matching itself (exact normalised phrase, or token-coverage for
# multi-word phrases) is `matching.normalize.match_phrase` — the same
# function the Stage 2 strong-title gate uses, so Stage 5 never scores zero
# for evidence that already passed Stage 2 (Part 3 of the fix).

_DESCRIPTION_FIELD_DAMPING = 0.5
# Mirrors PrefilterWeights.desc_only_damping's default — title-field
# evidence is preferred over description-only evidence in both stages.

_TITLE_TIER_WEIGHTS: dict[str, float] = {
    "search_target_title": 1.0,
    "search_title_alias": 1.0,
    "candidate_title_alias": 0.55,
    "candidate_previous_title": 0.4,
}
_ROLE_FAMILY_TIER_WEIGHTS: dict[str, float] = {
    "search_role_family": 1.0,
    "candidate_role_family": 0.85,
}
# candidate_title_alias/candidate_previous_title lowered from their
# pre-D-034 values of 0.85/0.7 (decisions.md D-034): a candidate's own
# historical title vocabulary is supplemental evidence for *this run's*
# active ask, not a near-equivalent of it — the pre-D-034 gap (0.85 vs the
# active tiers' 1.0) let a candidate-alias-only match combine with an
# incidental sector/skill hit to outrank a job with a genuine exact active
# `search_target_title` match and nothing else (the confirmed live
# regression: "Data Business Analyst" 31.25 > "Strategy Manager" 25.0).
# search_target_title/search_title_alias (this run's actual ask) and
# search_role_family/candidate_role_family (unchanged from D-032/D-033) are
# untouched — this fix only widens the gap between active-search and
# candidate-history *title* evidence.


class _EvidenceTier(StrEnum):
    """Part 1 (decisions.md D-034): a small, internal classification of a
    job's *strongest* title/role-family evidence, derived directly from the
    structured `_PhraseScore` provenance labels `_best_phrase_match`
    already produces — never by re-parsing the rendered evidence strings.
    Not a new public model/ScoreComponent (CLAUDE.md hard constraint 9/10):
    purely a transparency label recorded in `title_role_family`'s own
    evidence list, and the vocabulary is entirely provenance-based (which
    configured list matched), never a profession-specific term."""

    ACTIVE_TARGET_TITLE = "active_target_title"
    ACTIVE_TITLE_ALIAS = "active_title_alias"
    ACTIVE_ROLE_FAMILY = "active_role_family"
    CANDIDATE_HISTORY_ONLY = "candidate_history_only"
    NO_TITLE_OR_ROLE_EVIDENCE = "no_title_or_role_evidence"


def _classify_evidence_tier(
    title_scores: list[_PhraseScore], role_scores: list[_PhraseScore]
) -> _EvidenceTier:
    """Strongest evidence *category* present for this job, in the same
    active-over-candidate precedence the scoring formula below uses:
    an active target-title match outranks an active title-alias match,
    which outranks active role-family evidence, which outranks any
    candidate-only (history) match, which outranks no evidence at all."""
    if any(s.label == "search_target_title" for s in title_scores):
        return _EvidenceTier.ACTIVE_TARGET_TITLE
    if any(s.label == "search_title_alias" for s in title_scores):
        return _EvidenceTier.ACTIVE_TITLE_ALIAS
    if any(s.label == "search_role_family" for s in role_scores):
        return _EvidenceTier.ACTIVE_ROLE_FAMILY
    if title_scores or role_scores:
        return _EvidenceTier.CANDIDATE_HISTORY_ONLY
    return _EvidenceTier.NO_TITLE_OR_ROLE_EVIDENCE


_ACTIVE_ROLE_FAMILY_ALONE_CREDIT = 0.70
_ACTIVE_ROLE_FAMILY_REINFORCED_CREDIT = 0.85
_CANDIDATE_ROLE_FAMILY_ALONE_CREDIT = 0.5
# Part 2 (decisions.md D-034): the credit role-family evidence contributes
# on its own (no title match), replacing the old blanket
# `role_score / 2` — a flat 0.5 factor applied identically regardless of
# whether the match came from this run's active SearchProfile or only the
# candidate's own history — with three provenance-aware factors:
#   - a single active `search_role_family` match now earns substantially
#     more (0.70) than the old flat half-credit, reflecting that it *is*
#     this run's actual ask, just without a separately configured title
#     phrase also matching (e.g. "Consulting Project Team Lead - Corporate
#     Strategy", "Location Strategy Analyst");
#   - two or more *distinct* active role-family phrases matched in the
#     job's *title* field (never a single incidental description-only
#     mention — see `_role_family_alone_credit`'s `field == "title"`
#     filter) reinforce that credit further (0.85), since two independent
#     active-search phrases both matching the title is stronger evidence
#     than one;
#   - `_CANDIDATE_ROLE_FAMILY_ALONE_CREDIT` is deliberately left at the
#     pre-existing D-032/D-033 value (0.5) — candidate-only role-family
#     scores are unchanged by this fix; only active evidence gets more
#     credit, so active role-family-only evidence now strictly outranks
#     candidate-only role-family-only evidence of equal match strength
#     (0.70/0.85 vs 0.5), satisfying Part 2 requirement 3.


def _role_family_alone_credit(role_scores: list[_PhraseScore]) -> float:
    """The best credit this job's role-family evidence can contribute
    *on its own*, independent of any title match — folded into
    `_combine_title_role_family` via `max()`, so it can only ever raise the
    combined score, never lower it (Part 2 requirement 5: candidate
    evidence never reduces active evidence). Active (`search_role_family`)
    and candidate-only (`candidate_role_family`) evidence are evaluated
    independently — using the single overall best-scoring role phrase
    regardless of provenance would let a stronger candidate-only match
    (e.g. an exact title-field hit) shadow a weaker but still-genuine
    active match, silently discarding real active evidence."""
    active = [s for s in role_scores if s.label == "search_role_family"]
    candidate = [s for s in role_scores if s.label == "candidate_role_family"]

    active_credit = 0.0
    if active:
        reinforcing_title_matches = {s.phrase for s in active if s.field == "title"}
        factor = (
            _ACTIVE_ROLE_FAMILY_REINFORCED_CREDIT
            if len(reinforcing_title_matches) >= 2
            else _ACTIVE_ROLE_FAMILY_ALONE_CREDIT
        )
        active_credit = max(s.score for s in active) * factor

    candidate_credit = 0.0
    if candidate:
        candidate_credit = max(s.score for s in candidate) * _CANDIDATE_ROLE_FAMILY_ALONE_CREDIT

    return max(active_credit, candidate_credit)


def _combine_title_role_family(
    title_score: float, role_score: float, role_alone_credit: float
) -> float:
    """`raw = max(title_score, (title_score + role_score) / 2,
    role_alone_credit)` (decisions.md D-034, extending D-033's
    `max(title_score, average)`).

    `title_score` and the `(title_score + role_score) / 2` average behave
    exactly as under D-033 — title is the primary signal, an exact title
    match keeps its full strength regardless of role-family evidence, and
    a stronger role-family match can still raise the combined score above
    title alone. `role_alone_credit` (`_role_family_alone_credit`) is the
    new third candidate in the `max()`: the provenance-aware credit
    role-family evidence earns standing entirely on its own, replacing the
    old unconditional `role_score / 2` fallback with active-vs-candidate
    and single-vs-reinforced distinctions (Part 2 requirements 2–4). Since
    it only ever participates via `max()`, it can raise the combined score
    when role-family evidence is unusually strong for its provenance tier,
    but can never pull an existing title or averaged score down (Part 2
    requirement 5). All three inputs are already bounded to [0, 1] (each a
    match strength in [0, 1] scaled by a field factor and tier/credit
    factor, all <= 1), so their `max()` cannot exceed 1.0 (Part 2
    requirement 6)."""
    average = (title_score + role_score) / 2
    return max(title_score, average, role_alone_credit)


@dataclass(frozen=True)
class _PhraseScore:
    """One configured phrase's structured match result against a job —
    the single source of truth for both the human-readable evidence
    strings (`_render_phrase_evidence`) and the active-vs-candidate-history
    classification/aggregation above (`_classify_evidence_tier`,
    `_role_family_alone_credit`), so neither has to re-parse rendered
    evidence text to recover provenance (Part 1)."""

    label: str
    phrase: str
    field: str
    method: str
    strength: float
    score: float


def _best_phrase_match(
    labeled_phrases: list[tuple[str, str]],
    tier_weights: dict[str, float],
    *,
    title_norm: str,
    title_tokens: set[str],
    description_norm: str,
    coverage_threshold: float,
) -> list[_PhraseScore]:
    """Every configured phrase that matched this job at all, scaled by
    field (title > description) and provenance tier, sorted strongest
    first. Adding more phrases to `labeled_phrases` can only ever add
    candidates to this list — it can never lower an existing entry's
    score, so configuring more (unrelated) target titles never dilutes an
    exact match on one of them."""
    scored: list[_PhraseScore] = []
    for label, phrase in labeled_phrases:
        match = match_phrase(
            phrase,
            title_norm=title_norm,
            title_tokens=title_tokens,
            description_norm=description_norm,
            coverage_threshold=coverage_threshold,
        )
        if match.method == "none":
            continue
        field_factor = 1.0 if match.field == "title" else _DESCRIPTION_FIELD_DAMPING
        tier_weight = tier_weights.get(label, 1.0)
        score = match.strength * field_factor * tier_weight
        scored.append(
            _PhraseScore(
                label=label,
                phrase=phrase,
                field=match.field,
                method=match.method,
                strength=match.strength,
                score=score,
            )
        )
    scored.sort(key=lambda item: item.score, reverse=True)
    return scored


def _render_phrase_evidence(scores: list[_PhraseScore], evidence_label: str) -> list[str]:
    """Human-readable rendering of `_best_phrase_match`'s structured
    output — identical wording to the pre-D-034 inline formatting, kept as
    its own step so the classification/aggregation logic reads the
    structured `_PhraseScore` list directly rather than these strings."""
    if not scores:
        return []
    best = scores[0]
    evidence = [
        f"best_{evidence_label}_match:{best.label}:'{best.phrase}' "
        f"field={best.field} method={best.method} score={best.score:.3f}"
    ]
    evidence += [
        f"{evidence_label}_match:{s.label}:'{s.phrase}' field={s.field} "
        f"method={s.method} score={s.score:.3f}"
        for s in scores[1:]
    ]
    return evidence


def _title_role_family_component(
    job: Job,
    candidate: CandidateProfile,
    search: SearchProfile,
    weight: float,
    coverage_threshold: float,
) -> ScoreComponent:
    title_norm = normalize_text(job.title)
    description_norm = normalize_text(job.description_text)
    title_tokens = set(title_norm.split(" ")) if title_norm else set()

    title_phrases: list[tuple[str, str]] = (
        [("search_target_title", p) for p in search.target_titles]
        + [("search_title_alias", p) for p in search.title_aliases]
        + [("candidate_title_alias", p) for p in candidate.title_aliases]
        + [("candidate_previous_title", p) for p in candidate.previous_titles]
    )
    role_phrases: list[tuple[str, str]] = [
        ("search_role_family", p) for p in search.role_families
    ] + [("candidate_role_family", p) for p in candidate.role_families]

    title_scores = _best_phrase_match(
        title_phrases,
        _TITLE_TIER_WEIGHTS,
        title_norm=title_norm,
        title_tokens=title_tokens,
        description_norm=description_norm,
        coverage_threshold=coverage_threshold,
    )
    role_scores = _best_phrase_match(
        role_phrases,
        _ROLE_FAMILY_TIER_WEIGHTS,
        title_norm=title_norm,
        title_tokens=title_tokens,
        description_norm=description_norm,
        coverage_threshold=coverage_threshold,
    )

    title_score = title_scores[0].score if title_scores else 0.0
    role_score = role_scores[0].score if role_scores else 0.0
    role_alone_credit = _role_family_alone_credit(role_scores)
    raw = _combine_title_role_family(title_score, role_score, role_alone_credit)

    evidence_tier = _classify_evidence_tier(title_scores, role_scores)
    evidence = [
        *_render_phrase_evidence(title_scores, "title"),
        *_render_phrase_evidence(role_scores, "role_family"),
        f"active_search_intent_tier:{evidence_tier.value}",
    ]
    return ScoreComponent(
        name="title_role_family",
        weight=weight,
        raw_value=raw,
        weighted_value=weight * raw,
        evidence=evidence,
    )


# --- Part 4: search-profile-aware skill scoring -----------------------------
#
# Required/preferred skill signals are no longer candidate-history-only.
# SearchProfile's own required_skills/preferred_skills (this run's actual
# ask) are the primary signal; CandidateProfile's general primary_skills/
# secondary_skills/transferable_skills are supplemental support when a
# search-profile signal exists, or a capped lower-priority fallback when it
# doesn't — so generic historical overlap alone can never earn as much as a
# genuine search-specific match, and can't by itself out-score an exact
# title match. `_bounded_coverage` also replaces plain
# matches/len(vocabulary) division with a capped denominator, so a real
# signal (a few configured skills actually mentioned) isn't diluted by how
# many *other* skills happen to be configured — the same "don't divide by
# the whole vocabulary" principle Part 1/2 apply to titles/role families.
# `responsibilities` (below) reuses the same fallback weight/denominator,
# since SearchProfile has no "responsibilities" field of its own — that
# component is *always* candidate-history-only, so it must always be
# damped the same way required_skills/transferable_skills are when they
# have no search-specific signal to draw on either.

_SKILL_COVERAGE_CAP = 3
_SUPPLEMENTAL_CANDIDATE_SKILL_WEIGHT = 0.3
_FALLBACK_CANDIDATE_SKILL_WEIGHT = 0.2
# Deliberately small: candidate-history-only signal is spread across three
# components (responsibilities, required_skills, transferable_skills) when
# no search-specific skill/requirement is configured, and their combined
# contribution must stay below a single bare exact-title-only match's
# contribution (title_role_family's minimum positive raw of 0.5, weight
# 0.25 -> 0.125) — see the regression test asserting this in test_scoring.py.


def _bounded_coverage(matches: list[str], vocabulary: list[str]) -> float:
    if not vocabulary:
        return 0.0
    denominator = min(len(vocabulary), _SKILL_COVERAGE_CAP)
    return min(1.0, len(matches) / denominator)


def _responsibilities_component(
    job: Job, candidate: CandidateProfile, weight: float
) -> ScoreComponent:
    # Proxy corpus: role_families + primary_skills overlap against the
    # description body only (not the title) — a deliberately different
    # signal from the title/role component above (architecture.md section
    # 10, Stage 5, "Responsibilities match ... proxy until Stage 3/4 exist").
    # Always candidate-history-only (see module note above) -> always scored
    # at the fallback weight, using the same bounded-coverage denominator
    # Part 1/2 apply to titles/role families.
    vocabulary = dedupe_preserve_order([*candidate.role_families, *candidate.primary_skills])
    matches = keyword_overlap(vocabulary, job.description_text)
    coverage = _bounded_coverage(matches, vocabulary)
    raw = _FALLBACK_CANDIDATE_SKILL_WEIGHT * coverage
    return ScoreComponent(
        name="responsibilities",
        weight=weight,
        raw_value=raw,
        weighted_value=weight * raw,
        evidence=[f"responsibility_term:{m}" for m in matches],
    )


def _required_skills_component(
    job: Job, candidate: CandidateProfile, search: SearchProfile, weight: float
) -> ScoreComponent:
    combined_text = f"{job.title} {job.description_text}"
    search_matches = keyword_overlap(search.required_skills, combined_text)
    candidate_matches = keyword_overlap(candidate.primary_skills, combined_text)
    search_score = _bounded_coverage(search_matches, search.required_skills)
    candidate_score = _bounded_coverage(candidate_matches, candidate.primary_skills)

    if search.required_skills:
        # search.required_skills is this run's actual ask; candidate.primary_skills
        # is supplemental support only, at reduced weight (Part 4: "search-required
        # skills should carry more importance than generic candidate-history skills").
        raw = min(1.0, search_score + _SUPPLEMENTAL_CANDIDATE_SKILL_WEIGHT * candidate_score)
    else:
        # No search-specific requirement configured for this run -> fall back to
        # candidate.primary_skills, capped below what a genuine search-required
        # match would earn (Part 4: "lower-priority fallback").
        raw = _FALLBACK_CANDIDATE_SKILL_WEIGHT * candidate_score

    evidence = [f"search_required_skill:{m}" for m in search_matches]
    evidence += [f"candidate_primary_skill:{m}" for m in candidate_matches]
    return ScoreComponent(
        name="required_skills",
        weight=weight,
        raw_value=raw,
        weighted_value=weight * raw,
        evidence=evidence,
    )


def _transferable_skills_component(
    job: Job, candidate: CandidateProfile, search: SearchProfile, weight: float
) -> ScoreComponent:
    combined_text = f"{job.title} {job.description_text}"
    search_vocab = dedupe_preserve_order([*search.preferred_skills, *search.transferable_skills])
    candidate_vocab = dedupe_preserve_order(
        [*candidate.secondary_skills, *candidate.transferable_skills]
    )
    search_matches = keyword_overlap(search_vocab, combined_text)
    candidate_matches = keyword_overlap(candidate_vocab, combined_text)
    search_score = _bounded_coverage(search_matches, search_vocab)
    candidate_score = _bounded_coverage(candidate_matches, candidate_vocab)

    if search_vocab:
        # search.preferred_skills/transferable_skills is this run's stated
        # preference; candidate history is supplemental (Part 4: "search
        # preferred skill match should contribute positively but less than
        # required skill" — this component's weight is already half of
        # required_skills', reinforcing that at the config level too).
        raw = min(1.0, search_score + _SUPPLEMENTAL_CANDIDATE_SKILL_WEIGHT * candidate_score)
    else:
        raw = _FALLBACK_CANDIDATE_SKILL_WEIGHT * candidate_score

    evidence = [f"search_preferred_skill:{m}" for m in search_matches]
    evidence += [f"candidate_transferable_skill:{m}" for m in candidate_matches]
    return ScoreComponent(
        name="transferable_skills",
        weight=weight,
        raw_value=raw,
        weighted_value=weight * raw,
        evidence=evidence,
    )


# --- Part 7: generic, profession-agnostic entry-level seniority safeguard --

_ENTRY_LEVEL_TERMS = ["trainee", "graduate", "internship", "intern", "junior", "entry level"]
# Generic seniority-level markers, not profession vocabulary — every
# profession has trainee/graduate/junior roles, so this is a structural
# employment-level signal, not the kind of profession-specific keyword
# CLAUDE.md hard constraint 10 forbids hard-coding (the same category as the
# existing generic citizenship/clearance/no-sponsorship phrase lists in
# matching/hard_filters.py). Detected with word-boundary-safe token matching
# (`contains_phrase_tokens`) so a short term never matches inside an
# unrelated longer word.

_SENIOR_SEARCH_MIN_YEARS_THRESHOLD = 3.0
# A search profile asking for at least this many years is treated as
# targeting an experienced/senior hire. Deterministic and config-driven —
# SearchProfile.min_experience_years already encodes exactly this, so this
# is a threshold on existing configuration, not a new profession-specific
# rule invented for this fix.


def _detect_entry_level_mismatch(job: Job, search: SearchProfile) -> str | None:
    if (
        search.min_experience_years is None
        or search.min_experience_years < _SENIOR_SEARCH_MIN_YEARS_THRESHOLD
    ):
        return None
    combined_tokens = normalize_tokens(f"{job.title} {job.description_text}")
    for term in _ENTRY_LEVEL_TERMS:
        if contains_phrase_tokens(combined_tokens, normalize_tokens(term)):
            return term
    return None


# --- Part 6: no unconditional positive floor --------------------------------
#
# seniority_experience, sector_relevance, education, and visa_relocation
# used to default to a neutral raw_value of 0.5 whenever no evidence existed
# at all, which — combined across these four components' weights — gave
# every job that cleared Stage 2 an unconditional 15-point score floor
# regardless of relevance. "No evidence" now defaults to 0.0 (recorded as
# `not_evaluable` in the evidence list) in every component below; a
# genuine positive match still scores positively, and (for
# seniority_experience/visa_relocation, where an explicit negative signal is
# deterministically detectable) genuine negative evidence can now pull the
# component below zero rather than only ever sitting at the same neutral
# value as "no evidence found" would.


def _seniority_experience_component(
    job: Job, candidate: CandidateProfile, search: SearchProfile, weight: float
) -> ScoreComponent:
    seniority_term = candidate.effective_seniority_text()
    seniority_matched = seniority_term is not None and seniority_term.lower() in (
        job.description_text.lower()
    )
    entry_level_term = None if seniority_matched else _detect_entry_level_mismatch(job, search)

    if seniority_matched:
        seniority_raw = 1.0
        seniority_evidence = [f"seniority:{seniority_term}"]
    elif entry_level_term is not None:
        # Explicit mismatch: an entry-level-worded job against a search
        # profile targeting experienced hires (Part 7) -> negative, not just
        # "no evidence" neutral, so it can genuinely lower a weak role's score.
        seniority_raw = -1.0
        seniority_evidence = [f"seniority_mismatch:{entry_level_term}"]
    else:
        seniority_raw = 0.0
        seniority_evidence = ["not_evaluable:no_seniority_evidence_found"]

    parsed_range = parse_experience_range(job.description_text)
    if (
        parsed_range is not None
        and search.min_experience_years is not None
        and search.max_experience_years is not None
    ):
        job_min, job_max = parsed_range
        overlap = job_min <= search.max_experience_years and job_max >= search.min_experience_years
        # A non-overlapping parsed range is already rejected at Stage 1
        # (hard_filters.py::evaluate_hard_filters) whenever both min/max are
        # configured, so `overlap` is always True for any job that reaches
        # here — this branch stays defensive rather than assuming that.
        experience_raw = 1.0 if overlap else 0.0
        experience_evidence = [f"experience_range:{job_min:g}-{job_max:g} years"]
    else:
        experience_raw = 0.0
        experience_evidence = ["not_evaluable:no_parseable_experience_range"]

    raw = (seniority_raw + experience_raw) / 2
    return ScoreComponent(
        name="seniority_experience",
        weight=weight,
        raw_value=raw,
        weighted_value=weight * raw,
        evidence=[*seniority_evidence, *experience_evidence],
    )


# --- Part 5: real sector/industry relevance ---------------------------------


def _sector_relevance_component(
    job: Job, candidate: CandidateProfile, search: SearchProfile, weight: float
) -> ScoreComponent:
    included = dedupe_preserve_order(
        [
            *candidate.industries,
            *candidate.sectors,
            *search.included_industries,
            *search.included_sectors,
        ]
    )
    # candidate.excluded_industries and search.excluded_industries/
    # excluded_sectors *without* their hard_filters toggle enabled are the
    # only excluded-industry/sector signals that can ever reach Stage 5:
    # candidate.excluded_industries rejects unconditionally at Stage 1
    # whenever non-empty (hard_filters.py), so a job matching it can never
    # get here — only search.excluded_industries/excluded_sectors (opt-in
    # via HardFilterToggles, decisions.md D-025) can still be present.
    excluded = dedupe_preserve_order([*search.excluded_industries, *search.excluded_sectors])

    if not included and not excluded:
        # No industry/sector preference configured anywhere -> genuinely
        # neutral (not "no evidence found" — there is nothing to evaluate
        # against at all), per Part 5's documented neutral-value rule.
        return ScoreComponent(
            name="sector_relevance",
            weight=weight,
            raw_value=0.5,
            weighted_value=weight * 0.5,
            evidence=["not_evaluable:no_industry_or_sector_preference_configured"],
        )

    text_tokens = normalize_tokens(f"{job.title} {job.description_text}")
    included_matches = [
        term for term in included if contains_phrase_tokens(text_tokens, normalize_tokens(term))
    ]
    excluded_matches = [
        term for term in excluded if contains_phrase_tokens(text_tokens, normalize_tokens(term))
    ]

    if included_matches and not excluded_matches:
        raw = 1.0
    elif included_matches and excluded_matches:
        raw = 0.5  # mixed signal: matched both a wanted and an unwanted industry/sector
    elif excluded_matches:
        raw = 0.0  # excluded-only: reduces the component, never a hard rejection here
    else:
        # Preferences ARE configured but nothing was detected in this job's
        # text -> not_evaluable, never an automatic positive point (Part 6).
        raw = 0.0

    evidence = [f"included_industry_or_sector:{m}" for m in included_matches]
    evidence += [f"excluded_industry_or_sector:{m}" for m in excluded_matches]
    if not evidence:
        evidence = ["not_evaluable:no_matching_evidence_found"]

    return ScoreComponent(
        name="sector_relevance",
        weight=weight,
        raw_value=raw,
        weighted_value=weight * raw,
        evidence=evidence,
    )


def _education_component(job: Job, candidate: CandidateProfile, weight: float) -> ScoreComponent:
    # Milestone 1.1 (decisions.md D-017): no fixed/universal keyword list —
    # "MBA" (or any other credential) only ever counts if the *configured
    # candidate* actually lists it. Vocabulary is the candidate's own
    # education degrees/fields/levels plus qualifications/certifications/
    # licences (all generic, profession-agnostic fields).
    combined_text = f"{job.title} {job.description_text}"
    vocabulary = [
        *[e.degree for e in candidate.education if e.degree],
        *[e.field for e in candidate.education if e.field],
        *[e.level for e in candidate.education if e.level],
        *candidate.qualifications,
        *candidate.certifications,
        *candidate.licences,
    ]
    if not vocabulary:
        # Nothing configured to evaluate against -> not_evaluable, zero, not
        # a positive default (Part 6 removes the earlier neutral-0.5 floor).
        return ScoreComponent(
            name="education",
            weight=weight,
            raw_value=0.0,
            weighted_value=0.0,
            evidence=["not_evaluable:no_education_or_qualification_configured"],
        )
    matches = keyword_overlap(vocabulary, combined_text)
    if matches:
        return ScoreComponent(
            name="education",
            weight=weight,
            raw_value=1.0,
            weighted_value=weight * 1.0,
            evidence=[f"education_match:{m}" for m in matches],
        )
    return ScoreComponent(
        name="education",
        weight=weight,
        raw_value=0.0,
        weighted_value=0.0,
        evidence=["not_evaluable:no_matching_evidence_found"],
    )


def _visa_relocation_component(job: Job, weight: float) -> ScoreComponent:
    text = job.description_text
    positive = _first_match(VISA_POSITIVE_PATTERNS, text)
    if positive:
        return ScoreComponent(
            name="visa_relocation",
            weight=weight,
            raw_value=1.0,
            weighted_value=weight * 1.0,
            evidence=[positive],
        )
    negative = _first_match(VISA_NEGATIVE_PATTERNS, text)
    if negative:
        # Explicit negative evidence, not just "no evidence" neutral (Part
        # 6) -> pulls the component below the not_evaluable baseline.
        return ScoreComponent(
            name="visa_relocation",
            weight=weight,
            raw_value=-1.0,
            weighted_value=weight * -1.0,
            evidence=[f"negative:{negative}"],
        )
    # No evidence either way -> not_evaluable, zero (risk R-4: expect a lot
    # of these without sponsor-registry enrichment, which is out of scope).
    return ScoreComponent(
        name="visa_relocation",
        weight=weight,
        raw_value=0.0,
        weighted_value=0.0,
        evidence=["not_evaluable:no_visa_or_relocation_language_found"],
    )


def compute_score_components(
    job: Job, candidate: CandidateProfile, search: SearchProfile, weights: ScoringWeights
) -> list[ScoreComponent]:
    w = weights
    # Part 3: Stage 5's title/role-family matching reuses the exact same
    # coverage threshold Stage 2's strong gate used for this run (both are
    # derived from the same ScoringWeights via PrefilterWeights), so a job
    # that passed Stage 2 on token-coverage title evidence gets equivalent
    # credit here rather than silently scoring zero for that evidence.
    coverage_threshold = PrefilterWeights.from_scoring_weights(weights).strong_title_coverage
    return [
        _title_role_family_component(
            job, candidate, search, w.title_role_family, coverage_threshold
        ),
        _responsibilities_component(job, candidate, w.responsibilities),
        _required_skills_component(job, candidate, search, w.required_skills),
        _transferable_skills_component(job, candidate, search, w.transferable_skills),
        _seniority_experience_component(job, candidate, search, w.seniority_experience),
        _sector_relevance_component(job, candidate, search, w.sector_relevance),
        _education_component(job, candidate, w.education),
        _visa_relocation_component(job, w.visa_relocation),
    ]


def determine_notification_tier(
    final_score: float, thresholds: NotificationThresholds
) -> NotificationTier:
    if final_score >= thresholds.priority_score:
        return NotificationTier.PRIORITY
    if final_score >= thresholds.digest_score:
        return NotificationTier.DIGEST
    return NotificationTier.STORE_ONLY


def build_match_result(
    job: Job,
    candidate: CandidateProfile,
    search: SearchProfile,
    hard_filter_result: HardFilterResult,
    prefilter_result: PrefilterResult,
    weights: ScoringWeights,
) -> MatchResult:
    if not hard_filter_result.passed:
        return MatchResult(
            job_id=job.job_id,
            search_profile_ref=search.profile_id,
            hard_filter_result=hard_filter_result,
            prefilter_result=prefilter_result,
            final_score=None,
            score_components=[],
            notification_tier=NotificationTier.REJECTED,
        )

    if not prefilter_result.passed_threshold:
        return MatchResult(
            job_id=job.job_id,
            search_profile_ref=search.profile_id,
            hard_filter_result=hard_filter_result,
            prefilter_result=prefilter_result,
            final_score=None,
            score_components=[],
            notification_tier=NotificationTier.STORE_ONLY,
        )

    components = compute_score_components(job, candidate, search, weights)
    # Components with genuinely negative evidence (seniority mismatch,
    # explicit no-sponsorship language) can push the raw weighted sum below
    # zero; the displayed 0-100 scale stays bounded (Part 8) by clamping,
    # so "strong negative evidence" and "zero evidence" both floor at 0
    # rather than the score going negative on screen.
    raw_total = sum(c.weighted_value for c in components)
    final_score = round(max(0.0, min(100.0, 100 * raw_total)), 2)
    tier = determine_notification_tier(final_score, search.notification_thresholds)
    return MatchResult(
        job_id=job.job_id,
        search_profile_ref=search.profile_id,
        hard_filter_result=hard_filter_result,
        prefilter_result=prefilter_result,
        final_score=final_score,
        score_components=components,
        notification_tier=tier,
    )
