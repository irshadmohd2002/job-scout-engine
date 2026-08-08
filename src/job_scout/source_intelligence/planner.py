"""Deterministic source planner (architecture.md section 6, section 11a).

Builds a SearchExecutionPlan from a CandidateProfile + SearchProfile + the
loaded SourceRegistry. Pure function, no I/O, no adapter calls — see
MILESTONE_1.md: "The plan command must not call any source adapter or
consume API quota."
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from job_scout.config import ConfigError, EnvConfig, ExecutionLimits, SourceScoringWeights
from job_scout.countries import UnknownCountryError, resolve_regions
from job_scout.models import (
    CandidateProfile,
    ConfigStatus,
    CountryExclusion,
    ExcludedSource,
    PlannedQuery,
    SearchExecutionPlan,
    SearchProfile,
    SelectedSource,
    SourceRegistryEntry,
    SourceSearchParams,
    SourceType,
)
from job_scout.source_intelligence.compliance import authorize
from job_scout.source_intelligence.query_planner import build_planned_queries

_GLOBAL_COVERAGE = "global"
_GENERAL_ROLE_COVERAGE = "general"


def _country_supported(entry: SourceRegistryEntry, country: str) -> bool:
    coverage = set(entry.geographic_coverage)
    if _GLOBAL_COVERAGE in coverage or country in coverage:
        return True
    try:
        regions = set(resolve_regions(country))
    except UnknownCountryError:
        return False
    return bool(regions & coverage)


def _supported_countries(entry: SourceRegistryEntry, countries: list[str]) -> list[str]:
    return [c for c in countries if _country_supported(entry, c)]


def _role_supported(entry: SourceRegistryEntry, role_families: list[str]) -> bool:
    coverage = set(entry.role_coverage)
    if _GENERAL_ROLE_COVERAGE in coverage:
        return True
    if not role_families:
        return True
    return bool(coverage & set(role_families))


def _resolve_selected_countries(
    search_profile: SearchProfile, limits: ExecutionLimits
) -> tuple[list[str], list[str]]:
    excluded = set(search_profile.excluded_countries)
    requested: list[str] = []
    for country in search_profile.included_countries:
        if country in excluded or country in requested:
            continue
        requested.append(country)

    for country in requested:
        try:
            resolve_regions(country)
        except UnknownCountryError as exc:
            raise ConfigError(
                f"Search profile references an unknown country code: {exc.country}",
                field="included_countries",
            ) from exc

    if len(requested) <= limits.max_countries_per_run:
        return requested, []
    return requested[: limits.max_countries_per_run], requested[limits.max_countries_per_run :]


def _region_coverage_map(countries: list[str]) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for country in countries:
        for region in resolve_regions(country):
            mapping.setdefault(region, []).append(country)
    for region in mapping:
        mapping[region] = sorted(set(mapping[region]))
    return dict(sorted(mapping.items()))


def _coverage_raw(source_coverage: list[str], requested: list[str], neutral_prior: float) -> float:
    """Milestone 1.1 (architecture.md section 15.7; decisions.md D-024):
    empty source_coverage = unrestricted (relevant regardless); empty
    requested = no profile data to evaluate against, so neutral_prior;
    otherwise the fraction of requested tags the source's coverage covers."""
    if not source_coverage:
        return 1.0
    if not requested:
        return neutral_prior
    overlap = set(source_coverage) & set(requested)
    return len(overlap) / len(requested)


