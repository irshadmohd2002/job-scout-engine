"""SearchProfile-driven query planner (Milestone 2 Deliverable 5 step 3).

Unit coverage for `source_intelligence.query_planner.build_planned_queries`
in isolation — no adapter, no HTTP, no pipeline wiring (that's step 4).
`tests/test_planner.py` covers the `build_plan()` integration (estimated
request count, multi-country budgeting, legacy search_params/search_queries
staying unchanged)."""

from __future__ import annotations

from job_scout import config
from job_scout.models import SourceCapabilities
from job_scout.source_intelligence.query_planner import build_planned_queries
from tests.factories import make_candidate_profile, make_search_profile


def _limits(**overrides: object) -> config.ExecutionLimits:
    base: dict[str, object] = {
        "max_countries_per_run": 6,
        "max_pages_per_source_country": 3,
        "results_per_page": 50,
        "request_timeout_seconds": 15,
        "max_retries": 2,
        "max_jobs_processed_per_run": None,
        "max_queries_per_source_country": 3,
    }
    base.update(overrides)
    return config.ExecutionLimits.model_validate(base)


def _caps(**overrides: object) -> SourceCapabilities:
    return SourceCapabilities.model_validate(overrides)


def _plan(**overrides: object) -> list[object]:
    """Convenience: build_planned_queries with sane defaults, overridable."""
    kwargs: dict[str, object] = {
        "capabilities": _caps(),
        "candidate_profile": make_candidate_profile(),
        "search_profile": make_search_profile(),
        "execution_limits": _limits(),
    }
    kwargs.update(overrides)
    result = build_planned_queries(**kwargs)  # type: ignore[arg-type]
    return list(result.planned_queries)


# --- target_titles ------------------------------------------------------


def test_target_titles_generate_one_query_each_in_order() -> None:
    search = make_search_profile(target_titles=["Chief of Staff", "Transformation Lead"])
    queries = _plan(search_profile=search)
    assert [q.label for q in queries] == ["Chief of Staff", "Transformation Lead"]
    assert all(q.provenance == ["search.target_titles"] for q in queries)


def test_target_title_order_is_preserved_even_when_reversed_in_config() -> None:
    search = make_search_profile(target_titles=["Zeta Role", "Alpha Role"])
    queries = _plan(search_profile=search)
    assert [q.label for q in queries] == ["Zeta Role", "Alpha Role"]


def test_duplicate_target_titles_deduplicated_case_insensitive() -> None:
    search = make_search_profile(
        target_titles=["Chief of Staff", "chief   of  staff", "Chief of Staff"]
    )
    queries = _plan(search_profile=search)
    assert len(queries) == 1
    assert queries[0].label == "Chief of Staff"


def test_exact_phrase_mode_used_when_source_supports_it() -> None:
    search = make_search_profile(target_titles=["Chief of Staff"])
    queries = _plan(search_profile=search, capabilities=_caps(exact_phrase_search=True))
    assert queries[0].mode == "exact_phrase"
    assert queries[0].provenance == ["search.target_titles"]


def test_exact_phrase_unsupported_degrades_to_any_of_words_and_records_it() -> None:
    search = make_search_profile(target_titles=["Chief of Staff"])
    queries = _plan(search_profile=search, capabilities=_caps(exact_phrase_search=False))
    assert queries[0].mode == "any_of_words"
    assert queries[0].keywords == ["Chief of Staff"]  # title never dropped, never rewritten
    assert "capability.exact_phrase_search:unsupported" in queries[0].provenance
    assert "search.target_titles" in queries[0].provenance


# --- query budget ---------------------------------------------------------


def test_budget_never_exceeded() -> None:
    search = make_search_profile(
        target_titles=["Role A", "Role B", "Role C", "Role D", "Role E"]
    )
    limits = _limits(max_queries_per_source_country=2)
    queries = _plan(search_profile=search, execution_limits=limits)
    assert len(queries) == 2
    assert [q.label for q in queries] == ["Role A", "Role B"]


def test_truncation_records_skip_note() -> None:
    search = make_search_profile(target_titles=["Role A", "Role B", "Role C"])
    result = build_planned_queries(
        capabilities=_caps(),
        candidate_profile=make_candidate_profile(),
        search_profile=search,
        execution_limits=_limits(max_queries_per_source_country=1),
    )
    assert len(result.planned_queries) == 1
    assert len(result.notes) == 1
    assert "max_queries_per_source_country=1" in result.notes[0]
    assert "Role B" in result.notes[0]
    assert "Role C" in result.notes[0]
    assert "Role A" not in result.notes[0]  # kept, not skipped


def test_source_max_recommended_queries_further_caps_budget() -> None:
    search = make_search_profile(target_titles=["Role A", "Role B", "Role C"])
    queries = _plan(
        search_profile=search,
        capabilities=_caps(max_recommended_queries_per_request=1),
        execution_limits=_limits(max_queries_per_source_country=3),
    )
    assert len(queries) == 1
    assert queries[0].label == "Role A"


def test_duplicate_target_titles_never_consume_budget_twice() -> None:
    search = make_search_profile(target_titles=["Role A", "role a", "Role B"])
    limits = _limits(max_queries_per_source_country=2)
    queries = _plan(search_profile=search, execution_limits=limits)
    assert [q.label for q in queries] == ["Role A", "Role B"]


# --- grouped fallback query -------------------------------------------------


