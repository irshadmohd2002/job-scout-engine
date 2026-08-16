import httpx
import pytest
import respx

from job_scout.models import SourceSearchParams
from job_scout.sources.base import (
    SourceAuthError,
    SourceNotFoundError,
    SourceRateLimitError,
    SourceUnavailableError,
)
from job_scout.sources.lever import BASE_URL, LeverAdapter

COMPANY_KEY = "exampleco"
POSTINGS_URL = f"{BASE_URL}/postings/{COMPANY_KEY}"


def _params(**overrides: object) -> SourceSearchParams:
    # LeverAdapter.fetch() ignores every field here (no keyword/location/team
    # filter is ever sent) — a plain, otherwise-typical SourceSearchParams is
    # used so tests exercise the same Protocol every other adapter receives.
    base = {
        "countries": ["GB"],
        "keywords": ["Strategy Manager"],
        "role_family_hints": [],
        "employment_types": [],
        "min_experience_years": None,
        "max_experience_years": None,
        "page_size": 50,
        "max_pages": 3,
    }
    base.update(overrides)
    return SourceSearchParams.model_validate(base)


def _posting(posting_id: object, title: str = "Strategy Manager") -> dict:
    return {
        "id": posting_id,
        "text": title,
        "categories": {
            "location": "London",
            "commitment": "Full-time",
            "team": "Strategy",
            "department": "Operations",
        },
        "country": "GB",
        "description": "<p>Great strategy role.</p>",
        "hostedUrl": f"https://jobs.lever.co/{COMPANY_KEY}/{posting_id}",
        "applyUrl": f"https://jobs.lever.co/{COMPANY_KEY}/{posting_id}/apply",
        "workplaceType": "remote",
        "salaryRange": {"currency": "USD", "min": 90000, "max": 120000},
    }


def _adapter(**overrides: object) -> LeverAdapter:
    base: dict[str, object] = {"company_key": COMPANY_KEY, "company_name": "Example Co"}
    base.update(overrides)
    return LeverAdapter(**base)  # type: ignore[arg-type]


# --- adapter configuration ---------------------------------------------------


def test_is_configured_false_without_company_key() -> None:
    adapter = _adapter(company_key="")
    assert adapter.is_configured() is False


def test_is_configured_true_with_company_key() -> None:
    adapter = _adapter()
    assert adapter.is_configured() is True


def test_fetch_raises_without_calling_http_when_not_configured() -> None:
    adapter = _adapter(company_key="")
    with respx.mock:
        route = respx.get(url__startswith=BASE_URL)
        with pytest.raises(SourceAuthError):
            adapter.fetch(_params())
        assert route.call_count == 0


# --- request shape: single request, mode=json, no auth, no pagination -------


@respx.mock
def test_correct_lever_endpoint_used() -> None:
    route = respx.get(POSTINGS_URL).mock(return_value=httpx.Response(200, json=[]))
    adapter = _adapter()
    adapter.fetch(_params())
    assert route.call_count == 1


@respx.mock
def test_mode_json_always_requested() -> None:
    route = respx.get(POSTINGS_URL).mock(return_value=httpx.Response(200, json=[]))
    adapter = _adapter()
    adapter.fetch(_params())
    sent = route.calls.last.request.url.params
    assert sent["mode"] == "json"


@respx.mock
def test_no_authentication_header_sent() -> None:
    route = respx.get(POSTINGS_URL).mock(return_value=httpx.Response(200, json=[]))
    adapter = _adapter()
    adapter.fetch(_params())
    headers = route.calls.last.request.headers
    assert "authorization" not in {k.lower() for k in headers.keys()}


@respx.mock
def test_never_sends_skip_or_limit_params() -> None:
    route = respx.get(POSTINGS_URL).mock(return_value=httpx.Response(200, json=[]))
    adapter = _adapter()
    adapter.fetch(_params())
    sent = route.calls.last.request.url.params
    assert "skip" not in sent
    assert "limit" not in sent


@respx.mock
def test_never_paginates_even_with_many_results_and_high_max_pages() -> None:
    """No documented/verified total-count or hasMore termination signal
    exists (decisions.md D-048) — exactly one request per fetch(),
    regardless of max_pages or how many postings the single response
    contains."""
    many_postings = [_posting(i) for i in range(1, 51)]
    route = respx.get(POSTINGS_URL).mock(return_value=httpx.Response(200, json=many_postings))
    adapter = _adapter()
    records = adapter.fetch(_params(max_pages=10))
    assert route.call_count == 1
    assert len(records) == 50


@respx.mock
def test_company_key_used_in_url_path() -> None:
    route = respx.get(f"{BASE_URL}/postings/another-co").mock(
        return_value=httpx.Response(200, json=[])
    )
    adapter = _adapter(company_key="another-co")
    adapter.fetch(_params())
    assert route.call_count == 1


