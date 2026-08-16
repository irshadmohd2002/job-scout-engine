"""Lever RawJobRecord -> canonical Job normalisation (Milestone 2
Deliverable 5 step 8; decisions.md D-040/D-048). Mirrors the coverage shape
used for Greenhouse's normalizer, adapted to Lever's own verified,
genuinely more structured field set (real country/workplaceType/salaryRange
fields Greenhouse's feed does not have)."""

from datetime import UTC, datetime

from job_scout.models import AccessMode, RawJobRecord, RemoteType
from job_scout.pipeline import normalize_lever_record


def _record(payload: dict, *, company_name: str = "Example Co") -> RawJobRecord:
    return RawJobRecord(
        source_id="lever_public_postings",
        external_id=str(payload["id"]),
        raw_url=payload.get("hostedUrl", ""),
        raw_payload={**payload, "_company_name": company_name},
        fetched_at=datetime.now(UTC),
    )


def _full_payload(posting_id: int = 42) -> dict:
    return {
        "id": posting_id,
        "text": "Strategy Manager",
        "categories": {
            "location": "London",
            "commitment": "Full-time",
            "team": "Strategy",
            "department": "Operations",
        },
        "country": "GB",
        "description": "<p>Do strategy things, 4-8 years experience.</p>",
        "hostedUrl": "https://jobs.lever.co/exampleco/42",
        "applyUrl": "https://jobs.lever.co/exampleco/42/apply",
        "workplaceType": "remote",
        "salaryRange": {"currency": "USD", "min": 90000, "max": 120000},
    }


def test_lever_record_normalizes_to_job_with_required_fields_populated() -> None:
    job = normalize_lever_record(_record(_full_payload()))
    assert job.title == "Strategy Manager"
    assert job.company == "Example Co"
    assert job.location.city == "London"
    assert job.description_text == "Do strategy things, 4-8 years experience."
    assert job.external_ids[0].source_id == "lever_public_postings"
    assert job.external_ids[0].external_id == "42"
    assert job.fingerprint.normalized_title == "strategy manager"


def test_lever_source_provenance_correct() -> None:
    job = normalize_lever_record(_record(_full_payload()))
    assert len(job.source_provenance) == 1
    provenance = job.source_provenance[0]
    assert provenance.source_id == "lever_public_postings"
    assert provenance.access_mode == AccessMode.PUBLIC_ATS_FEED
    assert provenance.external_id == "42"
    assert provenance.raw_url == "https://jobs.lever.co/exampleco/42"


def test_company_name_comes_from_watchlist_stash_not_lever_payload() -> None:
    """Lever's own response never names the company — company must come
    from the `_company_name` stash LeverAdapter adds, never fabricated
    from title/description text."""
    job = normalize_lever_record(_record(_full_payload(), company_name="Acme Example Co"))
    assert job.company == "Acme Example Co"


def test_missing_company_name_stash_normalizes_to_unknown_not_a_crash() -> None:
    payload = _full_payload()
    record = RawJobRecord(
        source_id="lever_public_postings",
        external_id=str(payload["id"]),
        raw_url=payload["hostedUrl"],
        raw_payload=payload,  # no _company_name key at all
        fetched_at=datetime.now(UTC),
    )
    job = normalize_lever_record(record)
    assert job.company == "Unknown"


# --- country: real structured field, unlike Greenhouse -----------------------


def test_country_uses_structured_field_directly() -> None:
    """decisions.md D-048: unlike Greenhouse, Lever documents a real,
    structured country field — used directly, never inferred/guessed."""
    payload = _full_payload()
    payload["country"] = "DE"
    job = normalize_lever_record(_record(payload))
    assert job.location.country == "DE"


def test_null_country_normalizes_to_empty_string_not_fabricated() -> None:
    payload = _full_payload()
    payload["country"] = None
    job = normalize_lever_record(_record(payload))
    assert job.location.country == ""


def test_missing_country_key_normalizes_to_empty_string() -> None:
    payload = _full_payload()
    del payload["country"]
    job = normalize_lever_record(_record(payload))
    assert job.location.country == ""


def test_country_hard_filter_behavior_uses_structured_country() -> None:
    """A search profile scoping by included_countries should be able to
    correctly MATCH a Lever job via its real structured country field —
    the direct positive counterpart to Greenhouse's documented
    always-rejected consequence (decisions.md D-047)."""
    from job_scout.matching.hard_filters import evaluate_hard_filters
    from tests.factories import make_candidate_profile, make_search_profile

    payload = _full_payload()
    payload["country"] = "GB"
    job = normalize_lever_record(_record(payload))
    candidate = make_candidate_profile()
    # employment_types=[] isolates the country check from the unrelated
    # employment_type_mismatch rule ("Full-time" from Lever's categories.
    # commitment vs. make_search_profile's default "full_time").
    search = make_search_profile(included_countries=["GB"], employment_types=[])
    result = evaluate_hard_filters(job, candidate, search)
    assert result.passed is True


