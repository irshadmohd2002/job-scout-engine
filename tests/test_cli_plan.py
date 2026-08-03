from pathlib import Path

from typer.testing import CliRunner

from job_scout.cli import app

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
    included_countries: [GB, DE, AE]
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
    geographic_coverage: [GB, DE]
    role_coverage: [general]
    access_mode: public_api
    approval_status: approved
    terms_compliance_status: reviewed_ok
    auth_required: true
    technical_feasibility: high
    expected_value: high
    priority: 100
    polling_frequency_minutes: 720
    config_status: needs_credentials
    required_setup_actions: ["Set ADZUNA_APP_ID/ADZUNA_APP_KEY"]
    adapter_ref: adzuna
    reliability_score: 0.9
    historical_match_count: null
    duplicate_rate: null
"""


def _write_config(tmp_path: Path) -> tuple[Path, Path, Path]:
    candidate = tmp_path / "candidate_profile.yaml"
    search = tmp_path / "search_profiles.yaml"
    registry = tmp_path / "source_registry.yaml"
    candidate.write_text(CANDIDATE_YAML, encoding="utf-8")
    search.write_text(SEARCH_PROFILES_YAML, encoding="utf-8")
    registry.write_text(SOURCE_REGISTRY_YAML, encoding="utf-8")
    return candidate, search, registry


def test_plan_command_human_output(tmp_path: Path) -> None:
    candidate, search, registry = _write_config(tmp_path)
    result = runner.invoke(
        app,
        [
            "plan",
            "--profile",
            "strategy-global",
            "--candidate-profile",
            str(candidate),
            "--search-profiles",
            str(search),
            "--source-registry",
            str(registry),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "adzuna_api" in result.output
    assert "Excluded sources" in result.output
    # AE requested but not covered by adzuna -> visible, not silently dropped
    assert "AE" in result.output


def test_plan_command_json_output(tmp_path: Path) -> None:
    candidate, search, registry = _write_config(tmp_path)
    result = runner.invoke(
        app,
        [
            "plan",
            "--profile",
            "strategy-global",
            "--candidate-profile",
            str(candidate),
            "--search-profiles",
            str(search),
            "--source-registry",
            str(registry),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    import json

    data = json.loads(result.output)
    assert data["search_profile_ref"] == "strategy-global"
    assert data["selected_sources"][0]["source_id"] == "adzuna_api"


def test_plan_command_missing_config_exits_nonzero_no_traceback(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "plan",
            "--profile",
            "strategy-global",
            "--candidate-profile",
            str(tmp_path / "does_not_exist.yaml"),
        ],
    )
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "Configuration error" in result.output


def test_plan_command_never_makes_http_call(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import httpx

    def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("plan command must never perform HTTP requests")

    monkeypatch.setattr(httpx.Client, "send", _fail)
    candidate, search, registry = _write_config(tmp_path)
    result = runner.invoke(
        app,
        [
            "plan",
            "--profile",
            "strategy-global",
            "--candidate-profile",
            str(candidate),
            "--search-profiles",
            str(search),
            "--source-registry",
            str(registry),
        ],
    )
    assert result.exit_code == 0, result.output
