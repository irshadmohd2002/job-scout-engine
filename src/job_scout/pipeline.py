"""Pipeline orchestration (architecture.md section 11).

Runs the full run-once sequence: generate plan -> fetch (executable sources
only) -> normalise -> persist provenance -> deduplicate -> hard filters ->
pre-filter -> Stage 5 scoring -> persist. --dry-run only ever disables
notification/external-write actions (there are none in M1 — see
MILESTONE_1.md "Explicitly out of scope") — everything else in this module
runs identically regardless of dry_run; see "Dry-run semantics" in
architecture.md section 11.

Milestone 2 Deliverable 5 step 4: for each executable selected source,
`adapter.fetch()` is called once per `SelectedSource.planned_queries` entry
(the query planner's bounded, `SearchProfile`-driven output — step 3),
instead of once per source. `AdzunaAdapter` itself is unmodified; only the
call cardinality and which `SourceSearchParams.keywords` each call carries
changes. Raw records from every query are aggregated before
normalisation/dedup, which is otherwise unchanged.

Milestone 2 Deliverable 5 step 7 (decisions.md D-047): a selected source
whose `SourceRegistryEntry.capabilities.company_filter` is `True`
(Greenhouse today; Lever, step 8, the same shape) never has
`planned_queries` (query_planner.py already returns none for it) and is
never routed through the query-fan-out above. Instead `run_once` fans out
over `CompanyWatchlistEntry` rows matching that source_id — one
`adapter.fetch()` call per watchlisted company, via a fresh adapter
instance per company (`watchlist_adapter_factory`, mirroring
`adapter_factory`'s existing shape) — then joins back into the exact same
aggregation/normalisation/dedup path every other source already uses.
"""

from __future__ import annotations

import html
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from job_scout.config import EnvConfig, ExecutionLimits, ScoringWeights, SourceScoringWeights
from job_scout.deduplication import DedupTier, compute_fingerprint, match_against_recent
from job_scout.matching.hard_filters import evaluate_hard_filters
from job_scout.matching.prefilter import PrefilterWeights, run_prefilter
from job_scout.matching.scoring import build_match_result
from job_scout.models import (
    CandidateProfile,
    CompanyWatchlistEntry,
    Job,
    Location,
    MatchResult,
    RawJobRecord,
    RemoteType,
    SearchExecutionPlan,
    SearchProfile,
    SourceExternalId,
    SourceProvenance,
    SourceRegistryEntry,
    SourceRun,
    SourceRunStatus,
)
from job_scout.repository.base import JobRepository
from job_scout.source_intelligence.compliance import authorize
from job_scout.source_intelligence.planner import build_plan
from job_scout.sources.adzuna import AdzunaAdapter
from job_scout.sources.base import SourceAdapter, SourceAdapterError
from job_scout.sources.greenhouse import GreenhouseAdapter
from job_scout.sources.lever import LeverAdapter
from job_scout.sources.reed import ReedAdapter

RECENT_WINDOW_DAYS = 45
RECENT_WINDOW_LIMIT = 2000

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")

AdapterFactory = Callable[[str], SourceAdapter | None]


def _default_adapter_factory(env: EnvConfig, execution_limits: ExecutionLimits) -> AdapterFactory:
    def factory(source_id: str) -> SourceAdapter | None:
        if source_id == "adzuna_api":
            return AdzunaAdapter(
                app_id=env.adzuna_app_id,
                app_key=env.adzuna_app_key,
                request_timeout_seconds=execution_limits.request_timeout_seconds,
                max_retries=execution_limits.max_retries,
            )
        if source_id == "reed_api":
            return ReedAdapter(
                api_key=env.reed_api_key,
                request_timeout_seconds=execution_limits.request_timeout_seconds,
                max_retries=execution_limits.max_retries,
            )
        return None  # no adapter implemented for this source_id yet (decisions.md D-002)

    return factory


