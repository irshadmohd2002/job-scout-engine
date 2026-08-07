# Milestone 2 — Multi-Source Discovery & Sponsorship Intelligence

Status: **scope contract, not started**. This document was written before any
Milestone 2 code changed, per this project's ground rules ("Before
implementing beyond Milestone 1.1: Don't. Check `ROADMAP.md` and ask the user
first."). Milestone 1 and Milestone 1.1 are both implemented and released
(tag `v0.1.0`) — see `MILESTONE_1.md`/`MILESTONE_1_1.md` and `decisions.md`
D-001 through D-034. Baseline at the time of writing: `pytest` 288 passed / 1
skipped / 3 deselected, `ruff check .` clean, `mypy --strict src` clean.

## Goal

Make Job Scout substantially better at two things Milestone 1/1.1 left
narrow by design:

1. **Discovery breadth** — one adapter (Adzuna), driven by a single
   candidate-history-only OR-query, cannot exercise the "regional source
   intelligence" the architecture already models (§6 scoring, §7 compliance
   gate, §9 discovery-vs-collection). Milestone 2 adds a small number of
   additional compliant adapters and a real query-planning layer so what
   gets fetched actually reflects the run's configured intent.
2. **Visa/sponsorship signal strength** — `VisaAssessment` has existed in
   the schema since Milestone 1 (§2.12) but nothing in `src/job_scout/` has
   ever constructed one; visa evidence today is a regex scan folded into a
   Stage 5 `ScoreComponent`, not the richer evidence-based model the
   architecture already reserves. Milestone 2 wires `VisaAssessment` into
   the pipeline for real, adding authoritative sponsor-register evidence
   where it genuinely exists (UK, Netherlands) without inventing certainty
   where it doesn't.

Notification delivery and scheduling are explicitly **not** part of this
milestone (see "Explicitly out of scope").

## User outcome

Running `job-scout plan --profile <id>` after Milestone 2 shows: the exact
queries that will be sent to each source (not just a keyword list), an
estimated request count per source, and — for sources with real adapters —
which are executable today vs. pending a watchlist/credential/terms review.
Running `job-scout run-once` pulls from more than one compliant source when
configured, merges genuine cross-source duplicates into one job with
multiple source observations instead of showing the same vacancy twice, and
persists a real, evidence-carrying `VisaAssessment` per scored job — including
authoritative UK/NL sponsor-register corroboration when the user has
imported a register snapshot and the employer name matches. None of this
requires editing Python; it's all through existing YAML config plus two new,
narrowly-scoped config surfaces (company watchlist, sponsor-register import).

## In scope

- A deterministic query-planning layer, `SearchProfile`-driven, replacing
  the current single candidate-history OR-query (Workstream A).
- Two to three new, compliant `SourceAdapter` implementations, each gated by
  the same `ComplianceGate` as Adzuna and shipped `manual_review` by default
  in the packaged registry template (Workstream B).
- Cross-source deduplication tiers beyond Milestone 1's single-source
  fingerprinting, using deterministic evidence only — no embeddings
  (Workstream C).
- `VisaAssessment` actually constructed and persisted per scored job, with
  an evidence-precedence rule and optional authoritative sponsor-register
  corroboration for UK/NL (Workstream D).
- A repeatable, offline score-calibration tool (`job-scout evaluate`) against
  a hand-labelled fixture dataset — not a threshold change (Workstream E).
- A `company_watchlist.yaml` config surface (schema already reserved,
  `CompanyWatchlistEntry`, architecture.md §2.14) feeding the two
  watchlist-dependent adapters.
- A `sponsor_registries.yaml` config surface + `job-scout sponsors import`
  command for user-supplied, already-downloaded register snapshot files —
  never a live government-register download.
- Small, additive `SourceRegistryEntry`/`ExecutionLimits` schema growth to
  represent per-source query capabilities and bound query fan-out.
- Tests for every item above, opt-in `integration` tests for any new
  adapter that needs real credentials, no regression to the Milestone
  1/1.1 baseline.

## Explicitly out of scope

Everything Milestone 1/1.1 already excluded, plus, for this milestone
specifically:

- Email notification delivery, WhatsApp/Telegram/push channels, any
  outbound notification dispatch.
- A continuous scheduler, GitHub Actions scheduling, VPS deployment.
- A GUI or desktop installer.
- Browser automation or any scraping that bypasses auth/CAPTCHAs/robots/rate
  limits.
- LLM semantic scoring, embeddings, or a vector database — Milestone 3/4
  territory (`ROADMAP.md`), and explicitly excluded from the M2 cross-source
  dedup design (Workstream C) even as an *option*.
