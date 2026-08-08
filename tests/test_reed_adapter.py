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
from job_scout.sources.reed import BASE_URL, MAX_RESULTS_TO_TAKE, ReedAdapter

SEARCH_URL = f"{BASE_URL}/search"


def _params(**overrides: object) -> SourceSearchParams:
    base = {
        "countries": ["GB"],
        "keywords": ["Strategy Manager"],
        "role_family_hints": [],
        "employment_types": [],
        "min_experience_years": None,
        "max_experience_years": None,
        "page_size": 2,
        "max_pages": 3,
    }
    base.update(overrides)
    return SourceSearchParams.model_validate(base)


def _job(job_id: str) -> dict:
    return {
        "jobId": int(job_id) if job_id.isdigit() else job_id,
        "employerId": 999,
        "employerName": "Example Corp",
        "jobTitle": "Strategy Manager",
        "locationName": "London",
        "description": "Do strategy things.",
        "minimumSalary": 60000,
        "maximumSalary": 80000,
    }


# --- adapter request/auth ---------------------------------------------------


def test_is_configured_false_without_api_key() -> None:
    adapter = ReedAdapter(api_key=None)
    assert adapter.is_configured() is False


def test_fetch_raises_without_calling_http_when_not_configured() -> None:
    adapter = ReedAdapter(api_key=None)
    with respx.mock:
        route = respx.get(url__startswith=BASE_URL)
        with pytest.raises(SourceAuthError):
            adapter.fetch(_params())
        assert route.call_count == 0


@respx.mock
def test_correct_reed_endpoint_used() -> None:
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"results": []}))
    adapter = ReedAdapter(api_key="my-key")
    adapter.fetch(_params(max_pages=1))
    assert route.call_count == 1


@respx.mock
def test_api_key_sent_via_basic_auth_username_empty_password() -> None:
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"results": []}))
    adapter = ReedAdapter(api_key="my-secret-key")
    adapter.fetch(_params(max_pages=1))
    request = route.calls.last.request
    auth_header = request.headers["Authorization"]
    assert auth_header.startswith("Basic ")
    import base64

    decoded = base64.b64decode(auth_header.removeprefix("Basic ")).decode()
    assert decoded == "my-secret-key:"


@respx.mock
def test_api_key_never_appears_in_url() -> None:
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"results": []}))
    adapter = ReedAdapter(api_key="super-secret-key")
    adapter.fetch(_params(max_pages=1))
    request = route.calls.last.request
    assert "super-secret-key" not in str(request.url)


@respx.mock
def test_api_key_not_emitted_in_error_messages() -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(401))
    adapter = ReedAdapter(api_key="super-secret-key")
    with pytest.raises(SourceAuthError) as exc_info:
        adapter.fetch(_params(max_pages=1))
    assert "super-secret-key" not in str(exc_info.value)


def test_missing_credential_fails_safely_not_a_crash() -> None:
    adapter = ReedAdapter(api_key=None)
    with pytest.raises(SourceAuthError):
        adapter.fetch(_params())


# --- request translation ----------------------------------------------------


@respx.mock
def test_keywords_reach_reed_keywords_parameter() -> None:
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"results": []}))
    adapter = ReedAdapter(api_key="key")
    adapter.fetch(_params(keywords=["Chief of Staff"], max_pages=1))
    sent = route.calls.last.request.url.params
    assert sent["keywords"] == "Chief of Staff"


@respx.mock
def test_exact_phrase_and_any_of_words_send_identical_honest_request() -> None:
    """Reed's Search API exposes only a generic `keywords` parameter with no
    documented phrase syntax — SourceCapabilities.exact_phrase_search=False
    for reed_api, and this adapter must never fabricate a phrase-matching
    guarantee Reed's docs don't make, regardless of which keyword_mode a
    caller supplies."""
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"results": []}))
    adapter = ReedAdapter(api_key="key")

    adapter.fetch(_params(keywords=["Chief of Staff"], keyword_mode="exact_phrase", max_pages=1))
    exact_params = dict(route.calls.last.request.url.params)

    adapter.fetch(_params(keywords=["Chief of Staff"], keyword_mode="any_of_words", max_pages=1))
    or_params = dict(route.calls.last.request.url.params)

    assert exact_params["keywords"] == or_params["keywords"] == "Chief of Staff"


