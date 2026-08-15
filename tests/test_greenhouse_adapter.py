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
from job_scout.sources.greenhouse import BASE_URL, GreenhouseAdapter

BOARD_TOKEN = "exampleco"
JOBS_URL = f"{BASE_URL}/boards/{BOARD_TOKEN}/jobs"


def _params(**overrides: object) -> SourceSearchParams:
    # GreenhouseAdapter.fetch() ignores every field here (no keyword/country/
    # pagination request parameter exists on this endpoint) — a plain,
    # otherwise-typical SourceSearchParams is used so tests exercise the
    # same Protocol every other adapter receives.
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


def _job(job_id: object, title: str = "Strategy Manager") -> dict:
    return {
        "id": job_id,
        "internal_job_id": 999000 + (job_id if isinstance(job_id, int) else 0),
        "title": title,
        "updated_at": "2026-07-01T10:00:00-05:00",
        "requisition_id": "50",
        "location": {"name": "London"},
        "absolute_url": f"https://boards.greenhouse.io/{BOARD_TOKEN}/jobs/{job_id}",
        "language": "en",
        "metadata": None,
        "content": "<p>Great strategy role.</p>",
        "departments": [],
        "offices": [],
    }


def _adapter(**overrides: object) -> GreenhouseAdapter:
    base: dict[str, object] = {"board_token": BOARD_TOKEN, "company_name": "Example Co"}
    base.update(overrides)
    return GreenhouseAdapter(**base)  # type: ignore[arg-type]


# --- adapter configuration ---------------------------------------------------


def test_is_configured_false_without_board_token() -> None:
    adapter = _adapter(board_token="")
    assert adapter.is_configured() is False


def test_is_configured_true_with_board_token() -> None:
    adapter = _adapter()
    assert adapter.is_configured() is True


def test_fetch_raises_without_calling_http_when_not_configured() -> None:
    adapter = _adapter(board_token="")
    with respx.mock:
        route = respx.get(url__startswith=BASE_URL)
        with pytest.raises(SourceAuthError):
            adapter.fetch(_params())
        assert route.call_count == 0


# --- request shape: single request, no pagination, ?content=true ------------


@respx.mock
def test_correct_greenhouse_endpoint_used() -> None:
    route = respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json={"jobs": []}))
    adapter = _adapter()
    adapter.fetch(_params())
    assert route.call_count == 1


@respx.mock
def test_content_true_always_requested() -> None:
    route = respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json={"jobs": []}))
    adapter = _adapter()
    adapter.fetch(_params())
    sent = route.calls.last.request.url.params
    assert sent["content"] == "true"


@respx.mock
def test_never_paginates_even_with_many_results_and_high_max_pages() -> None:
    """The verified Job Board API documents no page/per_page/offset/cursor
    parameter at all — exactly one request per fetch(), regardless of
    max_pages or how many jobs the single response contains."""
    many_jobs = [_job(i) for i in range(1, 51)]
    route = respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json={"jobs": many_jobs}))
    adapter = _adapter()
    records = adapter.fetch(_params(max_pages=10))
    assert route.call_count == 1
    assert len(records) == 50


@respx.mock
def test_board_token_used_in_url_path() -> None:
    route = respx.get(f"{BASE_URL}/boards/another-co/jobs").mock(
        return_value=httpx.Response(200, json={"jobs": []})
    )
    adapter = _adapter(board_token="another-co")
    adapter.fetch(_params())
    assert route.call_count == 1


@respx.mock
def test_board_token_never_appears_in_error_message_as_a_secret() -> None:
    """The board token is a public routing identifier, not a credential —
    context in error messages is fine — but this still confirms no other
    sensitive request detail (query string, response body) leaks."""
    respx.get(JOBS_URL).mock(return_value=httpx.Response(500))
    adapter = _adapter(max_retries=0)
    with pytest.raises(SourceUnavailableError) as exc_info:
        adapter.fetch(_params())
    assert "content=true" not in str(exc_info.value)