def test_fallback_dropped_when_budget_exhausted_by_target_titles() -> None:
    search = make_search_profile(
        target_titles=["Role A", "Role B"], title_aliases=["Should Not Appear"]
    )
    limits = _limits(max_queries_per_source_country=2)
    queries = _plan(search_profile=search, execution_limits=limits)
    assert [q.label for q in queries] == ["Role A", "Role B"]
    assert not any(q.label == "fallback" for q in queries)


def test_fallback_added_when_budget_remains_after_target_titles() -> None:
    search = make_search_profile(target_titles=["Role A"], title_aliases=["Broader Term"])
    limits = _limits(max_queries_per_source_country=3)
    queries = _plan(search_profile=search, execution_limits=limits)
    labels = [q.label for q in queries]
    assert labels == ["Role A", "fallback"]
    fallback = queries[1]
    assert fallback.mode == "any_of_words"
    assert fallback.keywords == ["Broader Term"]
    assert fallback.provenance == ["search.title_aliases"]


def test_fallback_never_pulls_candidate_history_when_search_profile_has_any_intent() -> None:
    """CLAUDE.md/task instruction: active SearchProfile intent stays primary
    — candidate history must not dilute it, even when the grouped-fallback
    -specific fields (title_aliases/role_families/required_skills) are
    themselves empty but target_titles is not."""
    candidate = make_candidate_profile(
        title_aliases=["Should Not Appear"], role_families=["should_not_appear"]
    )
    search = make_search_profile(target_titles=["Role A"])
    queries = _plan(
        candidate_profile=candidate,
        search_profile=search,
        execution_limits=_limits(max_queries_per_source_country=3),
    )
    # only the target-title query — no fallback at all, since the
    # SearchProfile's own fallback-eligible fields are empty and candidate
    # history must not be substituted while target_titles carries intent.
    assert [q.label for q in queries] == ["Role A"]


def test_fallback_uses_candidate_profile_only_when_search_profile_has_no_intent_at_all() -> None:
    candidate = make_candidate_profile(
        title_aliases=["Strategy Manager"], role_families=["strategy_and_planning"]
    )
    search = make_search_profile()  # no target_titles/title_aliases/role_families/required_skills
    queries = _plan(candidate_profile=candidate, search_profile=search)
    assert len(queries) == 1
    fallback = queries[0]
    assert fallback.label == "fallback"
    assert fallback.mode == "any_of_words"
    assert fallback.keywords == ["Strategy Manager", "strategy and planning"]
    assert fallback.provenance == ["candidate.title_aliases", "candidate.role_families"]


def test_fallback_role_families_rendered_readable() -> None:
    search = make_search_profile(role_families=["strategy_and_planning"])
    queries = _plan(search_profile=search)
    assert queries[0].keywords == ["strategy and planning"]


def test_fallback_required_skills_truncated_to_top_n() -> None:
    search = make_search_profile(
        required_skills=["Skill A", "Skill B", "Skill C", "Skill D", "Skill E", "Skill F"]
    )
    queries = _plan(search_profile=search)
    assert queries[0].keywords == ["Skill A", "Skill B", "Skill C", "Skill D", "Skill E"]
    assert "Skill F" not in queries[0].keywords


def test_fallback_never_becomes_unbounded_word_soup() -> None:
    """Regression guard for the exact bug this milestone fixes (D-029): the
    grouped fallback stays a single bounded PlannedQuery, never one query per
    alias/role-family/skill."""
    search = make_search_profile(
        title_aliases=["Alias One", "Alias Two"],
        role_families=["family_one", "family_two"],
        required_skills=["Skill One", "Skill Two", "Skill Three"],
    )
    queries = _plan(search_profile=search)
    assert len(queries) == 1
    assert queries[0].mode == "any_of_words"


def test_no_fallback_when_nothing_to_search() -> None:
    search = make_search_profile()
    candidate = make_candidate_profile(title_aliases=[], role_families=[])
    queries = _plan(search_profile=search, candidate_profile=candidate)
    assert queries == []


# --- source capability gating ----------------------------------------------


def test_company_filter_suppresses_keyword_queries_even_when_keyword_search_supported() -> None:
    """decisions.md D-041 ("How the query planner consumes it"): company_filter=True
    means "one fetch is implicitly scoped to one watchlisted company" for
    this source (Greenhouse/Lever-shaped) and unconditionally tells the
    planner to skip keyword-PlannedQuery generation, in favour of future
    CompanyWatchlistEntry fan-out — independent of keyword_search's own
    value. `_caps(company_filter=True)` leaves `keyword_search` at its
    Pydantic default (True), so this specifically proves company_filter
    suppresses on its own, not merely as a side effect of keyword_search
    also being False."""
    search = make_search_profile(target_titles=["Role A"])
    caps = _caps(company_filter=True, keyword_search=True)
    queries = _plan(search_profile=search, capabilities=caps)
    assert queries == []


def test_keyword_search_unsupported_gets_zero_queries() -> None:
    search = make_search_profile(target_titles=["Role A"])
    queries = _plan(search_profile=search, capabilities=_caps(keyword_search=False))
    assert queries == []


# --- determinism ------------------------------------------------------------


def test_planner_output_is_deterministic_across_repeated_calls() -> None:
    search = make_search_profile(
        target_titles=["Role A", "Role B"], title_aliases=["Broad Term"]
    )
    kwargs: dict[str, object] = {
        "capabilities": _caps(),
        "candidate_profile": make_candidate_profile(),
        "search_profile": search,
        "execution_limits": _limits(),
    }
    first = build_planned_queries(**kwargs)  # type: ignore[arg-type]
    second = build_planned_queries(**kwargs)  # type: ignore[arg-type]
    assert first.planned_queries == second.planned_queries
    assert first.notes == second.notes
