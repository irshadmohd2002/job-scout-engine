import httpx
import pytest
import respx

from job_scout.models import SourceSearchParams
from job_scout.sources.adzuna import BASE_URL, AdzunaAdapter
from job_scout.sources.base import (
    SourceAuthError,
    SourceNotFoundError,
    SourceRateLimitError,
    SourceUnavailableError,
)


def _params(**overrides: object) -> SourceSearchParams:
    base = {
        "countries": ["GB"],
        "keywords": ["Strategy Manager"],
        "role_family_hints": [],
        "employment_types": ["full_time"],
        "min_experience_years": None,
        "max_experience_years": None,
        "page_size": 2,
        "max_pages": 3,
    }
    base.update(overrides)
    return SourceSearchParams.model_validate(base)


def _job(job_id: str) -> dict:
    return {
        "id": job_id,
        "title": "Strategy Manager",
        "company": {"display_name": "Example Corp"},
        "location": {"display_name": "London, UK", "area": ["UK", "London"]},
        "redirect_url": f"https://www.adzuna.co.uk/jobs/details/{job_id}",
        "created": "2026-07-01T10:00:00Z",
        "description": "Do strategy things.",
        "salary_min": 60000,
        "salary_max": 80000,
        "contract_time": "full_time",
    }


def test_is_configured_false_without_credentials() -> None:
    adapter = AdzunaAdapter(app_id=None, app_key="key")
    assert adapter.is_configured() is False
    adapter2 = AdzunaAdapter(app_id="id", app_key=None)
    assert adapter2.is_configured() is False


def test_fetch_raises_without_calling_http_when_not_configured() -> None:
    adapter = AdzunaAdapter(app_id=None, app_key=None)
    with respx.mock:
        route = respx.get(url__startswith=BASE_URL)
        with pytest.raises(SourceAuthError):
            adapter.fetch(_params())
        assert route.call_count == 0


@respx.mock
def test_fetch_returns_raw_job_records_single_page() -> None:
    respx.get(f"{BASE_URL}/jobs/gb/search/1").mock(
        return_value=httpx.Response(200, json={"results": [_job("1"), _job("2")]})
    )
    respx.get(f"{BASE_URL}/jobs/gb/search/2").mock(
        return_value=httpx.Response(200, json={"results": [_job("3")]})
    )
    adapter = AdzunaAdapter(app_id="id", app_key="key")
    records = adapter.fetch(_params(page_size=2, max_pages=3))
    assert [r.external_id for r in records] == ["1", "2", "3"]
    assert records[0].source_id == "adzuna_api"
    assert records[0].raw_url.startswith("https://www.adzuna.co.uk")


@respx.mock
def test_fetch_stops_pagination_when_page_short() -> None:
    route1 = respx.get(f"{BASE_URL}/jobs/gb/search/1").mock(
        return_value=httpx.Response(200, json={"results": [_job("1")]})
    )
    route2 = respx.get(f"{BASE_URL}/jobs/gb/search/2")
    adapter = AdzunaAdapter(app_id="id", app_key="key")
    records = adapter.fetch(_params(page_size=2, max_pages=3))
    assert len(records) == 1
    assert route1.call_count == 1
    assert route2.call_count == 0


@respx.mock
def test_fetch_never_exceeds_max_pages() -> None:
    for page in range(1, 4):
        respx.get(f"{BASE_URL}/jobs/gb/search/{page}").mock(
            return_value=httpx.Response(200, json={"results": [_job(str(page)), _job(f"{page}b")]})
        )
    adapter = AdzunaAdapter(app_id="id", app_key="key")
    records = adapter.fetch(_params(page_size=2, max_pages=3))
    assert len(records) == 6  # exactly 3 pages x 2 results, never more