- Automatic CV tailoring, cover-letter generation, auto-application.
- Multi-user SaaS, authentication, cloud database, PostgreSQL.
- A plugin framework, adapter factory/dynamic loading (CLAUDE.md hard
  constraint 9; `architecture.md` §12 "What Milestone 1 deliberately does
  not add" — still true for M2's small, fixed adapter count).
- **Email-alert ingestion** (inbound parsing of forwarded Naukri/iimjobs/
  foundit/Indeed-alert/GulfTalent/Bayt emails). The pre-existing
  `ROADMAP.md` draft listed this under "Milestone 2"; this planning pass
  narrows M2 to API/feed-based adapters + sponsorship enrichment instead,
  because (a) this task's own Workstream B evaluation list names only
  API/feed/government sources, never named email-alert portals, and (b)
  inbox ingestion is a materially different capability (mailbox
  authentication, per-portal parsing heuristics) that deserves its own
  scope contract rather than riding along. **Re-sequenced to Milestone 3**
  — see `decisions.md` D-035 and the `ROADMAP.md` update below. Flagged as
  an open decision for the user in the final preparation report.
- A general, human-reviewed **source-discovery workflow** (`architecture.md`
  §9) that proposes new `SourceRegistryEntry` rows automatically. This
  planning pass performs discovery *manually* (Workstream B's source
  priority matrix below) — a repeatable discovery *tool* is deferred, same
  reasoning as email-alert ingestion.
- Live government sponsor-register downloading/scraping. The engine only
  ever parses a file the user has already downloaded themselves.
- Fuzzy/alias sponsor-name matching (e.g. subsidiary/trading-name
  resolution) — M2 does exact normalized-name matching only; see Workstream
  D risk R-9.
- Changing `notification_thresholds` (85/70) — Workstream E builds the
  *tool* to calibrate them empirically later; it does not change them now.
- A new public `ScoreComponent`/`ScoringWeights` schema field, or any
  profession-specific keyword — every M2 signal source continues to come
  from `CandidateProfile`/`SearchProfile`/`SourceRegistryEntry` config
  (CLAUDE.md hard constraint 10, unchanged from M1/1.1).

## Architecture changes

- New module `source_intelligence/query_planner.py`: builds a bounded list
  of `PlannedQuery` objects per selected source from `SearchProfile`
  (primary) and `CandidateProfile` (fallback), replacing the inline
  `keywords=list(candidate_profile.title_aliases)` construction currently in
  `source_intelligence/planner.py::build_plan` (see Query-planning design
  below).
- `pipeline.py::run_once` calls `adapter.fetch()` once per `PlannedQuery`
  for a selected source (bounded by the new `max_queries_per_source_country`
  execution limit) instead of once per source. `SourceAdapter.fetch()`'s
  Protocol signature and every existing adapter (`AdzunaAdapter`) are
  **unchanged** — the fan-out happens at the pipeline call site, not inside
  the adapter, so this is additive to `architecture.md` §3, not a breaking
  change to it.
- New adapters (`sources/greenhouse.py`, `sources/lever.py`, `sources/reed.py`
  — see Source-adapter plan) follow the exact `AdzunaAdapter` shape: pure
  HTTP-in/`RawJobRecord`-out, typed exceptions from `sources/base.py`, no
  adapter-specific logic anywhere outside its own module.
- New module `matching/visa.py`: `assess_visa(job, candidate, search,
  registry_match, country_regime) -> VisaAssessment`, called from
  `pipeline.py` for every job that reaches Stage 5 scoring, alongside
  `build_match_result`. This is new orchestration wiring, not a new stage —
  `architecture.md` §1's pipeline diagram already reserves this slot
  conceptually (visa/relocation assessment sits beside, not inside, the
  Stage 1→5 match-scoring sequence).
- New module `source_intelligence/sponsor_registry.py`: loads an imported
  sponsor-register snapshot (SQLite-backed, see Persistence implications)
  and exposes `find_sponsor_match(company_name, country) ->
  SponsorRegistryMatch | None` via employer-name normalisation (reuses
  `deduplication.normalize_company`, not a second implementation).
- New module `evaluation.py`: `run_evaluation(dataset, candidate, search,
  weights) -> EvaluationReport`, a pure function over the existing Stage
  2/5 scoring functions — no pipeline, network, or persistence involvement.
  Backs the new `job-scout evaluate` command.
- `deduplication.py` gains one new tier (cross-source exact URL) and one
  new tier (probable duplicate via bounded token-overlap), both deterministic
  — see Cross-source deduplication implications below. No new module.
- **No new abstraction beyond the above.** No adapter registry/factory (still
  a plain `if source_id == ...` dispatch in `pipeline.py`, now with three
  more branches instead of one), no `SourceObservation` model distinct from
  the existing `SourceProvenance` (see Workstream F conclusion below), no
  dependency-injection container, no plugin loader — `architecture.md` §12's
  "What Milestone 1 deliberately does not add" list still holds for M2.

## Domain-model changes

All additive; every valid Milestone 1/1.1 config continues to validate
unchanged (same compatibility contract `MILESTONE_1_1.md` used).

`models.py`:
- `PlannedQuery(label: str, keywords: list[str], mode: Literal["exact_phrase",
  "any_of_words"], provenance: list[str])` — one concrete query a source will
  actually receive, with a human-readable `provenance` list (e.g.
  `["search.target_titles"]`) so `job-scout plan` can show *why* each query
  exists, not just what it is.
- `SourceQueryCapabilities(supports_exact_phrase: bool = True,
  supports_or_terms: bool = True, supports_industry_filter: bool = False,
  max_recommended_queries_per_request: int | None = None)` — new field on
  `SourceRegistryEntry` (`query_capabilities: SourceQueryCapabilities =
  SourceQueryCapabilities()`), so the query planner can ask "can this source
  even do an exact-phrase query" rather than assuming every source behaves
  like Adzuna.
- `SelectedSource` gains `planned_queries: list[PlannedQuery]` and
  `estimated_request_count: int` (== `len(supported_countries) *
  len(planned_queries) * max_pages_per_source_country`, the same guardrail
  arithmetic `architecture.md` §11a already documents, now surfaced instead
  of implied). The existing `search_queries: list[str]` field is kept,
  unchanged in type, but now populated with each `PlannedQuery`'s rendered
  expression for human display — no breaking change to anything that reads
  it as `list[str]`.
- `CompanyWatchlistEntry` (already reserved, §2.14) gains `source_id: str`
  (which adapter this entry is for — `greenhouse_public_feeds` /
  `lever_public_postings`) and `external_company_key: str` (board token /
  company slug). `priority`/`notes` unchanged.
- `SponsorRegistryEntry(country: str, registered_name: str,
  normalized_name: str, register_name: str, license_status: str | None,
  imported_at: datetime)` — new model, one row per register entry.
- `SponsorRegistryMatch(matched: bool, registered_name: str | None,
  register_name: str | None, confidence: float)` — the lookup result
  `sponsor_registry.py::find_sponsor_match` returns; feeds
  `VisaAssessment.employer_registry_match`/`employer_registry_match_confidence`
  /`registry_source` (already-reserved fields, populated for the first time).
- `EvaluationLabel` (`StrEnum`: `strong_match | adjacent_match | weak_match
  | reject`) and `EvaluationJobFixture(job_id, title, description, company,
  location, employment_type, posted_at, label, rationale)` — new, used only
  by `evaluation.py`/`job-scout evaluate`, not by the core matching pipeline.
- **`VisaAssessment` and `VisaStatus` are unchanged** — see Sponsorship/visa
  enrichment design and `decisions.md` D-036 for why the existing six-value
  enum plus separate evidence fields (`citizenship_restrictions`,
  `existing_work_authorisation_required`, `relocation_support_evidence`) is
  kept instead of the flatter nine-value enum this task's brief sketched:
  the existing model already separates *independent* dimensions (a job can
  be both `employer_eligible` *and* citizenship-restricted; a single flat
  enum can't represent both at once) — CLAUDE.md's "use a smaller enum if
  the architecture already defines a suitable one" applies directly.

`config.py`:
- `ExecutionLimits.max_queries_per_source_country: int` — new guardrail,
  same treatment as the existing five (§11a table), positive-int validated,
  with a conservative default (e.g. `3`).
- `load_company_watchlist(path=None, *, data_dir=None) ->
  list[CompanyWatchlistEntry]` — new loader, same YAML-first pattern as
  `load_source_registry` (no packaged-template fallback — this is
  user-specific like the candidate profile, not a generic default like
  `execution_limits.yaml`).
- `load_sponsor_registries_config(...) -> list[SponsorRegisterConfig]` —
  small typed config naming which registers are enabled and where their
  imported snapshot lives (see Persistence implications); *not* the
  register data itself.

## Source-adapter plan

Three new adapters, each following `AdzunaAdapter`'s exact shape (pure
HTTP-in/`RawJobRecord`-out, `is_configured()`, typed exceptions):

1. **`sources/reed.py`** (`reed_api`, `public_api`) — Reed.co.uk's
   documented REST API (`https://www.reed.co.uk/api/1.0/search`, HTTP Basic
   auth with a free API key as username, JSON response). No watchlist
   dependency — an aggregator like Adzuna, immediately useful once a user
   has a key. Recommended **first** new adapter: fastest to real value,
   exercises the query planner (keyword-based search) without needing the
   watchlist config to land first.
2. **`sources/greenhouse.py`** (`greenhouse_public_feeds`, `public_ats_feed`)
   — Greenhouse's public job-board JSON feed
   (`https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs`), no
   auth. One fetch per watchlisted board token — does not use the query
   planner's keyword queries at all (the feed returns *all* of that
   company's open roles; title/role filtering happens at Stage 1/2 as
   normal, same as any other source). Zero watchlist entries ⇒ zero calls
   ⇒ zero jobs, by design (R-10 below).
