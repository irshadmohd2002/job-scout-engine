from job_scout import config
from job_scout.models import (
    AccessMode,
    ApprovalStatus,
    ConfigStatus,
    SourceCapabilities,
    SourceType,
)
from job_scout.source_intelligence.planner import build_plan
from tests.factories import make_candidate_profile, make_search_profile, make_source_entry


def _adzuna_entry(**overrides: object) -> object:
    base: dict[str, object] = {
        "source_id": "adzuna_api",
        "access_mode": AccessMode.PUBLIC_API,
        "approval_status": ApprovalStatus.APPROVED,
        "geographic_coverage": ["GB"],
        "role_coverage": ["general"],
        "config_status": ConfigStatus.NEEDS_CREDENTIALS,
    }
    base.update(overrides)
    return make_source_entry(**base)


def _limits(**overrides: object) -> config.ExecutionLimits:
    base = {
        "max_countries_per_run": 6,
        "max_pages_per_source_country": 3,
        "results_per_page": 50,
        "request_timeout_seconds": 15,
        "max_retries": 2,
        "max_jobs_processed_per_run": None,
    }
    base.update(overrides)
    return config.ExecutionLimits.model_validate(base)


def _weights() -> config.SourceScoringWeights:
    return config.load_source_scoring_weights()


def test_source_scoring_weights_sum_to_one() -> None:
    weights = _weights()
    total = sum(weights.component_weights().values())
    assert abs(total - 1.0) < 1e-6


def test_selected_and_excluded_sources_with_reasons() -> None:
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["GB", "DE"])
    adzuna = make_source_entry(
        source_id="adzuna_api",
        access_mode=AccessMode.PUBLIC_API,
        approval_status=ApprovalStatus.APPROVED,
        geographic_coverage=["GB", "DE"],
        role_coverage=["general"],
    )
    irrelevant = make_source_entry(
        source_id="india_only_portal",
        access_mode=AccessMode.EMAIL_ALERT,
        approval_status=ApprovalStatus.ALERT_ONLY,
        geographic_coverage=["IN"],
        role_coverage=["general"],
    )
    plan = build_plan(candidate, search, [adzuna, irrelevant], _limits(), _weights())

    selected_ids = {s.source_id for s in plan.selected_sources}
    excluded_ids = {s.source_id for s in plan.excluded_sources}
    assert selected_ids == {"adzuna_api"}
    assert excluded_ids == {"india_only_portal"}

    adzuna_selected = next(s for s in plan.selected_sources if s.source_id == "adzuna_api")
    assert adzuna_selected.executable is True
    assert adzuna_selected.supported_countries == ["GB", "DE"]
    assert adzuna_selected.unsupported_countries == []

    excluded = next(s for s in plan.excluded_sources if s.source_id == "india_only_portal")
    assert "no_geographic_coverage" in excluded.reasons_excluded


def test_build_plan_populates_planned_queries_via_candidate_fallback() -> None:
    """Milestone 2 Deliverable 5 step 3: build_plan() now wires the
    SearchProfile-driven query planner. A SearchProfile with no
    target_titles/title_aliases/role_families/required_skills carries no
    active query intent at all, so the single grouped fallback query falls
    back to CandidateProfile data — same "one broad query" shape M1/1.1
    always had, now represented as a PlannedQuery instead of being implicit.
    The legacy `search_params`/`search_queries` representation
    (`AdzunaAdapter.fetch()`'s actual runtime input) stays byte-for-byte
    unchanged — no pipeline/runtime behaviour changes in this step."""
    candidate = make_candidate_profile(title_aliases=["Strategy Manager"])
    search = make_search_profile(included_countries=["GB"])
    adzuna = _adzuna_entry(
        geographic_coverage=["GB"],
        approval_status=ApprovalStatus.APPROVED,
        config_status=ConfigStatus.CONFIGURED,
    )
    plan = build_plan(candidate, search, [adzuna], _limits(), _weights())

    selected = next(s for s in plan.selected_sources if s.source_id == "adzuna_api")
    assert len(selected.planned_queries) == 1
    fallback = selected.planned_queries[0]
    assert fallback.mode == "any_of_words"
    assert fallback.provenance == ["candidate.title_aliases", "candidate.role_families"]
    # estimated_request_count = supported_countries * planned_queries * max_pages
    assert selected.estimated_request_count == 1 * 1 * 3
    # M1/1.1 runtime representation, byte-for-byte unchanged: exactly one
    # broad OR query, still driven by CandidateProfile.title_aliases alone —
    # pipeline.py's fetch() call pattern does not change until step 4.
    assert selected.search_queries == ["Strategy Manager"]
    assert selected.search_params is not None
    assert selected.search_params.keywords == ["Strategy Manager"]