@respx.mock
def test_auth_failure_raises_source_auth_error() -> None:
    respx.get(f"{BASE_URL}/jobs/gb/search/1").mock(return_value=httpx.Response(401))
    adapter = AdzunaAdapter(app_id="bad", app_key="bad")
    with pytest.raises(SourceAuthError):
        adapter.fetch(_params())


@respx.mock
def test_rate_limit_raises_after_retries_exhausted() -> None:
    route = respx.get(f"{BASE_URL}/jobs/gb/search/1").mock(return_value=httpx.Response(429))
    adapter = AdzunaAdapter(app_id="id", app_key="key", max_retries=2)
    with pytest.raises(SourceRateLimitError):
        adapter.fetch(_params())
    assert route.call_count == 3  # initial + 2 retries


@respx.mock
def test_server_error_raises_source_unavailable_after_retries() -> None:
    respx.get(f"{BASE_URL}/jobs/gb/search/1").mock(return_value=httpx.Response(503))
    adapter = AdzunaAdapter(app_id="id", app_key="key", max_retries=1)
    with pytest.raises(SourceUnavailableError):
        adapter.fetch(_params())


@respx.mock
def test_timeout_raises_source_unavailable_not_httpx_exception() -> None:
    respx.get(f"{BASE_URL}/jobs/gb/search/1").mock(side_effect=httpx.ConnectTimeout("boom"))
    adapter = AdzunaAdapter(app_id="id", app_key="key")
    with pytest.raises(SourceUnavailableError):
        adapter.fetch(_params())


@respx.mock
def test_multiple_countries_each_queried() -> None:
    respx.get(f"{BASE_URL}/jobs/gb/search/1").mock(
        return_value=httpx.Response(200, json={"results": [_job("gb1")]})
    )
    respx.get(f"{BASE_URL}/jobs/de/search/1").mock(
        return_value=httpx.Response(200, json={"results": [_job("de1")]})
    )
    adapter = AdzunaAdapter(app_id="id", app_key="key")
    records = adapter.fetch(_params(countries=["GB", "DE"], page_size=2, max_pages=1))
    assert {r.external_id for r in records} == {"gb1", "de1"}


@respx.mock
def test_gb_endpoint_uses_documented_path_and_page_placement() -> None:
    route = respx.get("https://api.adzuna.com/v1/api/jobs/gb/search/1").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    adapter = AdzunaAdapter(app_id="id", app_key="key")
    adapter.fetch(_params(countries=["GB"], max_pages=1))
    assert route.call_count == 1


@respx.mock
def test_ie_endpoint_uses_documented_path_lowercased() -> None:
    route = respx.get("https://api.adzuna.com/v1/api/jobs/ie/search/1").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    adapter = AdzunaAdapter(app_id="id", app_key="key")
    adapter.fetch(_params(countries=["IE"], max_pages=1))
    assert route.call_count == 1
    # respx only matches because the path segment is lowercase "ie"; an
    # uppercase "IE" segment would not have matched this route at all,
    # confirming SourceSearchParams.countries="IE" is lowercased on the wire.


@respx.mock
def test_required_query_parameter_names_are_sent() -> None:
    route = respx.get(f"{BASE_URL}/jobs/gb/search/1").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    adapter = AdzunaAdapter(app_id="my-app-id", app_key="my-app-key")
    adapter.fetch(_params(max_pages=1))
    sent = route.calls.last.request.url.params
    assert sent["app_id"] == "my-app-id"
    assert sent["app_key"] == "my-app-key"
    assert sent["results_per_page"] == "2"
    assert sent["content-type"] == "application/json"
    assert "what_or" in sent  # `what`/`what_or` per params.keywords


@respx.mock
def test_legacy_default_keyword_mode_uses_what_or() -> None:
    """`_params()` never sets `keyword_mode` — SourceSearchParams' default
    ("any_of_words") must reproduce the exact pre-M2 request shape so every
    caller that predates PlannedQuery keeps working unchanged."""
    route = respx.get(f"{BASE_URL}/jobs/gb/search/1").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    adapter = AdzunaAdapter(app_id="id", app_key="key")
    adapter.fetch(_params(keywords=["Strategy Manager"], max_pages=1))
    sent = route.calls.last.request.url.params
    assert sent["what_or"] == "Strategy Manager"
    assert "what" not in sent