@respx.mock
def test_multi_keyword_any_of_words_joined_with_space() -> None:
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"results": []}))
    adapter = ReedAdapter(api_key="key")
    adapter.fetch(
        _params(keywords=["Strategy Manager", "Transformation Lead"], max_pages=1)
    )
    sent = route.calls.last.request.url.params
    assert sent["keywords"] == "Strategy Manager Transformation Lead"


@respx.mock
def test_full_time_employment_type_maps_to_reed_fulltime_param() -> None:
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"results": []}))
    adapter = ReedAdapter(api_key="key")
    adapter.fetch(_params(employment_types=["full_time"], max_pages=1))
    sent = route.calls.last.request.url.params
    assert sent["fullTime"] == "true"


@respx.mock
def test_no_employment_type_means_no_fulltime_param() -> None:
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"results": []}))
    adapter = ReedAdapter(api_key="key")
    adapter.fetch(_params(employment_types=[], max_pages=1))
    sent = route.calls.last.request.url.params
    assert "fullTime" not in sent


@respx.mock
def test_results_to_take_mapping() -> None:
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"results": []}))
    adapter = ReedAdapter(api_key="key")
    adapter.fetch(_params(page_size=25, max_pages=1))
    sent = route.calls.last.request.url.params
    assert sent["resultsToTake"] == "25"


@respx.mock
def test_maximum_documented_page_size_is_never_exceeded() -> None:
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"results": []}))
    adapter = ReedAdapter(api_key="key")
    adapter.fetch(_params(page_size=500, max_pages=1))
    sent = route.calls.last.request.url.params
    assert sent["resultsToTake"] == str(MAX_RESULTS_TO_TAKE)


# --- pagination --------------------------------------------------------------


@respx.mock
def test_results_to_skip_progresses_across_pages_no_repeated_offsets() -> None:
    respx.get(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json={"results": [_job("1"), _job("2")]}),
            httpx.Response(200, json={"results": [_job("3"), _job("4")]}),
            httpx.Response(200, json={"results": [_job("5")]}),
        ]
    )
    adapter = ReedAdapter(api_key="key")
    records = adapter.fetch(_params(page_size=2, max_pages=3))
    assert [r.external_id for r in records] == ["1", "2", "3", "4", "5"]


@respx.mock
def test_first_page_uses_zero_skip() -> None:
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"results": [_job("1")]})
    )
    adapter = ReedAdapter(api_key="key")
    adapter.fetch(_params(page_size=2, max_pages=1))
    sent = route.calls.last.request.url.params
    assert sent["resultsToSkip"] == "0"


@respx.mock
def test_empty_page_terminates_cleanly() -> None:
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"results": []}))
    adapter = ReedAdapter(api_key="key")
    records = adapter.fetch(_params(page_size=2, max_pages=5))
    assert records == []
    assert route.call_count == 1  # never loops forever on an empty page


@respx.mock
def test_short_final_page_stops_pagination() -> None:
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"results": [_job("1")]})
    )
    adapter = ReedAdapter(api_key="key")
    records = adapter.fetch(_params(page_size=2, max_pages=3))
    assert len(records) == 1
    assert route.call_count == 1


@respx.mock
def test_max_pages_never_exceeded() -> None:
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, json={"results": [_job("a"), _job("b")]}
        )
    )
    adapter = ReedAdapter(api_key="key")
    records = adapter.fetch(_params(page_size=2, max_pages=3))
    assert len(records) == 6  # exactly 3 pages x 2 results, never more


# --- response parsing --------------------------------------------------------


