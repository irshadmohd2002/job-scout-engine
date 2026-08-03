"""URL canonicalisation and job fingerprinting (architecture.md section 8).

Fingerprint-first deduplication, fuzzy match as a second tier
(decisions.md D-008): exact fingerprint lookup (tier 1) is handled by
JobRepository.find_by_fingerprint; this module also provides tiers 2-3,
evaluated by the pipeline against a recent-jobs window.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from enum import StrEnum
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel

from job_scout.models import Job, JobFingerprint, Location

DEFAULT_REPOST_GAP_DAYS = 21

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
    CROSS_SOURCE_DUPLICATE = "cross_source_duplicate"
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


def match_against_recent(
    new_job: Job, recent_jobs: list[Job], *, repost_gap_days: int = DEFAULT_REPOST_GAP_DAYS
) -> DedupResult:
    """Tiers 2-3 (architecture.md section 8). Tier 1 (exact canonical_url +
    external_source_id) is handled separately via
    JobRepository.find_by_fingerprint before this is ever called."""
    same_identity = [
        existing
        for existing in recent_jobs
        if existing.job_id != new_job.job_id
        and _same_identity(existing.fingerprint, new_job.fingerprint)
    ]

    for existing in same_identity:
        if (
            existing.fingerprint.description_fingerprint
            == new_job.fingerprint.description_fingerprint
        ):
            return DedupResult(tier=DedupTier.CROSS_SOURCE_DUPLICATE, matched_job=existing)

    for existing in same_identity:
        if existing.posted_at is not None and new_job.posted_at is not None:
            gap_days = (new_job.posted_at - existing.posted_at).days
            if gap_days >= repost_gap_days:
                return DedupResult(tier=DedupTier.REPOST, matched_job=existing)

    return DedupResult(tier=DedupTier.DISTINCT, matched_job=None)
