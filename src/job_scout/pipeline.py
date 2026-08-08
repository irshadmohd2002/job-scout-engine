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
        return None  # no adapter implemented for this source in Milestone 1 (decisions.md D-002)

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


_NORMALIZERS: dict[str, Callable[[RawJobRecord], Job]] = {"adzuna_api": normalize_adzuna_record}


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
    registry_by_id = {entry.source_id: entry for entry in registry}
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
        if not selected.planned_queries:
            # M2 Task 4: the query planner (step 3) already decided this
            # source has no query intent to execute (an empty
            # SearchProfile/CandidateProfile, or a capability like
            # company_filter=True/keyword_search=False) — never fall back to
            # an unplanned query built from candidate_profile.title_aliases
            # directly. Mirrors the `not executable` skip immediately above:
            # nothing ran for this source, so no SourceRun record is produced.
            continue

        entry = registry_by_id.get(selected.source_id)
        if entry is None:
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

        adapter = factory(selected.source_id)
        if adapter is None:
            run.errors.append(
                f"No adapter implemented for source_id '{selected.source_id}' in Milestone 1."
            )
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

        # One adapter.fetch() call per PlannedQuery, in the planner's
        # deterministic order; each call reuses the same non-keyword
        # SourceSearchParams (countries/employment_types/paging — already
        # narrowed to this source's supported countries) and only swaps in
        # that query's own keywords/mode. `PlannedQuery.mode` and
        # `SourceSearchParams.keyword_mode` share the same source-agnostic
        # Literal values by design (models.py) — this is a plain field copy,
        # never an if/elif on source_id; the adapter alone decides what a
        # given mode means as an actual request parameter. Country iteration
        # and per-country pagination stay entirely inside the (unmodified)
        # adapter.
        raw_records: list[RawJobRecord] = []
        for query in selected.planned_queries:
            query_params = search_params_template.model_copy(
                update={"keywords": query.keywords, "keyword_mode": query.mode}
            )
            try:
                raw_records.extend(adapter.fetch(query_params))
            except SourceAdapterError as exc:
                run.errors.append(str(exc))
                # Fail-fast per source: stop attempting the remaining planned
                # queries, consistent with the adapter's own existing
                # per-country fail-fast behaviour inside a single fetch()
                # call (one country's failure already aborts the rest of
                # that call — see AdzunaAdapter.fetch()). Records already
                # gathered from earlier, successful queries in this run are
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
                dedup = match_against_recent(job, recent_jobs)
                if dedup.tier == DedupTier.CROSS_SOURCE_DUPLICATE and dedup.matched_job is not None:
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
