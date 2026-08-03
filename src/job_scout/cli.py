"""Typer CLI: `plan` and (later) `run-once`."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from job_scout.config import (
    ConfigError,
    ExecutionLimits,
    SourceScoringWeights,
    get_search_profile,
    load_candidate_profile,
    load_env,
    load_execution_limits,
    load_scoring_weights,
    load_search_profiles,
    load_source_scoring_weights,
)
from job_scout.models import (
    CandidateProfile,
    Job,
    MatchResult,
    NotificationTier,
    SearchExecutionPlan,
    SearchProfile,
    SourceRegistryEntry,
    SourceRunStatus,
)
from job_scout.pipeline import RunOnceResult, run_once
from job_scout.repository.sqlite_repo import SqliteJobRepository
from job_scout.source_intelligence.planner import build_plan
from job_scout.source_intelligence.registry import load_registry

app = typer.Typer(help="Job Scout Engine — deterministic job-monitoring and matching CLI.")


@app.callback()
def _main() -> None:
    """Job Scout Engine — deterministic job-monitoring and matching CLI.

    Present even with a single subcommand today so `plan`/`run-once` stay
    explicit named subcommands rather than Typer's single-command flattening.
    """


class _PlanningInputs:
    def __init__(
        self,
        candidate_profile: CandidateProfile,
        search_profile: SearchProfile,
        registry: list[SourceRegistryEntry],
        execution_limits: ExecutionLimits,
        source_scoring_weights: SourceScoringWeights,
    ) -> None:
        self.candidate_profile = candidate_profile
        self.search_profile = search_profile
        self.registry = registry
        self.execution_limits = execution_limits
        self.source_scoring_weights = source_scoring_weights


def _load_planning_inputs(
    *,
    profile_id: str,
    candidate_profile_path: Path | None,
    search_profiles_path: Path | None,
    source_registry_path: Path | None,
    execution_limits_path: Path | None,
    source_scoring_weights_path: Path | None,
) -> _PlanningInputs:
    candidate_profile = load_candidate_profile(candidate_profile_path)
    search_profiles = load_search_profiles(search_profiles_path)
    search_profile = get_search_profile(search_profiles, profile_id, file=search_profiles_path)
    registry = load_registry(source_registry_path)
    execution_limits = load_execution_limits(execution_limits_path)
    source_scoring_weights = load_source_scoring_weights(source_scoring_weights_path)
    return _PlanningInputs(
        candidate_profile, search_profile, registry, execution_limits, source_scoring_weights
    )


def _format_plan_human(plan: SearchExecutionPlan) -> str:
    lines: list[str] = []
    lines.append(f"Search Execution Plan for profile '{plan.search_profile_ref}'")
    lines.append(f"generated_at={plan.generated_at.isoformat()} plan_id={plan.plan_id}")
    lines.append("")
    lines.append("Region/country coverage:")
    if plan.region_country_coverage:
        for region, countries in plan.region_country_coverage.items():
            lines.append(f"  - {region}: {', '.join(countries)}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append(f"Selected sources ({len(plan.selected_sources)}):")
    if not plan.selected_sources:
        lines.append("  (none)")
    for source in plan.selected_sources:
        lines.append(
            f"  - {source.source_id}  executable={source.executable}  "
            f"score={source.score:.3f}  priority={source.priority}"
        )
        lines.append(
            f"      access_mode={source.access_mode}  approval_status={source.approval_status}"
        )
        lines.append(f"      config_status={source.config_status}")
        lines.append(f"      supported_countries={source.supported_countries}")
        if source.unsupported_countries:
            unsupported = ", ".join(
                f"{c.country} ({c.reason})" for c in source.unsupported_countries
            )
            lines.append(f"      unsupported_countries={unsupported}")
        if source.required_setup_actions:
            lines.append(f"      required_setup_actions={source.required_setup_actions}")
        lines.append(f"      reasons={source.reasons_selected}")
    lines.append("")
    lines.append(f"Excluded sources ({len(plan.excluded_sources)}):")
    if not plan.excluded_sources:
        lines.append("  (none)")
    for excluded_source in plan.excluded_sources:
        lines.append(
            f"  - {excluded_source.source_id}: {', '.join(excluded_source.reasons_excluded)}"
        )
    if plan.diversity_notes:
        lines.append("")
        lines.append("Notes:")
        for note in plan.diversity_notes:
            lines.append(f"  - {note}")
    return "\n".join(lines)


@app.command()
def plan(
    profile: str = typer.Option(..., "--profile", help="Search profile id to plan for."),
    candidate_profile: Path | None = typer.Option(
        None,
        "--candidate-profile",
        help="Path to candidate_profile.yaml (default: config/candidate_profile.yaml).",
    ),
    search_profiles: Path | None = typer.Option(
        None,
        "--search-profiles",
        help="Path to search_profiles.yaml (default: config/search_profiles.yaml).",
    ),
    source_registry: Path | None = typer.Option(
        None,
        "--source-registry",
        help="Path to source_registry.yaml (default: config/source_registry.yaml).",
    ),
    execution_limits: Path | None = typer.Option(
        None,
        "--execution-limits",
        help="Path to execution_limits.yaml (default: config/execution_limits.yaml, "
        "falling back to the tracked .example).",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print the full structured plan as JSON."
    ),
) -> None:
    """Print the SearchExecutionPlan for a search profile. Never calls a
    source adapter and never consumes API quota (MILESTONE_1.md)."""
    try:
        inputs = _load_planning_inputs(
            profile_id=profile,
            candidate_profile_path=candidate_profile,
            search_profiles_path=search_profiles,
            source_registry_path=source_registry,
            execution_limits_path=execution_limits,
            source_scoring_weights_path=None,
        )
        result = build_plan(
            inputs.candidate_profile,
            inputs.search_profile,
            inputs.registry,
            inputs.execution_limits,
            inputs.source_scoring_weights,
        )
    except ConfigError as exc:
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(code=1) from None

    if json_output:
        typer.echo(json.dumps(result.model_dump(mode="json"), indent=2))
    else:
        typer.echo(_format_plan_human(result))


def _format_score_components(match_result: MatchResult) -> list[str]:
    lines = []
    for component in match_result.score_components:
        evidence = f" evidence={component.evidence}" if component.evidence else ""
        lines.append(
            f"        {component.name}: raw={component.raw_value:.2f} "
            f"weight={component.weight:.2f} weighted={component.weighted_value:.2f}{evidence}"
        )
    return lines


def _format_results_human(results: list[tuple[Job, MatchResult]], *, verbose: bool) -> str:
    lines: list[str] = []
    by_tier: dict[NotificationTier, list[tuple[Job, MatchResult]]] = {
        tier: [] for tier in NotificationTier
    }
    for job, match_result in results:
        by_tier[match_result.notification_tier].append((job, match_result))

    for tier in (NotificationTier.PRIORITY, NotificationTier.DIGEST):
        tier_results = by_tier[tier]
        lines.append(f"{tier.value.upper()} ({len(tier_results)}):")
        for job, match_result in tier_results:
            lines.append(
                f"  - [{match_result.final_score:.1f}] {job.title} @ {job.company} "
                f"({job.location.country}) — {job.fingerprint.canonical_url}"
            )
            lines.extend(_format_score_components(match_result))
        if not tier_results:
            lines.append("  (none)")
        lines.append("")

    store_only = by_tier[NotificationTier.STORE_ONLY]
    rejected = by_tier[NotificationTier.REJECTED]
    lines.append(
        f"STORE_ONLY: {len(store_only)} job(s) (below notification threshold or pre-filter)"
    )
    if verbose:
        for job, match_result in store_only:
            score = (
                f"{match_result.final_score:.1f}"
                if match_result.final_score is not None
                else "unscored"
            )
            lines.append(f"  - [{score}] {job.title} @ {job.company}")
    lines.append(f"REJECTED: {len(rejected)} job(s) (failed Stage 1 hard filters)")
    if verbose:
        for job, match_result in rejected:
            reasons = "; ".join(r.rule for r in match_result.hard_filter_result.rejections)
            lines.append(f"  - {job.title} @ {job.company}: {reasons}")
    return "\n".join(lines)


def _all_source_runs_failed(result: RunOnceResult) -> bool:
    return bool(result.source_runs) and all(
        run.status == SourceRunStatus.FAILED for run in result.source_runs
    )


@app.command(name="run-once")
def run_once_command(
    profile: str = typer.Option(..., "--profile", help="Search profile id to run."),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Disable notification dispatch and other external-write actions. "
        "Fetching, normalisation, dedup, scoring, and local persistence still happen — "
        "see architecture.md section 11 'Dry-run semantics'.",
    ),
    candidate_profile: Path | None = typer.Option(None, "--candidate-profile"),
    search_profiles: Path | None = typer.Option(None, "--search-profiles"),
    source_registry: Path | None = typer.Option(None, "--source-registry"),
    execution_limits: Path | None = typer.Option(None, "--execution-limits"),
    scoring_weights_path: Path | None = typer.Option(None, "--scoring-weights"),
    source_scoring_weights_path: Path | None = typer.Option(None, "--source-scoring-weights"),
    env_path: Path | None = typer.Option(
        None, "--env-file", help="Path to .env (default: ./.env)."
    ),
    db_path: Path | None = typer.Option(
        None,
        "--db-path",
        help="Path to the SQLite database (default: JOB_SCOUT_DB_PATH from .env).",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        help="CLI-only convenience cap on jobs processed this run; only ever "
        "lowers, never raises, max_jobs_processed_per_run.",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", help="Also list store_only/rejected jobs individually."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print the full structured plan + results as JSON."
    ),
) -> None:
    """Fetch, normalise, deduplicate, filter, score, and locally persist jobs
    for a search profile (MILESTONE_1.md acceptance command)."""
    try:
        inputs = _load_planning_inputs(
            profile_id=profile,
            candidate_profile_path=candidate_profile,
            search_profiles_path=search_profiles,
            source_registry_path=source_registry,
            execution_limits_path=execution_limits,
            source_scoring_weights_path=source_scoring_weights_path,
        )
        scoring_weights = load_scoring_weights(scoring_weights_path)
        env = load_env(env_path)
    except ConfigError as exc:
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(code=1) from None

    plan_preview = build_plan(
        inputs.candidate_profile,
        inputs.search_profile,
        inputs.registry,
        inputs.execution_limits,
        inputs.source_scoring_weights,
    )
    if not json_output:
        typer.echo(_format_plan_human(plan_preview))
        typer.echo("")

    if not any(source.executable for source in plan_preview.selected_sources):
        message = (
            f"No executable sources in the plan for profile '{profile}'. "
            "See required_setup_actions above."
        )
        if json_output:
            typer.echo(
                json.dumps(
                    {"error": message, "plan": plan_preview.model_dump(mode="json")}, indent=2
                )
            )
        else:
            typer.echo(message, err=True)
        raise typer.Exit(code=1)

    resolved_db_path = db_path or env.db_path
    with SqliteJobRepository(resolved_db_path) as repository:
        result = run_once(
            candidate_profile=inputs.candidate_profile,
            search_profile=inputs.search_profile,
            registry=inputs.registry,
            execution_limits=inputs.execution_limits,
            scoring_weights=scoring_weights,
            source_scoring_weights=inputs.source_scoring_weights,
            repository=repository,
            env=env,
            dry_run=dry_run,
            limit=limit,
        )

    if json_output:
        payload = {
            "plan": result.plan.model_dump(mode="json"),
            "source_runs": [run.model_dump(mode="json") for run in result.source_runs],
            "results": [
                {
                    "job": job.model_dump(mode="json"),
                    "match_result": match_result.model_dump(mode="json"),
                }
                for job, match_result in result.results
            ],
        }
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo("Source runs:")
        for run in result.source_runs:
            typer.echo(
                f"  - {run.source_id}: status={run.status} fetched={run.jobs_fetched} "
                f"new={run.jobs_new} duplicate={run.jobs_duplicate}"
            )
            for error in run.errors:
                typer.echo(f"      error: {error}")
        typer.echo("")
        typer.echo(_format_results_human(result.results, verbose=verbose))

    if _all_source_runs_failed(result):
        typer.echo("All executable source runs failed — see errors above.", err=True)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