# Milestone 2 Deliverable 5 step 7 (decisions.md D-047): a separate, narrow
# factory shape for watchlist-scoped (company_filter=True) sources — one
# adapter instance per `CompanyWatchlistEntry`, not one per source_id like
# `AdapterFactory` above. Not a generalisation of `AdapterFactory` (CLAUDE.md
# hard constraint 9): the two factories exist because the two fan-out shapes
# are genuinely different (keyword queries vs. watchlisted companies), not
# because a single mechanism was made pluggable.
WatchlistAdapterFactory = Callable[[str, CompanyWatchlistEntry], SourceAdapter | None]


def _default_watchlist_adapter_factory(
    execution_limits: ExecutionLimits,
) -> WatchlistAdapterFactory:
    def factory(source_id: str, company: CompanyWatchlistEntry) -> SourceAdapter | None:
        if source_id == "greenhouse_public_feeds":
            return GreenhouseAdapter(
                board_token=company.external_company_key,
                company_name=company.company_name,
                request_timeout_seconds=execution_limits.request_timeout_seconds,
                max_retries=execution_limits.max_retries,
            )
        if source_id == "lever_public_postings":
            return LeverAdapter(
                company_key=company.external_company_key,
                company_name=company.company_name,
                request_timeout_seconds=execution_limits.request_timeout_seconds,
                max_retries=execution_limits.max_retries,
            )
        return None  # no watchlist-scoped adapter implemented for this source_id yet

    return factory


def strip_html(raw: str) -> str:
    text = html.unescape(_TAG_RE.sub(" ", raw))
    return _WHITESPACE_RE.sub(" ", text).strip()


def _guess_remote_type(description_text: str) -> RemoteType:
    text = description_text.lower()
    if "hybrid" in text:
        return RemoteType.HYBRID
    if "remote" in text:
        return RemoteType.REMOTE
    return RemoteType.UNKNOWN


def normalize_adzuna_record(record: RawJobRecord) -> Job:
    payload = record.raw_payload
    title = str(payload.get("title", "")).strip()
    company = str(payload.get("company", {}).get("display_name", "Unknown")).strip()
    country = str(payload.get("_query_country", "")).upper()
    location_display = payload.get("location", {}).get("display_name")
    location = Location(country=country, city=location_display)

    description_raw = str(payload.get("description", ""))
    description_text = strip_html(description_raw)

    posted_at: datetime | None = None
    created = payload.get("created")
    if created:
        try:
            posted_at = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        except ValueError:
            posted_at = None

    fingerprint = compute_fingerprint(
        source_id=record.source_id,
        external_id=record.external_id,
        raw_url=record.raw_url,
        company=company,
        title=title,
        location=location,
        description_text=description_text,
        posted_at=posted_at,
    )

    provenance = SourceProvenance(
        source_id=record.source_id,
        access_mode=AdzunaAdapter.access_mode,
        fetched_at=record.fetched_at,
        raw_url=record.raw_url,
        external_id=record.external_id,
    )

    return Job(
        job_id=str(uuid.uuid4()),
        external_ids=[SourceExternalId(source_id=record.source_id, external_id=record.external_id)],
        title=title,
        normalized_title=fingerprint.normalized_title,
        company=company,
        normalized_company=fingerprint.normalized_company,
        location=location,
        remote_type=_guess_remote_type(description_text),
        employment_type=payload.get("contract_time"),
        description_raw=description_raw,
        description_text=description_text,
        posted_at=posted_at,
        collected_at=datetime.now(UTC),
        salary_min=payload.get("salary_min"),
        salary_max=payload.get("salary_max"),
        salary_currency=None,
        source_provenance=[provenance],
        fingerprint=fingerprint,
        role_family_hints=[],
    )