def _factor_raw_values(
    entry: SourceRegistryEntry,
    supported: list[str],
    selected_countries: list[str],
    role_families: list[str],
    candidate_profile: CandidateProfile,
    search_profile: SearchProfile,
    weights: SourceScoringWeights,
) -> dict[str, float]:
    country_raw = (
        len(supported) / len(selected_countries) if selected_countries else weights.neutral_prior
    )

    role_cov = set(entry.role_coverage)
    if _GENERAL_ROLE_COVERAGE in role_cov:
        role_raw = 1.0
    elif role_families:
        role_raw = len(role_cov & set(role_families)) / len(role_families)
    else:
        role_raw = weights.neutral_prior

    # Milestone 1.1: industry+sector relevance are blended into the single
    # sector_relevance factor rather than adding a new weighted dimension
    # (decisions.md D-024) — both fall back to neutral_prior individually
    # when their own profile data is absent.
    requested_sectors = sorted({*search_profile.included_sectors, *candidate_profile.sectors})
    requested_industries = sorted(
        {*search_profile.included_industries, *candidate_profile.industries}
    )
    sector_raw = _coverage_raw(entry.sector_coverage, requested_sectors, weights.neutral_prior)
    industry_raw = _coverage_raw(
        entry.industry_coverage, requested_industries, weights.neutral_prior
    )
    blended_sector_raw = (sector_raw + industry_raw) / 2

    requested_seniority = list(search_profile.seniority_levels)
    candidate_seniority = candidate_profile.effective_seniority_text()
    if candidate_seniority:
        requested_seniority.append(candidate_seniority)
    seniority_raw = _coverage_raw(
        entry.seniority_coverage, requested_seniority, weights.neutral_prior
    )

    if entry.historical_match_count is None:
        historical_raw = weights.neutral_prior
    else:
        historical_raw = min(1.0, entry.historical_match_count / 100.0)

    expected_value_map = {"high": 1.0, "medium": 0.5, "low": 0.0}
    visa_raw = expected_value_map[entry.expected_value.value]

    reliability_raw = (
        entry.reliability_score if entry.reliability_score is not None else weights.neutral_prior
    )
    dup_raw = (
        1.0 - entry.duplicate_rate if entry.duplicate_rate is not None else weights.neutral_prior
    )

    technical_map = {"high": 1.0, "medium": 0.6, "low": 0.2, "unknown": weights.neutral_prior}
    tech_raw = technical_map[entry.technical_feasibility.value]

    return {
        "country_region_relevance": country_raw,
        "role_family_relevance": role_raw,
        "sector_relevance": blended_sector_raw,
        "seniority_relevance": seniority_raw,
        "historical_matching_jobs": historical_raw,
        "freshness": weights.neutral_prior,
        "visa_international_usefulness": visa_raw,
        "source_reliability": reliability_raw,
        "duplicate_rate_inverted": dup_raw,
        "technical_quality": tech_raw,
    }


def _score_source(
    entry: SourceRegistryEntry,
    supported: list[str],
    selected_countries: list[str],
    role_families: list[str],
    candidate_profile: CandidateProfile,
    search_profile: SearchProfile,
    weights: SourceScoringWeights,
) -> tuple[float, dict[str, float]]:
    raws = _factor_raw_values(
        entry,
        supported,
        selected_countries,
        role_families,
        candidate_profile,
        search_profile,
        weights,
    )
    weight_map = weights.component_weights()
    breakdown = {factor: raws[factor] * weight_map[factor] for factor in raws}
    return sum(breakdown.values()), breakdown


def _effective_config_status(entry: SourceRegistryEntry, env: EnvConfig | None) -> ConfigStatus:
    """Runtime-derived config status, distinct from `entry.config_status`
    (the registry's static, user-maintained metadata — architecture.md
    section 4 "config vs. database boundary", decisions.md D-009 keeps that
    field YAML-first). `env is None` means the caller has no runtime
    credential information available (e.g. an env-agnostic caller/test) —
    fall back to the declared status unchanged rather than guessing.

    Milestone 1 has exactly one adapter (`adzuna_api`, decisions.md D-002),
    so it's the only source with a known credential check; every other
    source_id's effective status is its declared status until it gets its
    own adapter and a matching credential rule here."""
    if env is None:
        return entry.config_status
    if entry.source_id == "adzuna_api":
        if env.adzuna_app_id and env.adzuna_app_key:
            return ConfigStatus.CONFIGURED
        return ConfigStatus.NEEDS_CREDENTIALS
    return entry.config_status


def _is_generic_aggregator(entry: SourceRegistryEntry) -> bool:
    return entry.source_type == SourceType.AGGREGATOR_API and set(entry.role_coverage) == {
        _GENERAL_ROLE_COVERAGE
    }


