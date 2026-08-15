from pathlib import Path

import pytest

from job_scout import config
from job_scout.models import CompanyWatchlistEntry

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


def test_load_source_registry_without_capabilities_block_defaults(tmp_path: Path) -> None:
    """decisions.md D-041: an existing registry file with no `capabilities`
    key (every valid M1/1.1 config) must keep loading unchanged, defaulting
    to Adzuna-equivalent capabilities."""
    from job_scout.models import SourceCapabilities

    p = tmp_path / "source_registry.yaml"
    p.write_text(VALID_SOURCE_REGISTRY_YAML, encoding="utf-8")
    entries = config.load_source_registry(p)
    assert entries[0].capabilities == SourceCapabilities()


def test_load_source_registry_with_explicit_capabilities_block(tmp_path: Path) -> None:
    p = tmp_path / "source_registry.yaml"
    p.write_text(
        VALID_SOURCE_REGISTRY_YAML.rstrip()
        + "\n    capabilities:\n"
        + "      keyword_search: false\n"
        + "      company_filter: true\n"
        + "      max_recommended_queries_per_request: 3\n",
        encoding="utf-8",
    )
    entries = config.load_source_registry(p)
    assert entries[0].capabilities.keyword_search is False
    assert entries[0].capabilities.company_filter is True
    assert entries[0].capabilities.max_recommended_queries_per_request == 3
    # Untouched fields keep their default.
    assert entries[0].capabilities.location_filter is True


def test_load_source_registry_rejects_invalid_capability_type(tmp_path: Path) -> None:
    p = tmp_path / "source_registry.yaml"
    p.write_text(
        VALID_SOURCE_REGISTRY_YAML.rstrip()
        + "\n    capabilities:\n"
        + "      keyword_search: not-a-boolean\n",
        encoding="utf-8",
    )
    with pytest.raises(config.ConfigError):
        config.load_source_registry(p)


def test_packaged_source_registry_template_adzuna_capabilities_are_verified_defaults() -> None:
    """decisions.md D-041/D-016/D-031: the packaged template's explicit
    adzuna_api capabilities block must reproduce SourceCapabilities()'s own
    defaults exactly (no invented support)."""
    import yaml

    from job_scout.models import SourceCapabilities, SourceRegistryEntry
    from job_scout.resources import template_text

    parsed = yaml.safe_load(template_text("source_registry.example.yaml"))
    by_id = {source["source_id"]: source for source in parsed["sources"]}
    adzuna = SourceRegistryEntry.model_validate(by_id["adzuna_api"])
    assert adzuna.capabilities == SourceCapabilities()


def test_packaged_source_registry_template_reed_capabilities_are_explicit() -> None:
    """decisions.md D-046 (Milestone 2 Deliverable 5 step 5): reed_api's
    packaged capabilities block must be the project's explicit, verified
    values — not the inherited Adzuna-shaped default — and must in
    particular never fabricate exact_phrase_search/company_filter/
    canonical_application_url support Reed's Search API doesn't document."""
    import yaml

    from job_scout.models import SourceCapabilities, SourceRegistryEntry
    from job_scout.resources import template_text

    parsed = yaml.safe_load(template_text("source_registry.example.yaml"))
    by_id = {source["source_id"]: source for source in parsed["sources"]}
    assert "capabilities" in by_id["reed_api"]
    reed = SourceRegistryEntry.model_validate(by_id["reed_api"])
    assert reed.capabilities != SourceCapabilities()
    assert reed.capabilities == SourceCapabilities(
        keyword_search=True,
        exact_phrase_search=False,
        location_filter=True,
        country_filter=False,
        city_filter=True,
        industry_filter=False,
        company_filter=False,
        remote_filter=False,
        salary_data=True,
        structured_description=False,
        pagination=True,
        page_size_control=True,
        posting_date_filter=False,
        stable_external_job_id=True,
        canonical_application_url=False,
        max_recommended_queries_per_request=None,
    )


def test_packaged_source_registry_template_greenhouse_capabilities_are_explicit() -> None:
    """decisions.md D-047 (Milestone 2 Deliverable 5 step 7): greenhouse_
    public_feeds' packaged capabilities block must be the project's
    explicit, verified values — not the inherited Adzuna-shaped default —
    and must in particular claim company_filter=True/keyword_search=False,
    never a keyword-search-shaped capability set the verified Job Board API
    contract doesn't support."""
    import yaml

    from job_scout.models import SourceCapabilities, SourceRegistryEntry
    from job_scout.resources import template_text

    parsed = yaml.safe_load(template_text("source_registry.example.yaml"))
    by_id = {source["source_id"]: source for source in parsed["sources"]}
    assert "capabilities" in by_id["greenhouse_public_feeds"]
    greenhouse = SourceRegistryEntry.model_validate(by_id["greenhouse_public_feeds"])
    assert greenhouse.adapter_ref == "greenhouse"
    assert greenhouse.capabilities != SourceCapabilities()
    assert greenhouse.capabilities == SourceCapabilities(
        keyword_search=False,
        exact_phrase_search=False,
        location_filter=False,
        country_filter=False,
        city_filter=False,
        industry_filter=False,
        company_filter=True,
        remote_filter=False,
        salary_data=False,
        structured_description=False,
        pagination=False,
        page_size_control=False,
        posting_date_filter=False,
        stable_external_job_id=True,
        canonical_application_url=True,
        max_recommended_queries_per_request=None,
    )