def normalize_reed_record(record: RawJobRecord) -> Job:
    # Milestone 2 Deliverable 5 step 5 (decisions.md D-046): field names
    # below follow ReedAdapter's own documented-label-to-camelCase mapping
    # (see sources/reed.py's module docstring for the verification gap).
    payload = record.raw_payload
    title = str(payload.get("jobTitle", "")).strip()
    company = str(payload.get("employerName", "Unknown")).strip()
    country = str(payload.get("_query_country", "")).upper()
    location = Location(country=country, city=payload.get("locationName"))

    description_raw = str(payload.get("description", ""))
    description_text = strip_html(description_raw)

    # Reed's documented Search Returns carries no posted-date field — never
    # guessed (D-040's normalization-field rule).
    posted_at: datetime | None = None

    fingerprint = compute_fingerprint(
        source_id=record.source_id,
        external_id=record.external_id,
        raw_url=record.raw_url,
        company=company,
        title=title,
        location=location,
        description_text=description_text,
        posted_at=posted_at,
    )

    provenance = SourceProvenance(
        source_id=record.source_id,
        access_mode=ReedAdapter.access_mode,
        fetched_at=record.fetched_at,
        raw_url=record.raw_url,
        external_id=record.external_id,
    )

    return Job(
        job_id=str(uuid.uuid4()),
        external_ids=[SourceExternalId(source_id=record.source_id, external_id=record.external_id)],
        title=title,
        normalized_title=fingerprint.normalized_title,
        company=company,
        normalized_company=fingerprint.normalized_company,
        location=location,
        remote_type=_guess_remote_type(description_text),
        # Reed's documented Search Returns has no job-type/contract-type
        # field (that detail is Details-endpoint-only, deliberately not
        # called here — see sources/reed.py) — never guessed from
        # title/description (D-040).
        employment_type=None,
        description_raw=description_raw,
        description_text=description_text,
        posted_at=posted_at,
        collected_at=datetime.now(UTC),
        salary_min=payload.get("minimumSalary"),
        salary_max=payload.get("maximumSalary"),
        # Currency is a Details-endpoint-only field per Reed's docs; the
        # Search endpoint this adapter uses never returns one.
        salary_currency=None,
        source_provenance=[provenance],
        fingerprint=fingerprint,
        role_family_hints=[],
    )


def normalize_greenhouse_record(record: RawJobRecord) -> Job:
    # Milestone 2 Deliverable 5 step 7 (decisions.md D-047): field names
    # verified against Greenhouse's official Job Board API docs — see
    # sources/greenhouse.py's module docstring for the full contract and
    # the reasoning behind every field left None/unmapped below.
    payload = record.raw_payload
    title = str(payload.get("title", "")).strip()
    # Greenhouse's list-jobs response never includes a company name (one
    # board = one company, implicitly) — the only source of truth is the
    # CompanyWatchlistEntry that produced this fetch, stashed by
    # GreenhouseAdapter._to_raw_record the same way Adzuna/Reed stash
    # `_query_country`.
    company = str(payload.get("_company_name", "Unknown")).strip()
    # No structured country code anywhere in the response — only a single
    # freeform `location.name` string. Never parsed/guessed into an ISO
    # country (this project's evidence bar forbids inferring structured
    # data from free text); "" is an honest "unknown", not a fabrication —
    # see sources/greenhouse.py's module docstring and decisions.md D-047
    # for the resulting (documented, not silently absorbed) hard-filter
    # interaction.
    location_name = payload.get("location", {}).get("name")
    location = Location(country="", city=location_name)

    description_raw = str(payload.get("content", ""))
    description_text = strip_html(description_raw)

    # Only `updated_at` (last-modified) is present — never conflated with a
    # posting date (D-040's normalization-field rule: None if
    # unparseable/absent, never guessed).
    posted_at: datetime | None = None

    fingerprint = compute_fingerprint(
        source_id=record.source_id,
        external_id=record.external_id,
        raw_url=record.raw_url,
        company=company,
        title=title,
        location=location,
        description_text=description_text,
        posted_at=posted_at,
    )

    provenance = SourceProvenance(
        source_id=record.source_id,
        access_mode=GreenhouseAdapter.access_mode,
        fetched_at=record.fetched_at,
        raw_url=record.raw_url,
        external_id=record.external_id,
    )

    return Job(
        job_id=str(uuid.uuid4()),
        external_ids=[SourceExternalId(source_id=record.source_id, external_id=record.external_id)],
        title=title,
        normalized_title=fingerprint.normalized_title,
        company=company,
        normalized_company=fingerprint.normalized_company,
        location=location,
        remote_type=_guess_remote_type(description_text),
        # No employment-type/contract-type field is documented anywhere on
        # this endpoint — never guessed from title/description (D-040).
        employment_type=None,
        description_raw=description_raw,
        description_text=description_text,
        posted_at=posted_at,
        collected_at=datetime.now(UTC),
        # No salary field on the list-jobs endpoint this adapter calls
        # (pay_input_ranges is single-job-detail-only, behind
        # ?pay_transparency=true — never called, see sources/greenhouse.py).
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        source_provenance=[provenance],
        fingerprint=fingerprint,
        role_family_hints=[],
    )


