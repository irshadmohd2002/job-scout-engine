from datetime import UTC, datetime, timedelta

from job_scout.deduplication import (
    DedupTier,
    canonicalize_url,
    compute_fingerprint,
    match_against_recent,
    normalize_company,
    normalize_title,
)
from job_scout.models import Location
from tests.factories import make_job


def test_canonicalize_url_strips_tracking_params() -> None:
    a = canonicalize_url("https://Example.com/Jobs/123?utm_source=x&utm_medium=y&ref=z")
    b = canonicalize_url("https://example.com/jobs/123/?ref=z")
    assert a == b


def test_canonicalize_url_strips_trailing_slash_and_lowercases() -> None:
    assert canonicalize_url("https://Example.com/Jobs/123/") == canonicalize_url(
        "https://example.com/jobs/123"
    )


def test_normalize_company_strips_suffix_and_punctuation() -> None:
    # both "Corp" and "Inc" are stripped as common suffixes, so postings for
    # the same employer under slightly different legal-entity naming
    # normalize to the same value.
    assert normalize_company("Example Corp, Inc.") == "example"
    assert normalize_company("Example Ltd") == "example"


def test_normalize_title_strips_remote_marker() -> None:
    assert normalize_title("Strategy Manager (Remote)") == "strategy manager"


def test_identical_url_different_tracking_params_same_fingerprint() -> None:
    fp_a = compute_fingerprint(
        source_id="adzuna_api",
        external_id="1",
        raw_url="https://example.com/jobs/1?utm_source=a",
        company="Example Corp",
        title="Strategy Manager",
        location=Location(country="GB", city="London"),
        description_text="Do strategy things.",
        posted_at=None,
    )
    fp_b = compute_fingerprint(
        source_id="adzuna_api",
        external_id="1",
        raw_url="https://example.com/jobs/1?utm_source=b&trk=1",
        company="Example Corp",
        title="Strategy Manager",
        location=Location(country="GB", city="London"),
        description_text="Do strategy things.",
        posted_at=None,
    )
    assert fp_a.canonical_url == fp_b.canonical_url
    assert fp_a == fp_b


def test_cross_source_duplicate_detected() -> None:
    now = datetime.now(UTC)
    existing = make_job(job_id="job-existing", posted_at=now - timedelta(days=1))
    new_job = make_job(
        job_id="job-new",
        posted_at=now,
        # same normalized company/title/location/description as `existing`
        # (matching fingerprint defaults), but discovered via a different
        # source/URL — a genuine cross-source duplicate.
        fingerprint=existing.fingerprint.model_copy(
            update={
                "external_source_id": "other_source:999",
                "canonical_url": "https://example.com/other",
            }
        ),
    )
    result = match_against_recent(new_job, [existing])
    assert result.tier == DedupTier.CROSS_SOURCE_DUPLICATE
    assert result.matched_job is not None
    assert result.matched_job.job_id == "job-existing"


def test_genuinely_different_jobs_at_same_company_not_merged() -> None:
    from tests.factories import make_fingerprint

    existing = make_job(
        job_id="job-a",
        fingerprint=make_fingerprint(
            normalized_company="example corp",
            normalized_title="strategy manager",
            normalized_location="london gb",
        ),
    )
    new_job = make_job(
        job_id="job-b",
        fingerprint=make_fingerprint(
            normalized_company="example corp",
            normalized_title="finance analyst",
            normalized_location="london gb",
        ),
    )
    result = match_against_recent(new_job, [existing])
    assert result.tier == DedupTier.DISTINCT


def test_repost_linked_when_older_than_gap_with_changed_description() -> None:
    from tests.factories import make_fingerprint

    now = datetime.now(UTC)
    existing = make_job(
        job_id="job-old",
        posted_at=now - timedelta(days=30),
        fingerprint=make_fingerprint(description_fingerprint="a" * 64),
    )
    new_job = make_job(
        job_id="job-repost",
        posted_at=now,
        fingerprint=make_fingerprint(description_fingerprint="b" * 64),
    )
    result = match_against_recent(new_job, [existing])
    assert result.tier == DedupTier.REPOST
    assert result.matched_job is not None
    assert result.matched_job.job_id == "job-old"


def test_recent_repost_within_gap_treated_as_distinct() -> None:
    from tests.factories import make_fingerprint

    now = datetime.now(UTC)
    existing = make_job(
        job_id="job-old",
        posted_at=now - timedelta(days=2),
        fingerprint=make_fingerprint(description_fingerprint="a" * 64),
    )
    new_job = make_job(
        job_id="job-newer",
        posted_at=now,
        fingerprint=make_fingerprint(description_fingerprint="b" * 64),
    )
    result = match_against_recent(new_job, [existing])
    assert result.tier == DedupTier.DISTINCT