@respx.mock
def test_normal_result_parses_to_raw_job_record() -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"results": [_job("42")]}))
    adapter = ReedAdapter(api_key="key")
    records = adapter.fetch(_params(max_pages=1))
    assert len(records) == 1
    record = records[0]
    assert record.source_id == "reed_api"
    assert record.external_id == "42"
    assert record.raw_url == ""  # Search response provides no application URL
    assert record.raw_payload["jobTitle"] == "Strategy Manager"
    assert record.raw_payload["employerName"] == "Example Corp"
    assert record.raw_payload["locationName"] == "London"


@respx.mock
def test_stable_external_job_id_preserved() -> None:
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"results": [_job("777")]})
    )
    adapter = ReedAdapter(api_key="key")
    records = adapter.fetch(_params(max_pages=1))
    assert records[0].external_id == "777"


@respx.mock
def test_salary_present() -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"results": [_job("1")]}))
    adapter = ReedAdapter(api_key="key")
    records = adapter.fetch(_params(max_pages=1))
    assert records[0].raw_payload["minimumSalary"] == 60000
    assert records[0].raw_payload["maximumSalary"] == 80000


@respx.mock
def test_salary_hidden_does_not_fail() -> None:
    job = _job("1")
    job["minimumSalary"] = None
    job["maximumSalary"] = None
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"results": [job]}))
    adapter = ReedAdapter(api_key="key")
    records = adapter.fetch(_params(max_pages=1))
    assert records[0].raw_payload["minimumSalary"] is None
    assert records[0].raw_payload["maximumSalary"] is None


@respx.mock
def test_optional_fields_missing_handled_cleanly() -> None:
    minimal = {"jobId": 1, "jobTitle": "Manager"}
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"results": [minimal]}))
    adapter = ReedAdapter(api_key="key")
    records = adapter.fetch(_params(max_pages=1))
    assert len(records) == 1
    assert records[0].external_id == "1"


@respx.mock
def test_empty_result_set() -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"results": []}))
    adapter = ReedAdapter(api_key="key")
    records = adapter.fetch(_params(max_pages=1))
    assert records == []


# --- error handling ----------------------------------------------------------


@respx.mock
def test_auth_failure_raises_source_auth_error() -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(401))
    adapter = ReedAdapter(api_key="bad")
    with pytest.raises(SourceAuthError):
        adapter.fetch(_params(max_pages=1))


@respx.mock
def test_forbidden_raises_source_auth_error() -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(403))
    adapter = ReedAdapter(api_key="bad")
    with pytest.raises(SourceAuthError):
        adapter.fetch(_params(max_pages=1))


@respx.mock
def test_not_found_raises_source_not_found_error() -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(404))
    adapter = ReedAdapter(api_key="key")
    with pytest.raises(SourceNotFoundError):
        adapter.fetch(_params(max_pages=1))


@respx.mock
def test_rate_limit_raises_after_retries_exhausted() -> None:
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(429))
    adapter = ReedAdapter(api_key="key", max_retries=2)
    with pytest.raises(SourceRateLimitError):
        adapter.fetch(_params(max_pages=1))
    assert route.call_count == 3  # initial + 2 retries


@respx.mock
def test_server_error_raises_source_unavailable_after_retries() -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(503))
    adapter = ReedAdapter(api_key="key", max_retries=1)
    with pytest.raises(SourceUnavailableError):
        adapter.fetch(_params(max_pages=1))


@respx.mock
def test_timeout_raises_source_unavailable_not_httpx_exception() -> None:
    respx.get(SEARCH_URL).mock(side_effect=httpx.ConnectTimeout("boom"))
    adapter = ReedAdapter(api_key="key")
    with pytest.raises(SourceUnavailableError):
        adapter.fetch(_params(max_pages=1))


@respx.mock
def test_unexpected_status_raises_source_unavailable() -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(418))
    adapter = ReedAdapter(api_key="key")
    with pytest.raises(SourceUnavailableError):
        adapter.fetch(_params(max_pages=1))
