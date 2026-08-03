import httpx
import pytest
import respx

from job_scout.models import SourceSearchParams
from job_scout.sources.adzuna import BASE_URL, AdzunaAdapter
from job_scout.sources.base import SourceAuthError, SourceRateLimitError, SourceUnavailableError


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
