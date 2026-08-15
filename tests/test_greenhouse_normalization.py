"""Greenhouse RawJobRecord -> canonical Job normalisation (Milestone 2
Deliverable 5 step 7; decisions.md D-040/D-047). Mirrors the coverage shape
used for Adzuna's/Reed's normalizers, adapted to Greenhouse's own verified
field set."""

from datetime import UTC, datetime

from job_scout.models import AccessMode, RawJobRecord
from job_scout.pipeline import normalize_greenhouse_record


def _record(payload: dict, *, company_name: str = "Example Co") -> RawJobRecord:
    return RawJobRecord(
        source_id="greenhouse_public_feeds",
        external_id=str(payload["id"]),
        raw_url=payload.get("absolute_url", ""),
        raw_payload={**payload, "_company_name": company_name},
        fetched_at=datetime.now(UTC),
    )


def _full_payload(job_id: int = 42) -> dict:
    return {
        "id": job_id,
        "internal_job_id": 999042,
        "title": "Strategy Manager",
        "updated_at": "2026-07-01T10:00:00-05:00",
        "requisition_id": "50",
        "location": {"name": "London"},
        "absolute_url": "https://boards.greenhouse.io/exampleco/jobs/42",
        "language": "en",
        "metadata": None,
        "content": "<p>Do strategy things, 4-8 years experience.</p>",
    }


def test_greenhouse_record_normalizes_to_job_with_required_fields_populated() -> None:
    job = normalize_greenhouse_record(_record(_full_payload()))
    assert job.title == "Strategy Manager"
    assert job.company == "Example Co"
    assert job.location.city == "London"
    assert job.description_text == "Do strategy things, 4-8 years experience."
    assert job.external_ids[0].source_id == "greenhouse_public_feeds"
    assert job.external_ids[0].external_id == "42"
    assert job.fingerprint.normalized_title == "strategy manager"


def test_greenhouse_source_provenance_correct() -> None:
    job = normalize_greenhouse_record(_record(_full_payload()))
    assert len(job.source_provenance) == 1
    provenance = job.source_provenance[0]
    assert provenance.source_id == "greenhouse_public_feeds"
    assert provenance.access_mode == AccessMode.PUBLIC_ATS_FEED
    assert provenance.external_id == "42"
    assert provenance.raw_url == "https://boards.greenhouse.io/exampleco/jobs/42"


def test_company_name_comes_from_watchlist_stash_not_greenhouse_payload() -> None:
    """Greenhouse's own response never names the company — company must come
    from the `_company_name` stash GreenhouseAdapter adds, never fabricated
    from title/description text."""
    job = normalize_greenhouse_record(_record(_full_payload(), company_name="Acme Example Co"))
    assert job.company == "Acme Example Co"


def test_missing_company_name_stash_normalizes_to_unknown_not_a_crash() -> None:
    payload = _full_payload()
    record = RawJobRecord(
        source_id="greenhouse_public_feeds",
        external_id=str(payload["id"]),
        raw_url=payload["absolute_url"],
        raw_payload=payload,  # no _company_name key at all
        fetched_at=datetime.now(UTC),
    )
    job = normalize_greenhouse_record(record)
    assert job.company == "Unknown"


def test_country_is_never_guessed_from_freeform_location_name() -> None:
    """decisions.md D-047: Greenhouse gives only a single freeform
    location.name string, no structured country code — country must stay
    "" (unknown), never parsed/guessed from text like "Remote - US"."""
    payload = _full_payload()
    payload["location"] = {"name": "Remote - US"}
    job = normalize_greenhouse_record(_record(payload))
    assert job.location.country == ""
    assert job.location.city == "Remote - US"


def test_missing_optional_fields_do_not_fabricate_evidence() -> None:
    minimal = {"id": 1, "title": "Manager"}
    job = normalize_greenhouse_record(_record(minimal))
    assert job.location.city is None
    assert job.location.country == ""
    assert job.salary_min is None
    assert job.salary_max is None
    assert job.salary_currency is None
    assert job.posted_at is None
    assert job.employment_type is None
    assert job.description_text == ""


def test_no_posted_date_field_never_guessed_from_updated_at() -> None:
    """Only `updated_at` (last-modified) is present on this endpoint —
    posted_at must stay None, never conflated with a last-modified
    timestamp (D-040/D-047)."""
    job = normalize_greenhouse_record(_record(_full_payload()))
    assert job.posted_at is None


def test_no_employment_type_field_never_guessed_from_title_or_description() -> None:
    payload = _full_payload()
    payload["title"] = "Full Time Strategy Manager"
    payload["content"] = "This is a full-time permanent role."
    job = normalize_greenhouse_record(_record(payload))
    assert job.employment_type is None


def test_no_salary_fields_from_list_jobs_endpoint() -> None:
    """pay_input_ranges only exists on the single-job detail endpoint behind
    ?pay_transparency=true, never called by this adapter — salary always
    normalizes to None, never fabricated."""
    job = normalize_greenhouse_record(_record(_full_payload()))
    assert job.salary_min is None
    assert job.salary_max is None
    assert job.salary_currency is None


def test_html_content_stripped_to_plain_text() -> None:
    payload = _full_payload()
    payload["content"] = "<p>Line one.</p><p>Line <b>two</b>.</p>"
    job = normalize_greenhouse_record(_record(payload))
    assert "<p>" not in job.description_text
    assert "<b>" not in job.description_text
    assert "Line one." in job.description_text
    assert "Line" in job.description_text and "two" in job.description_text


def test_remote_type_guessed_from_description_text() -> None:
    payload = _full_payload()
    payload["content"] = "<p>This is a fully remote role.</p>"
    job = normalize_greenhouse_record(_record(payload))
    from job_scout.models import RemoteType

    assert job.remote_type == RemoteType.REMOTE
