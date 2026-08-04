"""Shared deterministic text normalisation for matching (architecture.md
section 10). One function, reused by the Stage 2 pre-filter and Stage 5
scoring, so "Strategy & Transformation", "Strategy/Transformation", and
"strategy_transformation" all normalise to the same comparable form.

Profession-agnostic by construction: no vocabulary, no stemming, no
synonyms — purely structural case/punctuation rules (CLAUDE.md hard
constraint 10).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_AMPERSAND_RE = re.compile(r"&")
_NON_ALPHANUMERIC_RE = re.compile(r"[^a-z0-9\s]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Lowercase; underscores and `&` become spaces/" and "; every other
    non-alphanumeric character (slashes, hyphens, commas, parentheses, ...)
    becomes a space; repeated whitespace collapses to one. Alphanumeric
    tokens themselves are preserved verbatim — no stemming or pluralisation
    handling."""
    lowered = text.lower().replace("_", " ")
    lowered = _AMPERSAND_RE.sub(" and ", lowered)
    lowered = _NON_ALPHANUMERIC_RE.sub(" ", lowered)
    return _WHITESPACE_RE.sub(" ", lowered).strip()


def normalize_tokens(text: str) -> list[str]:
    """`normalize_text`, split into its whitespace-separated tokens."""
    normalized = normalize_text(text)
    return normalized.split(" ") if normalized else []


def dedupe_preserve_order(items: list[str]) -> list[str]:
    """De-duplicate a list of configured phrases by their normalised form,
    keeping the first-seen original (pre-normalisation) string — used when
    merging candidate-profile and search-profile phrase lists so the same
    phrase configured in both places isn't double-counted in a ratio
    denominator/numerator."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = normalize_text(item)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def contains_phrase_tokens(text_tokens: list[str], phrase_tokens: list[str]) -> bool:
    """Word-boundary-safe alternative to plain substring containment: True
    iff `phrase_tokens` appears as a contiguous run inside `text_tokens`.
    Both arguments are expected to already be `normalize_tokens()` output.

    `keyword_overlap` (matching/prefilter.py) intentionally keeps using raw
    substring containment everywhere it already did — that behaviour isn't
    being changed project-wide. This helper exists for the specific spots
    where a *short* configured token could otherwise match inside an
    unrelated longer word purely by substring accident (e.g. a configured
    industry code like "ai" matching inside "trainee"): Stage 5's
    sector/industry relevance component and its entry-level seniority-term
    detector (matching/scoring.py)."""
    n, m = len(text_tokens), len(phrase_tokens)
    if m == 0 or m > n:
        return False
    return any(text_tokens[start : start + m] == phrase_tokens for start in range(n - m + 1))


def phrase_token_coverage(phrase_tokens: list[str], available_tokens: set[str]) -> float:
    """Fraction of `phrase_tokens` present anywhere in `available_tokens`
    (order-independent). The structural, stopword-list-free safeguard
    against a single generic word completing a multi-word configured
    phrase — shared by the Stage 2 pre-filter's strong-title gate
    (matching/prefilter.py) and Stage 5's best-match title/role-family
    scoring (matching/scoring.py), so both stages agree on what counts as
    a genuine multi-word title/role-family match."""
    if not phrase_tokens:
        return 0.0
    matched = sum(1 for token in phrase_tokens if token in available_tokens)
    return matched / len(phrase_tokens)


@dataclass(frozen=True)
class PhraseMatch:
    """Result of testing one configured phrase against a job's normalised
    title/description. Shared by the Stage 2 pre-filter's strong-title gate
    and Stage 5's title/role-family scoring (matching/prefilter.py,
    matching/scoring.py) so a job can never pass Stage 2 on title evidence
    that Stage 5 then fails to recognise as the same evidence — both stages
    call `match_phrase` with the same normalisation and the same coverage
    threshold."""

    method: str  # "exact_phrase" | "token_coverage" | "none"
    field: str  # "title" | "description" | "none"
    strength: float  # 1.0 for exact_phrase; the coverage ratio for token_coverage; 0.0 for none


_NO_PHRASE_MATCH = PhraseMatch(method="none", field="none", strength=0.0)


def match_phrase(
    phrase: str,
    *,
    title_norm: str,
    title_tokens: set[str],
    description_norm: str,
    coverage_threshold: float,
) -> PhraseMatch:
    """Match one configured phrase against a job's title first — an exact
    normalised-phrase substring, or token coverage >= `coverage_threshold`
    of the phrase's own words (never a single generic word completing a
    multi-word phrase: a 2-word phrase needs both words, a 3-word phrase
    needs all 3, etc. — see `phrase_token_coverage`) — falling back to an
    exact-phrase substring match in the description only when the title
    doesn't match at all. Title evidence is always preferred over
    description-only evidence by construction: this function returns as
    soon as it finds a title-field match, and callers apply their own
    (typically lower) weighting to a `field="description"` result.

    Passing `description_norm=""` disables the description fallback
    entirely — used by the Stage 2 strong gate, which only ever considers
    the title field."""
    phrase_norm = normalize_text(phrase)
    if not phrase_norm:
        return _NO_PHRASE_MATCH
    if phrase_norm in title_norm:
        return PhraseMatch(method="exact_phrase", field="title", strength=1.0)
    phrase_tokens = phrase_norm.split(" ")
    coverage = phrase_token_coverage(phrase_tokens, title_tokens)
    if coverage >= coverage_threshold:
        return PhraseMatch(method="token_coverage", field="title", strength=coverage)
    if description_norm and phrase_norm in description_norm:
        return PhraseMatch(method="exact_phrase", field="description", strength=1.0)
    return _NO_PHRASE_MATCH
