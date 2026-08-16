"""URL canonicalisation and job fingerprinting (architecture.md section 8).

Fingerprint-first deduplication, fuzzy match as a second tier
(decisions.md D-008): exact fingerprint lookup (tier 1) is handled by
JobRepository.find_by_fingerprint; this module also provides tiers 2-4,
evaluated by the pipeline against a recent-jobs window.

Milestone 2 Deliverable 5 step 9 (decisions.md D-038): the exact-duplicate
tier gains a cross-source canonical-URL match (ignoring external_source_id),
gated by SourceCapabilities.canonical_application_url on both sides; the old
exact-hash-only cross-source tier is generalised into a "probable duplicate"
tier that also accepts a bounded token-set (Jaccard) similarity or a close
posted_date plus matching salary as corroborating evidence, still behind the
existing company+title+location identity precondition (never optional).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel

from job_scout.matching.normalize import normalize_tokens
from job_scout.models import Job, JobFingerprint, Location, SourceCapabilities

DEFAULT_REPOST_GAP_DAYS = 21

# Milestone 2 Deliverable 5 step 9 (decisions.md D-038): thresholds for the
# "probable duplicate" tier's corroborating signals. Deterministic, no
# embeddings, per explicit instruction.
PROBABLE_DUPLICATE_JACCARD_THRESHOLD = 0.6
PROBABLE_DUPLICATE_POSTED_DATE_WINDOW_DAYS = 3

_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAM_EXACT = {"gh_src", "lever-source", "trk"}

_SUFFIX_PARENS_RE = re.compile(r"\((remote|hybrid|onsite)\)")
_NON_WORD_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")
_COMPANY_SUFFIXES = {"inc", "ltd", "llc", "plc", "gmbh", "corp", "co"}


def canonicalize_url(url: str) -> str:
    """scheme+host+path lowercased, known tracking params stripped, trailing
    slash removed (architecture.md section 8)."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path.lower()
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith(_TRACKING_PARAM_PREFIXES)
        and key.lower() not in _TRACKING_PARAM_EXACT
    ]
    query_pairs.sort()
    query = urlencode(query_pairs)
    return urlunsplit((scheme, netloc, path, query, ""))


def _normalize_text(value: str) -> str:
    text = _SUFFIX_PARENS_RE.sub(" ", value.lower())
    text = _NON_WORD_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def normalize_company(name: str) -> str:
    tokens = _normalize_text(name).split()
    while tokens and tokens[-1] in _COMPANY_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def normalize_title(title: str) -> str:
    return _normalize_text(title)


def normalize_location(location: Location) -> str:
    parts = [location.city, location.country]
    return _normalize_text(" ".join(p for p in parts if p))


def description_fingerprint(description_text: str) -> str:
    normalized = _WHITESPACE_RE.sub(" ", description_text).strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def compute_fingerprint(
    *,
    source_id: str,
    external_id: str,
    raw_url: str,
    company: str,
    title: str,
    location: Location,
    description_text: str,
    posted_at: datetime | None,
) -> JobFingerprint:
    return JobFingerprint(
        canonical_url=canonicalize_url(raw_url),
        external_source_id=f"{source_id}:{external_id}",
        normalized_company=normalize_company(company),
        normalized_title=normalize_title(title),
        normalized_location=normalize_location(location),
        description_fingerprint=description_fingerprint(description_text),
        posted_date=posted_at.date() if posted_at else None,
    )


class DedupTier(StrEnum):
    # "No reasonable doubt" tiers (terminology: MILESTONE_2.md "Deduplication
    # implications"): same source+external id (Tier 1, handled separately via
    # JobRepository.find_by_fingerprint) or an exact cross-source
    # canonical-URL match (this module, new in M2).
    EXACT_DUPLICATE = "exact_duplicate"
    # Strong structural match (company+title+location) plus at least one
    # corroborating deterministic signal, short of certainty. Generalises the
    # old CROSS_SOURCE_DUPLICATE tier's over-strict exact-description-hash
    # requirement (decisions.md D-038).
    PROBABLE_DUPLICATE = "probable_duplicate"
    REPOST = "repost"
    DISTINCT = "distinct"


class DedupResult(BaseModel):
    tier: DedupTier
    matched_job: Job | None = None


