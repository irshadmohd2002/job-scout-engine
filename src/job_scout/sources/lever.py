"""LeverAdapter — Milestone 2 Deliverable 5 step 8 (decisions.md D-048).
`public_ats_feed` access_mode, ships `manual_review` in the packaged
registry template (never `approved` by default — CLAUDE.md hard
constraint 1). Watchlist-scoped, same execution shape as
`sources/greenhouse.py` (decisions.md D-047): one adapter instance is
constructed per `CompanyWatchlistEntry` whose `source_id` is
`lever_public_postings` (`pipeline.py`'s watchlist fan-out, unmodified by
this task — it already branches on `SourceCapabilities.company_filter`,
not a source_id string), never per keyword query —
`SourceCapabilities.company_filter=True`/`keyword_search=False` for this
source means `source_intelligence/query_planner.py` already emits zero
`PlannedQuery`s for it.

Endpoint contract verified against Lever's official Postings API
documentation (`lever/postings-api`,
https://github.com/lever/postings-api, checked at implementation time):
`GET https://api.lever.co/v0/postings/{site}?mode=json`, no authentication
("Public listing endpoints require no authentication"). `site` is the
company's Lever site slug (`CompanyWatchlistEntry.external_company_key`,
inserted directly into the URL path, never derived from `company_name`).
`mode=json` is always sent (the docs state the endpoint outputs HTML,
iframe, or JSON depending on `Accept` header/`mode` query param, with the
query param taking precedence) — omitting it risks an HTML response this
adapter cannot parse.

**Response shape: a bare JSON array, not a wrapped object.** Unlike
Greenhouse's `{"jobs": [...], "meta": {...}}`, a live, unauthenticated
request against a real Lever-hosted site
(`https://api.lever.co/v0/postings/lever?mode=json`, checked at
implementation time) returned `[]` directly at the top level — confirming
the documented per-posting field list is returned as a plain list, not
nested under a named key. `_get_postings` treats any other top-level shape
(an unexpected wrapped object) as zero postings rather than guessing a new
contract, mirroring Greenhouse's `payload.get("jobs", [])` empty-fallback
discipline for the analogous case.

**No pagination in this task (decisions.md D-048).** Lever's docs do
document `skip`/`limit` query parameters, unlike Greenhouse's endpoint
which has none at all — but neither the official docs nor the live check
above expose a total-count/`hasMore` termination signal, so there is no
verified way to know when to stop paging. Per this project's established
evidence bar (decisions.md D-016/D-027/D-028/D-031/D-046/D-047 — never
build against an unconfirmed contract), this adapter makes exactly **one**
HTTP request per `fetch()` call and never sends `skip`/`limit` at all.
`SourceCapabilities.pagination=False` records this as a known, deliberate
limitation, not an oversight; a future task could revisit this if a
reliable stop condition is confirmed (e.g. "fewer than `limit` results
returned" — itself unconfirmed without documentation stating `limit`'s
default/max, so not assumed here either).

**`posted_at` is never populated from `createdAt`.** A live response can
include an undocumented `createdAt` field, but Lever's own postings-api
issue tracker
(https://github.com/lever/postings-api/issues/35, "`createdAt` field not
documented and no correct (v0)") reports that its values do not parse into
sane timestamps and the field has no documented meaning. Treating an
unreliable, unofficial field as a posting date would be exactly the kind
of fabricated-certainty this project's evidence bar forbids (same
treatment D-046/D-047 already gave Reed's/Greenhouse's missing posted-date
signal) — `posted_at` stays `None`.

**`workplaceType` is real, structured, and used directly for
`remote_type`** (decisions.md D-048) — a documented enum
(`unspecified|on-site|remote|hybrid`), unlike Adzuna/Reed/Greenhouse which
have no structured remote signal at all and fall back to the shared
`pipeline._guess_remote_type` text heuristic. Using the source's own
authoritative field instead of guessing from description text is the more
accurate choice when real structured data exists — see
`pipeline.py::normalize_lever_record` for the mapping.

**Fields intentionally left `None`/unmapped**: `posted_at` (see above).
`applyUrl` is preserved on `raw_payload` (kept for potential future use —
Lever's separate hosted application-form link) but is not surfaced onto
any `Job`/`RawJobRecord` field today; only `hostedUrl` becomes `raw_url`
(`RawJobRecord.raw_url`/`Job`'s canonical URL — there is exactly one URL
field to populate, same as every other adapter). `country` uses Lever's
own documented `country` field (ISO 3166-1 alpha-2 or `null`) directly —
unlike Greenhouse, this is real structured data, not a freeform string, so
no inference/guessing is needed; `null`/absent normalizes to `""` (the
same "honest unknown" convention every other adapter already uses for a
missing required string).

`RawJobRecord.raw_url` uses the response's own `hostedUrl` — a genuine
documented canonical application/posting URL
(`SourceCapabilities.canonical_application_url=True` for this source, same
as Greenhouse).
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

BASE_URL = "https://api.lever.co/v0"


class LeverAdapter:
    source_id = "lever_public_postings"
    access_mode = AccessMode.PUBLIC_ATS_FEED

    def __init__(
        self,
        *,
        company_key: str,
        company_name: str,
        request_timeout_seconds: float = 15.0,
        max_retries: int = 2,
        client: httpx.Client | None = None,
    ) -> None:
        self.company_key = company_key
        # Not part of Lever's own response payload (the list-postings
        # endpoint never returns a company name — see module docstring);
        # carried here, from the CompanyWatchlistEntry that produced this
        # adapter instance, purely so _to_raw_record can stash it onto
        # raw_payload for pipeline.py::normalize_lever_record to read, the
        # same way GreenhouseAdapter stashes company_name (and
        # AdzunaAdapter/ReedAdapter stash `_query_country`).
        self.company_name = company_name
        self.request_timeout_seconds = request_timeout_seconds
        self.max_retries = max_retries
        self._injected_client = client

    def is_configured(self) -> bool:
        return bool(self.company_key)

    def fetch(self, params: SourceSearchParams) -> list[RawJobRecord]:
        # `params` is accepted only to satisfy the shared SourceAdapter
        # Protocol (architecture.md section 3) — this adapter always
        # fetches every open posting for `self.company_key` in one
        # unfiltered request (module docstring: no keyword/location/team
        # filter is ever sent), the same "company_filter=True source
        # ignores its SourceSearchParams" shape GreenhouseAdapter already
        # established.
        _ = params
        if not self.is_configured():
            raise SourceAuthError(
                f"Lever adapter is not configured — missing company_key for "
                f"watchlist entry '{self.company_name}'."
            )
        client = self._injected_client or httpx.Client(timeout=self.request_timeout_seconds)
        owns_client = self._injected_client is None
        try:
            postings = self._get_postings(client)
            records: list[RawJobRecord] = []
            for item in postings:
                # A posting missing its own `id` can never become a valid
                # RawJobRecord.external_id — skipped rather than fabricated
                # or allowed to crash the whole company's fetch over one
                # malformed entry (same discipline as Greenhouse's D-047).
                if item.get("id") is None:
                    continue
                records.append(self._to_raw_record(item))
            return records
        finally:
            if owns_client:
                client.close()

    def _get_postings(self, client: httpx.Client) -> list[dict[str, Any]]:
        url = f"{BASE_URL}/postings/{self.company_key}"
        # No skip/limit — module docstring: pagination is deliberately not
        # implemented in this task (decisions.md D-048).
        query = {"mode": "json"}
        # Context for error messages only — the company key is a public
        # routing identifier, not a credential, but is still kept out of
        # any persisted SourceRun.errors message body beyond this
        # diagnostic context, matching every other adapter's discipline.
        context = f"source_id={self.source_id} company_key={self.company_key}"

        attempt = 0
        while True:
            try:
                response = client.get(url, params=query)
            except httpx.TimeoutException as exc:
                raise SourceUnavailableError(
                    f"Lever request timed out ({type(exc).__name__}) [{context}]."
                ) from exc
            except httpx.HTTPError as exc:
                raise SourceUnavailableError(
                    f"Lever request failed ({type(exc).__name__}) [{context}]."
                ) from exc

            if response.status_code == 200:
                data: Any = response.json()
                if isinstance(data, list):
                    return data
                # Documented/live-verified contract is a bare JSON array
                # (module docstring) — an unexpected wrapped shape is
                # treated as zero postings, never guessed at.
                return []
            if response.status_code in (401, 403):
                # Not expected for a public, unauthenticated endpoint per
                # Lever's own docs — handled defensively anyway, same as
                # every other adapter's typed-exception coverage.
                raise SourceAuthError(
                    f"Lever authentication failed (HTTP {response.status_code}) [{context}]."
                )
            if response.status_code == 404:
                raise SourceNotFoundError(
                    f"Lever company not found (HTTP 404) [{context}] — check "
                    "external_company_key in company_watchlist.yaml."
                )
            if response.status_code == 429:
                if attempt >= self.max_retries:
                    raise SourceRateLimitError(
                        f"Lever rate limit exceeded; retries exhausted [{context}]."
                    )
                attempt += 1
                continue
            if response.status_code >= 500:
                if attempt >= self.max_retries:
                    raise SourceUnavailableError(
                        f"Lever returned HTTP {response.status_code}; retries exhausted "
                        f"[{context}]."
                    )
                attempt += 1
                continue
            raise SourceUnavailableError(
                f"Lever returned unexpected HTTP {response.status_code} [{context}]."
            )

    def _to_raw_record(self, item: dict[str, Any]) -> RawJobRecord:
        payload = {**item, "_company_name": self.company_name}
        return RawJobRecord(
            source_id=self.source_id,
            external_id=str(item["id"]),
            raw_url=item.get("hostedUrl", ""),
            raw_payload=payload,
            fetched_at=datetime.now(UTC),
        )
