from pathlib import Path

import pytest

from job_scout import config

VALID_CANDIDATE_YAML = """
candidate_id: default-candidate
years_experience: 7.0
current_location:
  country: IN
  region: null
  city: null
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
notification_thresholds:
  priority_score: 85
  digest_score: 70
"""

VALID_SEARCH_PROFILES_YAML = """
profiles:
  - profile_id: strategy-global
    candidate_profile_ref: default-candidate
    included_countries: [GB, DE]
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
    notification_thresholds:
      priority_score: 85
      digest_score: 70
    polling_frequency_minutes: 720
"""

VALID_SOURCE_REGISTRY_YAML = """
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
    required_setup_actions: []
    adapter_ref: adzuna
    reliability_score: 0.9
    historical_match_count: null
    duplicate_rate: null
"""


def test_load_candidate_profile(tmp_path: Path) -> None:
    p = tmp_path / "candidate_profile.yaml"
    p.write_text(VALID_CANDIDATE_YAML, encoding="utf-8")
    profile = config.load_candidate_profile(p)
    assert profile.candidate_id == "default-candidate"


def test_load_candidate_profile_missing_file(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError) as exc_info:
        config.load_candidate_profile(tmp_path / "does_not_exist.yaml")
    assert "not found" in str(exc_info.value)


def test_load_candidate_profile_missing_field(tmp_path: Path) -> None:
    p = tmp_path / "candidate_profile.yaml"
    p.write_text("candidate_id: x\n", encoding="utf-8")
    with pytest.raises(config.ConfigError) as exc_info:
        config.load_candidate_profile(p)
    assert exc_info.value.field is not None


def test_load_search_profiles_and_lookup(tmp_path: Path) -> None:
    p = tmp_path / "search_profiles.yaml"
    p.write_text(VALID_SEARCH_PROFILES_YAML, encoding="utf-8")
    profiles = config.load_search_profiles(p)
    profile = config.get_search_profile(profiles, "strategy-global")
    assert profile.included_countries == ["GB", "DE"]


def test_get_search_profile_unknown_id_raises(tmp_path: Path) -> None:
    p = tmp_path / "search_profiles.yaml"
    p.write_text(VALID_SEARCH_PROFILES_YAML, encoding="utf-8")
    profiles = config.load_search_profiles(p)
    with pytest.raises(config.ConfigError):
        config.get_search_profile(profiles, "does-not-exist")


def test_load_source_registry(tmp_path: Path) -> None:
    p = tmp_path / "source_registry.yaml"
    p.write_text(VALID_SOURCE_REGISTRY_YAML, encoding="utf-8")
    entries = config.load_source_registry(p)
    assert entries[0].source_id == "adzuna_api"


def test_execution_limits_rejects_non_positive(tmp_path: Path) -> None:
    p = tmp_path / "execution_limits.yaml"
    p.write_text(
        """
max_countries_per_run: 0
max_pages_per_source_country: 3
results_per_page: 50
request_timeout_seconds: 15
max_retries: 2
max_jobs_processed_per_run: null
""",
        encoding="utf-8",
    )
    with pytest.raises(config.ConfigError):
        config.load_execution_limits(p)


def test_execution_limits_falls_back_to_packaged_template(tmp_path: Path) -> None:
    # No local override anywhere under the (isolated, empty) data directory;
    # the loader must fall back to the packaged template
    # (job_scout.resources), not a CWD-relative file (decisions.md D-021).
    limits = config.load_execution_limits(data_dir=tmp_path / "empty-data-dir")
    assert limits.max_countries_per_run > 0


def test_scoring_weights_must_sum_to_one(tmp_path: Path) -> None:
    p = tmp_path / "scoring_weights.yaml"
    p.write_text(
        """
title_role_family: 0.5
responsibilities: 0.5
required_skills: 0.5
transferable_skills: 0.0
seniority_experience: 0.0
sector_relevance: 0.0
education: 0.0
visa_relocation: 0.0
prefilter_threshold: 0.15
""",
        encoding="utf-8",
    )
    with pytest.raises(config.ConfigError):
        config.load_scoring_weights(p)


def test_scoring_weights_falls_back_to_packaged_template(tmp_path: Path) -> None:
    weights = config.load_scoring_weights(data_dir=tmp_path / "empty-data-dir")
    assert abs(sum(weights.component_weights().values()) - 1.0) < 1e-6


def test_source_scoring_weights_falls_back_to_packaged_template(tmp_path: Path) -> None:
    weights = config.load_source_scoring_weights(data_dir=tmp_path / "empty-data-dir")
    assert abs(sum(weights.component_weights().values()) - 1.0) < 1e-6


def test_explicit_path_wins_over_data_dir(tmp_path: Path) -> None:
    """Priority: an explicit path argument always wins over --data-dir /
    JOB_SCOUT_DATA_DIR / platformdirs (architecture.md section 15.1)."""
    data_dir = tmp_path / "data-dir"
    data_dir_config = data_dir / "config" / "execution_limits.yaml"
    data_dir_config.parent.mkdir(parents=True)
    data_dir_config.write_text(
        "max_countries_per_run: 2\nmax_pages_per_source_country: 1\n"
        "results_per_page: 10\nrequest_timeout_seconds: 5\nmax_retries: 1\n"
        "max_jobs_processed_per_run: null\n",
        encoding="utf-8",
    )
    explicit = tmp_path / "explicit-execution-limits.yaml"
    explicit.write_text(
        "max_countries_per_run: 9\nmax_pages_per_source_country: 1\n"
        "results_per_page: 10\nrequest_timeout_seconds: 5\nmax_retries: 1\n"
        "max_jobs_processed_per_run: null\n",
        encoding="utf-8",
    )
    limits = config.load_execution_limits(explicit, data_dir=data_dir)
    assert limits.max_countries_per_run == 9  # explicit path, not the data_dir one


def test_data_dir_resolves_candidate_profile_default(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "candidate_profile.yaml").write_text(VALID_CANDIDATE_YAML, encoding="utf-8")
    profile = config.load_candidate_profile(data_dir=tmp_path)
    assert profile.candidate_id == "default-candidate"


def test_config_loading_does_not_depend_on_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Milestone 1.1: no default config path may assume the process is
    running from the repository root or any other specific CWD."""
    other_cwd = tmp_path / "somewhere-else-entirely"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)

    config_dir = tmp_path / "real-data-dir" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "candidate_profile.yaml").write_text(VALID_CANDIDATE_YAML, encoding="utf-8")

    profile = config.load_candidate_profile(data_dir=tmp_path / "real-data-dir")
    assert profile.candidate_id == "default-candidate"


def test_load_env_reads_adzuna_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    p = tmp_path / ".env"
    p.write_text("ADZUNA_APP_ID=abc\nADZUNA_APP_KEY=secret123\n", encoding="utf-8")
    env = config.load_env(p)
    assert env.adzuna_app_id == "abc"
    assert env.adzuna_app_key == "secret123"


def test_load_env_missing_credentials_are_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    p = tmp_path / ".env"
    p.write_text("LOG_LEVEL=DEBUG\n", encoding="utf-8")
    env = config.load_env(p)
    assert env.adzuna_app_id is None
    assert env.adzuna_app_key is None