def test_build_plan_prefers_target_titles_over_candidate_history() -> None:
    """The active SearchProfile's target_titles must dominate — candidate
    history never contributes once the search profile expresses real
    intent."""
    candidate = make_candidate_profile(
        title_aliases=["Should Not Appear"], role_families=["should_not_appear"]
    )
    search = make_search_profile(
        included_countries=["GB"], target_titles=["Chief of Staff", "Transformation Lead"]
    )
    adzuna = _adzuna_entry(
        geographic_coverage=["GB"],
        approval_status=ApprovalStatus.APPROVED,
        config_status=ConfigStatus.CONFIGURED,
    )
    plan = build_plan(candidate, search, [adzuna], _limits(), _weights())
    selected = next(s for s in plan.selected_sources if s.source_id == "adzuna_api")
    labels = [q.label for q in selected.planned_queries]
    assert labels == ["Chief of Staff", "Transformation Lead"]
    assert all(q.mode == "exact_phrase" for q in selected.planned_queries)
    for query in selected.planned_queries:
        assert "Should Not Appear" not in query.keywords
    # Legacy search_params still unaffected by any of this (unchanged runtime input).
    assert selected.search_params is not None
    assert selected.search_params.keywords == ["Should Not Appear"]


def test_source_selected_but_not_executable_when_manual_review() -> None:
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["GB"])
    entry = make_source_entry(
        source_id="pending_source",
        access_mode=AccessMode.PERMITTED_HTML,
        approval_status=ApprovalStatus.MANUAL_REVIEW,
        geographic_coverage=["GB"],
        role_coverage=["general"],
        config_status=ConfigStatus.NOT_CONFIGURED,
        required_setup_actions=["Confirm terms"],
    )
    plan = build_plan(candidate, search, [entry], _limits(), _weights())
    assert len(plan.selected_sources) == 1
    selected = plan.selected_sources[0]
    assert selected.executable is False
    assert selected.required_setup_actions == ["Confirm terms"]


def test_manual_review_source_with_full_capabilities_still_not_executable() -> None:
    """decisions.md D-041 step 1 non-goal: a declared `capabilities` block
    (however permissive) must never make a manual_review/blocked source
    executable — only the compliance gate's approval_status/access_mode
    truth table decides that."""
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["GB"])
    entry = make_source_entry(
        source_id="capable_but_unreviewed",
        access_mode=AccessMode.PUBLIC_ATS_FEED,
        approval_status=ApprovalStatus.MANUAL_REVIEW,
        geographic_coverage=["GB"],
        role_coverage=["general"],
        config_status=ConfigStatus.NOT_CONFIGURED,
        capabilities=SourceCapabilities(
            keyword_search=True,
            exact_phrase_search=True,
            company_filter=True,
            industry_filter=True,
            remote_filter=True,
        ),
    )
    plan = build_plan(candidate, search, [entry], _limits(), _weights())
    assert len(plan.selected_sources) == 1
    assert plan.selected_sources[0].executable is False


def test_partial_country_coverage_recorded_as_unsupported() -> None:
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["GB", "DE", "AE"])
    adzuna = make_source_entry(
        source_id="adzuna_api",
        access_mode=AccessMode.PUBLIC_API,
        approval_status=ApprovalStatus.APPROVED,
        geographic_coverage=["GB", "DE"],  # deliberately excludes AE
        role_coverage=["general"],
    )
    plan = build_plan(candidate, search, [adzuna], _limits(), _weights())
    selected = plan.selected_sources[0]
    assert selected.supported_countries == ["GB", "DE"]
    assert [c.country for c in selected.unsupported_countries] == ["AE"]
    # no HTTP request is ever planned for the unsupported country
    assert "AE" not in selected.search_params.countries  # type: ignore[union-attr]