def _apply_diversity_rule(
    candidates: list[SourceRegistryEntry],
    supported_map: dict[str, list[str]],
    scores: dict[str, float],
) -> tuple[list[SourceRegistryEntry], list[ExcludedSource]]:
    """architecture.md section 6, step 4: two or more generic aggregators
    covering the same underlying country with no differentiating role/company
    coverage are redundant; keep the highest-scoring one. See decisions.md
    for why the duplicate_rate-threshold branch isn't separately implemented
    (no pairwise field exists in the registry schema to compare against)."""

    generic = [c for c in candidates if _is_generic_aggregator(c)]
    parent: dict[str, str] = {c.source_id: c.source_id for c in generic}

    def find(source_id: str) -> str:
        while parent[source_id] != source_id:
            source_id = parent[source_id]
        return source_id

    def union(a: str, b: str) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_a] = root_b

    for i, entry_a in enumerate(generic):
        for entry_b in generic[i + 1 :]:
            overlap = set(supported_map[entry_a.source_id]) & set(supported_map[entry_b.source_id])
            if overlap:
                union(entry_a.source_id, entry_b.source_id)

    clusters: dict[str, list[SourceRegistryEntry]] = {}
    for entry in generic:
        clusters.setdefault(find(entry.source_id), []).append(entry)

    redundant_ids: set[str] = set()
    excluded: list[ExcludedSource] = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        ranked = sorted(
            members,
            key=lambda e: (-scores[e.source_id], -e.priority, e.source_id),
        )
        keeper = ranked[0]
        for loser in ranked[1:]:
            redundant_ids.add(loser.source_id)
            excluded.append(
                ExcludedSource(
                    source_id=loser.source_id,
                    reasons_excluded=[f"redundant_with:{keeper.source_id}"],
                )
            )

    survivors = [c for c in candidates if c.source_id not in redundant_ids]
    return survivors, excluded


def _render_query_expression(query: PlannedQuery) -> str:
    """Human-readable rendering of one `PlannedQuery`, for the legacy
    `SelectedSource.search_queries: list[str]` field (Milestone 2
    Deliverable 5 step 4 — reconciling the M1/1.1 representation now that
    `planned_queries` is what execution actually consumes). `exact_phrase`
    queries only ever carry one keyword (the phrase itself); `any_of_words`
    queries may carry several, rendered with an explicit `OR` so the display
    doesn't imply an exact-phrase match it isn't."""
    if query.mode == "exact_phrase":
        return " ".join(query.keywords)
    return " OR ".join(query.keywords)


