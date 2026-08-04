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