def test_non_verified_template_entries_have_no_explicit_capabilities_block() -> None:
    """Guardrail audit (2026-08-08, extended for Task 5, extended again for
    Task 7): only adzuna_api's, reed_api's, and now greenhouse_public_feeds'
    capabilities are actually verified (decisions.md D-016/D-031/D-046/
    D-047). Every other packaged template entry — including
    lever_public_postings — must NOT carry a fabricated `capabilities:`
    block claiming verified support; they inherit SourceCapabilities()'s
    field default only, per decisions.md D-041's explicit
    backward-compatibility design ("every existing registry entry ... keeps
    validating and behaving unchanged"). This is a documentation/inert-
    metadata concern only: nothing reads `.capabilities` for these entries'
    planning/execution, and every one of them is already non-executable
    regardless of capabilities (manual_review/alert_only/
    requires_authorisation/blocked)."""
    import yaml

    from job_scout.models import SourceCapabilities, SourceRegistryEntry
    from job_scout.resources import template_text

    parsed = yaml.safe_load(template_text("source_registry.example.yaml"))
    for raw_source in parsed["sources"]:
        if raw_source["source_id"] in ("adzuna_api", "reed_api", "greenhouse_public_feeds"):
            continue
        assert "capabilities" not in raw_source, (
            f"{raw_source['source_id']} must not carry a fabricated capabilities "
            "block until its real adapter verifies one (Milestone 2 Deliverable 5 "
            "step 8)."
        )
        entry = SourceRegistryEntry.model_validate(raw_source)
        # Inherits the inert, approved-by-D-041 default — not a verified claim.
        assert entry.capabilities == SourceCapabilities()


VALID_COMPANY_WATCHLIST_YAML = """
companies:
  - company_name: "Alpha Example Co"
    source_id: greenhouse_public_feeds
    external_company_key: alphaexampleco
    priority: 50
  - company_name: "Beta Example Co"
    source_id: lever_public_postings
    external_company_key: betaexampleco
    priority: 40
    notes: "second entry"
"""


def test_load_company_watchlist(tmp_path: Path) -> None:
    p = tmp_path / "company_watchlist.yaml"
    p.write_text(VALID_COMPANY_WATCHLIST_YAML, encoding="utf-8")
    entries = config.load_company_watchlist(p)
    assert entries[0].company_name == "Alpha Example Co"
    assert entries[0].source_id == "greenhouse_public_feeds"
    assert entries[0].external_company_key == "alphaexampleco"


def test_load_company_watchlist_preserves_order(tmp_path: Path) -> None:
    p = tmp_path / "company_watchlist.yaml"
    p.write_text(VALID_COMPANY_WATCHLIST_YAML, encoding="utf-8")
    entries = config.load_company_watchlist(p)
    assert [e.company_name for e in entries] == ["Alpha Example Co", "Beta Example Co"]


def test_load_company_watchlist_empty_list_is_valid(tmp_path: Path) -> None:
    p = tmp_path / "company_watchlist.yaml"
    p.write_text("companies: []\n", encoding="utf-8")
    entries = config.load_company_watchlist(p)
    assert entries == []


def test_load_company_watchlist_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError) as exc_info:
        config.load_company_watchlist(tmp_path / "does_not_exist.yaml")
    assert "not found" in str(exc_info.value)