3. **`sources/lever.py`** (`lever_public_postings`, `public_ats_feed`) —
   Lever's public postings API (`https://api.lever.co/v0/postings/{company}
   ?mode=json`), no auth. Same shape as Greenhouse, same watchlist
   dependency.

All three ship in the packaged `source_registry.example.yaml` template with
`approval_status: manual_review` (never `approved` by default — CLAUDE.md
hard constraint 1) and real `adapter_ref` values once implemented, so a user
who wants them promotes the entry in their own `source_registry.yaml`
exactly like any other registry edit — no code change required to turn one
on. See the source priority matrix (Deliverable 4) for the full evaluated
list, including sources **not** recommended for M2.

## Query-planning design

### Current state (audited)

`source_intelligence/planner.py::build_plan` builds exactly one
`SourceSearchParams` per selected source:

```python
search_params = SourceSearchParams(
    countries=supported,
    keywords=list(candidate_profile.title_aliases),   # <- CandidateProfile only
    ...
)
search_queries=list(candidate_profile.title_aliases)   # <- shown in `plan`, same list
```

`AdzunaAdapter._build_query` joins `keywords` with a single space into one
`what_or` parameter — Adzuna's own OR-of-individual-*words* semantics, not
OR-of-*phrases*. A `title_aliases` entry like `"Chief of Staff"` becomes an
OR over the tokens `chief`, `of`, `staff` individually. This is exactly the
"known next tuning item" flagged and deliberately not fixed in
`decisions.md` D-029, and is the concrete bug this milestone's Workstream A
asks to fix: `SearchProfile.target_titles`/`title_aliases`/`role_families`/
`required_skills` never inform retrieval at all today — only
`CandidateProfile.title_aliases` does, and always as one broad word-level OR.

### Alternatives considered

1. **One broad OR query** (current behaviour). Cheapest (1 call per
   source-country before pagination), but exactly the low-precision bug
   already diagnosed live (D-029's "150 jobs fetched, 1 scored" incident).
2. **One query per target title.** Most precise (an exact-phrase `what` query
   per configured title), but request count scales linearly with
   `len(target_titles) × countries × max_pages` — unbounded unless capped,
   and multi-word phrases need the source to support an AND/exact-phrase
   query at all (not every source will, per `SourceQueryCapabilities`).
3. **Grouped title families.** One query per role family, folding aliases
   within the family into that query's OR terms. Bounded by role-family
   count (typically small), but loses per-title precision — an exact
   `target_titles` match is diluted back into a family-level OR, re-creating
   a milder version of the current problem.
4. **Source-specific query batching.** Different sources get structurally
   different query plans (Adzuna: keyword queries; Greenhouse/Lever: one
   fetch per watchlisted company, no keywords at all). Not an alternative to
   1–3 so much as an orthogonal axis — M2 needs this regardless, since
   Greenhouse/Lever's query model has nothing to do with keywords.

### Recommendation (smallest approach for M2)

A bounded hybrid of #2 and #3, explicitly capped, not #1 or #3 alone:

- If `SearchProfile.target_titles` is non-empty, build one `PlannedQuery`
  per target title (`mode="exact_phrase"`, `provenance=["search.target_titles"]`),
  in the profile's configured order — this run's actual, explicit ask gets
  the most precise query type a source supports (`SourceQueryCapabilities
  .supports_exact_phrase`; falls back to `any_of_words` if the source
  doesn't support exact phrases).
- Truncate that list to `ExecutionLimits.max_queries_per_source_country`
  (same truncation pattern as the existing country cap, §11a/D-015 — preserve
  configured order, record what was skipped, never silently drop without a
  trace). This is the bound that prevents alternative #2's unbounded fan-out.
- If budget remains after `target_titles` (or none were configured), add
  **one** grouped fallback `PlannedQuery` (`mode="any_of_words"`,
  `provenance=[...]`) built from `SearchProfile.title_aliases` +
  `SearchProfile.role_families` (readable) + top
  `SearchProfile.required_skills`, falling back further to
  `CandidateProfile.title_aliases`/`role_families` only when the
  `SearchProfile` supplies nothing at all — so an existing user who has only
  ever populated `CandidateProfile` (every valid M1 config) keeps working
  exactly as today, unchanged, while a user who configures the newer
  `SearchProfile.target_titles` field gets meaningfully more precise
  retrieval without editing anything else.
- Deduplicate equivalent queries (same normalised keyword set, regardless of
  which config field contributed them) before applying the cap, so
  `target_titles` and `title_aliases` containing the same phrase don't both
  consume budget.
- `job-scout plan` prints each source's `planned_queries` (expression, mode,
  provenance) and `estimated_request_count`, so query fan-out is visible and
  auditable before any quota is spent — extending the existing "no HTTP
  request is ever made for an unsupported source-country combination"
  transparency guarantee (§11a) to cover query count too.

This never *broadens* retrieval beyond what's configured (unlike the current
single-word-OR behaviour, which effectively broadens every multi-word alias
into an OR of unrelated common words) — it only makes precise, configured
intent (`target_titles`) reachable, and keeps the existing broad fallback
for users who haven't configured it yet.

### Multi-word title handling

Handled by the `exact_phrase` mode above for sources that support it
(`SourceQueryCapabilities.supports_exact_phrase` — Adzuna's `what` parameter
is a genuine AND/phrase match, distinct from `what_or`). For a source that
doesn't support exact-phrase queries, the query planner falls back to
`any_of_words` for that specific title (never silently drops the title), and
this degradation is recorded in the `PlannedQuery`'s provenance/evidence so
`job-scout plan --json` shows exactly which sources are getting a weaker
match for a given configured title.

## Sponsorship/visa enrichment design

### Evidence precedence (never treated as equal)

1. **Authoritative employer-registry match** (UK/NL only, where imported) —
   highest confidence. A match means the employer is *licensed/recognised to
   sponsor*, not that this specific vacancy offers sponsorship (CLAUDE.md
   hard constraint 4) — sets `status = employer_eligible`, confidence ≈ 0.7
   (capped below "confirmed" — exact-normalized-name matching carries real
   false-positive risk from subsidiaries/trading names; see R-9).
2. **Explicit job-text wording** — the existing regex scan
   (`_VISA_POSITIVE_PATTERNS`/`_VISA_NEGATIVE_PATTERNS`, currently
   duplicated between `matching/scoring.py` and
   `matching/hard_filters.py`'s `_NO_SPONSORSHIP_PATTERNS` — M2 should
   consolidate these into one shared pattern set, e.g.
   `matching/visa_patterns.py`, imported by both, `hard_filters.py`,
   `scoring.py`, and the new `matching/visa.py`). Job-specific and
   unverified, but this run's actual posting — positive text raises status
   to `confirmed_yes` (job-specific, overrides a registry-derived
   `employer_eligible`); explicit negative text sets `confirmed_no`
   **regardless of a registry match** (a specific "we cannot sponsor this
   role" statement overrides a general employer-eligibility signal).
3. **Country-level structural regime** (`VisaAssessment
   .country_work_permit_regime`, already a field) — a generic, low-confidence
   prior only, never sufficient by itself to set anything other than
   `unknown`. Populated from the existing `countries.py` region lookup plus
   a small, config-driven regime label per region (not per country — keeps
   this profession-agnostic and avoids hand-maintaining 190 country entries
   for a context string, not a scored signal).
4. **`SearchProfile`/`CandidateProfile` work-authorisation fields** — not
   evidence *about the job*; unchanged Stage 1 hard-filter usage only
   (existing `requires_work_authorisation_support` behaviour in
   `hard_filters.py`). Never feeds `VisaAssessment.status`.

`assess_visa()`'s precedence: start from `unknown`; apply the registry match
(if any) to raise to `employer_eligible`; apply job-text evidence last (can
raise to `confirmed_yes` or override down to `confirmed_no`) since it's the
most specific, run-relevant signal available. `confidence` is the confidence
of whichever evidence source actually set the final `status`, not a blended
average — keeps the "why" auditable in `job_text_evidence`/
`negative_evidence`, consistent with CLAUDE.md hard constraint 5.

### Sponsor registers

Investigated per the task's minimum requirement:

- **UK — Register of licensed sponsors** (published by UK Home Office,
  updatable CSV/ODS listing organisation name, town/city, county, and route).
  Public, no auth, downloadable by any user. M2 does **not** automate the
  download (hard constraint 1 + explicit instruction) — the user downloads
  the file themselves and runs `job-scout sponsors import <file> --country
  GB --register uk_home_office_sponsor_list`.
- **Netherlands — IND Recognised Sponsors register.** Public register
  published by the Dutch immigration service (IND), similarly downloadable.
  Same import-only treatment.
- **Every other country**: no register is assumed to exist. `country_work_
  permit_regime` stays a generic structural-prior string only; `status`
  never exceeds what job-text evidence alone supports. This is a deliberate
  scope limit, not an oversight — inventing register coverage for countries
  without one would violate "don't invent certainty" and CLAUDE.md hard
  constraint 4.

### Employer-name normalisation

Reuses `deduplication.normalize_company` (already strips `Inc`/`Ltd`/`LLC`/
`PLC`/`GmbH`/`Corp`/`Co` suffixes, lowercases, strips punctuation) as the
join key for both `Job.normalized_company` and each imported
`SponsorRegistryEntry.normalized_name` — one normalisation function for both
cross-source dedup (Workstream C) and sponsor-register joining (Workstream
D), not two parallel implementations. M2 does **exact** normalized-name
matching only; no fuzzy/Levenshtein/alias matching (explicitly deferred,
R-9) — a real employer whose registry name differs meaningfully from its
job-posting display name (a subsidiary, a trading name) simply won't match,
staying `unknown` rather than guessing.

## Deduplication implications

### Current state (audited)

`deduplication.py::match_against_recent` already has three tiers
(`CROSS_SOURCE_DUPLICATE`, `REPOST`, `DISTINCT`), but Tier 1
(`find_by_fingerprint`, exact match) is keyed on `external_source_id =
"{source_id}:{external_id}"` — by construction, this **never** matches
across two different `source_id`s, so it only dedupes repeat fetches of the
*same* source. Tier 2 (`CROSS_SOURCE_DUPLICATE`) requires
`description_fingerprint` (a SHA-256 of the whole normalised description
text) to be **byte-identical** — in practice this will rarely fire across
real sources, since Adzuna truncates descriptions (`architecture.md` §3,
"Known limitation") while an ATS feed like Greenhouse returns the full text;
the wording will essentially never hash identically even for the exact same
vacancy. This is the real gap Workstream C needs to close.

### New tiers

1. **Exact duplicate, cross-source** (new): before falling to the
   company+title+location identity check, compare `canonical_url` alone
   (ignoring `external_source_id`) against the recent-jobs window. Some
   aggregators (Adzuna's `redirect_url`) resolve directly to the same
   application URL an ATS feed would also report — when the canonicalised
   URLs match exactly across two different sources, that's as strong a
   signal as Tier 1, just not scoped to one source. Requires a new
   non-unique index on `job_fingerprints.canonical_url` (see Persistence
   implications).
2. **Probable duplicate** (replaces/generalises the old
   `CROSS_SOURCE_DUPLICATE` tier's over-strict exact-hash requirement):
   same `normalized_company` + `normalized_title` + `normalized_location`
   (unchanged identity check) **and** at least one of:
   - identical `description_fingerprint` (kept — still the strongest
     available corroboration when it does happen to match), **or**
   - bounded token-set (Jaccard) similarity of the two descriptions above a
     conservative threshold (e.g. ≥ 0.6) — deterministic, explainable,
     **no embeddings** (explicit instruction), reusing the existing
     `matching.normalize.normalize_tokens` helper already used elsewhere in
     the codebase, **or**
   - `posted_date` within a small window (e.g. ±3 days) **and** matching
     `salary_min`/`salary_max` when both sources report salary.
   Company+title+location match is always a precondition, never optional —
   this bounds false-merge risk (R-8) to jobs that already look identical on
   every structured field, with the new signals only deciding *which*
   near-miss description differences still count as the same posting.
3. **Repost** — unchanged from M1 (same identity, different description,
   existing posting older than the configurable gap).
4. **Distinct job** — unchanged: fails every tier above.

### Terminology (as requested)

- **Exact duplicate**: Tier 1 (same source, same external id) or the new
  cross-source canonical-URL match — no reasonable doubt.
- **Probable duplicate**: the new Tier 2 — strong structural match plus at
  least one corroborating deterministic signal, short of certainty.
- **Distinct job**: fails both.
- **Source alias/reference**: not a new `Job` row at all — when a
  duplicate (exact or probable) is found, the *existing* canonical `Job` is
  kept and the new source's observation is recorded via
  `JobRepository.merge_provenance` (unchanged method, already exists) —
  provenance is preserved, never deleted, per the task's explicit
  preference.

## Persistence implications

Schema changes are additive `CREATE TABLE`/`CREATE INDEX IF NOT EXISTS`
statements, same pattern `sqlite_repo.py` already uses (no migration
framework, per CLAUDE.md hard constraint 9) — bump `_SCHEMA_VERSION` to `2`
so the existing `PRAGMA user_version` check (D-026) marks the point cleanly;
opening an M1.1 database with M2 code is a no-op schema upgrade (new tables/
indexes only), and opening an M2 database with older code still fails loudly
via the existing `SchemaVersionError`.

- `CREATE INDEX IF NOT EXISTS idx_job_fingerprints_canonical_url ON
  job_fingerprints(canonical_url)` — non-unique, backs the new cross-source
  exact-URL dedup tier (the existing `PRIMARY KEY (canonical_url,
  external_source_id)` already covers Tier 1's own lookup).
- `visa_assessments` table already exists (reserved since M1) — no schema
  change to the table itself, but M2 is the first milestone that actually
  calls `save_visa_assessment`. Add two plain indexed columns
  (`status TEXT`, mirroring how `match_results` already duplicates
  `notification_tier`/`final_score` alongside its JSON blob) so a future
  `job-scout` query/report command can filter by visa status without
  deserialising every row's JSON.
- New table `sponsor_registry_entries` (`country`, `registered_name`,
  `normalized_name`, `register_name`, `license_status`, `imported_at`),
  indexed on `(country, normalized_name)` for the join `sponsor_registry.py`
  performs. `job-scout sponsors import` replaces (not appends to) the rows
  for the given `(country, register_name)` pair on each import, so
  re-importing a refreshed snapshot is the documented update path — never an
  automatic re-download.
- **`company_watchlist.yaml` stays YAML-first**, like `source_registry.yaml`
  — no new SQLite table for it, consistent with D-009's "no database copy of
  YAML-first config" principle; it's small, hand-edited, user-owned data,
  not something the engine mutates at runtime.
- `source_provenance` table is **unchanged** — see Workstream F conclusion
  immediately below.

## Source observations and provenance (Workstream F)

**Conclusion: no new `SourceObservation` model.** Auditing
`SqliteJobRepository.merge_provenance` shows it already inserts a fresh
`source_provenance` row on *every* call — including repeat fetches of the
same source/external-id pair, since the in-memory `Job.source_provenance`
list dedup check only gates the JSON blob update, not the row insert
(`_insert_provenance_row` is called unconditionally). In practice, the
`source_provenance` table is already an append-only fetch-observation log:
each row already carries `source_id`, `source_url` (`raw_url`),
`fetched_at`, and `external_id`, and is already keyed to a canonical
`job_id`. That is exactly what `SourceObservation` was proposed to capture.

What's genuinely missing is *derived*, not structural: `first_seen_at`/
`last_seen_at` per `(job_id, source_id)` are `MIN`/`MAX(fetched_at)` over
existing rows — a query, not a new column. M2 adds one read method,
`JobRepository.list_provenance(job_id) -> list[SourceProvenance]` (exposing
what's already stored; nothing currently reads the full per-job provenance
history back out), and leaves the schema alone. This satisfies "preserve
evidence when one canonical vacancy is observed through several sources"
without adding the abstraction the task explicitly says to avoid unless
required — it isn't required here.

`RawJobRecord.raw_payload` is still discarded after normalisation (unchanged
from M1) — persisting raw payloads is a real but separate feature (audit/
re-normalisation tooling) that nothing in this milestone's dedup or
provenance design actually needs; **deferred**, listed in the gap table
below.

## CLI changes

- **`job-scout plan`** (existing command, extended, not a new command):
  human and `--json` output both gain `planned_queries` (expression, mode,
  provenance) and `estimated_request_count` per selected source — the
  "generated queries" / "estimated request counts" requirements are met by
  enriching the existing command, since `plan` already prints exactly this
  class of information (selected sources, unsupported countries,
  credential/compliance state) and a second command would just duplicate
  its data-loading path.
- **`job-scout sources`** (new): lists every `SourceRegistryEntry` from the
  loaded registry — id, `source_type`, `access_mode`, `approval_status`,
  `config_status`/`effective_config_status`, `adapter_ref` — independent of
  any search profile. Justified now (not in M1) because M2 quadruples the
  number of real adapter-capable entries; inspecting registry/credential
  state without picking a profile first becomes a genuine, distinct need.
  Read-only, no adapter calls, mirrors `plan`'s "never touches a source
  adapter" guarantee.
- **`job-scout sponsors import <file> --country <CC> --register
  <name>`** (new): parses a user-supplied, already-downloaded register
  snapshot file (CSV) into `sponsor_registry_entries`, replacing prior rows
  for that `(country, register_name)`. Never fetches anything itself.
- **`job-scout evaluate --dataset <path> --candidate-profile <ref>
  --search-profile <id>`** (new): runs Stage 2/5 scoring against a labelled
  fixture dataset (no adapters, no network, no persistence) and prints
  precision@10/@20, recall of labelled strong matches, false-positive rate,
  and a tier-vs-label cross-tab. Backs Workstream E.
- **`job-scout run-once`** — CLI surface unchanged (`--profile`, `--dry-run`,
  etc.); internally now issues multiple `fetch()` calls per source (bounded
  by `max_queries_per_source_country`) and persists a `VisaAssessment`
  alongside each `MatchResult`.

No other commands are added — `job-scout plan --profile <id>` and
`job-scout run-once --profile <id>` already exist and are unchanged in
shape; adding either again would be scope creep against this milestone's
own "keep the CLI small" instruction.

## Configuration changes

- `execution_limits.yaml`: `max_queries_per_source_country` (new,
  positive int, conservative default).
- `source_registry.yaml`: `query_capabilities` (new, optional, defaults to
  Adzuna-equivalent capabilities so every existing entry keeps working
  unchanged); three new real entries (`reed_api`, `greenhouse_public_feeds`
  with a real `adapter_ref`, `lever_public_postings` with a real
  `adapter_ref`), all `manual_review` by default.
- `company_watchlist.yaml` (new template): empty/illustrative list of
  `CompanyWatchlistEntry` rows, generic placeholder company names only
  (CLAUDE.md hard constraint 8 — no real employer names in a tracked
  template).
- `sponsor_registries.yaml` (new template): which registers are enabled
  (`GB`/`uk_home_office_sponsor_list`, `NL`/`ind_recognised_sponsors`) and
  where each snapshot is expected to live in the user's data directory —
  metadata only, never the register data itself.
- `scoring_weights.yaml`: **unchanged** — `visa_relocation`'s existing
  weight/component slot gets better evidence, not a new slot.
- Evaluation datasets are supplied via `--dataset <path>` on `job-scout
  evaluate`, not a new top-level YAML config surface — avoids the "YAML as
  unstructured dumping ground" risk the task explicitly warns about; a
  labelled dataset is closer to a test fixture than a runtime setting.

`job-scout init` copies the two new templates (`company_watchlist.yaml`,
`sponsor_registries.yaml`) alongside the existing six, same
never-overwrite/idempotent behaviour (`bootstrap.py::run_init`).

## Testing strategy

Every new behaviour gets unit coverage mirroring the existing suite's style
(fixtures, `respx` for HTTP, no real network in the default run):

- **Query generation**: per-alternative unit tests for the query planner
  (target-titles-present, target-titles-absent-fallback-to-candidate,
  dedup-of-equivalent-queries, truncation-at-`max_queries_per_source_country`
  with a recorded skip note, source-without-exact-phrase-support degrades to
  `any_of_words`).
- **API-call budgeting**: a fixture adapter counting `fetch()` invocations
  asserts total calls stay at `countries × queries × pages`, never more,
  across a broad profile — same style as the existing
  `test_fetch_never_exceeds_max_pages`.
- **Source capability differences**: planner tests with a mixed registry
  fixture (one entry supporting exact-phrase, one not) assert each gets the
  query mode its `query_capabilities` allows.
- **Adapter normalisation, pagination, timeouts/rate limits**: one test
  module per new adapter (`test_reed_adapter.py`,
  `test_greenhouse_adapter.py`, `test_lever_adapter.py`), same rigor as
  `test_adzuna_adapter.py` — `respx`-mocked pages, auth failure, rate-limit,
  never-fires-without-`is_configured()`.
- **Cross-source duplicates**: fixtures for exact-canonical-url match,
  probable-duplicate (Jaccard above/below threshold, posted-date-window),
  and explicit negative cases (same company, different location; similar
  title, different role) to guard against false merges.
- **Source provenance**: `list_provenance` returns every observation row in
  fetch order; `first_seen_at`/`last_seen_at` derivation tested against a
  multi-row fixture.
- **Sponsor-name normalisation / register matches / false matches**: exact
  match, case/punctuation-insensitive match, a deliberate near-miss
  (subsidiary-style name) that must **not** match, empty-register-returns-
  unknown.
- **Visa evidence precedence**: registry-only, job-text-only (positive and
  negative), both-agreeing, both-conflicting (explicit negative text must
  win over a registry match), neither (stays `unknown`).
- **Multi-source pipeline**: an end-to-end `run_once` test with two fixture
  adapters (Adzuna-shaped + a second source) whose fixtures represent the
  same real vacancy, asserting one canonical `Job` with two provenance
  entries reaches the results list, not two.
- **No regression to M1/1.1 scoring**: the full existing `test_scoring.py`/
  `test_prefilter.py`/`test_planner.py` suites must keep passing unmodified
  — any assertion that would need to change is a red flag for this
  milestone, since Workstream E explicitly says "do not change current
  thresholds," and the query-planner change must not alter Stage 2/5
  scoring formulas at all (it only changes what gets *fetched*).
- **No secrets in logs**: extend the existing pattern (Adzuna's adapter
  never puts query params in error messages) to Reed/Greenhouse/Lever —
  Reed's API key travels via HTTP Basic auth, so the same "never log the
  request itself" discipline applies there too.
- **CWD-independent installed use**: extend `test_cwd_independence.py`'s
  pattern to the two new config templates and the sponsor-registry SQLite
  table (opened via `AppPaths`, not a relative path).
- Any new adapter needing real credentials gets one opt-in `integration`
  -marked test (skipped by default), same as `test_adzuna_integration.py`.

## Compliance requirements

- Every new adapter call goes through `ComplianceGate.authorize()`
  immediately before the HTTP call, exactly like Adzuna today — no new gate
  logic needed, since Reed/Greenhouse/Lever are all `public_api`/
  `public_ats_feed`, both already in `AUTO_EXECUTABLE_ACCESS_MODES`.
  `compliance.py` itself needs **no code change**.
- All three new registry entries ship `approval_status: manual_review` in
  the packaged template — never `approved` by default (CLAUDE.md hard
  constraint 1). A user promotes an entry in their own
  `source_registry.yaml` after confirming current terms themselves; this
  planning document's own terms-status claims are marked `requires
  verification` where evidence is insufficient (see the source priority
  matrix) and must not be read as a completed terms review.
- Sponsor-register import is a local file parse of a file the user already
  downloaded — never a live scrape/download of a government site (hard
  constraint 1, and the task's explicit instruction not to implement live
  register downloading in this milestone at all).
- No credential value, query string, or response body may appear in any
  raised exception message or persisted log for the new adapters — same
  rule `AdzunaAdapter._get_page` already follows.

## Acceptance criteria

- [ ] `job-scout plan --profile <id>` shows `planned_queries` and
      `estimated_request_count` per selected source, and the total request
      count for a broad profile stays bounded by
      `max_queries_per_source_country × max_countries_per_run ×
      max_pages_per_source_country` — never unbounded.
- [ ] At least two new adapters (Reed + one of Greenhouse/Lever, minimum)
      pass a full `respx`-mocked test suite mirroring `test_adzuna_adapter
      .py`'s rigor, and are wired into `pipeline.py`'s normalizer/adapter
      dispatch.
- [ ] A synthetic two-source fixture representing the same real vacancy
      merges into one canonical `Job` with two `source_provenance` rows,
      not two `Job` rows.
- [ ] `VisaAssessment` is constructed and persisted
      (`repository.save_visa_assessment`) for every job that reaches Stage 5
      scoring; a fixture UK-register import + a job from a listed employer
      yields `employer_eligible`; explicit no-sponsorship job text overrides
      a registry match to `confirmed_no`.
- [ ] `job-scout sponsors import` round-trips a fixture CSV into
      `sponsor_registry_entries` and is queryable via
      `sponsor_registry.find_sponsor_match`.
- [ ] `job-scout evaluate` runs against a fixture labelled dataset and
      prints deterministic, hand-verifiable precision@10/@20/recall/
      false-positive-rate/tier-cross-tab numbers.
- [ ] Full `pytest` suite (existing 288 + all new tests) passes; `ruff
      check .` clean; `mypy --strict src` clean — same bar as the M1/1.1
      baseline, no regression.
- [ ] `notification_thresholds` (85/70) and every existing Stage 5 scoring
      formula are byte-for-byte unchanged by this milestone.

## Risks

- **R-7 (query fan-out)**: multiple `target_titles` could still multiply API
  calls faster than a user expects. Mitigated by
  `max_queries_per_source_country` (hard cap, config-visible) plus the same
  truncation-with-recorded-note pattern already used for country truncation
  (D-015).
- **R-8 (cross-source false merges)**: a too-loose probable-duplicate tier
  could merge genuinely different jobs (same company, same title, different
  team) into one canonical `Job`, silently hiding a real opportunity.
  Mitigated by requiring the full company+title+location identity match as
  a precondition for every probable-duplicate signal, never Jaccard/date/
  salary alone.
- **R-9 (sponsor false matches)**: exact-normalized-name matching can still
  collide (a subsidiary, a differently-branded trading name) or miss a
  genuine sponsor whose registry name differs from its job-posting display
  name. Mitigated by capping registry-match confidence below "confirmed"
  and explicitly deferring fuzzy/alias matching rather than guessing.
- **R-10 (watchlist-dependent adapters start empty)**: Greenhouse/Lever
  contribute zero jobs until the user populates `company_watchlist.yaml`.
  Mitigated by sequencing Reed (no watchlist dependency) first for
  immediate value, and documenting the watchlist requirement clearly in
  `job-scout init`'s next-steps output.
- **R-11 (small evaluation samples)**: a hand-labelled dataset of a few
  dozen jobs per profession isn't statistically robust. Documented as a
  directional signal for now, not a formal metric, until the dataset grows
  from real usage — consistent with not changing thresholds this milestone.
- **R-12 (Adzuna query-construction change is a live-scoring-adjacent risk)**:
  even though the query planner only changes *retrieval*, not *scoring*,
  changing what gets fetched changes what a live run's results look like.
  Mitigated the same way D-029/D-032/D-033/D-034 were: validate against a
  live (or live-shaped fixture) run before considering this workstream done,
  same rigor as the prior scoring-calibration fixes.

## Rollback/safety considerations

- Every new adapter defaults to `manual_review` in the packaged template — a
  fresh `job-scout init` (or an existing user who hasn't edited their
  registry) sees **zero** behavioural change until they explicitly promote
  an entry. This is the primary safety net for the whole milestone.
- The query-planner change to Adzuna's existing retrieval is the one
  modification to already-shipped behaviour. No feature flag (CLAUDE.md:
  don't use feature flags/backwards-compat shims) — the rollback story is
  the same one already used for the Stage 5 scoring-calibration ADRs
  (D-029/D-032/D-033/D-034): validate against a live-shaped fixture before
  merging, and a straightforward `git revert` if a live run afterward shows
  a regression.
- The schema-version bump (`_SCHEMA_VERSION = 2`) is purely additive
  (`CREATE TABLE`/`CREATE INDEX IF NOT EXISTS`) — an M1.1 database opens
  under M2 code with no data loss; an M2 database opened by pre-M2 code
  fails loudly via the existing `SchemaVersionError` rather than silently
  misreading new tables.
- Sponsor-register import is explicit, user-triggered, and replaces (not
  appends to) the prior snapshot for that `(country, register_name)` — never
  automatic, never partial.

## Definition of done

- `MILESTONE_2.md` (this document) and the `ROADMAP.md`/`decisions.md`
  updates below are merged before any Milestone 2 code is written, per this
  project's ground rules.
- Every acceptance-criteria checkbox above is checked.
- `decisions.md` carries a new ADR for every non-obvious M2 design choice
  actually implemented (query-planner shape, dedup tier design, visa
  precedence rule, sponsor-registry scope, the email-alert-ingestion
  re-sequencing) — same discipline as D-017 through D-034.
- No Milestone 3+ item (semantic similarity, LLM extraction, notification
  delivery, scheduling) was started under cover of this milestone.
- The full baseline (`pytest`, `ruff check .`, `mypy --strict src`) is green
  at the same bar this document's own preparation pass captured.

---

## Deliverable 3: Architecture gap audit

| Requirement | Current capability | Gap | M2 action | Deferred |
|---|---|---|---|---|
| Query planning | Single `CandidateProfile.title_aliases` OR-of-words query, one per source (D-029's known limitation) | `SearchProfile` signals never inform retrieval; no per-title precision; no visibility into generated queries | New `query_planner.py`; `PlannedQuery`/`SourceQueryCapabilities`; `plan` shows queries + estimated request counts | Per-source adaptive query tuning from historical hit-rate (needs `SourcePerformance`, M6) |
| Multi-source adapters | One adapter (Adzuna) | No adapter diversity to exercise source-selection scoring/diversity rule in practice | Reed, Greenhouse, Lever adapters + watchlist config | Every other evaluated source (UK Find a Job, EURES, Canada Job Bank, SEEK, Indeed alerts) — insufficient confirmed access or terms clarity (see priority matrix) |
| Cross-source deduplication | Tier 1 exact (per-source only); Tier 2 requires byte-identical description hash (rarely fires cross-source) | No practical cross-source merge path given real description differences (e.g. Adzuna truncation) | New exact-cross-source-URL tier + bounded Jaccard "probable duplicate" tier, no embeddings | Fuzzy company-name-only dedup (too weak a signal alone); near-duplicate SimHash (architecture.md §8 already earmarks this as a future swap, not needed yet) |
| Sponsorship enrichment | `VisaAssessment` schema exists, **never constructed** anywhere in `src/` (D-006's M1 exclusion never revisited) | No real visa signal beyond a Stage 5 regex `ScoreComponent`; no registry corroboration | `matching/visa.py::assess_visa`, wired into pipeline; UK/NL sponsor-register import + join | Fuzzy/alias sponsor-name matching; registers for countries without a public one; live register downloading |
| Source provenance | `source_provenance` table already an append-only observation log in practice (unintentionally) | No read path exposing full per-job observation history; no first/last-seen derivation | `JobRepository.list_provenance()`; document the existing append-only behaviour as intentional | Raw-payload persistence (not needed for the dedup/provenance design M2 actually uses) |
| Threshold calibration | Fixed thresholds (85/70), never empirically validated (architecture.md §10 explicitly flags this) | No tooling to measure precision/recall against labelled data | `job-scout evaluate` + fixture labelled dataset + metrics | Actually changing the thresholds (needs a larger, real-usage-derived dataset first) |
| Compliance | Gate + rule table already exhaustive and adapter-count-agnostic | None — gate needs no change for 3 more `public_api`/`public_ats_feed` sources | None (verified no code change needed) | Terms review for UK Find a Job/EURES/Canada Job Bank/SEEK (human action, not code) |
| Configuration | 6 templates, YAML-first, `job-scout init` | No config surface for watchlists (schema reserved but unused) or sponsor registers | 2 new templates (`company_watchlist.yaml`, `sponsor_registries.yaml`) + loaders | A structured per-profile threshold-override config (Workstream E's future consumer, not needed until thresholds are actually recalibrated) |
| Persistence | SQLite, schema v1, `visa_assessments`/watchlist tables reserved but write-path unused | Missing canonical-URL index; sponsor register has no table at all | Schema v2: 1 new index, 1 new table (`sponsor_registry_entries`), 2 new indexed columns on `visa_assessments` | Raw-payload storage; a `SourceRegistryRepository` for runtime registry mutation (still explicitly deferred per D-009) |
| CLI | `init`, `version`, `plan`, `run-once` | No registry-only view; no sponsor-import path; no calibration tool | `sources`, `sponsors import`, `evaluate` (3 new, minimal) | `doctor`, dashboards, any interactive prompt-based command |
| Testing | 288 tests, mocked HTTP, one opt-in integration test | No coverage for multi-adapter fan-out, cross-source dedup, visa precedence, sponsor matching | Full new test modules per the Testing strategy section above | Load/performance testing (not a concern at this milestone's volumes) |
| Distribution | Wheel/sdist install works outside the repo, `AppPaths`-resolved | 2 new templates + 1 new table must also resolve via `AppPaths`, not CWD | Extend `test_cwd_independence.py`/`test_packaging_smoke.py` coverage to the new surfaces | Standalone executable / installer (still explicitly out of scope, D-020) |

## Deliverable 4: Source priority matrix

| Source | Regions | Access method | Public/official interface | Compliance status | Implementation effort | Expected value | Recommendation for M2 | Reason |
|---|---|---|---|---|---|---|---|---|
| Adzuna | GB, DE, NL, SG, CA, AU, US (per current registry; R-1 coverage still unverified beyond D-028's live GB confirmation) | `public_api` | Yes — documented REST API, verified D-016/D-031 | `approved`, `reviewed_ok` | Already implemented | High | **Keep as-is**; benefits from the query planner automatically | Already the only real adapter; no change needed beyond retrieval-query quality |
| Reed (UK) | GB | `public_api` | Yes — documented REST API, free API key, HTTP Basic auth | Terms not yet reviewed by this project — `requires verification` before promoting to `approved` in a real registry | Low (mirrors Adzuna's shape closely) | High (immediate value, no watchlist dependency) | **Build for M2** | Fastest path to real multi-source value; exercises the query planner without needing watchlist config first |
| Greenhouse public job-board feeds | Global (per watchlisted company) | `public_ats_feed` | Yes — documented public JSON feed, no auth | Terms not yet reviewed — `requires verification`; existing registry entry already marks `terms_compliance_status: reviewed_ok` from M1 authoring, should be re-confirmed before promoting | Low-medium (needs watchlist config first) | High for watchlisted companies, zero otherwise | **Build for M2** | Schema (`CompanyWatchlistEntry`) already reserved for exactly this; ROADMAP.md's original M2 draft already named it |
| Lever public postings | GB, DE, NL, IE, US, CA (per current registry) | `public_ats_feed` | Yes — documented public JSON API, no auth | Same as Greenhouse — `requires verification` for a fresh terms confirmation | Low-medium (same watchlist dependency) | Medium-high for watchlisted companies | **Build for M2** (third pick, or defer to immediately-following work if only 2 are wanted for the first cut) | Same shape as Greenhouse; low marginal effort once the watchlist loader exists |
| UK Find a Job | GB | `permitted_html` (declared) | Not confirmed — no evidence of a public API found in this repository or this planning pass | `requires verification` | Unknown until confirmed | Medium | **Not recommended for M2** | No confirmed programmatic interface; would require either an undocumented API or scraping, neither acceptable without a real terms review |
| EURES | Europe | `rss_or_sitemap` (declared) | Plausible (EU institutions commonly publish open data) but **not confirmed** in this pass | `requires verification` | Unknown until confirmed | Medium | **Not recommended for M2** | No live, verified feed/API contract established — same evidence bar as D-016/D-027 requires before building against it |
| Canada Job Bank | CA | `rss_or_sitemap` (declared) | Plausible (Canadian government open-data initiatives exist) but **not confirmed** in this pass | `requires verification` | Unknown until confirmed | Medium | **Not recommended for M2** | Same — no confirmed contract; candidate for a future terms-review pass |
| SEEK (ANZ) | AU, NZ | `search_discovery` (declared) | No — SEEK has no current public jobs API; `search_discovery` is never auto-executable regardless of `approval_status` (D-010) | `unclear` | High/blocked by design | Medium | **Not recommended for M2** | Compliance gate already permanently blocks this access mode (R-5); no confirmed alternative access method exists |
| Indeed country alerts | GB, AE, SG, CA, AU, IN | `email_alert` (declared) | Publisher/XML API terms explicitly unclear per existing registry entry | `requires_authorisation`, `unclear` | Unknown | High (if terms clear) | **Not recommended for M2** | Terms review explicitly flagged as unresolved in the existing template; email-alert ingestion itself is out of scope for this milestone (see re-sequencing) |
| Example portal-alert placeholders (`example_job_portal_alerts`, `example_niche_portal_alerts`, `example_regional_portal_alerts`) | Illustrative only | `email_alert` | N/A — these are generic template placeholders, not real sources | N/A | N/A | N/A | **No action** | Not real sources; exist only to demonstrate the schema, per the template's own header comment |
| Workday (reverse-engineered) | Global (declared) | `disabled` | No — explicitly not a public interface | `blocked`, `prohibited` | N/A | N/A | **No action — negative example only** | Kept deliberately as the compliance gate's own negative-example fixture; must never be built |

## Deliverable 5: M2 implementation sequence

1. **Domain-model and config scaffolding.** `PlannedQuery`,
   `SourceQueryCapabilities`, extended `CompanyWatchlistEntry`,
   `SponsorRegistryEntry`/`SponsorRegistryMatch`, `EvaluationLabel`/
   `EvaluationJobFixture`; `ExecutionLimits.max_queries_per_source_country`;
   `sqlite_repo.py` schema bump to v2 (new index, new table, new visa columns)
   with no behavioural change yet. **Files**: `models.py`, `config.py`,
   `repository/sqlite_repo.py`. **Tests**: model validation round-trips,
   config loader tests, a schema-migration test opening a v1-stamped fixture
   database under v2 code and asserting the new objects appear without data
   loss. **Acceptance**: full existing suite still green; new models/config
   importable and validated in isolation. **Dependency**: none.

2. **Query planner.** New `source_intelligence/query_planner.py`
   implementing the bounded hybrid design above; `planner.py::build_plan`
   populates `SelectedSource.planned_queries`/`estimated_request_count`;
   `cli.py::_format_plan_human` (and `--json` output) show them. **Files**:
   new `query_planner.py`, `planner.py`, `cli.py`, `models.py`. **Tests**:
   query-planner unit tests (all branches from the Query-planning design
   section), planner integration test, `test_cli_plan.py` output test.
   **Acceptance**: `job-scout plan` shows queries and a request-count
   estimate that matches a hand-computed bound. **Dependency**: step 1.

3. **Wire planned queries into execution.** `pipeline.py::run_once` calls
   `adapter.fetch()` once per `PlannedQuery`, aggregating raw records before
   normalisation/dedup (unchanged downstream). `AdzunaAdapter` itself is
   **not** modified. **Files**: `pipeline.py`. **Tests**: a fixture adapter
   counting `fetch()` calls, asserting the count equals
   `len(planned_queries)` per source and stays within the guardrail bound
   across a broad profile. **Acceptance**: existing Adzuna-only tests still
   pass with the new call pattern (1 query ⇒ 1 call, same as before for any
   config that hasn't set `target_titles`). **Dependency**: step 2.

4. **First new adapter: Reed.** `sources/reed.py`, registry template entry
   (`manual_review`), pipeline normalizer + `_default_adapter_factory`
   branch. **Files**: new `sources/reed.py`, `pipeline.py`,
   `source_registry.example.yaml`. **Tests**: `respx`-mocked adapter tests
   mirroring `test_adzuna_adapter.py`; normalisation fixture test; one
   opt-in `integration`-marked test. **Acceptance**: a fixture-driven
   `run-once` pulls from both Adzuna and Reed fixtures in one run.
   **Dependency**: step 3 (Reed uses the query planner's keyword queries).

5. **Company watchlist config.** `load_company_watchlist`,
   `company_watchlist.example.yaml` template, `job-scout init` wiring.
   **Files**: `config.py`, new template, `bootstrap.py`. **Tests**: loader
   tests, template validation, `test_init.py` extension. **Acceptance**:
   `job-scout init` creates the new template; an empty watchlist validates
   cleanly (zero entries is a valid, common state). **Dependency**: step 1.

6. **Greenhouse adapter.** `sources/greenhouse.py`, one fetch per
   watchlisted board token, registry template entry updated with a real
   `adapter_ref`. **Files**: new `sources/greenhouse.py`, `pipeline.py`,
   template. **Tests**: `respx`-mocked pagination/rate-limit tests,
   normalisation fixture, empty-watchlist-produces-zero-calls test.
   **Acceptance**: mirrors Adzuna/Reed's test rigor. **Dependency**: step 5.

7. **Lever adapter.** `sources/lever.py`, same shape as Greenhouse. **Files/
   Tests/Acceptance**: mirror step 6. **Dependency**: step 5 (can run in
   parallel with step 6 — no shared code beyond the watchlist loader).

8. **Cross-source deduplication tiers.** `deduplication.py` gains the
   exact-cross-source-URL tier and the bounded-Jaccard probable-duplicate
   tier; `sqlite_repo.py` gains the canonical-URL index and (if not already
   done in step 1) the lookup it backs. **Files**: `deduplication.py`,
   `repository/sqlite_repo.py`, `pipeline.py` (wiring the new tier into the
   existing dedup call site). **Tests**: unit tests per new tier, including
   explicit false-merge-guard negative cases; an end-to-end multi-source
   pipeline test (step 4/6/7's fixtures) asserting one canonical `Job` with
   multiple provenance rows. **Acceptance**: the multi-source pipeline test
   passes; every existing Tier 1–3 test still passes unmodified.
   **Dependency**: steps 4, 6, 7 (needs ≥2 real source shapes to test
   meaningfully, though the tier logic itself only depends on step 1's
   schema work).

9. **Sponsor-registry model + visa evidence enrichment.**
   `SponsorRegistryEntry` persistence + `sponsor_registry.py::
   find_sponsor_match`; `job-scout sponsors import`; new `matching/visa.py::
   assess_visa` (consolidating the duplicated positive/negative regex
   patterns out of `scoring.py`/`hard_filters.py` into a shared
   `matching/visa_patterns.py`); pipeline wiring to construct and persist a
   `VisaAssessment` per scored job. **Files**: `models.py` (if not done in
   step 1), `repository/sqlite_repo.py`, `source_intelligence/
   sponsor_registry.py`, `matching/visa.py`, `matching/visa_patterns.py`,
   `matching/scoring.py`/`matching/hard_filters.py` (import from the shared
   module instead of their own copies), `cli.py`, `pipeline.py`. **Tests**:
   sponsor-name normalisation/join tests (true positive, deliberate
   near-miss false-positive guard), visa-precedence tests (all five
   evidence-combination cases from the Testing strategy section), a
   pipeline integration test asserting `save_visa_assessment` is actually
   called. **Acceptance**: the two fixture scenarios in this document's
   Acceptance criteria (registry match ⇒ `employer_eligible`; explicit
   negative text overrides a registry match) both pass. **Dependency**:
   step 1 only — independent of adapters/dedup, can run in parallel with
   steps 4–8.

10. **Evaluation dataset + calibration tooling.** `evaluation.py`, a small
    fixture labelled dataset (illustrative, generic — no real personal
    data), `job-scout evaluate` command. **Files**: new `evaluation.py`,
    `cli.py`, fixture dataset file(s) under `tests/fixtures/evaluation/`.
    **Tests**: metric-arithmetic unit tests against a hand-computed fixture
    (precision@k, recall, false-positive rate, tier cross-tab all verified
    by hand for a small known dataset). **Acceptance**: `job-scout evaluate
    --dataset <fixture>` prints numbers matching the hand-computed
    expectations exactly. **Dependency**: none beyond the existing Stage
    2/5 scoring functions — can run fully in parallel with everything else.

11. **End-to-end acceptance.** Full `pytest`/`ruff check .`/`mypy --strict
    src` green; a live (or live-shaped) smoke run of `job-scout plan`/
    `run-once --dry-run` exercising Adzuna + Reed together; every
    `MILESTONE_2.md` acceptance-criteria checkbox verified;
    `ROADMAP.md`/`decisions.md` confirmed consistent with what was actually
    built (not just what was planned, per this repo's own documentation
    discipline). **Dependency**: all prior steps.