# Milestone 2 Deliverable 5 step 8 (decisions.md D-048): Lever's
# `workplaceType` is real, documented, structured data — unlike every other
# source in this project so far, which has no native remote-work signal and
# falls back to `_guess_remote_type`'s text heuristic. Mapping a source's
# own authoritative field directly is more accurate than guessing when that
# data genuinely exists; `"unspecified"`/an unrecognised/missing value maps
# to `RemoteType.UNKNOWN`, the same "no evidence" sentinel every other
# normalizer already uses (never `None` — `Job.remote_type` is a required
# `RemoteType` field, not optional).
_LEVER_WORKPLACE_TYPE_MAP: dict[str, RemoteType] = {
    "remote": RemoteType.REMOTE,
    "hybrid": RemoteType.HYBRID,
    "on-site": RemoteType.ONSITE,
}


def normalize_lever_record(record: RawJobRecord) -> Job:
    # Milestone 2 Deliverable 5 step 8 (decisions.md D-048): field names
    # verified against Lever's official Postings API docs — see
    # sources/lever.py's module docstring for the full contract and the
    # reasoning behind every field left None/unmapped below.
    payload = record.raw_payload
    title = str(payload.get("text", "")).strip()
    # Lever's list-postings response never includes a company name (one
    # site = one company, implicitly) — the only source of truth is the
    # CompanyWatchlistEntry that produced this fetch, stashed by
    # LeverAdapter._to_raw_record the same way GreenhouseAdapter stashes
    # company_name.
    company = str(payload.get("_company_name", "Unknown")).strip()
    # Lever documents a real, structured `country` field (ISO 3166-1
    # alpha-2, or null) — unlike Greenhouse, this is genuine structured
    # data, never inferred/guessed from free text; null/absent normalizes
    # to "" (the same honest-unknown convention every adapter already uses
    # for a missing required string).
    country = str(payload.get("country") or "").strip()
    categories = payload.get("categories") or {}
    location_name = categories.get("location")
    location = Location(country=country, city=location_name)

    description_raw = str(payload.get("description", ""))
    description_text = strip_html(description_raw)

    # A live response can carry an undocumented `createdAt` field, but
    # Lever's own postings-api issue tracker (issue #35: "createdAt field
    # not documented and no correct (v0)") reports its values do not parse
    # into sane timestamps — never treated as a posting date (D-040's rule:
    # None if unconfirmed, never guessed; see sources/lever.py's module
    # docstring for the full citation).
    posted_at: datetime | None = None

    employment_type = categories.get("commitment") or None

    workplace_type = payload.get("workplaceType")
    remote_type = _LEVER_WORKPLACE_TYPE_MAP.get(str(workplace_type), RemoteType.UNKNOWN)

    salary_range = payload.get("salaryRange") or {}

    fingerprint = compute_fingerprint(
        source_id=record.source_id,
        external_id=record.external_id,
        raw_url=record.raw_url,
        company=company,
        title=title,
        location=location,
        description_text=description_text,
        posted_at=posted_at,
    )

    provenance = SourceProvenance(
        source_id=record.source_id,
        access_mode=LeverAdapter.access_mode,
        fetched_at=record.fetched_at,
        raw_url=record.raw_url,
        external_id=record.external_id,
    )

    return Job(
        job_id=str(uuid.uuid4()),
        external_ids=[SourceExternalId(source_id=record.source_id, external_id=record.external_id)],
        title=title,
        normalized_title=fingerprint.normalized_title,
        company=company,
        normalized_company=fingerprint.normalized_company,
        location=location,
        remote_type=remote_type,
        employment_type=employment_type,
        description_raw=description_raw,
        description_text=description_text,
        posted_at=posted_at,
        collected_at=datetime.now(UTC),
        # salaryRange is optional per Lever's docs — missing entirely
        # normalizes to None, never fabricated/defaulted to 0 (item 13 of
        # this task's frozen scope); never inferred from salaryDescription
        # freeform text either.
        salary_min=salary_range.get("min"),
        salary_max=salary_range.get("max"),
        salary_currency=salary_range.get("currency"),
        source_provenance=[provenance],
        fingerprint=fingerprint,
        role_family_hints=[],
    )


