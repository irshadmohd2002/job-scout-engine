import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

import job_scout.pipeline as pipeline_module
from job_scout.cli import app
from job_scout.models import AccessMode, RawJobRecord, SourceSearchParams

runner = CliRunner()

CANDIDATE_YAML = """
candidate_id: default-candidate
years_experience: 7.0
current_location: {country: IN, region: null, city: null}
nationality: IN
seniority_level: manager
education: []
employment_history_summary: []
role_families: [strategy_and_planning]
title_aliases: ["Strategy Manager"]
primary_skills: [strategy_development]
secondary_skills: []
target_countries: []
target_regions: []
requires_work_authorisation_support: true
open_to_relocation: true
excluded_industries: []
excluded_role_families: []
notification_thresholds: {priority_score: 85, digest_score: 70}
"""

SEARCH_PROFILES_YAML = """
profiles:
  - profile_id: strategy-global
    candidate_profile_ref: default-candidate
    included_countries: [GB]
    excluded_countries: []
    included_cities: []
    excluded_cities: []
    role_families: []
    employment_types: [full_time]
    min_experience_years: 4
    max_experience_years: 12
    required_languages: []
    mandatory_qualifications: []
    security_clearance_allowed: false
    reject_on_explicit_no_sponsorship: true
    notification_thresholds: {priority_score: 85, digest_score: 70}
    polling_frequency_minutes: 720
"""

SOURCE_REGISTRY_YAML = """
sources:
  - source_id: adzuna_api
    name: "Adzuna Job Search API"
    source_type: aggregator_api
    geographic_coverage: [GB]
    role_coverage: [general]
    access_mode: public_api
    approval_status: approved
    terms_compliance_status: reviewed_ok
    auth_required: true
    technical_feasibility: high
    expected_value: high
    priority: 100
    polling_frequency_minutes: 720
    config_status: configured
    required_setup_actions: []
    adapter_ref: adzuna
    reliability_score: 0.9
    historical_match_count: null
    duplicate_rate: null
"""

UNCONFIGURED_REGISTRY_YAML = """
sources:
  - source_id: adzuna_api
    name: "Adzuna Job Search API"
    source_type: aggregator_api
    geographic_coverage: [GB]
    role_coverage: [general]
    access_mode: public_api
    approval_status: manual_review
    terms_compliance_status: unclear
    auth_required: true
    technical_feasibility: high
    expected_value: high
    priority: 100
    polling_frequency_minutes: 720
    config_status: not_configured
    required_setup_actions: ["Review terms"]
    adapter_ref: null
    reliability_score: null
    historical_match_count: null
    duplicate_rate: null
"""


class _FakeAdzunaAdapter:
    source_id = "adzuna_api"
    access_mode = AccessMode.PUBLIC_API

    def __init__(
        self, *, app_id, app_key, request_timeout_seconds=15.0, max_retries=2, client=None
    ):  # type: ignore[no-untyped-def]
        self.app_id = app_id
        self.app_key = app_key

    def is_configured(self) -> bool:
        return True

    def fetch(self, params: SourceSearchParams) -> list[RawJobRecord]:
        return [
            RawJobRecord(
                source_id="adzuna_api",
                external_id="1",
                raw_url="https://example.com/jobs/1",
                raw_payload={
                    "id": "1",
                    "title": "Strategy Manager",
                    "company": {"display_name": "Example Corp"},
                    "location": {"display_name": "London"},
                    "redirect_url": "https://example.com/jobs/1",
                    "created": "2026-07-01T10:00:00Z",
                    "description": "Great strategy role, 4-8 years experience. MBA preferred.",
                    "salary_min": 60000,
                    "salary_max": 80000,
                    "contract_time": "full_time",
                    "_query_country": "GB",
                },
                fetched_at=datetime.now(UTC),
            )
        ]


def _write_config(tmp_path: Path, registry_yaml: str = SOURCE_REGISTRY_YAML) -> dict[str, Path]:
    candidate = tmp_path / "candidate_profile.yaml"
    search = tmp_path / "search_profiles.yaml"
    registry = tmp_path / "source_registry.yaml"
    candidate.write_text(CANDIDATE_YAML, encoding="utf-8")
    search.write_text(SEARCH_PROFILES_YAML, encoding="utf-8")
    registry.write_text(registry_yaml, encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("ADZUNA_APP_ID=id\nADZUNA_APP_KEY=key\n", encoding="utf-8")
    return {"candidate": candidate, "search": search, "registry": registry, "env": env}


def _base_args(paths: dict[str, Path], db_path: Path, **extra: str) -> list[str]:
    args = [
        "run-once",
        "--profile",
        "strategy-global",
        "--candidate-profile",
        str(paths["candidate"]),
        "--search-profiles",
        str(paths["search"]),
        "--source-registry",
        str(paths["registry"]),
        "--env-file",
        str(paths["env"]),
        "--db-path",
        str(db_path),
    ]
    for key, value in extra.items():
        args.extend([key, value])
    return args


def test_run_once_end_to_end_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline_module, "AdzunaAdapter", _FakeAdzunaAdapter)
    paths = _write_config(tmp_path)
    db_path = tmp_path / "job_scout.sqlite3"
    result = runner.invoke(app, [*_base_args(paths, db_path), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Selected sources" in result.output
    assert "Source runs" in result.output
    assert "adzuna_api" in result.output


def test_run_once_persists_to_sqlite_even_under_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline_module, "AdzunaAdapter", _FakeAdzunaAdapter)
    paths = _write_config(tmp_path)
    db_path = tmp_path / "job_scout.sqlite3"
    result = runner.invoke(app, [*_base_args(paths, db_path), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert db_path.exists()

    import sqlite3

    conn = sqlite3.connect(db_path)
    job_count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    run_count = conn.execute("SELECT COUNT(*) FROM source_runs").fetchone()[0]
    conn.close()
    assert job_count == 1
    assert run_count == 1


def test_run_once_json_output_is_valid_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline_module, "AdzunaAdapter", _FakeAdzunaAdapter)
    paths = _write_config(tmp_path)
    db_path = tmp_path / "job_scout.sqlite3"
    result = runner.invoke(app, [*_base_args(paths, db_path), "--dry-run", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["plan"]["search_profile_ref"] == "strategy-global"
    assert len(data["results"]) == 1


def test_run_once_no_executable_sources_exits_nonzero(tmp_path: Path) -> None:
    paths = _write_config(tmp_path, registry_yaml=UNCONFIGURED_REGISTRY_YAML)
    db_path = tmp_path / "job_scout.sqlite3"
    result = runner.invoke(app, [*_base_args(paths, db_path), "--dry-run"])
    assert result.exit_code == 1
    assert "No executable sources" in result.output


def test_run_once_missing_credentials_exits_nonzero_with_clear_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # load_env() deliberately never overrides an already-set real env var
    # with a .env file value, so an earlier test's ADZUNA_APP_ID/KEY could
    # otherwise leak into this one via process-global os.environ.
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    paths = _write_config(tmp_path)
    paths["env"].write_text("", encoding="utf-8")  # no ADZUNA_APP_ID/KEY
    db_path = tmp_path / "job_scout.sqlite3"
    result = runner.invoke(app, [*_base_args(paths, db_path), "--dry-run"])
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "failed" in result.output.lower() or "not configured" in result.output.lower()


def test_run_once_missing_config_exits_nonzero_no_traceback(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "run-once",
            "--profile",
            "strategy-global",
            "--candidate-profile",
            str(tmp_path / "does_not_exist.yaml"),
            "--dry-run",
        ],
    )
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "Configuration error" in result.output