def test_diversity_rule_excludes_redundant_generic_aggregator() -> None:
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["GB"])
    strong = make_source_entry(
        source_id="aggregator_strong",
        access_mode=AccessMode.PUBLIC_API,
        approval_status=ApprovalStatus.APPROVED,
        geographic_coverage=["GB"],
        role_coverage=["general"],
        priority=100,
        reliability_score=0.95,
    )
    weak = make_source_entry(
        source_id="aggregator_weak",
        access_mode=AccessMode.PUBLIC_API,
        approval_status=ApprovalStatus.APPROVED,
        geographic_coverage=["GB"],
        role_coverage=["general"],
        priority=10,
        reliability_score=0.2,
    )
    plan = build_plan(candidate, search, [strong, weak], _limits(), _weights())
    selected_ids = {s.source_id for s in plan.selected_sources}
    assert selected_ids == {"aggregator_strong"}
    excluded = next(s for s in plan.excluded_sources if s.source_id == "aggregator_weak")
    assert excluded.reasons_excluded == ["redundant_with:aggregator_strong"]


def test_country_truncation_preserves_profile_order_and_records_skips() -> None:
    candidate = make_candidate_profile()
    search = make_search_profile(
        included_countries=["GB", "DE", "NL", "IE", "AE", "SG", "CA", "AU", "US"]
    )
    adzuna = make_source_entry(
        source_id="adzuna_api",
        access_mode=AccessMode.PUBLIC_API,
        approval_status=ApprovalStatus.APPROVED,
        geographic_coverage=["GB", "DE", "NL", "IE", "SG", "CA", "AU", "US"],
        role_coverage=["general"],
    )
    limits = _limits(max_countries_per_run=6)
    plan = build_plan(candidate, search, [adzuna], limits, _weights())

    # first 6 in profile order: GB, DE, NL, IE, AE, SG — AE is selected but not
    # covered by adzuna's geographic_coverage, so it lands in unsupported_countries.
    selected = plan.selected_sources[0]
    assert selected.supported_countries == ["GB", "DE", "NL", "IE", "SG"]
    assert [c.country for c in selected.unsupported_countries] == ["AE"]
    skipped_notes = [n for n in plan.diversity_notes if "skipped" in n]
    assert any("CA" in n for n in skipped_notes)
    assert any("AU" in n for n in skipped_notes)
    assert any("US" in n for n in skipped_notes)
    # skipped countries never reach search params for any source
    assert "CA" not in selected.search_params.countries  # type: ignore[union-attr]
    assert "AU" not in selected.search_params.countries  # type: ignore[union-attr]
    assert "US" not in selected.search_params.countries  # type: ignore[union-attr]


def test_adzuna_gb_ie_profile_reports_ie_unsupported_and_only_queries_gb() -> None:
    """Regression for the live D-028 finding: Adzuna 404s on IE, so the
    packaged registry template's adzuna_api.geographic_coverage no longer
    lists IE. A profile that still requests GB+IE (Ireland is never removed
    from the candidate's own search profile) must show Adzuna executable
    for GB, IE reported as unsupported with reason not_in_geographic_coverage,
    and IE must never reach the adapter's search params."""
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["GB", "IE"])
    adzuna = make_source_entry(
        source_id="adzuna_api",
        access_mode=AccessMode.PUBLIC_API,
        approval_status=ApprovalStatus.APPROVED,
        geographic_coverage=["GB"],  # IE removed per D-028
        role_coverage=["general"],
    )
    plan = build_plan(candidate, search, [adzuna], _limits(), _weights())

    assert len(plan.selected_sources) == 1
    selected = plan.selected_sources[0]
    assert selected.source_id == "adzuna_api"
    assert selected.executable is True
    assert selected.supported_countries == ["GB"]
    assert [c.country for c in selected.unsupported_countries] == ["IE"]
    assert [c.reason for c in selected.unsupported_countries] == ["not_in_geographic_coverage"]

    assert selected.search_params is not None
    assert selected.search_params.countries == ["GB"]
    assert "IE" not in selected.search_params.countries


def test_no_role_coverage_excludes_source() -> None:
    candidate = make_candidate_profile(role_families=["engineering"])
    search = make_search_profile(included_countries=["GB"], role_families=[])
    entry = make_source_entry(
        source_id="finance_only",
        access_mode=AccessMode.PUBLIC_API,
        approval_status=ApprovalStatus.APPROVED,
        geographic_coverage=["GB"],
        role_coverage=["finance"],
    )
    plan = build_plan(candidate, search, [entry], _limits(), _weights())
    assert plan.selected_sources == []
    excluded = plan.excluded_sources[0]
    assert "no_role_coverage" in excluded.reasons_excluded