_NORMALIZERS: dict[str, Callable[[RawJobRecord], Job]] = {
    "adzuna_api": normalize_adzuna_record,
    "reed_api": normalize_reed_record,
    "greenhouse_public_feeds": normalize_greenhouse_record,
    "lever_public_postings": normalize_lever_record,
}


@dataclass
class RunOnceResult:
    plan: SearchExecutionPlan
    source_runs: list[SourceRun] = field(default_factory=list)
    results: list[tuple[Job, MatchResult]] = field(default_factory=list)


def _effective_job_limit(execution_limits: ExecutionLimits, cli_limit: int | None) -> float:
    limits = [v for v in (execution_limits.max_jobs_processed_per_run, cli_limit) if v is not None]
    return min(limits) if limits else float("inf")


def run_once(
    *,
    candidate_profile: CandidateProfile,
    search_profile: SearchProfile,
    registry: list[SourceRegistryEntry],
    execution_limits: ExecutionLimits,
    scoring_weights: ScoringWeights,
    source_scoring_weights: SourceScoringWeights,
    repository: JobRepository,
    env: EnvConfig,
    dry_run: bool,
    limit: int | None = None,
    adapter_factory: AdapterFactory | None = None,
    company_watchlist: list[CompanyWatchlistEntry] | None = None,
    watchlist_adapter_factory: WatchlistAdapterFactory | None = None,
) -> RunOnceResult:
    # dry_run is accepted but unused: M1 ships no notification/external-write
    # channel to disable yet (MILESTONE_1.md "out of scope") — the parameter
    # exists now so the CLI's --dry-run flag has somewhere real to plug in
    # without a pipeline signature change once Milestone 5 adds one.
    _ = dry_run

    plan = build_plan(
        candidate_profile,
        search_profile,
        registry,
        execution_limits,
        source_scoring_weights,
        env=env,
    )

    factory = adapter_factory or _default_adapter_factory(env, execution_limits)
    watchlist_factory = watchlist_adapter_factory or _default_watchlist_adapter_factory(
        execution_limits
    )
    watchlist = company_watchlist or []
    registry_by_id = {entry.source_id: entry for entry in registry}
    # Milestone 2 Deliverable 5 step 9 (decisions.md D-041): source_id ->
    # SourceCapabilities, passed through to match_against_recent so the new
    # cross-source exact-URL dedup tier can gate on
    # canonical_application_url without deduplication.py depending on the
    # registry type itself.
    capabilities_by_source = {sid: entry.capabilities for sid, entry in registry_by_id.items()}
    prefilter_weights = PrefilterWeights.from_scoring_weights(scoring_weights)

    job_cap = _effective_job_limit(execution_limits, limit)
    processed_count = 0

    recent_since = datetime.now(UTC) - timedelta(days=RECENT_WINDOW_DAYS)
    recent_jobs = repository.list_recent_jobs(since=recent_since, limit=RECENT_WINDOW_LIMIT)

    source_runs: list[SourceRun] = []
    results: list[tuple[Job, MatchResult]] = []

    for selected in plan.selected_sources:
        if not selected.executable:
            continue

        entry = registry_by_id.get(selected.source_id)
        if entry is None:
            continue

        # Milestone 2 Deliverable 5 step 7 (decisions.md D-047):
        # company_filter=True sources (Greenhouse today; Lever, step 8) never
        # get keyword PlannedQuerys — query_planner.py already returns none
        # for them — and are fanned out over the watchlist instead, below.
        # Every other source keeps the step-4 planned_queries fan-out
        # unchanged.
        is_watchlist_source = entry.capabilities.company_filter
        matching_companies: list[CompanyWatchlistEntry] = []
        if is_watchlist_source:
            matching_companies = [c for c in watchlist if c.source_id == selected.source_id]
            if not matching_companies:
                # R-10 (MILESTONE_2.md): zero watchlist entries -> zero
                # calls -> zero jobs. Mirrors the `not executable` skip
                # above: nothing ran for this source, so no SourceRun.
                continue
        elif not selected.planned_queries:
            # M2 Task 4: the query planner (step 3) already decided this
            # source has no query intent to execute (an empty
            # SearchProfile/CandidateProfile, or a capability like
            # keyword_search=False) — never fall back to an unplanned query
            # built from candidate_profile.title_aliases directly. Mirrors
            # the `not executable` skip above: nothing ran for this source,
            # so no SourceRun record is produced.
            continue

        # defence-in-depth re-check immediately before any adapter call
        # (architecture.md section 3/7): registry state could have changed
        # since the plan was generated.
        decision = authorize(entry, entry.access_mode)

        run = SourceRun(
            run_id=str(uuid.uuid4()),
            source_id=selected.source_id,
            search_profile_ref=search_profile.profile_id,
            started_at=datetime.now(UTC),
            status=SourceRunStatus.FAILED,
            jobs_fetched=0,
            jobs_new=0,
            jobs_duplicate=0,
            errors=[],
        )

        if not decision.allowed:
            run.errors.append(f"compliance re-check failed at execution time: {decision.reason}")
            run.completed_at = datetime.now(UTC)
            source_runs.append(run)
            repository.save_source_run(run)
            continue

        normalizer = _NORMALIZERS.get(selected.source_id)
        search_params_template = selected.search_params
        if normalizer is None or search_params_template is None:
            run.errors.append(f"No normaliser implemented for source_id '{selected.source_id}'.")
            run.completed_at = datetime.now(UTC)
            source_runs.append(run)
            repository.save_source_run(run)
            continue

        raw_records: list[RawJobRecord] = []
        if is_watchlist_source:
            # One fresh adapter instance, and one adapter.fetch() call, per
            # matching CompanyWatchlistEntry (decisions.md D-047) — never one
            # shared adapter looping over companies internally, so a single
            # company's failure is attributable and fail-fast stays at the
            # same per-source granularity the query-fan-out path already
            # uses below.
            for company in matching_companies:
                company_adapter = watchlist_factory(selected.source_id, company)
                if company_adapter is None:
                    run.errors.append(
                        f"No adapter implemented for source_id '{selected.source_id}'."
                    )
                    break
                try:
                    raw_records.extend(company_adapter.fetch(search_params_template))
                except SourceAdapterError as exc:
                    run.errors.append(str(exc))
                    break
        else:
            adapter = factory(selected.source_id)
            if adapter is None:
                run.errors.append(
                    f"No adapter implemented for source_id '{selected.source_id}' in Milestone 1."
                )
            else:
                # One adapter.fetch() call per PlannedQuery, in the planner's
                # deterministic order; each call reuses the same non-keyword
                # SourceSearchParams (countries/employment_types/paging —
                # already narrowed to this source's supported countries) and
                # only swaps in that query's own keywords/mode.
                # `PlannedQuery.mode` and `SourceSearchParams.keyword_mode`
                # share the same source-agnostic Literal values by design
                # (models.py) — this is a plain field copy, never an if/elif
                # on source_id; the adapter alone decides what a given mode
                # means as an actual request parameter. Country iteration and
                # per-country pagination stay entirely inside the (unmodified)
                # adapter.
                for query in selected.planned_queries:
                    query_params = search_params_template.model_copy(
                        update={"keywords": query.keywords, "keyword_mode": query.mode}
                    )
                    try:
                        raw_records.extend(adapter.fetch(query_params))
                    except SourceAdapterError as exc:
                        run.errors.append(str(exc))
                        # Fail-fast per source: stop attempting the remaining
                        # planned queries, consistent with the adapter's own
                        # existing per-country fail-fast behaviour inside a
                        # single fetch() call (one country's failure already
                        # aborts the rest of that call — see
                        # AdzunaAdapter.fetch()). Records already gathered
                        # from earlier, successful queries in this run are
                        # kept, not discarded.
                        break

        if not raw_records and run.errors:
            # Every attempted query failed (or the first one did, before any
            # data came back) — same "isolated FAILED run, zero jobs" outcome
            # a single-query source has always had on a fetch error.
            run.completed_at = datetime.now(UTC)
            source_runs.append(run)
            repository.save_source_run(run)
            continue

        run.jobs_fetched = len(raw_records)
        hit_cap = False

        for raw in raw_records:
            if processed_count >= job_cap:
                hit_cap = True
                break
            processed_count += 1

            job = normalizer(raw)
            existing = repository.find_by_fingerprint(job.fingerprint)
            is_new = False

            if existing is not None:
                repository.merge_provenance(existing.job_id, job.source_provenance[0])
                run.jobs_duplicate += 1
                effective_job = existing
            else:
                dedup = match_against_recent(
                    job, recent_jobs, source_capabilities=capabilities_by_source
                )
                if (
                    dedup.tier in (DedupTier.EXACT_DUPLICATE, DedupTier.PROBABLE_DUPLICATE)
                    and dedup.matched_job is not None
                ):
                    repository.merge_provenance(dedup.matched_job.job_id, job.source_provenance[0])
                    run.jobs_duplicate += 1
                    effective_job = dedup.matched_job
                else:
                    if dedup.tier == DedupTier.REPOST and dedup.matched_job is not None:
                        job = job.model_copy(update={"previous_job_id": dedup.matched_job.job_id})
                    effective_job = job
                    is_new = True
                    run.jobs_new += 1

            hard_filter_result = evaluate_hard_filters(
                effective_job, candidate_profile, search_profile
            )
            prefilter_result = run_prefilter(
                effective_job, candidate_profile, search_profile, prefilter_weights
            )

            if is_new:
                effective_job = effective_job.model_copy(
                    update={"role_family_hints": prefilter_result.role_family_hints}
                )
                repository.save_job(effective_job)
                recent_jobs.append(effective_job)

            match_result = build_match_result(
                effective_job,
                candidate_profile,
                search_profile,
                hard_filter_result,
                prefilter_result,
                scoring_weights,
            )
            repository.save_match_result(match_result)
            results.append((effective_job, match_result))

        run.status = SourceRunStatus.PARTIAL if (hit_cap or run.errors) else SourceRunStatus.SUCCESS
        run.completed_at = datetime.now(UTC)
        source_runs.append(run)
        repository.save_source_run(run)

        if processed_count >= job_cap:
            break

    results.sort(key=lambda pair: (pair[1].final_score is None, -(pair[1].final_score or 0)))
    return RunOnceResult(plan=plan, source_runs=source_runs, results=results)
