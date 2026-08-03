from job_scout import config
from job_scout.models import AccessMode, ApprovalStatus, ConfigStatus, SourceType
from job_scout.source_intelligence.planner import build_plan
from tests.factories import make_candidate_profile, make_search_profile, make_source_entry


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
