"""Configuration loading and validation.

Env + YAML loading for every config surface: CandidateProfile, SearchProfile,
SourceRegistryEntry, ExecutionLimits, ScoringWeights, CompanyWatchlistEntry,
and the .env-backed environment configuration. All errors raise ConfigError,
which the CLI catches to exit cleanly with a concise, actionable message —
never a traceback, never a secret value (architecture.md section 12 module
layout; CLAUDE.md "Configuration requirements").

Milestone 1.1 (architecture.md section 15): default paths for every config
file now resolve via `paths.resolve_app_paths()` (platformdirs-based, not
CWD-relative — decisions.md D-018) instead of a hard-coded `config/*.yaml`
path. `execution_limits`/`scoring_weights`/`source_scoring_weights` keep
their Milestone 1 "fall back to a generic default when no local override
exists" behaviour, but the fallback now reads the packaged template
(`job_scout.resources`) instead of a CWD-relative `config/*.example.yaml`
file — decisions.md D-021.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ValidationError, field_validator

from job_scout.models import (
    CandidateProfile,
    CompanyWatchlistEntry,
    SearchProfile,
    SourceRegistryEntry,
)
from job_scout.paths import resolve_app_paths
from job_scout.resources import template_text

DEFAULT_ENV_PATH = Path(".env")

BOOTSTRAP_HINT = (
    "Run 'job-scout init' (optionally with --data-dir) to create starter config "
    "files, then edit them with your own details — see README.md 'Configuration "
    "bootstrap and privacy'."
)


class ConfigError(Exception):
    """Raised for any configuration problem. Carries enough detail (file,
    field) for the CLI to print a concise, actionable message without a
    traceback, and never includes secret values."""

    def __init__(
        self, message: str, *, file: Path | str | None = None, field: str | None = None
    ) -> None:
        self.file = str(file) if file is not None else None
        self.field = field
        parts = [message]
        if file is not None:
            parts.append(f"File: {file}")
        if field is not None:
            parts.append(f"Field: {field}")
        super().__init__(" | ".join(parts))

    @classmethod
    def from_validation_error(cls, file: Path | str, exc: ValidationError) -> ConfigError:
        lines = []
        for err in exc.errors():
            loc = ".".join(str(part) for part in err["loc"]) or "<root>"
            lines.append(f"{loc}: {err['msg']}")
        field = lines[0].split(":")[0] if lines else None
        detail = "; ".join(lines)
        return cls(f"Invalid configuration: {detail}", file=file, field=field)

    @classmethod
    def missing_file(cls, file: Path, *, hint: str = BOOTSTRAP_HINT) -> ConfigError:
        return cls(f"Configuration file not found. {hint}", file=file)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError.missing_file(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Could not read configuration file: {exc}", file=path) from exc
    return _parse_yaml_mapping(raw, path)


def _parse_yaml_mapping(raw: str, file: Path | str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Malformed YAML: {exc}", file=file) from exc
    if data is None:
        raise ConfigError("Configuration file is empty.", file=file)
    if not isinstance(data, dict):
        raise ConfigError(
            "Configuration file must contain a YAML mapping at the top level.", file=file
        )
    return data


def _load_with_template_fallback(
    explicit_path: Path | None, default_path: Path, template_name: str
) -> tuple[dict[str, Any], Path | str]:
    """Resolve a config file that has a generic packaged-template fallback
    (execution_limits/scoring_weights/source_scoring_weights — decisions.md
    D-013/D-014/D-021): explicit path > AppPaths-resolved default path >
    the packaged template, read directly from package data (no filesystem
    path required, so this works identically for an installed package with
    no local override at all).

    An explicitly-provided path must exist; it is never silently substituted.
    """
    if explicit_path is not None:
        if not explicit_path.exists():
            raise ConfigError.missing_file(
                explicit_path, hint="No fallback applies to an explicitly given path."
            )
        return _read_yaml(explicit_path), explicit_path
    if default_path.exists():
        return _read_yaml(default_path), default_path
    label = f"<packaged template: {template_name}>"
    return _parse_yaml_mapping(template_text(template_name), label), label


# --- CandidateProfile / SearchProfile / SourceRegistry ----------------------
# These carry (or, for source registry, gate access to) real candidate data
# and have no automatic fallback to a generic template — the user must
# explicitly bootstrap them via `job-scout init` (README.md "Configuration
# bootstrap").


def load_candidate_profile(
    path: Path | None = None, *, data_dir: Path | None = None
) -> CandidateProfile:
    resolved = path or resolve_app_paths(data_dir_override=data_dir).candidate_profile_path
    data = _read_yaml(resolved)
    try:
        return CandidateProfile.model_validate(data)
    except ValidationError as exc:
        raise ConfigError.from_validation_error(resolved, exc) from exc


class _SearchProfilesFile(BaseModel):
    profiles: list[SearchProfile]


def load_search_profiles(
    path: Path | None = None, *, data_dir: Path | None = None
) -> dict[str, SearchProfile]:
    resolved = path or resolve_app_paths(data_dir_override=data_dir).search_profiles_path
    data = _read_yaml(resolved)
    try:
        parsed = _SearchProfilesFile.model_validate(data)
    except ValidationError as exc:
        raise ConfigError.from_validation_error(resolved, exc) from exc
    by_id: dict[str, SearchProfile] = {}
    for profile in parsed.profiles:
        if profile.profile_id in by_id:
            raise ConfigError(
                f"Duplicate profile_id '{profile.profile_id}'.",
                file=resolved,
                field="profiles[].profile_id",
            )
        by_id[profile.profile_id] = profile
    return by_id


def get_search_profile(
    profiles: dict[str, SearchProfile], profile_id: str, *, file: Path | None = None
) -> SearchProfile:
    try:
        return profiles[profile_id]
    except KeyError as exc:
        available = ", ".join(sorted(profiles)) or "<none defined>"
        raise ConfigError(
            f"Unknown search profile '{profile_id}'. Available profiles: {available}.",
            file=file,
            field="profile_id",
        ) from exc


class _SourceRegistryFile(BaseModel):
    sources: list[SourceRegistryEntry]


def load_source_registry(
    path: Path | None = None, *, data_dir: Path | None = None
) -> list[SourceRegistryEntry]:
    resolved = path or resolve_app_paths(data_dir_override=data_dir).source_registry_path
    data = _read_yaml(resolved)
    try:
        parsed = _SourceRegistryFile.model_validate(data)
    except ValidationError as exc:
        raise ConfigError.from_validation_error(resolved, exc) from exc
    seen: set[str] = set()
    for entry in parsed.sources:
        if entry.source_id in seen:
            raise ConfigError(
                f"Duplicate source_id '{entry.source_id}'.",
                file=resolved,
                field="sources[].source_id",
            )
        seen.add(entry.source_id)
    return parsed.sources


# --- CompanyWatchlistEntry ---------------------------------------------------
# Milestone 2 Deliverable 5 step 6 (MILESTONE_2.md "config.py"): user-specific
# like candidate_profile/search_profiles/source_registry above — no
# packaged-template fallback (company_watchlist.yaml is hand-edited,
# per-user data, not a generic default like execution_limits.yaml). Config
# surface only: nothing here reads a network resource, constructs an
# adapter, or feeds the query planner (MILESTONE_2.md Deliverable 5 steps
# 7/8 own that).


class _CompanyWatchlistFile(BaseModel):
    companies: list[CompanyWatchlistEntry] = []


def load_company_watchlist(
    path: Path | None = None, *, data_dir: Path | None = None
) -> list[CompanyWatchlistEntry]:
    resolved = path or resolve_app_paths(data_dir_override=data_dir).company_watchlist_path
    data = _read_yaml(resolved)
    try:
        parsed = _CompanyWatchlistFile.model_validate(data)
    except ValidationError as exc:
        raise ConfigError.from_validation_error(resolved, exc) from exc
    seen: set[tuple[str, str]] = set()
    for entry in parsed.companies:
        key = (entry.source_id, entry.external_company_key)
        if key in seen:
            raise ConfigError(
                f"Duplicate watchlist entry for source_id '{entry.source_id}' "
                f"external_company_key '{entry.external_company_key}'.",
                file=resolved,
                field="companies[].external_company_key",
            )
        seen.add(key)
    return parsed.companies


# --- SponsorRegisterConfig ---------------------------------------------------
# Milestone 2 Deliverable 5 step 10 (MILESTONE_2.md "config.py"): metadata
# only — which registers are enabled — never the imported register data
# itself (that lives in the `sponsor_registry_entries` SQLite table, written
# only by `job-scout sponsors import`). Same "no packaged-template fallback,
# user-specific" treatment as CompanyWatchlistEntry above (decisions.md
# D-050): the engine never writes this file back (D-009's YAML-first
# principle), so there is no "last_imported_at"/row-count field here — that
# state genuinely lives only in the database.


class SponsorRegisterConfig(BaseModel):
    country: str
    register_name: str
    enabled: bool = True


class _SponsorRegistriesFile(BaseModel):
    registers: list[SponsorRegisterConfig] = []


def load_sponsor_registries_config(
    path: Path | None = None, *, data_dir: Path | None = None
) -> list[SponsorRegisterConfig]:
    resolved = path or resolve_app_paths(data_dir_override=data_dir).sponsor_registries_path
    data = _read_yaml(resolved)
    try:
        parsed = _SponsorRegistriesFile.model_validate(data)
    except ValidationError as exc:
        raise ConfigError.from_validation_error(resolved, exc) from exc
    seen: set[tuple[str, str]] = set()
    for register in parsed.registers:
        key = (register.country, register.register_name)
        if key in seen:
            raise ConfigError(
                f"Duplicate sponsor register entry for country '{register.country}' "
                f"register_name '{register.register_name}'.",
                file=resolved,
                field="registers[].register_name",
            )
        seen.add(key)
    return parsed.registers


# --- ExecutionLimits ----------------------------------------------------------


class ExecutionLimits(BaseModel):
    max_countries_per_run: int
    max_pages_per_source_country: int
    results_per_page: int
    request_timeout_seconds: float
    max_retries: int
    max_jobs_processed_per_run: int | None = None
    # Milestone 2 Deliverable 5 step 2 (domain model only): bounds how many
    # PlannedQuery objects the future query planner (step 3) may generate
    # per (source, country) pair. Defaulted so every existing
    # execution_limits.yaml without this key keeps loading unchanged
    # (MILESTONE_2.md "Configuration changes"). Not enforced by any
    # query-generation logic yet — no such logic exists in this step.
    max_queries_per_source_country: int = 3

    @field_validator(
        "max_countries_per_run",
        "max_pages_per_source_country",
        "results_per_page",
        "max_retries",
        "max_queries_per_source_country",
    )
    @classmethod
    def _must_be_positive_int(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("must be a positive integer")
        return value

    @field_validator("request_timeout_seconds")
    @classmethod
    def _must_be_positive_float(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("must be a positive number")
        return value

    @field_validator("max_jobs_processed_per_run")
    @classmethod
    def _must_be_positive_if_set(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("must be a positive integer when set")
        return value


def load_execution_limits(
    path: Path | None = None, *, data_dir: Path | None = None
) -> ExecutionLimits:
    default_path = resolve_app_paths(data_dir_override=data_dir).execution_limits_path
    data, label = _load_with_template_fallback(
        path, default_path, "execution_limits.example.yaml"
    )
    try:
        return ExecutionLimits.model_validate(data)
    except ValidationError as exc:
        raise ConfigError.from_validation_error(label, exc) from exc


# --- ScoringWeights -------------------------------------------------------
# Added at implementation time per architecture.md section 10 ("Weights are
# config, not code ... config/scoring_weights.yaml — to be added at
# implementation time"). See decisions.md for the ADR.


class ScoringWeights(BaseModel):
    title_role_family: float
    responsibilities: float
    required_skills: float
    transferable_skills: float
    seniority_experience: float
    sector_relevance: float
    education: float
    visa_relocation: float
    prefilter_threshold: float

    @field_validator("prefilter_threshold")
    @classmethod
    def _prefilter_threshold_in_range(cls, value: float) -> float:
        if not (0.0 <= value <= 1.0):
            raise ValueError("prefilter_threshold must be between 0 and 1")
        return value

    @field_validator(
        "title_role_family",
        "responsibilities",
        "required_skills",
        "transferable_skills",
        "seniority_experience",
        "sector_relevance",
        "education",
        "visa_relocation",
    )
    @classmethod
    def _weight_in_range(cls, value: float) -> float:
        if not (0.0 <= value <= 1.0):
            raise ValueError("component weights must be between 0 and 1")
        return value

    def component_weights(self) -> dict[str, float]:
        return {
            "title_role_family": self.title_role_family,
            "responsibilities": self.responsibilities,
            "required_skills": self.required_skills,
            "transferable_skills": self.transferable_skills,
            "seniority_experience": self.seniority_experience,
            "sector_relevance": self.sector_relevance,
            "education": self.education,
            "visa_relocation": self.visa_relocation,
        }


def load_scoring_weights(
    path: Path | None = None, *, data_dir: Path | None = None
) -> ScoringWeights:
    default_path = resolve_app_paths(data_dir_override=data_dir).scoring_weights_path
    data, label = _load_with_template_fallback(path, default_path, "scoring_weights.example.yaml")
    try:
        weights = ScoringWeights.model_validate(data)
    except ValidationError as exc:
        raise ConfigError.from_validation_error(label, exc) from exc
    total = sum(weights.component_weights().values())
    if abs(total - 1.0) > 1e-6:
        raise ConfigError(
            f"Score component weights must sum to 1.0, got {total:.4f}.",
            file=label,
            field="(title_role_family + responsibilities + ... + visa_relocation)",
        )
    return weights


# --- SourceScoringWeights ---------------------------------------------------
# Section 6's source-selection scoring table, externalised per its own text:
# "All weights and the neutral-prior value live in config (not hard-coded) so
# they can be tuned without a code change once real SourceRun/MatchResult
# history accumulates." See decisions.md D-013 (same rationale/pattern as
# scoring_weights.yaml, applied here to the planner's source-selection score
# instead of Stage 5's job-match score — two distinct scoring systems that
# happen to share the word "score").


class SourceScoringWeights(BaseModel):
    country_region_relevance: float
    role_family_relevance: float
    sector_relevance: float
    seniority_relevance: float
    historical_matching_jobs: float
    freshness: float
    visa_international_usefulness: float
    source_reliability: float
    duplicate_rate_inverted: float
    technical_quality: float
    neutral_prior: float
    diversity_duplicate_rate_threshold: float

    @field_validator(
        "country_region_relevance",
        "role_family_relevance",
        "sector_relevance",
        "seniority_relevance",
        "historical_matching_jobs",
        "freshness",
        "visa_international_usefulness",
        "source_reliability",
        "duplicate_rate_inverted",
        "technical_quality",
    )
    @classmethod
    def _weight_in_range(cls, value: float) -> float:
        if not (0.0 <= value <= 1.0):
            raise ValueError("component weights must be between 0 and 1")
        return value

    @field_validator("neutral_prior", "diversity_duplicate_rate_threshold")
    @classmethod
    def _fraction_in_range(cls, value: float) -> float:
        if not (0.0 <= value <= 1.0):
            raise ValueError("must be between 0 and 1")
        return value

    def component_weights(self) -> dict[str, float]:
        return {
            "country_region_relevance": self.country_region_relevance,
            "role_family_relevance": self.role_family_relevance,
            "sector_relevance": self.sector_relevance,
            "seniority_relevance": self.seniority_relevance,
            "historical_matching_jobs": self.historical_matching_jobs,
            "freshness": self.freshness,
            "visa_international_usefulness": self.visa_international_usefulness,
            "source_reliability": self.source_reliability,
            "duplicate_rate_inverted": self.duplicate_rate_inverted,
            "technical_quality": self.technical_quality,
        }


def load_source_scoring_weights(
    path: Path | None = None, *, data_dir: Path | None = None
) -> SourceScoringWeights:
    default_path = resolve_app_paths(data_dir_override=data_dir).source_scoring_weights_path
    data, label = _load_with_template_fallback(
        path, default_path, "source_scoring_weights.example.yaml"
    )
    try:
        weights = SourceScoringWeights.model_validate(data)
    except ValidationError as exc:
        raise ConfigError.from_validation_error(label, exc) from exc
    total = sum(weights.component_weights().values())
    if abs(total - 1.0) > 1e-6:
        raise ConfigError(
            f"Source-selection score component weights must sum to 1.0, got {total:.4f}.",
            file=label,
            field="(country_region_relevance + ... + technical_quality)",
        )
    return weights


# --- SemanticConfig ---------------------------------------------------------
# Milestone 3 D3, Phase 1 (decisions.md D-057 point 8): Stage 3's own
# configuration surface, kept separate from scoring_weights.yaml (which
# stays weights-only per D4's own constraint, MILESTONE_3.md). Same
# load-time packaged-template-fallback pattern as ExecutionLimits/
# ScoringWeights/SourceScoringWeights above (decisions.md D-013/D-014/
# D-021) — an explicit path must exist, a missing default_path falls back
# to the packaged `semantic_matching.example.yaml` template. Phase 1 only
# adds this config surface; nothing in the pipeline reads it yet (Stage 5
# rescue integration is a later Milestone 3 D3 phase).


class SemanticConfig(BaseModel):
    enabled: bool
    model_name: str = "BAAI/bge-small-en-v1.5"
    similarity_threshold: float
    rescue_cap: float

    @field_validator("model_name")
    @classmethod
    def _model_name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("similarity_threshold", "rescue_cap")
    @classmethod
    def _fraction_in_range(cls, value: float) -> float:
        if not (0.0 <= value <= 1.0):
            raise ValueError("must be between 0 and 1")
        return value


def load_semantic_config(
    path: Path | None = None, *, data_dir: Path | None = None
) -> SemanticConfig:
    default_path = resolve_app_paths(data_dir_override=data_dir).semantic_matching_path
    data, label = _load_with_template_fallback(
        path, default_path, "semantic_matching.example.yaml"
    )
    try:
        return SemanticConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError.from_validation_error(label, exc) from exc


# --- Environment (.env) -----------------------------------------------------


class EnvConfig(BaseModel):
    adzuna_app_id: str | None = None
    adzuna_app_key: str | None = None
    reed_api_key: str | None = None
    anthropic_api_key: str | None = None
    anthropic_model: str | None = None
    db_path: str = "./data/job_scout.sqlite3"
    log_level: str = "INFO"


def load_env(path: Path | None = None, *, data_dir: Path | None = None) -> EnvConfig:
    """Load .env (if present) into the process environment without
    overriding already-set real environment variables, then build a typed
    EnvConfig. Never logs or echoes values.

    Resolution order for the .env file itself (architecture.md section 15.4):
    an explicit `path` argument > `AppPaths.environment_file_path` (the
    resolved data directory's `.env`) if it exists > a `.env` in the current
    working directory if it exists (retained only as a documented
    development convenience — never the primary default, decisions.md
    D-018). `db_path`'s own default (when JOB_SCOUT_DB_PATH is unset) is
    `AppPaths.database_path`, not a CWD-relative path.
    """
    app_paths = resolve_app_paths(data_dir_override=data_dir)

    if path is not None:
        resolved = path
    elif app_paths.environment_file_path is not None and app_paths.environment_file_path.exists():
        resolved = app_paths.environment_file_path
    elif DEFAULT_ENV_PATH.exists():
        resolved = DEFAULT_ENV_PATH
    else:
        resolved = DEFAULT_ENV_PATH  # doesn't exist; the load step below is a no-op

    if resolved.exists():
        file_values: dict[str, str | None] = dotenv_values(resolved)
        for key, value in file_values.items():
            if value is not None and key not in os.environ:
                os.environ[key] = value
    return EnvConfig(
        adzuna_app_id=os.environ.get("ADZUNA_APP_ID") or None,
        adzuna_app_key=os.environ.get("ADZUNA_APP_KEY") or None,
        reed_api_key=os.environ.get("REED_API_KEY") or None,
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        anthropic_model=os.environ.get("ANTHROPIC_MODEL") or None,
        db_path=os.environ.get("JOB_SCOUT_DB_PATH") or str(app_paths.database_path),
        log_level=os.environ.get("LOG_LEVEL") or "INFO",
    )
