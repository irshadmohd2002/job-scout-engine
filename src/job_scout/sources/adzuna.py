"""AdzunaAdapter — the only SourceAdapter implemented in Milestone 1
(decisions.md D-002). public_api access_mode, approved in the example
registry.

Endpoint contract verified against Adzuna's developer documentation at
implementation time (decisions.md D-016): `GET /v1/api/jobs/{country}/
search/{page}`, `app_id`/`app_key` query-string auth, JSON response with a
`results` array of job objects (`id`, `title`, `company.display_name`,
`location`, `redirect_url`, `created`, `description`, `salary_min`,
`salary_max`, `contract_time`).
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

BASE_URL = "https://api.adzuna.com/v1/api"


class AdzunaAdapter:
    source_id = "adzuna_api"
    access_mode = AccessMode.PUBLIC_API

    def __init__(
        self,
        *,
        app_id: str | None,
        app_key: str | None,
        request_timeout_seconds: float = 15.0,
        max_retries: int = 2,
        client: httpx.Client | None = None,
    ) -> None:
        self.app_id = app_id
        self.app_key = app_key
        self.request_timeout_seconds = request_timeout_seconds
        self.max_retries = max_retries
        self._injected_client = client

    def is_configured(self) -> bool:
        return bool(self.app_id) and bool(self.app_key)

    def fetch(self, params: SourceSearchParams) -> list[RawJobRecord]:
        if not self.is_configured():
            raise SourceAuthError(
                "Adzuna adapter is not configured — set ADZUNA_APP_ID and ADZUNA_APP_KEY."
            )
        client = self._injected_client or httpx.Client(timeout=self.request_timeout_seconds)
        owns_client = self._injected_client is None
        try:
            records: list[RawJobRecord] = []
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
        for page in range(1, params.max_pages + 1):
            payload = self._get_page(client, country, page, params)
            page_results = payload.get("results", [])
            for item in page_results:
                records.append(self._to_raw_record(item, country))
            if len(page_results) < params.page_size:
                break  # no more pages for this country
        return records

    def _build_query(self, params: SourceSearchParams) -> dict[str, Any]:
        query: dict[str, Any] = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "results_per_page": params.page_size,
            "content-type": "application/json",
        }
        if params.keywords:
            # Milestone 2 Deliverable 5 step 4 correction: `keyword_mode`
            # (source-agnostic, set by the pipeline from PlannedQuery.mode)
            # selects which of Adzuna's two documented query parameters this
            # request uses — never both on the same request. Per Adzuna's own
            # docs (developer.adzuna.com/docs/search, checked at
            # implementation time): `what` and `what_or` are both real,
            # documented parameters; `what_or` is confirmed OR-of-individual-
            # words (the M1 behaviour this branch preserves for
            # "any_of_words"). `what` is the stricter of the two — evidence
            # available at implementation time could not confirm it
            # guarantees literal word-adjacency/quoted-phrase matching (a
            # separate `what_phrase` parameter also appears in Adzuna's docs,
            # which would be the literal-phrase option if one is needed
            # later); treat `what` here as "all supplied terms required" —
            # stricter/AND-style, not proven to be phrase-adjacency — per
            # this project's evidence bar (decisions.md D-016/D-027/D-028/
            # D-031: don't overclaim an unverified API contract). No quote
            # characters or other unverified quoting syntax are added.
            if params.keyword_mode == "exact_phrase":
                query["what"] = " ".join(params.keywords)
            else:
                query["what_or"] = " ".join(params.keywords)
        if "full_time" in params.employment_types:
            query["full_time"] = 1
        return query

    def _get_page(
        self, client: httpx.Client, country: str, page: int, params: SourceSearchParams
    ) -> dict[str, Any]:
        url = f"{BASE_URL}/jobs/{country.lower()}/search/{page}"
        query = self._build_query(params)
        # Context for error messages only — never the query dict itself, so
        # app_id/app_key (and the response body, which may echo request
        # details) can never end up in a raised message or persisted log.
        context = f"source_id={self.source_id} country={country} page={page}"

        attempt = 0
        while True:
            try:
                response = client.get(url, params=query)
            except httpx.TimeoutException as exc:
                raise SourceUnavailableError(
                    f"Adzuna request timed out ({type(exc).__name__}) [{context}]."
                ) from exc
            except httpx.HTTPError as exc:
                raise SourceUnavailableError(
                    f"Adzuna request failed ({type(exc).__name__}) [{context}]."
                ) from exc

            if response.status_code == 200:
                result: dict[str, Any] = response.json()
                return result
            if response.status_code in (401, 403):
                raise SourceAuthError(
                    f"Adzuna authentication failed (HTTP {response.status_code}) [{context}] — "
                    "check ADZUNA_APP_ID/ADZUNA_APP_KEY."
                )
            if response.status_code == 404:
                raise SourceNotFoundError(
                    f"Adzuna endpoint not found (HTTP 404) [{context}] — the request path was "
                    "well-formed but Adzuna has no matching route; this usually means the "
                    "country code isn't a supported Adzuna market, not a URL bug."
                )
            if response.status_code == 429:
                if attempt >= self.max_retries:
                    raise SourceRateLimitError(
                        f"Adzuna rate limit exceeded; retries exhausted [{context}]."
                    )
                attempt += 1
                continue
            if response.status_code >= 500:
                if attempt >= self.max_retries:
                    raise SourceUnavailableError(
                        f"Adzuna returned HTTP {response.status_code}; retries exhausted "
                        f"[{context}]."
                    )
                attempt += 1
                continue
            raise SourceUnavailableError(
                f"Adzuna returned unexpected HTTP {response.status_code} [{context}]."
            )

    def _to_raw_record(self, item: dict[str, Any], country: str) -> RawJobRecord:
        # Adzuna's own payload has no reliable ISO country code field; stash
        # the queried country (already validated by the planner's
        # per-source-country support check) so pipeline normalisation can
        # build Job.location without re-parsing free-text location strings.
        payload = {**item, "_query_country": country.upper()}
        return RawJobRecord(
            source_id=self.source_id,
            external_id=str(item["id"]),
            raw_url=item.get("redirect_url", ""),
            raw_payload=payload,
            fetched_at=datetime.now(UTC),
        )