def build_plan(
    candidate_profile: CandidateProfile,
    search_profile: SearchProfile,
    registry: list[SourceRegistryEntry],
    execution_limits: ExecutionLimits,
    source_scoring_weights: SourceScoringWeights,
    *,
    plan_id: str | None = None,
    generated_at: datetime | None = None,
    env: EnvConfig | None = None,
) -> SearchExecutionPlan:
    selected_countries, skipped_countries = _resolve_selected_countries(
        search_profile, execution_limits
    )

    diversity_notes: list[str] = []
    for country in skipped_countries:
        diversity_notes.append(
            f"country {country} skipped: execution_limit max_countries_per_run="
            f"{execution_limits.max_countries_per_run} reached (profile priority order preserved)"
        )

    role_families = search_profile.role_families or candidate_profile.role_families

    candidates: list[SourceRegistryEntry] = []
    excluded_sources: list[ExcludedSource] = []
    for entry in registry:
        supported = _supported_countries(entry, selected_countries)
        geo_ok = bool(supported)
        role_ok = _role_supported(entry, role_families)
        if geo_ok and role_ok:
            candidates.append(entry)
            continue
        reasons = []
        if not geo_ok:
            reasons.append("no_geographic_coverage")
        if not role_ok:
            reasons.append("no_role_coverage")
        excluded_sources.append(ExcludedSource(source_id=entry.source_id, reasons_excluded=reasons))

    supported_map = {c.source_id: _supported_countries(c, selected_countries) for c in candidates}
    score_map: dict[str, float] = {}
    breakdown_map: dict[str, dict[str, float]] = {}
    for entry in candidates:
        score, breakdown = _score_source(
            entry,
            supported_map[entry.source_id],
            selected_countries,
            role_families,
            candidate_profile,
            search_profile,
            source_scoring_weights,
        )
        score_map[entry.source_id] = score
        breakdown_map[entry.source_id] = breakdown

    survivors, redundancy_exclusions = _apply_diversity_rule(candidates, supported_map, score_map)
    excluded_sources.extend(redundancy_exclusions)

    selected_sources: list[SelectedSource] = []
    for entry in survivors:
        supported = supported_map[entry.source_id]
        unsupported = [
            CountryExclusion(country=c, reason="not_in_geographic_coverage")
            for c in selected_countries
            if c not in supported
        ]
        decision = authorize(entry, entry.access_mode)

        # Milestone 2 Deliverable 5 step 3: SearchProfile-driven PlannedQuery
        # generation.
        query_plan = build_planned_queries(
            capabilities=entry.capabilities,
            candidate_profile=candidate_profile,
            search_profile=search_profile,
            execution_limits=execution_limits,
        )
        estimated_request_count = (
            len(supported)
            * len(query_plan.planned_queries)
            * execution_limits.max_pages_per_source_country
        )

        # Milestone 2 Deliverable 5 step 4: `keywords`/`keyword_mode` here are
        # template values only — `pipeline.py::run_once` overrides both per
        # `PlannedQuery` before each `adapter.fetch()` call (keywords ->
        # query.keywords, keyword_mode -> query.mode), so this no longer
        # drives actual retrieval. `keyword_mode` is left at its default
        # ("any_of_words") deliberately. The other fields
        # (countries/employment_types/paging, already narrowed to this
        # source's supported countries) are the part of `SourceSearchParams`
        # execution actually reuses unchanged across every planned query for
        # this source.
        search_params = SourceSearchParams(
            countries=supported,
            keywords=list(candidate_profile.title_aliases),
            role_family_hints=role_families,
            employment_types=search_profile.employment_types,
            min_experience_years=search_profile.min_experience_years,
            max_experience_years=search_profile.max_experience_years,
            page_size=execution_limits.results_per_page,
            max_pages=execution_limits.max_pages_per_source_country,
        )

        reasons_selected = [
            f"score={score_map[entry.source_id]:.3f}",
            f"covers {len(supported)}/{len(selected_countries)} requested countries",
            decision.reason,
            *query_plan.notes,
        ]

        region_coverage = sorted({region for c in supported for region in resolve_regions(c)})

        selected_sources.append(
            SelectedSource(
                source_id=entry.source_id,
                score=score_map[entry.source_id],
                score_breakdown=breakdown_map[entry.source_id],
                reasons_selected=reasons_selected,
                access_mode=entry.access_mode,
                approval_status=entry.approval_status,
                search_params=search_params,
                # Milestone 2 Deliverable 5 step 4: `search_queries` is now
                # rendered from `planned_queries` — the same expressions
                # `job-scout plan` shows and `pipeline.py` actually executes —
                # rather than the M1/1.1 candidate-history word list, so this
                # field never claims a different search ran than the one that
                # did (MILESTONE_2.md "Domain-model changes").
                search_queries=[_render_query_expression(q) for q in query_plan.planned_queries],
                planned_queries=query_plan.planned_queries,
                estimated_request_count=estimated_request_count,
                polling_frequency_minutes=entry.polling_frequency_minutes,
                config_status=entry.config_status,
                effective_config_status=_effective_config_status(entry, env),
                required_setup_actions=[]
                if decision.allowed
                else list(entry.required_setup_actions),
                region_country_coverage=region_coverage,
                priority=entry.priority,
                executable=decision.allowed,
                supported_countries=supported,
                unsupported_countries=unsupported,
            )
        )

    selected_sources.sort(key=lambda s: (-s.score, -s.priority, s.source_id))
    excluded_sources.sort(key=lambda s: s.source_id)

    return SearchExecutionPlan(
        plan_id=plan_id or str(uuid.uuid4()),
        search_profile_ref=search_profile.profile_id,
        generated_at=generated_at or datetime.now(UTC),
        selected_sources=selected_sources,
        excluded_sources=excluded_sources,
        region_country_coverage=_region_coverage_map(selected_countries),
        diversity_notes=diversity_notes,
    )