def test_search_discovery_source_never_executable_even_when_approved() -> None:
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["AU"])
    entry = make_source_entry(
        source_id="seek_anz",
        access_mode=AccessMode.SEARCH_DISCOVERY,
        approval_status=ApprovalStatus.APPROVED,
        geographic_coverage=["AU", "NZ"],
        role_coverage=["general"],
    )
    plan = build_plan(candidate, search, [entry], _limits(), _weights())
    selected = plan.selected_sources[0]
    assert selected.executable is False


def test_source_type_not_generic_aggregator_is_not_deduplicated() -> None:
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["GB"])
    a = make_source_entry(
        source_id="a",
        access_mode=AccessMode.PUBLIC_API,
        approval_status=ApprovalStatus.APPROVED,
        geographic_coverage=["GB"],
        role_coverage=["general"],
        priority=100,
    )
    government_b = make_source_entry(
        source_id="b_gov",
        access_mode=AccessMode.PUBLIC_API,
        approval_status=ApprovalStatus.APPROVED,
        geographic_coverage=["GB"],
        role_coverage=["general"],
        priority=10,
    )
    government_b = government_b.model_copy(update={"source_type": SourceType.GOVERNMENT})
    plan = build_plan(candidate, search, [a, government_b], _limits(), _weights())
    selected_ids = {s.source_id for s in plan.selected_sources}
    assert selected_ids == {"a", "b_gov"}


# --- Runtime configuration status (decisions.md D-030) ----------------------


def test_effective_config_status_defaults_to_declared_when_no_env_passed() -> None:
    """build_plan(..., env=None) — the default — must not change behaviour
    for any existing caller that doesn't opt in to runtime credential
    checks."""
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["GB"])
    entry = _adzuna_entry()
    plan = build_plan(candidate, search, [entry], _limits(), _weights())
    selected = plan.selected_sources[0]
    assert selected.config_status == ConfigStatus.NEEDS_CREDENTIALS
    assert selected.effective_config_status == ConfigStatus.NEEDS_CREDENTIALS


def test_effective_config_status_configured_when_credentials_present() -> None:
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["GB"])
    entry = _adzuna_entry()  # declared config_status stays needs_credentials
    env = config.EnvConfig(adzuna_app_id="id", adzuna_app_key="key")
    plan = build_plan(candidate, search, [entry], _limits(), _weights(), env=env)
    selected = plan.selected_sources[0]
    assert selected.config_status == ConfigStatus.NEEDS_CREDENTIALS  # declared: unchanged
    assert selected.effective_config_status == ConfigStatus.CONFIGURED  # runtime: live check


def test_effective_config_status_needs_credentials_when_absent() -> None:
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["GB"])
    entry = _adzuna_entry(config_status=ConfigStatus.CONFIGURED)  # declared says configured...
    env = config.EnvConfig(adzuna_app_id=None, adzuna_app_key=None)
    plan = build_plan(candidate, search, [entry], _limits(), _weights(), env=env)
    selected = plan.selected_sources[0]
    assert selected.config_status == ConfigStatus.CONFIGURED  # declared: unchanged
    assert selected.effective_config_status == ConfigStatus.NEEDS_CREDENTIALS  # ...but isn't live


def test_effective_config_status_partial_credentials_still_needs_credentials() -> None:
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["GB"])
    entry = _adzuna_entry()
    env = config.EnvConfig(adzuna_app_id="id", adzuna_app_key=None)
    plan = build_plan(candidate, search, [entry], _limits(), _weights(), env=env)
    selected = plan.selected_sources[0]
    assert selected.effective_config_status == ConfigStatus.NEEDS_CREDENTIALS


