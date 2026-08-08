"""Reed RawJobRecord -> canonical Job normalisation (Milestone 2 Deliverable
5 step 5; decisions.md D-040/D-046). Mirrors the coverage shape used for
Adzuna's normalizer, adapted to Reed's own verified field set."""

from datetime import UTC, datetime

from job_scout.models import AccessMode, RawJobRecord
from job_scout.pipeline import normalize_reed_record


def _record(payload: dict) -> RawJobRecord:
    return RawJobRecord(
        source_id="reed_api",
        external_id=str(payload["jobId"]),
        raw_url="",
        raw_payload={**payload, "_query_country": "GB"},
        fetched_at=datetime.now(UTC),
    )


def _full_payload(job_id: int = 42) -> dict:
    return {
        "jobId": job_id,
        "employerId": 999,
        "employerName": "Example Corp",
        "jobTitle": "Strategy Manager",
        "locationName": "London",
        "description": "<p>Do strategy things, 4-8 years experience.</p>",
        "minimumSalary": 60000,
        "maximumSalary": 80000,
    }


def test_reed_record_normalizes_to_job_with_required_fields_populated() -> None:
    job = normalize_reed_record(_record(_full_payload()))
    assert job.title == "Strategy Manager"
    assert job.company == "Example Corp"
    assert job.location.country == "GB"
    assert job.location.city == "London"
    assert job.description_text == "Do strategy things, 4-8 years experience."
    assert job.salary_min == 60000
    assert job.salary_max == 80000
    assert job.external_ids[0].source_id == "reed_api"
    assert job.external_ids[0].external_id == "42"
    assert job.fingerprint.normalized_title == "strategy manager"


def test_reed_source_provenance_correct() -> None:
    job = normalize_reed_record(_record(_full_payload()))
    assert len(job.source_provenance) == 1
    provenance = job.source_provenance[0]
    assert provenance.source_id == "reed_api"
    assert provenance.access_mode == AccessMode.PUBLIC_API
    assert provenance.external_id == "42"
    assert provenance.raw_url == ""


def test_missing_optional_fields_do_not_fabricate_evidence() -> None:
    minimal = {"jobId": 1, "jobTitle": "Manager"}
    job = normalize_reed_record(_record(minimal))
    assert job.company == "Unknown"
    assert job.location.city is None
    assert job.salary_min is None
    assert job.salary_max is None
    assert job.salary_currency is None
    assert job.posted_at is None
    assert job.employment_type is None
    assert job.description_text == ""


def test_hidden_salary_normalizes_to_none_not_zero() -> None:
    payload = _full_payload()
    payload["minimumSalary"] = None
    payload["maximumSalary"] = None
    job = normalize_reed_record(_record(payload))
    assert job.salary_min is None
    assert job.salary_max is None


def test_no_posted_date_field_never_guessed() -> None:
    """Reed's documented Search Returns has no posted-date field — posted_at
    must stay None, never inferred from another field (D-040)."""
    job = normalize_reed_record(_record(_full_payload()))
    assert job.posted_at is None


def test_no_employment_type_field_never_guessed_from_title_or_description() -> None:
    payload = _full_payload()
    payload["jobTitle"] = "Full Time Strategy Manager"
    payload["description"] = "This is a full-time permanent role."
    job = normalize_reed_record(_record(payload))
    assert job.employment_type is None


def test_no_currency_field_from_search_endpoint() -> None:
    """Currency is a Details-endpoint-only field per Reed's docs; this
    adapter only calls Search, so salary_currency always normalizes to
    None, never fabricated."""
    job = normalize_reed_record(_record(_full_payload()))
    assert job.salary_currency is None
