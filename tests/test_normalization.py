from datetime import UTC, datetime

from job_scout.matching.normalize import (
    PhraseMatch,
    match_phrase,
    meaningful_tokens,
    normalize_text,
    normalize_tokens,
)
from job_scout.models import RawJobRecord, RemoteType
from job_scout.pipeline import normalize_adzuna_record, strip_html


def _raw_record(**payload_overrides: object) -> RawJobRecord:
    payload = {
        "id": "12345",
        "title": "Strategy Manager",
        "company": {"display_name": "Example Corp"},
        "location": {"display_name": "London"},
        "redirect_url": "https://example.com/jobs/12345?utm_source=x",
        "created": "2026-07-01T10:00:00Z",
        "description": "<p>Great <b>strategy</b> role.</p>\n<p>4-8 years experience.</p>&nbsp;",
        "salary_min": 60000,
        "salary_max": 80000,
        "contract_time": "full_time",
        "_query_country": "GB",
    }
    payload.update(payload_overrides)
    return RawJobRecord(
        source_id="adzuna_api",
        external_id=str(payload["id"]),
        raw_url=str(payload["redirect_url"]),
        raw_payload=payload,
        fetched_at=datetime.now(UTC),
    )


def test_strip_html_removes_tags_and_entities() -> None:
    assert strip_html("<p>Hello&nbsp;<b>World</b></p>") == "Hello World"


def test_normalize_adzuna_record_maps_expected_fields() -> None:
    job = normalize_adzuna_record(_raw_record())
    assert job.title == "Strategy Manager"
    assert job.company == "Example Corp"
    assert job.location.country == "GB"
    assert job.location.city == "London"
    assert job.employment_type == "full_time"
    assert job.salary_min == 60000
    assert job.salary_max == 80000
    assert job.source_provenance[0].source_id == "adzuna_api"
    assert job.source_provenance[0].raw_url.endswith("12345?utm_source=x")
    assert job.external_ids[0].external_id == "12345"


def test_normalize_adzuna_record_strips_html_from_description() -> None:
    job = normalize_adzuna_record(_raw_record())
    assert "<p>" not in job.description_text
    assert "<b>" not in job.description_text
    assert job.description_text == "Great strategy role. 4-8 years experience."
    # the raw HTML is preserved separately, per architecture.md section 2.4
    assert "<p>" in job.description_raw


def test_normalize_adzuna_record_parses_posted_at() -> None:
    job = normalize_adzuna_record(_raw_record(created="2026-07-01T10:00:00Z"))
    assert job.posted_at is not None
    assert job.posted_at.year == 2026
    assert job.posted_at.month == 7


def test_normalize_adzuna_record_handles_missing_created_date() -> None:
    job = normalize_adzuna_record(_raw_record(created=None))
    assert job.posted_at is None


def test_normalize_adzuna_record_guesses_remote_type() -> None:
    remote_job = normalize_adzuna_record(_raw_record(description="Fully remote role."))
    hybrid_job = normalize_adzuna_record(_raw_record(description="Hybrid working available."))
    onsite_job = normalize_adzuna_record(_raw_record(description="Office-based role."))
    assert remote_job.remote_type == RemoteType.REMOTE
    assert hybrid_job.remote_type == RemoteType.HYBRID
    assert onsite_job.remote_type == RemoteType.UNKNOWN


def test_normalize_adzuna_record_fingerprint_uses_canonical_url() -> None:
    job = normalize_adzuna_record(_raw_record())
    assert "utm_source" not in job.fingerprint.canonical_url


# --- Connector-token handling (decisions.md D-033) --------------------------

_STRONG_TITLE_COVERAGE = 0.75


def _title_match(phrase: str, title: str) -> PhraseMatch:
    return match_phrase(
        phrase,
        title_norm=normalize_text(title),
        title_tokens=set(normalize_tokens(title)),
        description_norm="",
        coverage_threshold=_STRONG_TITLE_COVERAGE,
    )


def test_data_and_analytics_associate_does_not_strongly_match_reporting_partner_title() -> None:
    """The confirmed false positive: 'Data & Analytics Associate' shares
    data/and/analytics (3/4 raw tokens = 0.75) with 'People Reporting and
    Analytics Data Partner', but the occupational head term 'associate' is
    entirely absent. Meaningful-token coverage (data, analytics, associate
    -> 2/3 = 0.67) must fall below the strong-match gate."""
    result = _title_match(
        "Data & Analytics Associate", "People Reporting and Analytics Data Partner"
    )
    assert result.method == "none"


def test_data_and_analytics_associate_does_match_reordered_variant() -> None:
    result = _title_match("Data & Analytics Associate", "Associate - Data and Analytics")
    assert result.method in {"exact_phrase", "token_coverage"}
    assert result.strength >= _STRONG_TITLE_COVERAGE


def test_strategy_and_transformation_lead_matches_ampersand_and_and_variants() -> None:
    result = _title_match("Strategy & Transformation Lead", "Strategy and Transformation Lead")
    assert result.method == "exact_phrase"


def test_business_strategy_analyst_consultant_matches_punctuation_variants() -> None:
    for variant in [
        "Business Strategy Analyst/Consultant",
        "Business Strategy Analyst - Consultant",
        "Business Strategy Analyst, Consultant",
    ]:
        result = _title_match("Business Strategy Analyst/Consultant", variant)
        assert result.method == "exact_phrase", variant


def test_connector_word_removal_never_produces_an_empty_phrase() -> None:
    assert meaningful_tokens(["and", "the", "of"]) == ["and", "the", "of"]
    assert meaningful_tokens([]) == []


def test_one_matching_content_token_from_multiword_phrase_does_not_pass() -> None:
    result = _title_match("Data & Analytics Associate", "Data Entry Clerk")
    assert result.method == "none"
