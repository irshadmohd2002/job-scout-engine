"""GreenhouseAdapter — Milestone 2 Deliverable 5 step 7 (decisions.md D-047).
`public_ats_feed` access_mode, ships `manual_review` in the packaged
registry template (never `approved` by default — CLAUDE.md hard
constraint 1). Watchlist-scoped: one adapter instance is constructed per
`CompanyWatchlistEntry` whose `source_id` is `greenhouse_public_feeds`
(`pipeline.py`'s watchlist fan-out, not this module), never per keyword
query — `SourceCapabilities.company_filter=True` /
`keyword_search=False` for this source (decisions.md D-041) means
`source_intelligence/query_planner.py` already emits zero `PlannedQuery`s
for it.

Endpoint contract verified against Greenhouse's official public API
documentation (`grnhse/greenhouse-api-docs`, "Job Board API" -> "Jobs" ->
"List jobs", https://developers.greenhouse.io/job-board.html, checked at
implementation time — mirrored at
https://github.com/grnhse/greenhouse-api-docs/blob/master/source/includes/job-board/_jobs.md):
`GET /v1/boards/{board_token}/jobs`, no authentication (Greenhouse's own
docs: "Anyone can access the respective API endpoints... to list published
jobs"). **No pagination parameters are documented for this endpoint at
all** — no `page`/`per_page`/`offset`/`cursor` — one request returns every
open job post for that board in a single response
(`{"jobs": [...], "meta": {"total": N}}`); this adapter therefore makes
exactly one HTTP request per `fetch()` call, never a page loop.

This adapter always requests `?content=true` (a real, documented
querystring parameter) so the response includes each job's actual
`content` (description), `departments`, and `offices` — omitting it would
return no description text at all, which would make every fetched job
unscoreable downstream. This is not optional fan-out (unlike Reed's
Details endpoint, deferred per `sources/reed.py`'s own docstring): it is
one flag on the same list request, at no extra request cost.

**Fields intentionally left `None`/unmapped** (never fabricated — see
`decisions.md` D-047 for the full reasoning): `posted_at` (only
`updated_at`, a last-modified timestamp, is present on this endpoint —
never conflated with a posting date); `employment_type` (no such field is
documented anywhere on this endpoint); `salary_min`/`salary_max`/
`salary_currency` (`pay_input_ranges` exists only on the single-job detail
endpoint behind `?pay_transparency=true`, which this adapter never calls,
for the same "no unbounded per-result fan-out" reason `sources/reed.py`
already documents); `Location.country` (Greenhouse gives only a single
freeform `location.name` string — e.g. "London" or "Remote - US" — no
structured country code; parsing/guessing one from free text is exactly
the kind of inference this project's evidence bar forbids, so `country`
stays `""` — see `pipeline.py::normalize_greenhouse_record` and
`decisions.md` D-047 for the resulting hard-filter interaction and why
that is treated as an honest limitation, not a bug to paper over).

`RawJobRecord.raw_url` uses the response's own `absolute_url` — a genuine
documented canonical application URL (`SourceCapabilities
.canonical_application_url=True` for this source, unlike Reed).
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

BASE_URL = "https://boards-api.greenhouse.io/v1"


class GreenhouseAdapter:
    source_id = "greenhouse_public_feeds"
    access_mode = AccessMode.PUBLIC_ATS_FEED

    def __init__(
        self,
        *,
        board_token: str,
        company_name: str,
        request_timeout_seconds: float = 15.0,
        max_retries: int = 2,
        client: httpx.Client | None = None,
    ) -> None:
        self.board_token = board_token
        # Not part of Greenhouse's own response payload (the list-jobs
        # endpoint never returns a company name — see module docstring);
        # carried here, from the CompanyWatchlistEntry that produced this
        # adapter instance, purely so _to_raw_record can stash it onto
        # raw_payload for pipeline.py::normalize_greenhouse_record to read,
        # the same way AdzunaAdapter/ReedAdapter stash `_query_country`.
        self.company_name = company_name
        self.request_timeout_seconds = request_timeout_seconds
        self.max_retries = max_retries
        self._injected_client = client

    def is_configured(self) -> bool:
        return bool(self.board_token)

    def fetch(self, params: SourceSearchParams) -> list[RawJobRecord]:
        # `params` is accepted only to satisfy the shared SourceAdapter
        # Protocol (architecture.md section 3) — Greenhouse's public
        # Job Board API has no keyword, country, or pagination request
        # parameters (verified contract, module docstring); this adapter
        # ignores every field on it and always returns every open post for
        # `self.board_token` in one request. `SourceAdapter.fetch()`'s
        # signature stays exactly as every other adapter's.
        _ = params
        if not self.is_configured():
            raise SourceAuthError(
                f"Greenhouse adapter is not configured — missing board_token for "
                f"watchlist entry '{self.company_name}'."
            )
        client = self._injected_client or httpx.Client(timeout=self.request_timeout_seconds)
        owns_client = self._injected_client is None
        try:
            payload = self._get_jobs(client)
            jobs = payload.get("jobs", [])
            records: list[RawJobRecord] = []
            for item in jobs:
                # A job post missing its own `id` can never become a valid
                # RawJobRecord.external_id — skipped rather than fabricated
                # or allowed to crash the whole board's fetch over one
                # malformed entry (decisions.md D-047).
                if item.get("id") is None:
                    continue
                records.append(self._to_raw_record(item))
            return records
        finally:
            if owns_client:
                client.close()

    def _get_jobs(self, client: httpx.Client) -> dict[str, Any]:
        url = f"{BASE_URL}/boards/{self.board_token}/jobs"
        query = {"content": True}
        # Context for error messages only — the board token is a public
        # routing identifier, not a credential, but is still kept out of
        # any persisted SourceRun.errors message body beyond this
        # diagnostic context, matching every other adapter's discipline.
        context = f"source_id={self.source_id} board_token={self.board_token}"

        attempt = 0
        while True:
            try:
                response = client.get(url, params=query)
            except httpx.TimeoutException as exc:
                raise SourceUnavailableError(
                    f"Greenhouse request timed out ({type(exc).__name__}) [{context}]."
                ) from exc
            except httpx.HTTPError as exc:
                raise SourceUnavailableError(
                    f"Greenhouse request failed ({type(exc).__name__}) [{context}]."
                ) from exc

            if response.status_code == 200:
                result: dict[str, Any] = response.json()
                return result
            if response.status_code in (401, 403):
                # Not expected for a public, unauthenticated endpoint per
                # Greenhouse's own docs — handled defensively anyway, same
                # as every other adapter's typed-exception coverage.
                raise SourceAuthError(
                    f"Greenhouse authentication failed (HTTP {response.status_code}) [{context}]."
                )
            if response.status_code == 404:
                raise SourceNotFoundError(
                    f"Greenhouse board not found (HTTP 404) [{context}] — check "
                    "external_company_key in company_watchlist.yaml."
                )
            if response.status_code == 429:
                if attempt >= self.max_retries:
                    raise SourceRateLimitError(
                        f"Greenhouse rate limit exceeded; retries exhausted [{context}]."
                    )
                attempt += 1
                continue
            if response.status_code >= 500:
                if attempt >= self.max_retries:
                    raise SourceUnavailableError(
                        f"Greenhouse returned HTTP {response.status_code}; retries exhausted "
                        f"[{context}]."
                    )
                attempt += 1
                continue
            raise SourceUnavailableError(
                f"Greenhouse returned unexpected HTTP {response.status_code} [{context}]."
            )

    def _to_raw_record(self, item: dict[str, Any]) -> RawJobRecord:
        payload = {**item, "_company_name": self.company_name}
        return RawJobRecord(
            source_id=self.source_id,
            external_id=str(item["id"]),
            raw_url=item.get("absolute_url", ""),
            raw_payload=payload,
            fetched_at=datetime.now(UTC),
        )