def test_effective_config_status_unaffected_for_sources_without_a_credential_rule() -> None:
    """Only adzuna_api has a known credential check in Milestone 1
    (decisions.md D-002/D-030); every other source_id's effective status
    just mirrors its declared status."""
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["GB"])
    entry = make_source_entry(
        source_id="some_other_source",
        access_mode=AccessMode.PUBLIC_API,
        approval_status=ApprovalStatus.APPROVED,
        geographic_coverage=["GB"],
        role_coverage=["general"],
        config_status=ConfigStatus.NEEDS_SETUP,
    )
    env = config.EnvConfig(adzuna_app_id="id", adzuna_app_key="key")
    plan = build_plan(candidate, search, [entry], _limits(), _weights(), env=env)
    selected = plan.selected_sources[0]
    assert selected.effective_config_status == ConfigStatus.NEEDS_SETUP


# --- Milestone 2 Deliverable 5 step 3: query-planner wiring (build_plan level) ---


def test_estimated_request_count_matches_countries_times_queries_times_pages() -> None:
    candidate = make_candidate_profile()
    search = make_search_profile(
        included_countries=["GB", "DE"], target_titles=["Role A", "Role B"]
    )
    adzuna = _adzuna_entry(
        geographic_coverage=["GB", "DE"],
        approval_status=ApprovalStatus.APPROVED,
        config_status=ConfigStatus.CONFIGURED,
    )
    limits = _limits(max_pages_per_source_country=3, max_queries_per_source_country=5)
    plan = build_plan(candidate, search, [adzuna], limits, _weights())
    selected = plan.selected_sources[0]
    assert selected.supported_countries == ["GB", "DE"]
    assert len(selected.planned_queries) == 2
    # 2 countries * 2 planned queries * 3 max_pages_per_source_country
    assert selected.estimated_request_count == 2 * 2 * 3


def test_estimated_request_count_reflects_query_budget_truncation() -> None:
    candidate = make_candidate_profile()
    search = make_search_profile(
        included_countries=["GB"], target_titles=["Role A", "Role B", "Role C"]
    )
    adzuna = _adzuna_entry(
        geographic_coverage=["GB"],
        approval_status=ApprovalStatus.APPROVED,
        config_status=ConfigStatus.CONFIGURED,
    )
    limits = _limits(max_pages_per_source_country=2, max_queries_per_source_country=1)
    plan = build_plan(candidate, search, [adzuna], limits, _weights())
    selected = plan.selected_sources[0]
    assert len(selected.planned_queries) == 1
    assert selected.estimated_request_count == 1 * 1 * 2
    assert any("query_budget" in reason for reason in selected.reasons_selected)


def test_company_filter_source_has_no_planned_queries_via_build_plan() -> None:
    """decisions.md D-041: a Greenhouse/Lever-shaped source (company_filter)
    relies on watchlist fan-out, not keyword PlannedQuerys."""
    candidate = make_candidate_profile()
    search = make_search_profile(included_countries=["GB"], target_titles=["Role A"])
    ats_feed = make_source_entry(
        source_id="greenhouse_public_feeds",
        access_mode=AccessMode.PUBLIC_ATS_FEED,
        approval_status=ApprovalStatus.MANUAL_REVIEW,
        geographic_coverage=["GB"],
        role_coverage=["general"],
        capabilities=SourceCapabilities(company_filter=True, keyword_search=False),
    )
    plan = build_plan(candidate, search, [ats_feed], _limits(), _weights())
    selected = plan.selected_sources[0]
    assert selected.planned_queries == []
    assert selected.estimated_request_count == 0


def test_source_selection_and_compliance_decisions_unaffected_by_query_planner() -> None:
    """Query-planner wiring must not change source selection, scoring, or
    compliance decisions — only planned_queries/estimated_request_count."""
    candidate = make_candidate_profile()
    search = make_search_profile(
        included_countries=["GB"], target_titles=["Role A", "Role B"]
    )
    entry = make_source_entry(
        source_id="pending_source",
        access_mode=AccessMode.PERMITTED_HTML,
        approval_status=ApprovalStatus.MANUAL_REVIEW,
        geographic_coverage=["GB"],
        role_coverage=["general"],
        config_status=ConfigStatus.NOT_CONFIGURED,
        required_setup_actions=["Confirm terms"],
    )
    plan = build_plan(candidate, search, [entry], _limits(), _weights())
    selected = plan.selected_sources[0]
    # still not executable — compliance gate unaffected by having planned_queries
    assert selected.executable is False
    assert selected.required_setup_actions == ["Confirm terms"]
    # planning data is still generated even for a non-executable source
    assert len(selected.planned_queries) == 2