# --- response parsing ---------------------------------------------------


@respx.mock
def test_normal_result_parses_to_raw_job_record() -> None:
    respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json={"jobs": [_job(42)]}))
    adapter = _adapter()
    records = adapter.fetch(_params())
    assert len(records) == 1
    record = records[0]
    assert record.source_id == "greenhouse_public_feeds"
    assert record.external_id == "42"
    assert record.raw_url == f"https://boards.greenhouse.io/{BOARD_TOKEN}/jobs/42"
    assert record.raw_payload["title"] == "Strategy Manager"


@respx.mock
def test_company_name_stashed_onto_raw_payload() -> None:
    """Greenhouse's own response never includes a company name — the
    watchlisted company_name given to the adapter's constructor is stashed
    onto raw_payload the same way Adzuna/Reed stash `_query_country`."""
    respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json={"jobs": [_job(1)]}))
    adapter = _adapter(company_name="Example Co")
    records = adapter.fetch(_params())
    assert records[0].raw_payload["_company_name"] == "Example Co"


@respx.mock
def test_empty_jobs_list() -> None:
    respx.get(JOBS_URL).mock(
        return_value=httpx.Response(200, json={"jobs": [], "meta": {"total": 0}})
    )
    adapter = _adapter()
    records = adapter.fetch(_params())
    assert records == []


@respx.mock
def test_missing_jobs_key_treated_as_empty() -> None:
    respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json={}))
    adapter = _adapter()
    records = adapter.fetch(_params())
    assert records == []


@respx.mock
def test_job_missing_id_is_skipped_not_a_crash() -> None:
    malformed = _job(1)
    del malformed["id"]
    well_formed = _job(2)
    respx.get(JOBS_URL).mock(
        return_value=httpx.Response(200, json={"jobs": [malformed, well_formed]})
    )
    adapter = _adapter()
    records = adapter.fetch(_params())
    assert [r.external_id for r in records] == ["2"]


@respx.mock
def test_job_missing_absolute_url_defaults_to_empty_string() -> None:
    job = _job(1)
    del job["absolute_url"]
    respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json={"jobs": [job]}))
    adapter = _adapter()
    records = adapter.fetch(_params())
    assert records[0].raw_url == ""


# --- error handling ----------------------------------------------------------


@respx.mock
def test_auth_failure_raises_source_auth_error() -> None:
    respx.get(JOBS_URL).mock(return_value=httpx.Response(401))
    adapter = _adapter()
    with pytest.raises(SourceAuthError):
        adapter.fetch(_params())


@respx.mock
def test_not_found_raises_source_not_found_error() -> None:
    respx.get(JOBS_URL).mock(return_value=httpx.Response(404))
    adapter = _adapter()
    with pytest.raises(SourceNotFoundError):
        adapter.fetch(_params())


@respx.mock
def test_rate_limit_raises_after_retries_exhausted() -> None:
    route = respx.get(JOBS_URL).mock(return_value=httpx.Response(429))
    adapter = _adapter(max_retries=2)
    with pytest.raises(SourceRateLimitError):
        adapter.fetch(_params())
    assert route.call_count == 3  # initial + 2 retries


@respx.mock
def test_server_error_raises_source_unavailable_after_retries() -> None:
    respx.get(JOBS_URL).mock(return_value=httpx.Response(503))
    adapter = _adapter(max_retries=1)
    with pytest.raises(SourceUnavailableError):
        adapter.fetch(_params())


@respx.mock
def test_timeout_raises_source_unavailable_not_httpx_exception() -> None:
    respx.get(JOBS_URL).mock(side_effect=httpx.ConnectTimeout("boom"))
    adapter = _adapter()
    with pytest.raises(SourceUnavailableError):
        adapter.fetch(_params())


@respx.mock
def test_unexpected_status_raises_source_unavailable() -> None:
    respx.get(JOBS_URL).mock(return_value=httpx.Response(418))
    adapter = _adapter()
    with pytest.raises(SourceUnavailableError):
        adapter.fetch(_params())
