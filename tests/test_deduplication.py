from datetime import UTC, date, datetime, timedelta

from job_scout.deduplication import (
    DedupTier,
    canonicalize_url,
    compute_fingerprint,
    match_against_recent,
    normalize_company,
    normalize_title,
)
from job_scout.models import Location, SourceCapabilities
from tests.factories import make_fingerprint, make_job


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


def test_probable_duplicate_detected_via_identical_description_hash() -> None:
    now = datetime.now(UTC)
    existing = make_job(job_id="job-existing", posted_at=now - timedelta(days=1))
    new_job = make_job(
        job_id="job-new",
        posted_at=now,
        # same normalized company/title/location/description as `existing`
        # (matching fingerprint defaults), but discovered via a different
        # source/URL — a genuine cross-source duplicate corroborated by an
        # identical description hash.
        fingerprint=existing.fingerprint.model_copy(
            update={
                "external_source_id": "other_source:999",
                "canonical_url": "https://example.com/other",
            }
        ),
    )
    result = match_against_recent(new_job, [existing])
    assert result.tier == DedupTier.PROBABLE_DUPLICATE
    assert result.matched_job is not None
    assert result.matched_job.job_id == "job-existing"


def test_genuinely_different_jobs_at_same_company_not_merged() -> None:
    existing = make_job(
        job_id="job-a",
        fingerprint=make_fingerprint(
            canonical_url="https://example.com/jobs/a",
            normalized_company="example corp",
            normalized_title="strategy manager",
            normalized_location="london gb",
        ),
    )
    new_job = make_job(
        job_id="job-b",
        fingerprint=make_fingerprint(
            canonical_url="https://example.com/jobs/b",
            normalized_company="example corp",
            normalized_title="finance analyst",
            normalized_location="london gb",
        ),
    )
    result = match_against_recent(new_job, [existing])
    assert result.tier == DedupTier.DISTINCT


def test_repost_linked_when_older_than_gap_with_changed_description() -> None:
    now = datetime.now(UTC)
    existing = make_job(
        job_id="job-old",
        posted_at=now - timedelta(days=30),
        description_text="Great strategy role.",
        fingerprint=make_fingerprint(
            canonical_url="https://example.com/jobs/old", description_fingerprint="a" * 64
        ),
    )
    new_job = make_job(
        job_id="job-repost",
        posted_at=now,
        # genuinely different text (not just a different hash) and a
        # different URL (a repost is a new listing), so neither the new
        # exact cross-source-URL tier nor the Jaccard corroboration signal
        # accidentally fires and masks this as a duplicate instead of a
        # repost.
        description_text="Nothing at all matches here whatsoever.",
        fingerprint=make_fingerprint(
            canonical_url="https://example.com/jobs/repost", description_fingerprint="b" * 64
        ),
    )
    result = match_against_recent(new_job, [existing])
    assert result.tier == DedupTier.REPOST
    assert result.matched_job is not None
    assert result.matched_job.job_id == "job-old"


def test_recent_repost_within_gap_treated_as_distinct() -> None:
    now = datetime.now(UTC)
    existing = make_job(
        job_id="job-old",
        posted_at=now - timedelta(days=2),
        description_text="Great strategy role.",
        fingerprint=make_fingerprint(
            canonical_url="https://example.com/jobs/old", description_fingerprint="a" * 64
        ),
    )
    new_job = make_job(
        job_id="job-newer",
        posted_at=now,
        description_text="Nothing at all matches here whatsoever.",
        fingerprint=make_fingerprint(
            canonical_url="https://example.com/jobs/newer", description_fingerprint="b" * 64
        ),
    )
    result = match_against_recent(new_job, [existing])
    assert result.tier == DedupTier.DISTINCT


# --- Milestone 2 Deliverable 5 step 9: new tiers -----------------------------


def test_exact_duplicate_via_canonical_url_alone_ignores_identity() -> None:
    """decisions.md D-038: an exact canonical_url match across two different
    sources is "no reasonable doubt" evidence, even when company/title/
    location normalisation differs (e.g. one source's free-text location
    parsing vs. another's structured field)."""
    existing = make_job(
        job_id="job-existing",
        fingerprint=make_fingerprint(
            normalized_company="example corp",
            normalized_title="strategy manager",
            normalized_location="london gb",
        ),
    )
    new_job = make_job(
        job_id="job-new",
        fingerprint=make_fingerprint(
            external_source_id="other_source:999",
            normalized_company="different co",
            normalized_title="finance analyst",
            normalized_location="berlin de",
        ),
    )
    result = match_against_recent(new_job, [existing])
    assert result.tier == DedupTier.EXACT_DUPLICATE
    assert result.matched_job is not None
    assert result.matched_job.job_id == "job-existing"


