import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from job_scout.cli import app

runner = CliRunner()

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
  - source_id: greenhouse_public_feeds
    name: "Greenhouse public job boards"
    source_type: ats_feed
    geographic_coverage: [global]
    role_coverage: [general]
    access_mode: public_ats_feed
    approval_status: manual_review
    terms_compliance_status: unclear
    auth_required: false
    technical_feasibility: high
    expected_value: high
    priority: 60
    polling_frequency_minutes: 720
    config_status: configured
    required_setup_actions: []
    adapter_ref: greenhouse
    reliability_score: null
    historical_match_count: null
    duplicate_rate: null
    capabilities:
      keyword_search: false
      company_filter: true
"""


def _write_registry(tmp_path: Path, env_text: str = "") -> tuple[Path, Path]:
    registry = tmp_path / "source_registry.yaml"
    env = tmp_path / ".env"
    registry.write_text(SOURCE_REGISTRY_YAML, encoding="utf-8")
    # Always write an explicit (possibly empty) .env and pass --env-file so
    # these tests never accidentally pick up a real .env from the machine's
    # actual per-user data directory — test isolation, same pattern as
    # test_cli_plan.py.
    env.write_text(env_text, encoding="utf-8")
    return registry, env


def test_sources_command_lists_every_registry_entry_independent_of_a_profile(
    tmp_path: Path,
) -> None:
    registry, env = _write_registry(tmp_path)
    result = runner.invoke(
        app,
        ["sources", "--source-registry", str(registry), "--env-file", str(env)],
    )
    assert result.exit_code == 0, result.output
    assert "adzuna_api" in result.output
    assert "greenhouse_public_feeds" in result.output
    assert "Source registry (2 entries)" in result.output


def test_sources_command_human_output_shows_required_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An earlier test in the same pytest process may have loaded real-looking
    # Adzuna credentials into os.environ (config.py::load_env never clears a
    # value it once set — same pre-existing pollution test_cli_plan.py's
    # test_plan_shows_effective_needs_credentials_when_absent already guards
    # against with this exact pattern). Guarantee a clean slate so
    # effective_config_status is deterministic here.
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    registry, env = _write_registry(tmp_path)
    result = runner.invoke(
        app,
        ["sources", "--source-registry", str(registry), "--env-file", str(env)],
    )
    assert result.exit_code == 0, result.output
    assert "source_type=aggregator_api" in result.output
    assert "access_mode=public_api" in result.output
    assert "approval_status=approved" in result.output
    assert "config_status=needs_credentials" in result.output
    assert "effective_config_status=needs_credentials" in result.output
    assert "adapter_ref=adzuna" in result.output
    # decisions.md D-041: `sources` lists each entry's full capabilities block.
    assert "'company_filter': True" in result.output


def test_sources_command_shows_effective_config_status_from_live_credentials(
    tmp_path: Path,
) -> None:
    registry, env = _write_registry(
        tmp_path, env_text="ADZUNA_APP_ID=real-id-value\nADZUNA_APP_KEY=real-key-value\n"
    )
    result = runner.invoke(
        app,
        ["sources", "--source-registry", str(registry), "--env-file", str(env)],
    )
    assert result.exit_code == 0, result.output
    # declared registry status is still shown, unchanged...
    assert "config_status=needs_credentials" in result.output
    # ...alongside the live, credential-derived effective status
    assert "effective_config_status=configured" in result.output
    # never echo the credential values themselves
    assert "real-id-value" not in result.output
    assert "real-key-value" not in result.output


def test_sources_command_json_output(tmp_path: Path) -> None:
    registry, env = _write_registry(
        tmp_path, env_text="ADZUNA_APP_ID=id\nADZUNA_APP_KEY=key\n"
    )
    result = runner.invoke(
        app,
        ["sources", "--source-registry", str(registry), "--env-file", str(env), "--json"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert len(data) == 2
    adzuna = next(entry for entry in data if entry["source_id"] == "adzuna_api")
    assert adzuna["source_type"] == "aggregator_api"
    assert adzuna["access_mode"] == "public_api"
    assert adzuna["approval_status"] == "approved"
    assert adzuna["config_status"] == "needs_credentials"
    assert adzuna["effective_config_status"] == "configured"
    assert adzuna["adapter_ref"] == "adzuna"
    assert "capabilities" in adzuna
    assert adzuna["capabilities"]["keyword_search"] is True

    greenhouse = next(entry for entry in data if entry["source_id"] == "greenhouse_public_feeds")
    assert greenhouse["capabilities"]["company_filter"] is True
    assert greenhouse["capabilities"]["keyword_search"] is False


def test_sources_command_never_makes_http_call(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import httpx

    def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("sources command must never perform HTTP requests")

    monkeypatch.setattr(httpx.Client, "send", _fail)
    registry, env = _write_registry(
        tmp_path, env_text="ADZUNA_APP_ID=id\nADZUNA_APP_KEY=key\n"
    )
    result = runner.invoke(
        app,
        ["sources", "--source-registry", str(registry), "--env-file", str(env)],
    )
    assert result.exit_code == 0, result.output


def test_sources_command_missing_registry_exits_nonzero_no_traceback(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["sources", "--source-registry", str(tmp_path / "does_not_exist.yaml")],
    )
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "Configuration error" in result.output


def test_sources_command_empty_registry(tmp_path: Path) -> None:
    registry = tmp_path / "source_registry.yaml"
    registry.write_text("sources: []\n", encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    result = runner.invoke(
        app,
        ["sources", "--source-registry", str(registry), "--env-file", str(env)],
    )
    assert result.exit_code == 0, result.output
    assert "Source registry (0 entries)" in result.output
    assert "(none)" in result.output
