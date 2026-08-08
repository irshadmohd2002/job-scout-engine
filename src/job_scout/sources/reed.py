"""ReedAdapter — Milestone 2 Deliverable 5 step 5 (decisions.md D-046).
public_api access_mode, ships `manual_review` in the packaged registry
template (never `approved` by default — CLAUDE.md hard constraint 1).

Endpoint contract verified against Reed's official developer documentation
(https://www.reed.co.uk/developers/jobseeker) at implementation time:
`GET /api/1.0/search` (keyword/location/salary/employment-type filters,
`resultsToTake`/`resultsToSkip` offset pagination, results capped at 100 per
page) and `GET /api/1.0/jobs/{jobId}` (a separate, richer Details endpoint —
see "Why the Details endpoint is not used" below). Authentication is HTTP
Basic Auth with the API key as username and an empty password.

**Known documentation gap — response field casing**: the official docs
describe the Search endpoint's returned job information only as prose row
labels in a "Returns" table (`Job Id`, `Employer Id`, `Employer Name`,
`Employer Profile Id`, `Job Title`, `Description`, `Location Name`,
`Minimum Salary`, `Maximum Salary`) — no literal JSON response example is
published on the page. The field names below (`jobId`, `employerId`,
`employerName`, `employerProfileId`, `jobTitle`, `description`,
`locationName`, `minimumSalary`, `maximumSalary`) are this project's direct
camelCase translation of those documented labels (standard REST/JSON
convention), not a literally-quoted API contract. This is a known limitation
to confirm via the opt-in integration test (`tests/test_reed_integration.py`)
before relying on a live Reed run — see the Milestone 2 Task 5 final report.

**Why the Details endpoint is not used**: it exposes richer fields
(contract type, job type, expiration date, external application URL, reed.co.uk
URL) that the Search endpoint's response does not include. Calling it once
per search result would add an unbounded per-result HTTP fan-out that the
existing request-budget/pagination guardrails (`ExecutionLimits`,
`SelectedSource.estimated_request_count`) do not account for. Deferred,
not implemented, per MILESTONE_2.md Deliverable 5 step 5's own "Non-goals".
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from job_scout.models import AccessMode, RawJobRecord, SourceSearchParams
from job_scout.sources.base import (
    SourceAuthError,
    SourceNotFoundError,
    SourceRateLimitError,
    SourceUnavailableError,
)

BASE_URL = "https://www.reed.co.uk/api/1.0"

# Reed's documented hard cap on `resultsToTake` ("defaults and is limited to
# 100 results") — never exceeded regardless of what a caller's
# SourceSearchParams.page_size requests.
MAX_RESULTS_TO_TAKE = 100


class ReedAdapter:
    source_id = "reed_api"
    access_mode = AccessMode.PUBLIC_API

    def __init__(
        self,
        *,
        api_key: str | None,
        request_timeout_seconds: float = 15.0,
        max_retries: int = 2,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.request_timeout_seconds = request_timeout_seconds
        self.max_retries = max_retries
        self._injected_client = client

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def fetch(self, params: SourceSearchParams) -> list[RawJobRecord]:
        if not self.is_configured():
            raise SourceAuthError("Reed adapter is not configured — set REED_API_KEY.")
        client = self._injected_client or httpx.Client(timeout=self.request_timeout_seconds)
        owns_client = self._injected_client is None
        try:
            records: list[RawJobRecord] = []
            # Reed's Search API has no country-scoping parameter (it is a
            # single UK-wide job board, per the verified docs) — the loop
            # structure mirrors AdzunaAdapter's own per-country shape only so
            # every `SourceSearchParams.countries` entry (ordinarily just
            # ["GB"], per the packaged registry's geographic_coverage) gets
            # its own tagged, fully-paginated fetch; it never adds a real
            # per-country request parameter that doesn't exist.
            for country in params.countries:
                records.extend(self._fetch_country(client, country, params))
            return records
        finally:
            if owns_client:
                client.close()

    def _fetch_country(
        self, client: httpx.Client, country: str, params: SourceSearchParams
    ) -> list[RawJobRecord]:
        records: list[RawJobRecord] = []
        results_to_take = min(params.page_size, MAX_RESULTS_TO_TAKE)
        if results_to_take <= 0:
            return records
        for page_index in range(params.max_pages):
            results_to_skip = page_index * results_to_take
            payload = self._get_page(client, country, page_index + 1, results_to_skip, params)
            page_results = payload.get("results", [])
            if not page_results:
                break  # empty page terminates cleanly — no infinite pagination
            for item in page_results:
                records.append(self._to_raw_record(item, country))
            if len(page_results) < results_to_take:
                break  # short/final page — no more results for this country
        return records

    def _build_query(self, params: SourceSearchParams) -> dict[str, Any]:
        query: dict[str, Any] = {}
        if params.keywords:
            # Reed's documented Search API exposes exactly one keyword
            # parameter (`keywords`) with no documented literal-phrase/quote
            # syntax (SourceCapabilities.exact_phrase_search=False for
            # reed_api — see the packaged registry template). Both
            # PlannedQuery modes therefore translate to the identical,
            # honest request: a plain space-joined term list. This adapter
            # never fabricates a phrase-matching guarantee Reed's docs don't
            # make, regardless of which `keyword_mode` a caller supplies.
            query["keywords"] = " ".join(params.keywords)
        # Milestone 2 Deliverable 5 step 5: the same generic
        # SourceSearchParams.employment_types signal AdzunaAdapter already
        # reads (only the one value this project currently uses,
        # "full_time") maps onto Reed's own documented `fullTime` boolean
        # filter — not a new filter category, the same generic mechanism
        # applied at this adapter's boundary.
        if "full_time" in params.employment_types:
            query["fullTime"] = True
        return query

    def _get_page(
        self,
        client: httpx.Client,
        country: str,
        page: int,
        results_to_skip: int,
        params: SourceSearchParams,
    ) -> dict[str, Any]:
        url = f"{BASE_URL}/search"
        query = self._build_query(params)
        query["resultsToTake"] = min(params.page_size, MAX_RESULTS_TO_TAKE)
        query["resultsToSkip"] = results_to_skip
        # Context for error messages only — never the query dict itself
        # (Reed's API key travels via the Basic Auth header, set below, and
        # is never included here either), so it can never end up in a raised
        # message or persisted SourceRun.errors entry.
        context = f"source_id={self.source_id} country={country} page={page}"

        attempt = 0
        while True:
            try:
                response = client.get(
                    url, params=query, auth=httpx.BasicAuth(self.api_key or "", "")
                )
            except httpx.TimeoutException as exc:
                raise SourceUnavailableError(
                    f"Reed request timed out ({type(exc).__name__}) [{context}]."
                ) from exc
            except httpx.HTTPError as exc:
                raise SourceUnavailableError(
                    f"Reed request failed ({type(exc).__name__}) [{context}]."
                ) from exc

            if response.status_code == 200:
                result: dict[str, Any] = response.json()
                return result
            if response.status_code in (401, 403):
                raise SourceAuthError(
                    f"Reed authentication failed (HTTP {response.status_code}) [{context}] — "
                    "check REED_API_KEY."
                )
            if response.status_code == 404:
                raise SourceNotFoundError(
                    f"Reed endpoint not found (HTTP 404) [{context}] — the request path was "
                    "well-formed but Reed has no matching route."
                )
            if response.status_code == 429:
                if attempt >= self.max_retries:
                    raise SourceRateLimitError(
                        f"Reed rate limit exceeded; retries exhausted [{context}]."
                    )
                attempt += 1
                continue
            if response.status_code >= 500:
                if attempt >= self.max_retries:
                    raise SourceUnavailableError(
                        f"Reed returned HTTP {response.status_code}; retries exhausted "
                        f"[{context}]."
                    )
                attempt += 1
                continue
            raise SourceUnavailableError(
                f"Reed returned unexpected HTTP {response.status_code} [{context}]."
            )

    def _to_raw_record(self, item: dict[str, Any], country: str) -> RawJobRecord:
        # Reed's documented Search Returns carries no country code and no
        # canonical application URL (see the module docstring's "Why the
        # Details endpoint is not used") — `_query_country` mirrors
        # AdzunaAdapter's own stash so normalisation never has to re-parse
        # `locationName` free text to recover a country, and `raw_url` stays
        # "" (SourceCapabilities.canonical_application_url=False for
        # reed_api is not merely declared, it is what this adapter actually
        # provides).
        payload = {**item, "_query_country": country.upper()}
        return RawJobRecord(
            source_id=self.source_id,
            external_id=str(item["jobId"]),
            raw_url="",
            raw_payload=payload,
            fetched_at=datetime.now(UTC),
        )