def test_exact_url_tier_gated_off_when_new_jobs_source_lacks_canonical_url_capability() -> None:
    """decisions.md D-041: a source without a stable canonical URL (Reed's
    real, verified capability — canonical_application_url: false) must never
    participate in the exact-URL tier, even when its URL happens to coincide
    with another source's — guards against a false merge from a
    non-canonical/session-scoped URL."""
    existing = make_job(job_id="job-existing")
    new_job = make_job(
        job_id="job-new",
        fingerprint=make_fingerprint(external_source_id="reed_api:999"),
    )
    capabilities = {"reed_api": SourceCapabilities(canonical_application_url=False)}
    result = match_against_recent(new_job, [existing], source_capabilities=capabilities)
    # falls through to the probable-duplicate tier instead (same identity +
    # identical description hash from the shared make_fingerprint default)
    assert result.tier == DedupTier.PROBABLE_DUPLICATE


def test_exact_url_tier_gated_off_when_existing_jobs_source_lacks_capability() -> None:
    existing = make_job(
        job_id="job-existing", fingerprint=make_fingerprint(external_source_id="reed_api:1")
    )
    new_job = make_job(
        job_id="job-new", fingerprint=make_fingerprint(external_source_id="other_source:999")
    )
    capabilities = {"reed_api": SourceCapabilities(canonical_application_url=False)}
    result = match_against_recent(new_job, [existing], source_capabilities=capabilities)
    assert result.tier == DedupTier.PROBABLE_DUPLICATE


def test_probable_duplicate_via_bounded_token_overlap() -> None:
    """Same identity, different description hash, but the description text
    itself overlaps enough (Jaccard >= 0.6) to corroborate a duplicate —
    deterministic, no embeddings."""
    existing = make_job(
        job_id="job-existing",
        description_text="Great strategy and transformation role for a senior leader.",
        fingerprint=make_fingerprint(
            canonical_url="https://example.com/jobs/existing", description_fingerprint="a" * 64
        ),
    )
    new_job = make_job(
        job_id="job-new",
        description_text="Great strategy and transformation role for a senior manager.",
        fingerprint=make_fingerprint(
            canonical_url="https://example.com/jobs/new",
            external_source_id="other_source:1",
            description_fingerprint="b" * 64,
        ),
    )
    result = match_against_recent(new_job, [existing])
    assert result.tier == DedupTier.PROBABLE_DUPLICATE
    assert result.matched_job is not None
    assert result.matched_job.job_id == "job-existing"


def test_probable_duplicate_via_close_posted_date_and_matching_salary() -> None:
    """Same identity, different/low-overlap description, but posted within
    the window and identical salary on both sides."""
    existing = make_job(
        job_id="job-existing",
        description_text="Alpha bravo charlie delta echo foxtrot golf.",
        salary_min=60000,
        salary_max=80000,
        fingerprint=make_fingerprint(
            canonical_url="https://example.com/jobs/existing",
            description_fingerprint="a" * 64,
            posted_date=date(2026, 7, 1),
        ),
    )
    new_job = make_job(
        job_id="job-new",
        description_text="Nothing overlaps with the other listing whatsoever now.",
        salary_min=60000,
        salary_max=80000,
        fingerprint=make_fingerprint(
            canonical_url="https://example.com/jobs/new",
            external_source_id="other_source:1",
            description_fingerprint="b" * 64,
            posted_date=date(2026, 7, 3),
        ),
    )
    result = match_against_recent(new_job, [existing])
    assert result.tier == DedupTier.PROBABLE_DUPLICATE
    assert result.matched_job is not None
    assert result.matched_job.job_id == "job-existing"


def test_no_probable_duplicate_signal_without_corroboration_stays_distinct() -> None:
    """Same identity is never sufficient alone (MILESTONE_2.md risk R-8) —
    without a matching description hash, sufficient token overlap, or a
    close posted_date + matching salary, this is a distinct job, not a
    repost either (posted_at gap below the repost threshold)."""
    now = datetime.now(UTC)
    existing = make_job(
        job_id="job-existing",
        posted_at=now,
        description_text="Alpha bravo charlie delta echo foxtrot golf.",
        salary_min=None,
        salary_max=None,
        fingerprint=make_fingerprint(
            canonical_url="https://example.com/jobs/existing", description_fingerprint="a" * 64
        ),
    )
    new_job = make_job(
        job_id="job-new",
        posted_at=now,
        description_text="Nothing overlaps with the other listing whatsoever now.",
        salary_min=None,
        salary_max=None,
        fingerprint=make_fingerprint(
            canonical_url="https://example.com/jobs/new",
            external_source_id="other_source:1",
            description_fingerprint="b" * 64,
        ),
    )
    result = match_against_recent(new_job, [existing])
    assert result.tier == DedupTier.DISTINCT