@respx.mock
def test_any_of_words_keyword_mode_uses_what_or() -> None:
    route = respx.get(f"{BASE_URL}/jobs/gb/search/1").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    adapter = AdzunaAdapter(app_id="id", app_key="key")
    adapter.fetch(
        _params(
            keywords=["Strategy Manager", "Transformation Lead"],
            keyword_mode="any_of_words",
            max_pages=1,
        )
    )
    sent = route.calls.last.request.url.params
    assert sent["what_or"] == "Strategy Manager Transformation Lead"
    assert "what" not in sent


@respx.mock
def test_exact_phrase_keyword_mode_uses_what_not_what_or() -> None:
    route = respx.get(f"{BASE_URL}/jobs/gb/search/1").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    adapter = AdzunaAdapter(app_id="id", app_key="key")
    adapter.fetch(_params(keywords=["Chief of Staff"], keyword_mode="exact_phrase", max_pages=1))
    sent = route.calls.last.request.url.params
    assert sent["what"] == "Chief of Staff"
    assert "what_or" not in sent


@respx.mock
def test_exact_phrase_and_any_of_words_never_render_identical_requests() -> None:
    """The invariant this correction exists to establish: the two modes must
    no longer silently produce the exact same Adzuna request."""
    route = respx.get(f"{BASE_URL}/jobs/gb/search/1").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    adapter = AdzunaAdapter(app_id="id", app_key="key")

    adapter.fetch(_params(keywords=["Chief of Staff"], keyword_mode="exact_phrase", max_pages=1))
    exact_params = dict(route.calls.last.request.url.params)

    adapter.fetch(_params(keywords=["Chief of Staff"], keyword_mode="any_of_words", max_pages=1))
    or_params = dict(route.calls.last.request.url.params)

    assert exact_params != or_params
    assert "what" in exact_params and "what_or" not in exact_params
    assert "what_or" in or_params and "what" not in or_params


@respx.mock
def test_http_404_raises_source_not_found_error_with_diagnostic_context() -> None:
    respx.get(f"{BASE_URL}/jobs/ie/search/1").mock(return_value=httpx.Response(404))
    adapter = AdzunaAdapter(app_id="id", app_key="key")
    with pytest.raises(SourceNotFoundError) as exc_info:
        adapter.fetch(_params(countries=["IE"], max_pages=1))
    message = str(exc_info.value)
    assert "404" in message
    assert "source_id=adzuna_api" in message
    assert "country=IE" in message
    assert "page=1" in message


@respx.mock
def test_credentials_never_appear_in_raised_error_messages() -> None:
    secret_id, secret_key = "super-secret-app-id", "super-secret-app-key"
    respx.get(f"{BASE_URL}/jobs/gb/search/1").mock(return_value=httpx.Response(404))
    adapter = AdzunaAdapter(app_id=secret_id, app_key=secret_key)
    with pytest.raises(SourceNotFoundError) as exc_info:
        adapter.fetch(_params(max_pages=1))
    message = str(exc_info.value)
    assert secret_id not in message
    assert secret_key not in message


@respx.mock
def test_auth_error_message_carries_context_not_credentials() -> None:
    respx.get(f"{BASE_URL}/jobs/gb/search/1").mock(return_value=httpx.Response(401))
    adapter = AdzunaAdapter(app_id="secret-id", app_key="secret-key")
    with pytest.raises(SourceAuthError) as exc_info:
        adapter.fetch(_params(max_pages=1))
    message = str(exc_info.value)
    assert "secret-id" not in message
    assert "secret-key" not in message
    assert "country=GB" in message
    assert "page=1" in message