def _same_identity(a: JobFingerprint, b: JobFingerprint) -> bool:
    return (
        a.normalized_company == b.normalized_company
        and a.normalized_title == b.normalized_title
        and a.normalized_location == b.normalized_location
    )


def _jaccard_similarity(tokens_a: list[str], tokens_b: list[str]) -> float:
    set_a, set_b = set(tokens_a), set(tokens_b)
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def _has_probable_duplicate_signal(existing: Job, candidate: Job) -> bool:
    """One of three deterministic corroborating signals, evaluated only once
    the company+title+location identity precondition already holds
    (decisions.md D-038; MILESTONE_2.md risk R-8 — identity match is never
    optional)."""
    if (
        existing.fingerprint.description_fingerprint
        == candidate.fingerprint.description_fingerprint
    ):
        return True

    similarity = _jaccard_similarity(
        normalize_tokens(existing.description_text), normalize_tokens(candidate.description_text)
    )
    if similarity >= PROBABLE_DUPLICATE_JACCARD_THRESHOLD:
        return True

    if (
        existing.fingerprint.posted_date is not None
        and candidate.fingerprint.posted_date is not None
        and abs((candidate.fingerprint.posted_date - existing.fingerprint.posted_date).days)
        <= PROBABLE_DUPLICATE_POSTED_DATE_WINDOW_DAYS
        and existing.salary_min is not None
        and candidate.salary_min is not None
        and existing.salary_max is not None
        and candidate.salary_max is not None
        and existing.salary_min == candidate.salary_min
        and existing.salary_max == candidate.salary_max
    ):
        return True

    return False


def _source_id_of(fingerprint: JobFingerprint) -> str:
    return fingerprint.external_source_id.split(":", 1)[0]


def _canonical_url_capable(
    fingerprint: JobFingerprint, source_capabilities: Mapping[str, SourceCapabilities]
) -> bool:
    """decisions.md D-041: canonical_application_url is the one capability
    wired into dedup-tier eligibility this milestone. A source_id absent from
    the map falls back to SourceCapabilities()'s own default (True,
    Adzuna-equivalent) — matching how every other capability consumption
    point in M2 treats a missing/unspecified capabilities block."""
    capabilities = source_capabilities.get(_source_id_of(fingerprint), SourceCapabilities())
    return capabilities.canonical_application_url


def match_against_recent(
    new_job: Job,
    recent_jobs: list[Job],
    *,
    repost_gap_days: int = DEFAULT_REPOST_GAP_DAYS,
    source_capabilities: Mapping[str, SourceCapabilities] | None = None,
) -> DedupResult:
    """Tiers 2-4 (architecture.md section 8). Tier 1 (exact canonical_url +
    external_source_id) is handled separately via
    JobRepository.find_by_fingerprint before this is ever called.

    `source_capabilities` maps source_id -> SourceCapabilities (typically the
    caller's source registry) and gates the new exact cross-source
    canonical-URL tier (decisions.md D-041); omitting it defaults every
    source to SourceCapabilities()'s own True default, same as leaving a
    registry entry's `capabilities` block unset.
    """
    capabilities = source_capabilities or {}
    others = [existing for existing in recent_jobs if existing.job_id != new_job.job_id]

    if _canonical_url_capable(new_job.fingerprint, capabilities):
        for existing in others:
            if existing.fingerprint.canonical_url == new_job.fingerprint.canonical_url and (
                _canonical_url_capable(existing.fingerprint, capabilities)
            ):
                return DedupResult(tier=DedupTier.EXACT_DUPLICATE, matched_job=existing)

    same_identity = [
        existing for existing in others if _same_identity(existing.fingerprint, new_job.fingerprint)
    ]

    for existing in same_identity:
        if _has_probable_duplicate_signal(existing, new_job):
            return DedupResult(tier=DedupTier.PROBABLE_DUPLICATE, matched_job=existing)

    for existing in same_identity:
        if existing.posted_at is not None and new_job.posted_at is not None:
            gap_days = (new_job.posted_at - existing.posted_at).days
            if gap_days >= repost_gap_days:
                return DedupResult(tier=DedupTier.REPOST, matched_job=existing)

    return DedupResult(tier=DedupTier.DISTINCT, matched_job=None)