def test_load_company_watchlist_malformed_yaml_raises(tmp_path: Path) -> None:
    p = tmp_path / "company_watchlist.yaml"
    p.write_text("companies: [unterminated\n", encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load_company_watchlist(p)


def test_load_company_watchlist_missing_required_field_raises(tmp_path: Path) -> None:
    p = tmp_path / "company_watchlist.yaml"
    p.write_text(
        "companies:\n  - company_name: Alpha\n    priority: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(config.ConfigError) as exc_info:
        config.load_company_watchlist(p)
    assert exc_info.value.field is not None


def test_load_company_watchlist_rejects_duplicate_source_and_key(tmp_path: Path) -> None:
    p = tmp_path / "company_watchlist.yaml"
    p.write_text(
        VALID_COMPANY_WATCHLIST_YAML.rstrip()
        + "\n  - company_name: \"Alpha Duplicate\"\n"
        + "    source_id: greenhouse_public_feeds\n"
        + "    external_company_key: alphaexampleco\n"
        + "    priority: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(config.ConfigError):
        config.load_company_watchlist(p)


def test_load_company_watchlist_allows_same_key_across_different_sources(
    tmp_path: Path,
) -> None:
    """Same external_company_key under two different source_ids is not a
    duplicate — the (source_id, external_company_key) pair is the identity,
    not external_company_key alone."""
    p = tmp_path / "company_watchlist.yaml"
    p.write_text(
        "companies:\n"
        "  - company_name: Alpha\n"
        "    source_id: greenhouse_public_feeds\n"
        "    external_company_key: samekey\n"
        "    priority: 1\n"
        "  - company_name: Alpha On Lever\n"
        "    source_id: lever_public_postings\n"
        "    external_company_key: samekey\n"
        "    priority: 1\n",
        encoding="utf-8",
    )
    entries = config.load_company_watchlist(p)
    assert len(entries) == 2


def test_packaged_company_watchlist_template_parses() -> None:
    import yaml

    from job_scout.resources import template_text

    parsed = yaml.safe_load(template_text("company_watchlist.example.yaml"))
    entries = [CompanyWatchlistEntry.model_validate(c) for c in parsed["companies"]]
    assert len(entries) >= 2
    source_ids = {e.source_id for e in entries}
    assert "greenhouse_public_feeds" in source_ids
    assert "lever_public_postings" in source_ids


def test_packaged_company_watchlist_template_contains_no_secrets() -> None:
    from job_scout.resources import template_text

    raw = template_text("company_watchlist.example.yaml").lower()
    for forbidden in ("api_key", "apikey", "secret", "token:", "password"):
        assert forbidden not in raw


def test_source_registry_entries_do_not_share_mutable_capability_state() -> None:
    """Guardrail audit (2026-08-08): SourceRegistryEntry.capabilities uses a
    direct nested-model default (`= SourceCapabilities()`), matching this
    project's existing convention (SearchProfile.hard_filters uses the same
    pattern). Pydantic v2 deep-copies BaseModel field defaults per instance
    (verified empirically against pydantic 2.13.4 in this environment), so
    this is safe without switching to Field(default_factory=...) — this test
    guards that guarantee directly rather than relying on general Pydantic
    knowledge."""
    from tests.factories import make_source_entry

    first = make_source_entry(source_id="first")
    second = make_source_entry(source_id="second")
    assert first.capabilities is not second.capabilities

    first.capabilities.keyword_search = False
    assert second.capabilities.keyword_search is True


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


def test_execution_limits_max_queries_per_source_country_defaults_when_omitted(
    tmp_path: Path,
) -> None:
    """Milestone 2 Deliverable 5 step 2: an existing execution_limits.yaml
    written before this field existed (no max_queries_per_source_country key
    at all) must keep loading unchanged."""
    p = tmp_path / "execution_limits.yaml"
    p.write_text(
        """
max_countries_per_run: 6
max_pages_per_source_country: 3
results_per_page: 50
request_timeout_seconds: 15
max_retries: 2
max_jobs_processed_per_run: null
""",
        encoding="utf-8",
    )
    limits = config.load_execution_limits(p)
    assert limits.max_queries_per_source_country == 3


def test_execution_limits_max_queries_per_source_country_explicit_value(
    tmp_path: Path,
) -> None:
    p = tmp_path / "execution_limits.yaml"
    p.write_text(
        """
max_countries_per_run: 6
max_pages_per_source_country: 3
results_per_page: 50
request_timeout_seconds: 15
max_retries: 2
max_jobs_processed_per_run: null
max_queries_per_source_country: 5
""",
        encoding="utf-8",
    )
    limits = config.load_execution_limits(p)
    assert limits.max_queries_per_source_country == 5


def test_execution_limits_rejects_non_positive_max_queries_per_source_country(
    tmp_path: Path,
) -> None:
    p = tmp_path / "execution_limits.yaml"
    p.write_text(
        """
max_countries_per_run: 6
max_pages_per_source_country: 3
results_per_page: 50
request_timeout_seconds: 15
max_retries: 2
max_jobs_processed_per_run: null
max_queries_per_source_country: 0
""",
        encoding="utf-8",
    )
    with pytest.raises(config.ConfigError):
        config.load_execution_limits(p)


def test_execution_limits_packaged_template_sets_max_queries_per_source_country(
    tmp_path: Path,
) -> None:
    limits = config.load_execution_limits(data_dir=tmp_path / "empty-data-dir")
    assert limits.max_queries_per_source_country == 3


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