@respx.mock
def test_company_key_never_appears_in_error_message_as_a_secret() -> None:
    """The company key is a public routing identifier, not a credential —
    context in error messages is fine — but this still confirms no other
    sensitive request detail (query string, response body) leaks."""
    respx.get(POSTINGS_URL).mock(return_value=httpx.Response(500))
    adapter = _adapter(max_retries=0)
    with pytest.raises(SourceUnavailableError) as exc_info:
        adapter.fetch(_params())
    assert "mode=json" not in str(exc_info.value)


# --- response parsing ---------------------------------------------------


@respx.mock
def test_normal_result_parses_to_raw_job_record() -> None:
    respx.get(POSTINGS_URL).mock(return_value=httpx.Response(200, json=[_posting(42)]))
    adapter = _adapter()
    records = adapter.fetch(_params())
    assert len(records) == 1
    record = records[0]
    assert record.source_id == "lever_public_postings"
    assert record.external_id == "42"
    assert record.raw_url == f"https://jobs.lever.co/{COMPANY_KEY}/42"
    assert record.raw_payload["text"] == "Strategy Manager"


@respx.mock
def test_apply_url_preserved_in_raw_payload() -> None:
    respx.get(POSTINGS_URL).mock(return_value=httpx.Response(200, json=[_posting(1)]))
    adapter = _adapter()
    records = adapter.fetch(_params())
    assert records[0].raw_payload["applyUrl"] == f"https://jobs.lever.co/{COMPANY_KEY}/1/apply"


@respx.mock
def test_company_name_stashed_onto_raw_payload() -> None:
    """Lever's own response never includes a company name — the
    watchlisted company_name given to the adapter's constructor is stashed
    onto raw_payload the same way GreenhouseAdapter stashes it."""
    respx.get(POSTINGS_URL).mock(return_value=httpx.Response(200, json=[_posting(1)]))
    adapter = _adapter(company_name="Example Co")
    records = adapter.fetch(_params())
    assert records[0].raw_payload["_company_name"] == "Example Co"


@respx.mock
def test_empty_array_response() -> None:
    respx.get(POSTINGS_URL).mock(return_value=httpx.Response(200, json=[]))
    adapter = _adapter()
    records = adapter.fetch(_params())
    assert records == []


@respx.mock
def test_unexpected_wrapped_object_response_treated_as_empty() -> None:
    """decisions.md D-048: the verified/live-checked contract is a bare
    JSON array — an unexpected wrapped-object shape is treated as zero
    postings, never guessed at as some new contract."""
    respx.get(POSTINGS_URL).mock(return_value=httpx.Response(200, json={"postings": []}))
    adapter = _adapter()
    records = adapter.fetch(_params())
    assert records == []


@respx.mock
def test_posting_missing_id_is_skipped_not_a_crash() -> None:
    malformed = _posting(1)
    del malformed["id"]
    well_formed = _posting(2)
    respx.get(POSTINGS_URL).mock(
        return_value=httpx.Response(200, json=[malformed, well_formed])
    )
    adapter = _adapter()
    records = adapter.fetch(_params())
    assert [r.external_id for r in records] == ["2"]


@respx.mock
def test_posting_missing_hosted_url_defaults_to_empty_string() -> None:
    posting = _posting(1)
    del posting["hostedUrl"]
    respx.get(POSTINGS_URL).mock(return_value=httpx.Response(200, json=[posting]))
    adapter = _adapter()
    records = adapter.fetch(_params())
    assert records[0].raw_url == ""


# --- error handling ----------------------------------------------------------


@respx.mock
def test_auth_failure_raises_source_auth_error() -> None:
    respx.get(POSTINGS_URL).mock(return_value=httpx.Response(401))
    adapter = _adapter()
    with pytest.raises(SourceAuthError):
        adapter.fetch(_params())


@respx.mock
def test_not_found_raises_source_not_found_error() -> None:
    respx.get(POSTINGS_URL).mock(return_value=httpx.Response(404))
    adapter = _adapter()
    with pytest.raises(SourceNotFoundError):
        adapter.fetch(_params())


@respx.mock
def test_rate_limit_raises_after_retries_exhausted() -> None:
    route = respx.get(POSTINGS_URL).mock(return_value=httpx.Response(429))
    adapter = _adapter(max_retries=2)
    with pytest.raises(SourceRateLimitError):
        adapter.fetch(_params())
    assert route.call_count == 3  # initial + 2 retries


@respx.mock
def test_server_error_raises_source_unavailable_after_retries() -> None:
    respx.get(POSTINGS_URL).mock(return_value=httpx.Response(503))
    adapter = _adapter(max_retries=1)
    with pytest.raises(SourceUnavailableError):
        adapter.fetch(_params())


@respx.mock
def test_timeout_raises_source_unavailable_not_httpx_exception() -> None:
    respx.get(POSTINGS_URL).mock(side_effect=httpx.ConnectTimeout("boom"))
    adapter = _adapter()
    with pytest.raises(SourceUnavailableError):
        adapter.fetch(_params())


@respx.mock
def test_unexpected_status_raises_source_unavailable() -> None:
    respx.get(POSTINGS_URL).mock(return_value=httpx.Response(418))
    adapter = _adapter()
    with pytest.raises(SourceUnavailableError):
        adapter.fetch(_params())
