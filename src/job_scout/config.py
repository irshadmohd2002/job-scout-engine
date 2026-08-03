"""Configuration loading and validation.

Env + YAML loading for every config surface: CandidateProfile, SearchProfile,
SourceRegistryEntry, ExecutionLimits, ScoringWeights, and the .env-backed
environment configuration. All errors raise ConfigError, which the CLI
catches to exit cleanly with a concise, actionable message — never a
traceback, never a secret value (architecture.md section 12 module layout;
CLAUDE.md "Configuration requirements").
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ValidationError, field_validator

from job_scout.models import CandidateProfile, SearchProfile, SourceRegistryEntry

DEFAULT_CANDIDATE_PROFILE_PATH = Path("config/candidate_profile.yaml")
DEFAULT_SEARCH_PROFILES_PATH = Path("config/search_profiles.yaml")
DEFAULT_SOURCE_REGISTRY_PATH = Path("config/source_registry.yaml")

DEFAULT_EXECUTION_LIMITS_PATH = Path("config/execution_limits.yaml")
EXAMPLE_EXECUTION_LIMITS_PATH = Path("config/execution_limits.example.yaml")

DEFAULT_SCORING_WEIGHTS_PATH = Path("config/scoring_weights.yaml")
EXAMPLE_SCORING_WEIGHTS_PATH = Path("config/scoring_weights.example.yaml")

DEFAULT_SOURCE_SCORING_WEIGHTS_PATH = Path("config/source_scoring_weights.yaml")
EXAMPLE_SOURCE_SCORING_WEIGHTS_PATH = Path("config/source_scoring_weights.example.yaml")

DEFAULT_ENV_PATH = Path(".env")

BOOTSTRAP_HINT = (
    "Copy the matching config/*.example.yaml to the real filename and edit it "
    "with your own details — see README.md 'Configuration bootstrap and privacy'."
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
    def from_validation_error(cls, file: Path, exc: ValidationError) -> ConfigError:
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
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Malformed YAML: {exc}", file=path) from exc
    if data is None:
        raise ConfigError("Configuration file is empty.", file=path)
    if not isinstance(data, dict):
        raise ConfigError(
            "Configuration file must contain a YAML mapping at the top level.", file=path
        )
    return data


def _resolve_with_fallback(
    explicit_path: Path | None, default_path: Path, example_path: Path
) -> Path:
    """Resolve a config path that has a tracked .example fallback (currently
    execution_limits and scoring_weights — see architecture.md section 11 for
    the documented execution_limits case; scoring_weights follows the same
    pattern, see decisions.md).

    An explicitly-provided path must exist; it is never silently substituted.
    """
    if explicit_path is not None:
        if not explicit_path.exists():
            raise ConfigError.missing_file(
                explicit_path, hint="No fallback applies to an explicitly given path."
            )
        return explicit_path
    if default_path.exists():
        return default_path
    if example_path.exists():
        return example_path
    raise ConfigError.missing_file(default_path)


# --- CandidateProfile / SearchProfile / SourceRegistry ----------------------
# These carry (or, for source registry, gate access to) real candidate data
# and have no automatic fallback to the placeholder .example file — the user
# must explicitly bootstrap them (README.md "Configuration bootstrap").


def load_candidate_profile(path: Path | None = None) -> CandidateProfile:
    resolved = path or DEFAULT_CANDIDATE_PROFILE_PATH
    data = _read_yaml(resolved)
    try:
        return CandidateProfile.model_validate(data)
    except ValidationError as exc:
        raise ConfigError.from_validation_error(resolved, exc) from exc


class _SearchProfilesFile(BaseModel):
    profiles: list[SearchProfile]


def load_search_profiles(path: Path | None = None) -> dict[str, SearchProfile]:
    resolved = path or DEFAULT_SEARCH_PROFILES_PATH
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


def load_source_registry(path: Path | None = None) -> list[SourceRegistryEntry]:
    resolved = path or DEFAULT_SOURCE_REGISTRY_PATH
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


# --- ExecutionLimits ----------------------------------------------------------


class ExecutionLimits(BaseModel):
    max_countries_per_run: int
    max_pages_per_source_country: int
    results_per_page: int
    request_timeout_seconds: float
    max_retries: int
    max_jobs_processed_per_run: int | None = None

    @field_validator(
        "max_countries_per_run",
        "max_pages_per_source_country",
        "results_per_page",
        "max_retries",
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


def load_execution_limits(path: Path | None = None) -> ExecutionLimits:
    resolved = _resolve_with_fallback(
        path, DEFAULT_EXECUTION_LIMITS_PATH, EXAMPLE_EXECUTION_LIMITS_PATH
    )
    data = _read_yaml(resolved)
    try:
        return ExecutionLimits.model_validate(data)
    except ValidationError as exc:
        raise ConfigError.from_validation_error(resolved, exc) from exc


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


def load_scoring_weights(path: Path | None = None) -> ScoringWeights:
    resolved = _resolve_with_fallback(
        path, DEFAULT_SCORING_WEIGHTS_PATH, EXAMPLE_SCORING_WEIGHTS_PATH
    )
    data = _read_yaml(resolved)
    try:
        weights = ScoringWeights.model_validate(data)
    except ValidationError as exc:
        raise ConfigError.from_validation_error(resolved, exc) from exc
    total = sum(weights.component_weights().values())
    if abs(total - 1.0) > 1e-6:
        raise ConfigError(
            f"Score component weights must sum to 1.0, got {total:.4f}.",
            file=resolved,
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


def load_source_scoring_weights(path: Path | None = None) -> SourceScoringWeights:
    resolved = _resolve_with_fallback(
        path, DEFAULT_SOURCE_SCORING_WEIGHTS_PATH, EXAMPLE_SOURCE_SCORING_WEIGHTS_PATH
    )
    data = _read_yaml(resolved)
    try:
        weights = SourceScoringWeights.model_validate(data)
    except ValidationError as exc:
        raise ConfigError.from_validation_error(resolved, exc) from exc
    total = sum(weights.component_weights().values())
    if abs(total - 1.0) > 1e-6:
        raise ConfigError(
            f"Source-selection score component weights must sum to 1.0, got {total:.4f}.",
            file=resolved,
            field="(country_region_relevance + ... + technical_quality)",
        )
    return weights


# --- Environment (.env) -----------------------------------------------------


class EnvConfig(BaseModel):
    adzuna_app_id: str | None = None
    adzuna_app_key: str | None = None
    anthropic_api_key: str | None = None
    anthropic_model: str | None = None
    db_path: str = "./data/job_scout.sqlite3"
    log_level: str = "INFO"


def load_env(path: Path | None = None) -> EnvConfig:
    """Load .env (if present) into the process environment without
    overriding already-set real environment variables, then build a typed
    EnvConfig. Never logs or echoes values."""
    resolved = path or DEFAULT_ENV_PATH
    file_values: dict[str, str | None] = {}
    if resolved.exists():
        file_values = dotenv_values(resolved)
        for key, value in file_values.items():
            if value is not None and key not in os.environ:
                os.environ[key] = value
    return EnvConfig(
        adzuna_app_id=os.environ.get("ADZUNA_APP_ID") or None,
        adzuna_app_key=os.environ.get("ADZUNA_APP_KEY") or None,
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        anthropic_model=os.environ.get("ANTHROPIC_MODEL") or None,
        db_path=os.environ.get("JOB_SCOUT_DB_PATH") or "./data/job_scout.sqlite3",
        log_level=os.environ.get("LOG_LEVEL") or "INFO",
    )
