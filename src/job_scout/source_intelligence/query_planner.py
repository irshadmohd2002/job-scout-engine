"""SearchProfile-driven query planner (MILESTONE_2.md "Query-planning
design"; Deliverable 5 step 3; decisions.md D-040/D-041 for the
`SourceCapabilities` inputs this module reads).

Builds a bounded list of `PlannedQuery` objects for one source, from the
active `SearchProfile` (primary) and `CandidateProfile` (fallback only).
Pure function, no I/O, no adapter calls — same discipline as
`source_intelligence/planner.py`. Nothing here executes a query; that is
Milestone 2 Deliverable 5 step 4 (`pipeline.py` calling `adapter.fetch()`
once per `PlannedQuery`), not this module.

Bounded hybrid design (MILESTONE_2.md "Recommendation"):
1. One `PlannedQuery` per `SearchProfile.target_titles` entry, in configured
   order, deduplicated by normalised keyword set. `mode="exact_phrase"` when
   the source supports it (`SourceCapabilities.exact_phrase_search`),
   degraded to `mode="any_of_words"` (never dropped, never fabricated
   syntax) otherwise — the degradation is recorded in the query's own
   `provenance`.
2. Truncated to the query budget (`ExecutionLimits
   .max_queries_per_source_country`, further capped by a source's own
   `SourceCapabilities.max_recommended_queries_per_request` when set).
3. If budget remains, at most one grouped fallback `PlannedQuery`
   (`mode="any_of_words"`) built from `SearchProfile.title_aliases` +
   `role_families` + top `required_skills` — only falling back to
   `CandidateProfile.title_aliases`/`role_families` when the *entire*
   `SearchProfile` (`target_titles` included) carries no query intent at
   all, so an active `SearchProfile` is never diluted by candidate history
   (CLAUDE.md hard constraint: active search intent stays primary).
4. A source whose `SourceCapabilities.keyword_search` is False, or whose
   `company_filter` is True (Greenhouse/Lever-shaped: one fetch per
   watchlisted company, no keyword queries at all — decisions.md D-041),
   gets zero `PlannedQuery`s; watchlist fan-out is a later step's concern.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from job_scout.config import ExecutionLimits
from job_scout.matching.normalize import dedupe_preserve_order, normalize_text
from job_scout.models import CandidateProfile, PlannedQuery, SearchProfile, SourceCapabilities

# Bounds how many configured required_skills feed the grouped fallback query
# — the design calls for "top required_skills", not the full list, so a long
# skills list can't quietly regrow the fallback into an unbounded word-soup
# query. Not config-driven: this is an internal shape limit on one query's
# term count, not a scoring/matching signal (CLAUDE.md hard constraint 10).
_MAX_FALLBACK_REQUIRED_SKILLS = 5

_PROVENANCE_TARGET_TITLE = "search.target_titles"
_PROVENANCE_DEGRADED_EXACT_PHRASE = "capability.exact_phrase_search:unsupported"
_PROVENANCE_SEARCH_TITLE_ALIASES = "search.title_aliases"
_PROVENANCE_SEARCH_ROLE_FAMILIES = "search.role_families"
_PROVENANCE_SEARCH_REQUIRED_SKILLS = "search.required_skills"
_PROVENANCE_CANDIDATE_TITLE_ALIASES = "candidate.title_aliases"
_PROVENANCE_CANDIDATE_ROLE_FAMILIES = "candidate.role_families"

_FALLBACK_LABEL = "fallback"


@dataclass(frozen=True)
class QueryPlanResult:
    """`build_planned_queries`'s return value: the bounded query list plus a
    human-readable trail of anything truncated, so a query-budget cap never
    silently drops configured intent (mirrors the existing country-truncation
    `diversity_notes` pattern, architecture.md section 11a)."""

    planned_queries: list[PlannedQuery] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _dedup_key(keywords: list[str]) -> frozenset[str]:
    return frozenset(normalize_text(keyword) for keyword in keywords if normalize_text(keyword))


def _readable(role_family: str) -> str:
    """`role_families` are configured as identifier-shaped strings (e.g.
    "strategy_and_planning") — render as space-separated words for use as a
    query term, per MILESTONE_2.md's "role_families (readable)" note."""
    return role_family.replace("_", " ").strip()