def test_country_hard_filter_rejects_when_lever_reports_unsupported_country() -> None:
    from job_scout.matching.hard_filters import evaluate_hard_filters
    from tests.factories import make_candidate_profile, make_search_profile

    payload = _full_payload()
    payload["country"] = "FR"
    job = normalize_lever_record(_record(payload))
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["GB"])
    result = evaluate_hard_filters(job, candidate, search)
    assert result.passed is False
    assert any(r.rule == "country_not_included" for r in result.rejections)


# --- employment type -----------------------------------------------------


def test_employment_type_from_categories_commitment() -> None:
    job = normalize_lever_record(_record(_full_payload()))
    assert job.employment_type == "Full-time"


def test_missing_commitment_normalizes_employment_type_to_none() -> None:
    payload = _full_payload()
    del payload["categories"]["commitment"]
    job = normalize_lever_record(_record(payload))
    assert job.employment_type is None


# --- remote_type: structured workplaceType, not the shared heuristic --------


def test_workplace_type_remote_maps_to_remote_type_remote() -> None:
    payload = _full_payload()
    payload["workplaceType"] = "remote"
    job = normalize_lever_record(_record(payload))
    assert job.remote_type == RemoteType.REMOTE


def test_workplace_type_hybrid_maps_to_remote_type_hybrid() -> None:
    payload = _full_payload()
    payload["workplaceType"] = "hybrid"
    job = normalize_lever_record(_record(payload))
    assert job.remote_type == RemoteType.HYBRID


def test_workplace_type_on_site_maps_to_remote_type_onsite() -> None:
    payload = _full_payload()
    payload["workplaceType"] = "on-site"
    job = normalize_lever_record(_record(payload))
    assert job.remote_type == RemoteType.ONSITE


def test_workplace_type_unspecified_maps_to_remote_type_unknown() -> None:
    payload = _full_payload()
    payload["workplaceType"] = "unspecified"
    job = normalize_lever_record(_record(payload))
    assert job.remote_type == RemoteType.UNKNOWN


def test_missing_workplace_type_maps_to_remote_type_unknown() -> None:
    payload = _full_payload()
    del payload["workplaceType"]
    job = normalize_lever_record(_record(payload))
    assert job.remote_type == RemoteType.UNKNOWN


def test_remote_type_never_falls_back_to_description_text_heuristic() -> None:
    """decisions.md D-048: even when the description text strongly implies
    a different remote arrangement, the structured workplaceType field
    always wins — this is an intentional source-specific mapping, not the
    shared _guess_remote_type heuristic."""
    payload = _full_payload()
    payload["description"] = "<p>This is a fully remote role.</p>"
    payload["workplaceType"] = "on-site"
    job = normalize_lever_record(_record(payload))
    assert job.remote_type == RemoteType.ONSITE


# --- salary: real, optional salaryRange field -------------------------------


def test_salary_range_parsed_when_present() -> None:
    job = normalize_lever_record(_record(_full_payload()))
    assert job.salary_min == 90000
    assert job.salary_max == 120000
    assert job.salary_currency == "USD"


def test_missing_salary_range_normalizes_to_none_never_zero() -> None:
    payload = _full_payload()
    del payload["salaryRange"]
    job = normalize_lever_record(_record(payload))
    assert job.salary_min is None
    assert job.salary_max is None
    assert job.salary_currency is None


def test_partial_salary_range_missing_fields_normalize_to_none() -> None:
    payload = _full_payload()
    payload["salaryRange"] = {"currency": "USD"}
    job = normalize_lever_record(_record(payload))
    assert job.salary_min is None
    assert job.salary_max is None
    assert job.salary_currency == "USD"


def test_salary_never_inferred_from_salary_description_text() -> None:
    payload = _full_payload()
    del payload["salaryRange"]
    payload["salaryDescription"] = "Competitive, $90,000 - $120,000 annually"
    job = normalize_lever_record(_record(payload))
    assert job.salary_min is None
    assert job.salary_max is None
    assert job.salary_currency is None


# --- posted_at: never guessed from createdAt --------------------------------


def test_no_posted_date_field_never_guessed_from_created_at() -> None:
    """Only an undocumented, reportedly-unreliable `createdAt` field may be
    present on a live response (lever/postings-api issue #35) — posted_at
    must stay None, never conflated with it (D-040/D-048)."""
    payload = _full_payload()
    payload["createdAt"] = 1740000000000
    job = normalize_lever_record(_record(payload))
    assert job.posted_at is None


# --- generic missing-field / html-stripping coverage ------------------------


def test_missing_optional_fields_do_not_fabricate_evidence() -> None:
    minimal = {"id": 1, "text": "Manager"}
    job = normalize_lever_record(_record(minimal))
    assert job.location.city is None
    assert job.location.country == ""
    assert job.salary_min is None
    assert job.salary_max is None
    assert job.salary_currency is None
    assert job.posted_at is None
    assert job.employment_type is None
    assert job.description_text == ""
    assert job.remote_type == RemoteType.UNKNOWN


def test_html_description_stripped_to_plain_text() -> None:
    payload = _full_payload()
    payload["description"] = "<p>Line one.</p><p>Line <b>two</b>.</p>"
    job = normalize_lever_record(_record(payload))
    assert "<p>" not in job.description_text
    assert "<b>" not in job.description_text
    assert "Line one." in job.description_text
    assert "Line" in job.description_text and "two" in job.description_text