def _target_title_queries(
    search_profile: SearchProfile, capabilities: SourceCapabilities
) -> list[PlannedQuery]:
    titles = dedupe_preserve_order(search_profile.target_titles)
    queries: list[PlannedQuery] = []
    for title in titles:
        mode: Literal["exact_phrase", "any_of_words"]
        if capabilities.exact_phrase_search:
            mode = "exact_phrase"
            provenance = [_PROVENANCE_TARGET_TITLE]
        else:
            mode = "any_of_words"
            provenance = [_PROVENANCE_TARGET_TITLE, _PROVENANCE_DEGRADED_EXACT_PHRASE]
        queries.append(
            PlannedQuery(
                label=title,
                keywords=[title],
                mode=mode,
                provenance=provenance,
            )
        )
    return queries


def _grouped_fallback_query(
    candidate_profile: CandidateProfile, search_profile: SearchProfile
) -> PlannedQuery | None:
    search_profile_has_intent = bool(
        search_profile.target_titles
        or search_profile.title_aliases
        or search_profile.role_families
        or search_profile.required_skills
    )

    keywords: list[str] = []
    provenance: list[str] = []

    if search_profile.title_aliases:
        keywords.extend(search_profile.title_aliases)
        provenance.append(_PROVENANCE_SEARCH_TITLE_ALIASES)
    if search_profile.role_families:
        keywords.extend(_readable(rf) for rf in search_profile.role_families)
        provenance.append(_PROVENANCE_SEARCH_ROLE_FAMILIES)
    if search_profile.required_skills:
        keywords.extend(search_profile.required_skills[:_MAX_FALLBACK_REQUIRED_SKILLS])
        provenance.append(_PROVENANCE_SEARCH_REQUIRED_SKILLS)

    # CandidateProfile only ever supplements when the *active* SearchProfile
    # supplies no query intent at all (not even via target_titles) — an
    # active SearchProfile's own configured fields are never diluted by
    # candidate history, per this task's explicit "active search intent
    # stays primary" instruction.
    if not search_profile_has_intent:
        if candidate_profile.title_aliases:
            keywords.extend(candidate_profile.title_aliases)
            provenance.append(_PROVENANCE_CANDIDATE_TITLE_ALIASES)
        if candidate_profile.role_families:
            keywords.extend(_readable(rf) for rf in candidate_profile.role_families)
            provenance.append(_PROVENANCE_CANDIDATE_ROLE_FAMILIES)

    deduped = dedupe_preserve_order(keywords)
    if not deduped:
        return None
    return PlannedQuery(
        label=_FALLBACK_LABEL,
        keywords=deduped,
        mode="any_of_words",
        provenance=provenance,
    )


def build_planned_queries(
    *,
    capabilities: SourceCapabilities,
    candidate_profile: CandidateProfile,
    search_profile: SearchProfile,
    execution_limits: ExecutionLimits,
) -> QueryPlanResult:
    """Bounded, deterministic `PlannedQuery` list for one source (all of its
    supported countries share the same list — `PlannedQuery` carries no
    geography of its own; `SelectedSource.search_params`/`supported_countries`
    already do, see MILESTONE_2.md "Geography"). Calling this twice with the
    same inputs always returns the same result (no randomness, no unstable
    set ordering)."""
    if not capabilities.keyword_search or capabilities.company_filter:
        return QueryPlanResult()

    budget = execution_limits.max_queries_per_source_country
    if capabilities.max_recommended_queries_per_request is not None:
        budget = min(budget, capabilities.max_recommended_queries_per_request)
    if budget <= 0:
        return QueryPlanResult()

    candidate_target_queries = _target_title_queries(search_profile, capabilities)

    selected: list[PlannedQuery] = []
    seen_keys: set[frozenset[str]] = set()
    skipped: list[PlannedQuery] = []
    for query in candidate_target_queries:
        key = _dedup_key(query.keywords)
        if key in seen_keys:
            continue  # equivalent query already counted once; never consumes budget twice
        if len(selected) >= budget:
            skipped.append(query)
            continue
        seen_keys.add(key)
        selected.append(query)

    notes: list[str] = []
    if skipped:
        labels = ", ".join(f'"{q.label}"' for q in skipped)
        notes.append(
            f"query_budget: {len(skipped)} lower-priority target-title "
            f"quer{'y' if len(skipped) == 1 else 'ies'} skipped "
            f"(max_queries_per_source_country={budget}): {labels}"
        )

    if len(selected) < budget:
        fallback = _grouped_fallback_query(candidate_profile, search_profile)
        if fallback is not None:
            key = _dedup_key(fallback.keywords)
            if key not in seen_keys:
                selected.append(fallback)

    return QueryPlanResult(planned_queries=selected, notes=notes)
