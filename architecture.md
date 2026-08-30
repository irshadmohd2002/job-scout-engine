# Job Scout Engine — Architecture

Status: **Milestone 1 and Milestone 1.1 implemented** against this contract
(see `MILESTONE_1.md`/`MILESTONE_1_1.md` for scope/acceptance criteria and
`decisions.md` for the reasoning behind choices made here, including D-013
through D-016 (Milestone 1 corrections) and D-017 through D-026 (Milestone
1.1: profession-agnostic scoring/config and locally distributable paths,
templates, and `init`) — see §15 for the Milestone 1.1 additions
specifically).

## 1. System shape

Job Scout Engine is a deterministic job-monitoring and matching pipeline with an
optional LLM enrichment stage. It is not a browser agent: every external
interaction goes through an explicit, typed adapter behind a compliance gate.

```
CandidateProfile (YAML) ─┐
SearchProfile (YAML)     ├─▶ SourceIntelligence.Planner ─▶ SearchExecutionPlan
SourceRegistry (YAML)    ┘                                        │
                                                                   ▼
                                                        ComplianceGate.authorize()
                                                                   │
                                                        (approved sources only)
                                                                   ▼
                                                          SourceAdapter.fetch()
                                                                   │
                                                                   ▼
                                                        Normalisation → Job
                                                                   │
                                                                   ▼
                                                          Deduplication (fingerprint)
                                                                   │
                                                                   ▼
                                                Matching: Stage 1 → 2 → (3) → (4) → 5
                                                                   │
                                                                   ▼
                                                          Repository (SQLite, M1)
                                                                   │
                                                                   ▼
                                          CLI console output (M1) / Notifications (later)
```

Stages 3 (semantic similarity) and 4 (LLM extraction) are architected for now
(interfaces exist, data model has slots for their output) but not implemented
until later milestones. Milestone 1 runs Stage 1 → 2 → 5, where Stage 5 uses
deterministic proxies in place of semantic/LLM signal.

## 2. Core domain models

All models are Pydantic v2 `BaseModel`s, typed, immutable where practical
(`model_config = {"frozen": True}` for value objects like `Location` and
`JobFingerprint`). Enums are Python `StrEnum`. This section defines fields and
intent; exact module layout is in §12.

### 2.1 `Location`
```
country: str            # ISO 3166-1 alpha-2
region: str | None       # free-form region tag, e.g. "middle_east", "europe"
city: str | None
```

### 2.2 `CandidateProfile`
Loaded from `src/job_scout/resources/templates/candidate_profile.example.yaml` (or a user copy). Fields:
`candidate_id`, `years_experience`, `current_location`, `nationality`,
`seniority_level` (enum: `associate|manager|senior_manager|associate_director|director`),
`education: list[Education]`, `employment_history_summary: list[str]`,
`role_families: list[str]`, `title_aliases: list[str]`, `primary_skills: list[str]`,
`secondary_skills: list[str]`, `target_countries: list[str]`, `target_regions: list[str]`,
`requires_work_authorisation_support: bool`, `open_to_relocation: bool`,
`excluded_industries: list[str]`, `excluded_role_families: list[str]`,
`notification_thresholds: NotificationThresholds`.

`role_families` and `title_aliases` are deliberately separate: role families are
canonical internal ids used for scoring/registry role-coverage matching; title
aliases are surface strings used by the Stage 2 keyword pre-filter.

### 2.3 `SearchProfile`
Loaded from `src/job_scout/resources/templates/search_profiles.example.yaml`, selected by `--profile` on
the CLI. Narrows a `CandidateProfile` for one run: `profile_id`,
`candidate_profile_ref`, `included_countries/excluded_countries`,
`included_cities/excluded_cities`, `role_families` (empty = inherit candidate's),
`employment_types`, `min_experience_years/max_experience_years`,
`required_languages`, `mandatory_qualifications`, `security_clearance_allowed`,
`reject_on_explicit_no_sponsorship`, `notification_thresholds` (override),
`polling_frequency_minutes`.

### 2.4 `Job` (normalised)
`job_id` (internal UUID4), `external_ids: list[SourceExternalId]`, `title`,
`normalized_title`, `company`, `normalized_company`, `location: Location`,
`remote_type` (enum: `onsite|hybrid|remote|unknown`), `employment_type: str | None`,
`description_raw: str`, `description_text: str` (HTML-stripped, whitespace-normalised),
`posted_at: datetime | None`, `collected_at: datetime`,
`salary_min/salary_max/salary_currency: optional`,
`source_provenance: list[SourceProvenance]`, `fingerprint: JobFingerprint`,
`role_family_hints: list[str]` (Stage 2 output, cached on the job record).

### 2.5 `SourceProvenance`
`source_id`, `access_mode`, `fetched_at`, `raw_url`, `external_id`. A job can have
more than one provenance entry once deduplication merges cross-source duplicates
— this is the audit trail answering "which sources surfaced this job."

### 2.6 `JobFingerprint`
See §8 (Deduplication) for the algorithm. Fields: `canonical_url`,
`external_source_id`, `normalized_company`, `normalized_title`,
`normalized_location`, `description_fingerprint`, `posted_date: date | None`.

### 2.7 `SourceRegistryEntry`
See `src/job_scout/resources/templates/source_registry.example.yaml` for worked examples across every
enum value. Fields: `source_id`, `name`, `source_type` (enum:
`aggregator_api|ats_feed|government|company_career_page|job_portal|recruitment_agency`),
`geographic_coverage: list[str]`, `role_coverage: list[str]`,
`access_mode: AccessMode`, `approval_status: ApprovalStatus`,
`terms_compliance_status` (enum: `reviewed_ok|unclear|prohibited`),
`auth_required: bool`, `technical_feasibility` (enum: `high|medium|low|unknown`),
`expected_value` (enum: `high|medium|low`), `priority: int`,
`polling_frequency_minutes: int | None`, `config_status` (enum:
`configured|needs_credentials|needs_setup|not_configured`),
`required_setup_actions: list[str]`, `adapter_ref: str | None`,
`reliability_score: float | None`, `historical_match_count: int | None`,
`duplicate_rate: float | None`, `last_successful_run: datetime | None`,
`capabilities: SourceCapabilities` (Milestone 2 addition — see §16.1).

### 2.8 `AccessMode` (`StrEnum`)
`public_api | public_ats_feed | rss_or_sitemap | email_alert | permitted_html |
search_discovery | manual_only | disabled`

### 2.9 `ApprovalStatus` (`StrEnum`)
`approved | alert_only | requires_authorisation | manual_review | blocked | deprecated`

### 2.10 `SearchExecutionPlan`
Output of `source_intelligence.planner`. Fields:
`plan_id`, `search_profile_ref`, `generated_at`,
`selected_sources: list[SelectedSource]`, `excluded_sources: list[ExcludedSource]`,
`region_country_coverage: dict[str, list[str]]`, `diversity_notes: list[str]`.

`SelectedSource`: `source_id`, `score: float`, `score_breakdown: dict[str, float]`,
`reasons_selected: list[str]`, `access_mode`, `approval_status`,
`search_params: SourceSearchParams`, `search_queries: list[str]`,
`polling_frequency_minutes`, `config_status`, `effective_config_status`,
`required_setup_actions: list[str]`,
`region_country_coverage: list[str]`, `priority: int`,
`executable: bool` (compliance-gate result — see §7; a source can be *selected*
for its relevance and still be `executable=False`, surfacing as a "needs setup"
line rather than being silently dropped), `supported_countries: list[str]`
(the subset of the request this source's `geographic_coverage` actually
covers), `unsupported_countries: list[CountryExclusion]` (the requested
countries this source does *not* cover — always populated even when the
source is otherwise `executable`, so a partially-applicable source is fully
transparent per-country; see §11a).

`config_status` vs `effective_config_status` (Milestone 1.1, decisions.md
D-029; extended Milestone 2 Deliverable 5 step 5, decisions.md D-046):
`config_status` stays exactly what §4 describes below — static,
user-maintained registry metadata, never written back by the engine.
`effective_config_status` is a live view computed by `build_plan` from an
optional `EnvConfig`: for `adzuna_api` it's `configured` when
`ADZUNA_APP_ID`/`ADZUNA_APP_KEY` are both present, else `needs_credentials`;
for `reed_api` it's `configured` when `REED_API_KEY` is present, else
`needs_credentials`; both regardless of what the registry YAML declares.
Every other source_id (no adapter/credential rule implemented yet) falls
back to its declared `config_status` unchanged — this is a narrow, per-
source_id if/elif addition each time a new credentialed adapter ships, not a
generic credential-mapping mechanism (acknowledged debt, D-046).
`build_plan(..., env=None)` (the default) leaves `effective_config_status ==
config_status` for every source — existing callers that don't pass `env` see
no behaviour change. Never derived from or displaying a secret value, only a
boolean-derived enum.

`CountryExclusion`: `country: str`, `reason: str` (e.g.
`"not_in_geographic_coverage"`).

`ExcludedSource`: `source_id`, `reasons_excluded: list[str]`.

### 2.11 `MatchResult`
`job_id`, `search_profile_ref`, `hard_filter_result: HardFilterResult`,
`prefilter_result: PrefilterResult`, `semantic_result: SemanticResult | None` (M3+),
`llm_extraction: LlmExtraction | None` (M4+), `final_score: float | None`,
`score_components: list[ScoreComponent]`, `notification_tier` (enum:
`priority|digest|store_only|rejected`).

`HardFilterResult`: `passed: bool`, `rejections: list[RejectionReason]`
(`RejectionReason`: `rule`, `detail`, `evidence: str | None`).

`ScoreComponent`: `name`, `weight: float`, `raw_value: float`,
`weighted_value: float`, `evidence: list[str]`.

### 2.12 `VisaAssessment`
Never a boolean. `job_id`, `status` (enum: `confirmed_yes|likely|employer_eligible|
unknown|confirmed_no|not_required`), `confidence: float`,
`job_text_evidence: list[str]`, `negative_evidence: list[str]`,
`employer_registry_match: bool | None`,
`employer_registry_match_confidence: float | None`, `registry_source: str | None`,
`country_work_permit_regime: str`, `existing_work_authorisation_required: bool | None`,
`citizenship_restrictions: list[str]`, `security_clearance_restrictions: list[str]`,
`relocation_support_evidence: list[str]`, `international_candidate_evidence: list[str]`,
`last_verification_date: datetime`.

M1 note: `employer_registry_match*` and `registry_source` are always `None` in
Milestone 1 (no sponsor-registry enrichment yet — see `decisions.md` D-006).
`status` in M1 is derived only from job-text evidence, so it typically resolves
to `unknown`, `likely`, or `confirmed_no`, rarely `confirmed_yes`.

M2 update (Milestone 2 Deliverable 5 step 10, §21): `matching/visa.py
::assess_visa` is now actually called, from `pipeline.py::run_once`, once per
job that reaches Stage 5 scoring, and `repository.save_visa_assessment` is
called with the result. `employer_registry_match`/
`employer_registry_match_confidence`/`registry_source` are now populated
whenever `source_intelligence/sponsor_registry.py::find_sponsor_match` finds
an authoritative match; `status` can resolve to `employer_eligible` (registry
match only) or `confirmed_yes` (job-text evidence), not just `unknown`/
`likely`/`confirmed_no` as before. The model's own shape is unchanged — only
the values it's actually constructed with are new.

### 2.13 `SourceRun`
`run_id`, `source_id`, `search_profile_ref`, `started_at`, `completed_at`,
`status` (enum: `success|partial|failed`), `jobs_fetched`, `jobs_new`,
`jobs_duplicate`, `errors: list[str]`.

### 2.14 Reserved for later milestones (schema defined now, not written to in M1)
`NotificationRecord`, `UserFeedback`, `ApplicationStatus`, `SourcePerformance`.
Defining these now means the repository interface (§4) does not need a
breaking change when they're implemented.

`CompanyWatchlistEntry` is no longer purely reserved as of Milestone 2
Deliverable 5 step 6: `company_name: str`, `source_id: str`,
`external_company_key: str`, `priority: int`, `notes: str | None`.
`source_id` names which `SourceRegistryEntry` a watchlist entry is for (the
watchlist-scoped ATS-feed sources' own ids, e.g. `greenhouse_public_feeds` /
`lever_public_postings`); `external_company_key` is that source's own public
routing identifier for the company's jobs feed (a Greenhouse board token, a
Lever company/site slug) — never a display name, internal ID, or
credential. This model is a YAML-first config surface
(`company_watchlist.yaml`, `config.py::load_company_watchlist`,
`resources/templates/company_watchlist.example.yaml` — see §15.2/15.3), not
a `JobRepository`-persisted model (decisions.md D-009's "no database copy of
YAML-first config" still applies). It identifies *which companies* to
inspect within an already-approved watchlist-scoped source; it does not
itself gate access — that stays the source registry's
`approval_status`/`ComplianceGate` job (§7). The query planner (§6) never
consumes it — `source_intelligence/query_planner.py` gates on
`SourceCapabilities.company_filter` alone and always returns zero
`PlannedQuery`s for such a source, regardless of watchlist contents. As of
Milestone 2 Deliverable 5 step 7, `pipeline.py::run_once` reads this model
at runtime for any selected source whose `SourceCapabilities.company_filter`
is `True`: one `SourceAdapter` instance is constructed per matching
`CompanyWatchlistEntry` and `fetch()`-ed once — see section 18. Lever (step
8) will consume it the same way, once it exists.

## 3. Source-adapter contract

```python
class SourceSearchParams(BaseModel):
    countries: list[str]
    keywords: list[str]
    role_family_hints: list[str]
    employment_types: list[str]
    min_experience_years: float | None
    max_experience_years: float | None
    page_size: int
    max_pages: int

class RawJobRecord(BaseModel):
    """Source-native shape, pre-normalisation. Adapters return this, never Job."""
    source_id: str
    external_id: str
    raw_url: str
    raw_payload: dict[str, Any]
    fetched_at: datetime

class SourceAdapter(Protocol):
    source_id: str
    access_mode: AccessMode

    def is_configured(self) -> bool: ...
    def fetch(self, params: SourceSearchParams) -> list[RawJobRecord]: ...
```

Rules every adapter must follow:
- No adapter method may be called unless `ComplianceGate.authorize()` returned
  `allowed=True` for that source **at call time** (not just at planning time —
  registry state can change between plan generation and execution).
- Adapters raise typed exceptions (`SourceAuthError`, `SourceRateLimitError`,
  `SourceNotFoundError`, `SourceUnavailableError`) rather than leaking `httpx`
  exceptions; the pipeline catches these per-source so one failing source
  doesn't abort a run. `SourceRun.status` reflects this (`partial` on a
  caught per-source failure). `SourceNotFoundError` (HTTP 404) is kept
  distinct from `SourceUnavailableError` because a 404 on a well-formed
  request path usually signals a persistent config/routing problem (e.g. an
  unsupported country for that source) rather than a transient outage.
- Exception messages carry diagnostic context (source id, country, page,
  status code) but never the request's query parameters or response body,
  since credentials travel in the query string and `SourceRun.errors`
  persists these messages (`pipeline.py`).
- Adapters own their own rate-limit/backoff behaviour; they never bypass
  authentication, CAPTCHAs, or published rate limits (per project ground rules).
- Adapters are pure at the boundary: HTTP in, `RawJobRecord` out. Normalisation
  into `Job` happens in the pipeline, not the adapter, so every source is
  normalised the same way.

Milestone 1 implements exactly one adapter: `AdzunaAdapter` (`public_api`,
`approved`). Milestone 2 Deliverable 5 step 5 adds a second: `ReedAdapter`
(`sources/reed.py`, `public_api`, ships `manual_review` in the packaged
registry template — see decisions.md D-046). It follows the same structural
shape (per-country loop, `is_configured()`, typed exceptions) and calls only
Reed's documented Search endpoint (`GET /api/1.0/search`), never the
separate Details endpoint — see D-046 point 1 for why, and its module
docstring for the full verified contract, including a documented gap in
Reed's own published docs (no literal JSON response example, so response
field-name casing is this project's best-effort camelCase inference from the
docs' prose "Returns" labels, flagged for live confirmation via the new
opt-in `tests/test_reed_integration.py`).

Milestone 2 Deliverable 5 step 7 adds a third: `GreenhouseAdapter`
(`sources/greenhouse.py`, `public_ats_feed`, ships `manual_review` in the
packaged registry template — decisions.md D-047). It differs structurally
from Adzuna/Reed in two documented ways rather than following their shape
byte-for-byte: it makes exactly one HTTP request per `fetch()` call (the
verified Job Board API contract documents no pagination parameters at all),
and it is constructed once per `CompanyWatchlistEntry` — see section 18
below for the full watchlist-fan-out design, which is the actual reason
`SourceAdapter.fetch()`'s Protocol signature stays unchanged for a
company_filter=True source.

Milestone 2 Deliverable 5 step 8 adds a fourth: `LeverAdapter`
(`sources/lever.py`, `public_ats_feed`, ships `manual_review` in the
packaged registry template — decisions.md D-048). Same watchlist-scoped
execution shape as `GreenhouseAdapter` (one instance per
`CompanyWatchlistEntry`, section 18), but a genuinely different verified
contract in three respects: (1) its list endpoint returns a bare JSON
array, not a wrapped object; (2) it documents real `skip`/`limit`
pagination parameters, but this adapter deliberately never sends them —
neither the docs nor a live check expose a total-count/`hasMore`
termination signal, so exactly one unpaginated request is made per
`fetch()` call, the same conservative "don't guess an unconfirmed
contract" discipline as D-016/D-027/D-031/D-046/D-047; (3) it exposes
genuinely structured `country` and `workplaceType` fields that Greenhouse's
feed does not — `Location.country` is populated for real (not always `""`)
and `Job.remote_type` is read directly from `workplaceType` instead of the
shared `_guess_remote_type` text heuristic every other source falls back
to, since using a source's own authoritative field is more accurate than
guessing when that data genuinely exists.

**Known limitation — truncated descriptions**: Adzuna's `/search` endpoint
returns only a snippet of each job's description (their own docs state this
explicitly); there is no documented request parameter to obtain the full
description (confirmed against `developer.adzuna.com` at investigation time,
decisions.md D-031 — not merely unconfirmed, actively checked and not
found). `Job.description_text` for Adzuna-sourced jobs is therefore
routinely truncated to roughly the first ~500 characters. Stage 2/5 matching
already weights title-field evidence over description-only evidence
specifically because of this (see Stage 2 above), so title matching does not
depend on the full description being available — but description-only
evidence (skills/keywords/responsibilities mentioned later in a longer
posting) can be missed. If Adzuna documents such a parameter in the future,
add it to `AdzunaAdapter._build_query` and update this note.

## 4. Repository contract

```python
class JobRepository(Protocol):
    def find_by_fingerprint(self, fingerprint: JobFingerprint) -> Job | None: ...
    def save_job(self, job: Job) -> None: ...
    def merge_provenance(self, job_id: str, provenance: SourceProvenance) -> None: ...
    def save_source_run(self, run: SourceRun) -> None: ...
    def save_match_result(self, result: MatchResult) -> None: ...
    def list_recent_jobs(self, since: datetime, limit: int = 200) -> list[Job]: ...
    def list_provenance(self, job_id: str) -> list[SourceProvenance]: ...

    # save_visa_assessment is fully implemented and actually called as of
    # Milestone 2 Deliverable 5 step 10 (§21) — no longer a reserved stub.
    def save_visa_assessment(self, assessment: VisaAssessment) -> None: ...

    # Milestone 2 Deliverable 5 step 10 (§21): sponsor-register persistence,
    # behind the same Protocol as everything else — source_intelligence/
    # sponsor_registry.py never opens its own SQLite connection.
    def replace_sponsor_registry_entries(
        self, country: str, register_name: str, entries: list[SponsorRegistryEntry]
    ) -> None: ...
    def find_sponsor_registry_entry(
        self, normalized_name: str, country: str
    ) -> SponsorRegistryEntry | None: ...

    # Defined for interface stability; no-op or NotImplementedError-free stub
    # in M1's SQLite implementation until the relevant milestone lands.
    def save_notification(self, record: NotificationRecord) -> None: ...
    def save_feedback(self, feedback: UserFeedback) -> None: ...
    def save_application_status(self, status: ApplicationStatus) -> None: ...
    def save_source_performance(self, perf: SourcePerformance) -> None: ...
```

M1 ships `SqliteJobRepository` implementing the first six methods fully
(`jobs`, `source_provenance`, `source_runs`, `match_results`,
`job_fingerprints` tables) with real schema. `list_provenance` was added in
Milestone 2 Deliverable 5 step 9 (§20) — a read method over the
already-existing `source_provenance` table, not a schema change.
`save_visa_assessment` and the two sponsor-registry methods were completed
in Milestone 2 Deliverable 5 step 10 (§21). The remaining methods exist on
the Protocol and have a table reserved in the SQLite schema, but the schema
migration for them can lag — the *interface* is what must not change.

**Config vs. database boundary**: `CandidateProfile`, `SearchProfile`, and
`SourceRegistryEntry` are YAML-first in every milestone through M1; the
repository does not persist them. A future milestone may add a
`SourceRegistryRepository` if the registry needs runtime mutation (e.g., a
discovery process writing back `config_status`), but that is out of scope now
(see `decisions.md` D-009). §2.10's `SelectedSource.effective_config_status`
(decisions.md D-029) is not an exception to this: it's computed fresh on
every `build_plan` call from `EnvConfig` and never written back to the
registry YAML — `SourceRegistryEntry.config_status` itself stays exactly the
static field D-009 describes.

## 5. Country/region resolution

A small static lookup (`countries.py` or a bundled YAML) maps ISO country codes
to one or more region tags (`south_asia`, `middle_east`, `europe`, `uk` ⊂
`europe`-adjacent-but-distinct, `north_america`, `anz`, `southeast_asia`).
This resolution is used by (a) the planner to match `SearchProfile.included_countries`
against `SourceRegistryEntry.geographic_coverage` when coverage is expressed at
region granularity, and (b) the country-level visa/work-permit context slot in
`VisaAssessment.country_work_permit_regime`. It is intentionally a plain lookup
table, not a service — no external dependency for something this static.

## 6. `SearchExecutionPlan` generation (the planner)

Input: one `CandidateProfile` + one `SearchProfile` + the loaded
`SourceRegistry`. Output: one `SearchExecutionPlan`.

Steps:
1. Resolve the search profile's countries to regions (§5).
2. Filter the registry to entries whose `geographic_coverage` intersects the
   resolved countries/regions, and whose `role_coverage` intersects the
   profile's role families (or is `general`).
3. Score each candidate source (§ "Source-selection scoring" below).
4. Apply the **diversity rule**: if two or more sources have historical
   `duplicate_rate` above a configurable threshold against each other (or, cold
   start, are known aggregators of the same underlying listings — e.g. two
   generic aggregators covering the same country with no differentiating
   role/company coverage), keep the higher-scoring one and record the other in
   `excluded_sources` with reason `"redundant_with:<source_id>"`.
5. For every source that survives steps 2–4, call `ComplianceGate.authorize()`.
   The source is still included in `selected_sources` (so the plan is honest
   about what's *relevant*), but `executable=False` and
   `required_setup_actions` surface why it can't run yet.
6. Build `SourceSearchParams`/`search_queries` per selected source from the
   profile's role families, title aliases, and primary skills.
7. Everything that didn't survive step 2 goes into `excluded_sources` with a
   relevance-based reason (`"no_geographic_coverage"`, `"no_role_coverage"`).

### Source-selection scoring
Weighted sum, each factor normalised to `[0, 1]`:

| Factor | Weight | Cold-start default |
|---|---|---|
| Country/region relevance | 0.20 | computed from overlap, no default needed |
| Role-family relevance | 0.20 | computed from overlap |
| Sector relevance | 0.05 | 0.5 (neutral) if profile has no sector filter |
| Seniority relevance | 0.05 | 0.5 |
| Historical matching jobs | 0.10 | 0.5 (neutral prior — see risk R-3) |
| Freshness (adapter's typical posting latency) | 0.10 | 0.5 |
| Visa/international-hiring usefulness | 0.10 | from registry's static `expected_value` until real data exists |
| Source reliability | 0.10 | `reliability_score` from registry, default 0.5 |
| Duplicate rate (inverted) | 0.05 | 0.5 |
| Technical/official-source quality | 0.05 | from `technical_feasibility` |

All weights and the neutral-prior value live in config (not hard-coded) so they
can be tuned without a code change once real `SourceRun`/`MatchResult` history
accumulates.

## 7. Compliance gate

```python
def authorize(entry: SourceRegistryEntry, requested_mode: AccessMode) -> ComplianceDecision:
    ...
```

Rule table (this *is* the compliance gate — deliberately simple and exhaustive,
not a scoring model):

| approval_status | auto-executable access_modes | otherwise |
|---|---|---|
| `approved` | `public_api`, `public_ats_feed`, `rss_or_sitemap` | any other mode on an `approved` entry is a **config error** (raise, don't silently downgrade) |
| `alert_only` | none (ingestion, not adapter execution) | surfaced as "requires email-alert ingestion" |
| `requires_authorisation` | none | surfaced as a required setup action |
| `manual_review` | none | surfaced as pending review |
| `blocked` | none | never selected for execution, kept in registry only as a documented negative example |
| `deprecated` | none | excluded with reason `"deprecated"` |

`search_discovery` is never auto-executable regardless of `approval_status` —
it exists only for the source-*discovery* process (§9) and must be promoted to
another access mode after a manual terms review before any collection happens.

The gate is enforced twice: once by the planner (sets `executable` on
`SelectedSource`) and once inside the pipeline immediately before invoking
`SourceAdapter.fetch()` (defence in depth — registry YAML could change between
plan generation and execution in a long-lived process).

## 8. Deduplication

`JobFingerprint` fields (§2.6) are computed at normalisation time:
- `canonical_url`: scheme+host+path lowercased, with known tracking params
  stripped (`utm_*`, `gh_src`, `lever-source`, `trk`, etc.) and trailing slash
  removed.
- `normalized_company` / `normalized_title`: lowercased, punctuation stripped,
  common suffixes removed (`Inc.`, `Ltd`, `(Remote)`), whitespace collapsed.
- `normalized_location`: city+country lowercased via the same `Location`
  normalisation used for matching.
- `description_fingerprint`: SHA-256 of the whitespace-normalised description
  text (M1). A future milestone may switch to SimHash for near-duplicate
  detection across reworded reposts; the field is a plain string so that swap
  doesn't change the schema.
- `posted_date`: date-only, when the source provides it.

Matching tiers, applied in order at ingestion (tiers 2 and 3 below were
extended in Milestone 2 Deliverable 5 step 9 — see §20 for the full design;
this section states the current, post-step-9 behaviour):
1. **Exact, same source**: same `canonical_url` + `external_source_id` →
   same job. Merge provenance, keep the earliest `posted_at`.
2. **Exact, cross-source**: same `canonical_url` alone (ignoring
   `external_source_id`) → same job, gated by
   `SourceCapabilities.canonical_application_url` on *both* the new and the
   matched job's originating source (a source without a stable canonical URL
   must never be compared on URL alone). Merge provenance.
3. **Probable duplicate**: same `normalized_company` + `normalized_title` +
   `normalized_location` (never optional) **and** at least one corroborating
   signal — identical `description_fingerprint`, bounded token-set (Jaccard)
   similarity of the two descriptions ≥ 0.6, or a `posted_date` within ±3
   days combined with matching `salary_min`/`salary_max` when both sources
   report salary. Merge provenance.
4. **Repost**: same company+title+location, no probable-duplicate signal, and
   the existing job's `posted_at` is older than a configurable gap (default
   21 days) → new `Job` row, linked via `previous_job_id`, subject to the
   repost notification policy (do not re-notify unless materially changed or
   repost policy allows).
5. Anything else → distinct job.

`find_by_fingerprint` in the repository only implements tier 1 lookup
efficiently (indexed exact match, `job_fingerprints`'s primary key); tier 2's
cross-source URL lookup is backed by a separate non-unique index
(`idx_job_fingerprints_canonical_url`, §20); tiers 3–4 are pipeline-level
comparisons against a recent-jobs window (`list_recent_jobs`), acceptable at
Milestone 1/2 volumes. `deduplication.py::DedupTier` names these
`EXACT_DUPLICATE` (tier 2) / `PROBABLE_DUPLICATE` (tier 3) / `REPOST` /
`DISTINCT`.

## 9. Source discovery vs. job collection

These are separate processes with separate outputs, per the project's ground
rules:
- **Discovery** (not implemented in M1 beyond the registry schema): given a
  region/sector/role family, produces *candidate* `SourceRegistryEntry` rows
  with `approval_status: manual_review` and `access_mode` best-guessed from
  what was found (often `search_discovery` initially). It never sets
  `approved`. A human (or a later review workflow) promotes entries.
- **Collection**: the pipeline described in §6–§8, which only ever touches
  registry entries that are already `approved` + auto-executable per §7.

## 10. Matching pipeline detail

### Stage 1 — Hard eligibility filters
Deterministic, profile-driven, evidence-producing. Evaluates (from
`SearchProfile` + `CandidateProfile`): included/excluded countries & cities,
employment type, min/max experience (parsed from description text via simple
numeric-range heuristics — best-effort, not guaranteed), citizenship
restrictions, security-clearance restrictions, existing-work-authorisation
requirements, mandatory qualifications, required languages, explicit
no-sponsorship wording (regex/phrase list, e.g. "not able to sponsor",
"must have the right to work"), excluded industries, excluded role families.
Any failing rule appends a `RejectionReason` with the matched evidence
substring. A job failing any hard filter gets `notification_tier: rejected`
and is *not* scored further, but **is still persisted** with its rejection
reasons (useful for tuning filters later and required by the acceptance
criteria's "store jobs" behaviour).

### Stage 2 — Cheap deterministic pre-filter
`run_prefilter(job, candidate, search, weights: PrefilterWeights)` (Milestone
1.1, decisions.md D-029) considers configured signals from *both*
`CandidateProfile` (`title_aliases`, `role_families`, `primary_skills`,
`secondary_skills`, `transferable_skills`, `industries`, `sectors`) and
`SearchProfile` (`target_titles`, `title_aliases`, `role_families`,
`required_skills`, `preferred_skills`, `transferable_skills`,
`included_keywords`, `included_industries`, `included_sectors`) — a job is no
longer left unscored just because the run's *search* profile named a target
title the candidate's own profile didn't happen to repeat. All phrase
comparison goes through `matching/normalize.py`'s shared, profession-agnostic
normalisation (case, underscores, `&`→"and", other punctuation/slashes/
hyphens → space, collapsed whitespace — no stemming, no synonyms).

A job passes Stage 2 if *either*:
- **Strong title/role evidence gate**: a configured target title, title
  alias, or role family (candidate or search profile) strongly matches the
  job's *title* field specifically — an exact normalised-phrase match, or
  token coverage ≥ `PrefilterWeights.strong_title_coverage` (default 0.75) of
  that phrase's own words. This is the structural, stopword-list-free
  safeguard against one generic word ("consultant", "manager", "strategy", …)
  completing a multi-word configured phrase: a 2-word phrase needs both
  words (1/2 = 0.5 fails), a 3-word phrase needs all 3, a 4-word phrase
  tolerates at most one missing word. A single strong title-field match is,
  by design, normally sufficient on its own — Stage 2 is a recall gate, not
  the ranking model. A deliberately single-word configured phrase still
  gates on its own; that's the profile author's explicit choice, not a
  generic-token loophole the algorithm invented.
- **Weighted additive score** ≥ `PrefilterWeights.threshold` (the
  user-tunable `scoring_weights.yaml: prefilter_threshold`, unchanged
  meaning): category ratios over title/role-family/skills/keywords/
  industry-sector-context/seniority, each counting title-field evidence at
  full weight and description-only evidence at `desc_only_damping` (default
  0.5) — title matches outweigh description matches, since job descriptions
  returned by a source may be truncated (see Stage 2 adapter note below).

Produces a `prefilter_score`, `passed_threshold`, `role_family_hints` (cached
on `Job`), and an `evidence: list[str]` explaining which configured signal
matched, whether in the title or description, by which method
(`exact_phrase` / `token_coverage=<ratio>`), and a trailing `decision:` entry
stating why the job passed or failed.

The strong-title gate's phrase-vs-title matching is `matching.normalize.match_phrase`
— the same function Stage 5's best-match title/role-family scoring uses
(decisions.md D-032, Part 3) — so a job cannot pass this gate on title
evidence that Stage 5 then fails to recognise as the same evidence.

### Stage 3 — Semantic similarity (not yet integrated)
Interface reserved (`SemanticResult` field on `MatchResult`). Will use
embeddings to catch equivalents like "Head of Special Projects" ↔ strategic
initiatives. Milestone 3 D3's backend boundary (config surface + local
`Embedder`) is implemented as of §23 below; `SemanticResult`'s finalized
schema and the Stage 5 rescue wiring described there are not yet built.

### Stage 4 — Optional Anthropic extraction (not in M1)
Interface reserved (`LlmExtraction` field). Runs only on jobs that clear Stage
2, returns structured evidence (required/preferred skills, responsibilities,
seniority, experience range, qualifications, visa/relocation evidence,
citizenship/work-authorisation restrictions, match reasons, critical gaps,
ambiguities) — never a final score. Model id comes from `ANTHROPIC_MODEL` env
var; the dependency is optional (`pip install .[llm]`) and the pipeline must
run fully without it.

### Stage 5 — Transparent final scoring
Weighted components, each with visible evidence (`ScoreComponent`):

| Component | Weight | Computation (decisions.md D-032) |
|---|---|---|
| Title / role-family match | 25% | best-match strength (not a vocabulary-size ratio) across CandidateProfile/SearchProfile title and role-family signals — see below |
| Responsibilities match | 15% | role_families+primary_skills overlap against description_text, capped-denominator coverage at a reduced fallback weight (always candidate-history-only, decisions.md D-032) |
| Required skills match | 20% | SearchProfile.required_skills (primary signal) + CandidateProfile.primary_skills (supplemental/fallback) — see below |
| Transferable skills match | 10% | SearchProfile.preferred_skills+transferable_skills (primary) + CandidateProfile.secondary_skills+transferable_skills (supplemental/fallback) — a missing secondary skill never zeroes this component, see below |
| Seniority & experience match | 10% | seniority keyword match (positive), generic entry-level term mismatch (negative, decisions.md D-032 Part 7), parsed experience range vs. candidate's; no evidence either way → 0 (`not_evaluable`), never a positive default |
| Sector relevance | 10% | word-boundary-safe phrase match against CandidateProfile.industries/sectors + SearchProfile.included/excluded_industries/sectors; 0.5 only when *nothing* is configured, 0 when preferences are configured but no evidence is found |
| Education match | 5% | MBA/degree keyword presence in description, using only the *configured* candidate's own education/qualifications/certifications/licences; no evidence (nothing configured, or configured but not found) → 0, never a positive default |
| Visa & relocation compatibility | 5% | positive/negative/unknown regex evidence scan; unknown → 0 (`not_evaluable`), not a neutral positive |

Weights are config (`config/scoring_weights.yaml`), unaffected by the
formula rewrite below — decisions.md D-013. Per the requirement "a job must
not be rejected simply because one secondary skill is missing," the
transferable-skills component is a *soft* overlap ratio, never a gating
condition — it can only add to the score, never by itself drop a job below a
rejection line (Stage 1 is the only stage that rejects).

**Best-match title/role-family scoring (decisions.md D-032, Parts 1–3;
D-033; D-034)**: the original formula divided every matched phrase by the
*entire* configured title/role-family vocabulary, which mechanically
diluted an exact target-title match by however many *other* titles a
profile happened to configure. `title_role_family`'s raw score is now
`max(best_title_match_strength, (best_title_match_strength +
best_role_family_match_strength) / 2, role_family_alone_credit)`
(`_combine_title_role_family`, D-033/D-034), where each "best match" is the
single strongest configured phrase match — never an average or ratio over
the full vocabulary, so configuring additional unrelated titles/
role-families can never lower an existing exact match's score. Title
strength is the primary signal: an exact title match alone keeps its full
strength regardless of whether a separate role-family phrase also matched
(the pre-D-033 unconditional `(title + role_family) / 2` average wrongly
halved a title-only exact match down to 0.5), a role-family match *weaker
than or equal to* the title match never drags the combined score down (the
average then falls at or below title strength, so the `max()` keeps title
strength unchanged), and a role-family match *stronger* than the title
match can raise the combined score above title alone. Matching itself
reuses Stage 2's `matching.normalize.match_phrase` (exact normalised
phrase, or token-coverage ≥ the same `PrefilterWeights.strong_title_coverage`
threshold for multi-word phrases, computed over each phrase's *meaningful*
content tokens — connector/function words like "and"/"of"/"the" are
excluded from both the numerator and denominator via
`matching.normalize.meaningful_tokens`, D-033 — so a connector word can no
longer complete a multi-word phrase's coverage) — the same function in
both stages, so a job that clears Stage 2 on token-coverage title evidence
is guaranteed non-zero credit for that same evidence at Stage 5
(previously Stage 5 used exact-substring-only matching, so some jobs
passed the Stage 2 gate on evidence Stage 5 then scored as zero). Each
match is further scaled by: field (a title-field match always outweighs a
description-only match, mirroring Stage 2's `desc_only_damping`) and
provenance tier (an active `SearchProfile` signal —
`target_titles`/`title_aliases`/`role_families` — outweighs a
`CandidateProfile` signal of otherwise-equal match quality, and
`CandidateProfile.previous_titles` ranks lowest of all, so a candidate's
purely historical job titles cannot automatically outrank this run's actual
search targets). D-034 lowered the `candidate_title_alias`/
`candidate_previous_title` provenance tiers (0.85/0.7 → 0.55/0.4) — a
candidate's own historical title vocabulary is supplemental evidence for
this run's active ask, not a near-equivalent of it.

**Role-family-alone credit and active-search-intent classification
(decisions.md D-034)**: role-family evidence with no title match at all no
longer contributes a flat `role_family_match_strength / 2` regardless of
provenance. `_role_family_alone_credit` (`matching/scoring.py`) computes
this credit independently for active (`SearchProfile.role_families`) and
candidate-only (`CandidateProfile.role_families`) evidence: a single active
role-family match now earns up to 0.70 of its own strength (substantial
credit — this run's actual ask, just without a separately configured title
phrase also matching), two or more *distinct* active role-family phrases
matched in the job's *title* field (never a single incidental
description-only mention) reinforce that credit to up to 0.85, and
candidate-only role-family evidence keeps its pre-existing 0.5 factor
unchanged — so active role-family-only evidence now strictly outranks
candidate-only role-family-only evidence of equal match strength, and two
reinforcing active matches outrank one. This credit only ever participates
in the `max()` above, so it can raise the combined score but never lower an
existing title or averaged score (Part 2 requirement 5 of the audit this
fix implements). Alongside this, `_classify_evidence_tier` derives a small
internal classification — `active_target_title` / `active_title_alias` /
`active_role_family` / `candidate_history_only` /
`no_title_or_role_evidence` — directly from the same structured phrase-match
data (never by re-parsing rendered evidence text) and records it in
`title_role_family`'s own evidence as `active_search_intent_tier:<value>`;
it is a transparency label, not a new public `ScoreComponent` or schema
field.

**Search-profile-aware skill scoring (decisions.md D-032, Part 4)**:
`required_skills` and `transferable_skills` are no longer
candidate-history-only. `SearchProfile.required_skills` /
`preferred_skills`+`transferable_skills` (this run's actual ask) are the
primary signal; the corresponding `CandidateProfile` fields
(`primary_skills` / `secondary_skills`+`transferable_skills`) are
supplemental support at a reduced weight when a search-profile signal
exists, or a capped lower-priority fallback when it doesn't — so generic
historical skill overlap alone can never earn as much as a genuine
search-specific match, and (combined with the same capped-denominator
"bounded coverage" `responsibilities` also uses) cannot by itself outrank an
exact title match through incidental description overlap.

**Sector/industry relevance (decisions.md D-032, Part 5)**: no longer
hard-coded to a flat neutral 0.5 for every job — `CandidateProfile.industries`/
`sectors` and `SearchProfile.included_industries`/`included_sectors` (the
positive vocabulary) and `SearchProfile.excluded_industries`/
`excluded_sectors` (the soft-negative vocabulary — an opt-in *hard* filter
on the same fields already rejects a job at Stage 1 when its
`hard_filters` toggle is on, so anything reaching here by definition wasn't
hard-rejected) are matched against the job's title+description using
word-boundary-safe token-sequence matching (`contains_phrase_tokens`), not
plain substring containment — preventing a short configured term (e.g. a
2-letter industry code) from matching inside an unrelated longer word.
0.5 is reserved for the case where *nothing* is configured at all (a
genuinely neutral "no preference stated"); when preferences are configured
but no evidence is found in this job, the component is 0, not 0.5.

**Entry-level seniority safeguard (decisions.md D-032, Part 7)**: a small,
generic (profession-agnostic) list of entry-level terms — trainee,
graduate, internship, intern, junior, entry level — is matched
word-boundary-safe against the job's title/description. When
`SearchProfile.min_experience_years` is at least 3 (this run targets an
experienced hire) and one of these terms is found, `seniority_experience`
receives explicit negative evidence rather than the neutral "no evidence"
value, so an entry-level-worded job scores measurably lower for an
experienced-hire search than an otherwise-equivalent standard-level posting.

**No unconditional score floor (decisions.md D-032, Part 6)**: `seniority_experience`,
`sector_relevance`, `education`, and `visa_relocation` previously defaulted
to a neutral `raw_value` of 0.5 whenever no evidence existed at all, which
— summed across those four components' weights — gave every job that
cleared Stage 2 an unconditional 15-point floor regardless of relevance. No
evidence now defaults to 0 (`not_evaluable`, always recorded explicitly in
the component's evidence list) in every component; a genuine positive match
still scores positively, and where a negative signal is deterministically
detectable (an entry-level/seniority mismatch, explicit no-sponsorship
language), the component can go negative rather than only ever sitting at
the same value "no evidence" would. `final_score` is clamped to `[0, 100]`
(`build_match_result`) so the displayed 0–100 scale stays meaningful even
when negative-evidence components pull a job's raw weighted sum below zero
— "strong negative evidence" and "zero evidence" both floor at a displayed
0, same scale as before, not a negative number on screen.

The final score is a **deterministic weighted relevance score**, not a
statistically calibrated match probability — a 60 does not mean "60% likely
to be a good fit" in any validated sense, only "this job's configured-signal
overlap summed to 60 on this weighting." Interpret differences between jobs
scored by the *same* profile as meaningful ranking signal; do not compare
raw scores across different candidate/search profiles or read the number as
a percentage.

Notification tiers (configurable, defaults per spec): `final_score >= 85` →
`priority`; `70 <= final_score < 85` → `digest`; `< 70` → `store_only`. This
scoring calibration fix (decisions.md D-032) deliberately did not touch
these threshold values — see the ADR for why recalibrating them should wait
for a live re-run under the new formula.

## 11. CLI behaviour

```
job-scout run-once --profile strategy-global --dry-run
                    [--execution-limits PATH] [--candidate-profile PATH] [--search-profiles PATH]
                    [--source-registry PATH] [--limit N] [--verbose] [--json]
```

`--execution-limits PATH` points at the engine-level guardrail config
(defaults to `config/execution_limits.yaml`, falling back to
`src/job_scout/resources/templates/execution_limits.example.yaml`'s values if no local override exists —
see §11a). `--limit N` is a separate, CLI-only convenience cap on top of
`max_jobs_processed_per_run` for ad-hoc testing; it never raises the
configured ceiling, only lowers it for that one invocation.

Execution sequence (maps directly to the Milestone 1 acceptance criteria):
1. Load and validate `.env`/config; fail fast with a specific, actionable error
   if required config is missing or malformed (e.g., missing Adzuna
   credentials → exit non-zero with a message naming the missing variable, not
   a stack trace).
2. Load `CandidateProfile`.
3. Load the requested `SearchProfile` by `--profile` id.
4. Resolve countries → regions.
5. Generate the `SearchExecutionPlan` via the planner.
6. Print selected/excluded sources with reasons (human-readable table in
   default mode; full structured plan under `--json`).
7. For each `executable=True` selected source, call its adapter's `fetch()`.
8. Normalise raw records into `Job`.
9. Persist `SourceProvenance` for every job (even duplicates — the audit trail
   is provenance-complete).
10. Deduplicate (§8).
11. Apply Stage 1 hard filters.
12. Apply Stage 2 pre-filter, then Stage 5 scoring for jobs that clear it.
13. Print ranked results with per-component evidence.
14. Persist jobs, match results, and the `SourceRun` record regardless of
    `--dry-run`.
15. `--dry-run` disables the notification-dispatch step and any other
    external write action (see "Dry-run semantics" immediately below for the
    authoritative, non-negotiable definition).
16. Non-zero exit with a specific message on: missing/invalid config, no
    executable sources for the plan, or an unhandled adapter error after
    per-source isolation (see §3).

### Dry-run semantics (authoritative definition)

`--dry-run` is **not a read-only database mode** and it is **not a
no-network mode**. It draws exactly one line: real, permitted external I/O
that *collects or persists information* is allowed; external I/O that
*pushes something out to the world on the user's behalf* is not.

With `--dry-run`, the engine:
- **does** perform permitted job fetching from every `executable=True`
  source in the plan (real HTTP calls to Adzuna, subject to the guardrails
  in §11a) — it does not simulate or skip collection;
- **does** normalise, deduplicate, and score jobs exactly as a non-dry-run
  invocation would;
- **does** write jobs, source-run records, and match results to the local
  SQLite database;
- **does** print ranked results and scoring evidence to the console;
- **disables** every outbound notification channel (email, WhatsApp,
  Telegram, push — whichever exist in a given milestone);
- **disables** every other external write action (anything that would
  create/modify state outside this process's own local database — e.g. a
  future "mark as applied" write-back to a third-party tracker, or posting
  to a webhook).

The only difference between `--dry-run` and a normal run is therefore: *does
this run notify/write externally, or not*. Everything upstream of that
(fetch → normalise → dedupe → filter → score → persist locally → print) is
identical in both modes. This definition is the single source of truth;
`README.md` and `MILESTONE_1.md` restate it briefly and must not diverge from
it.

## 11a. API quota and execution guardrails

Milestone 1 talks to a real, rate-limited, quota-metered external API
(Adzuna). The engine must never be able to generate an uncontrolled number of
requests, regardless of how broad a `SearchProfile` is. These limits are
config (overridable per environment), not hard-coded, and apply identically
whether or not `--dry-run` is set (dry-run does not relax guardrails — it
only disables notification/external-write actions, per the definition
above).

| Guardrail | Purpose | Example default |
|---|---|---|
| `max_countries_per_run` | Caps how many countries one invocation will query, even if a search profile lists more | 6 |
| `max_pages_per_source_country` | Caps pagination depth per (source, country) pair | 3 |
| `results_per_page` | Page size requested from the source | 50 |
| `request_timeout_seconds` | HTTPX timeout per request | 15 |
| `max_retries` | Retry attempts on a transient failure (e.g. 429/5xx) before the source is marked `partial` for this run | 2 |
| `max_jobs_processed_per_run` | Optional hard ceiling on total jobs pulled through the pipeline in one invocation, across all sources | unset by default; recommended when testing broad profiles |

These live in `config/execution_limits.yaml` (example at
`src/job_scout/resources/templates/execution_limits.example.yaml`), an engine-level runtime config file
— deliberately **not** part of `SearchProfile`, so a broad search profile
can never override its own safety ceiling. If a search profile's resolved
country list
exceeds `max_countries_per_run`, the planner truncates to the first
`max_countries_per_run` countries **by source-selection score** (§6) and
records the truncation as a `diversity_notes`-style note on the
`SearchExecutionPlan`, so it's visible, not silent.

Retry/backoff is the adapter's responsibility (per §3, adapters own their own
rate-limit behaviour) but the *limit* on retries is engine config, not an
adapter-local constant, so it's auditable and tunable in one place.

### Per-source-country support check (planner responsibility)

Before any request is planned for a given `(source, country)` pair, the
planner checks `SourceRegistryEntry.geographic_coverage` for that source
against the requested country/region. If the source does not cover that
country:
- it is recorded as an `ExcludedSource` (if the source has *no* usable
  overlap with the profile at all), **or**
- if the source *is* selected for other countries but does not cover this
  particular one, the country is added to that `SelectedSource`'s own
  `unsupported_countries: list[CountryExclusion]` (§2.10) — a source can be
  partially applicable (e.g. `adzuna_api` covers `GB`/`DE`/`CA` but not `AE`,
  per `src/job_scout/resources/templates/source_registry.example.yaml`) — and is simply absent from
  `supported_countries` / `search_queries` for that country, so no
  `SourceSearchParams` referencing it is ever built.

Either way, **no HTTP request is ever made for a source-country combination
the registry doesn't claim to support.** This check happens at plan-generation
time, purely against registry metadata — it never costs an API call, and a
failing/empty API response is not how the engine learns a country is
unsupported.

A secondary command, `job-scout plan --profile strategy-global`, prints the
`SearchExecutionPlan` without fetching anything — useful for testing the
planner/compliance gate in isolation and for the user to sanity-check source
selection before spending API quota. Confirmed in scope for M1 (`decisions.md`
D-011).

## 12. Proposed module layout

Trimmed for Milestone 1's actual size — see "What Milestone 1 deliberately
does not add" below for why this is flatter than an earlier draft of this
document.

```
src/job_scout/
├── __init__.py
├── config.py            # env + YAML loading for all config (candidate profile,
│                         # search profiles, source registry, ExecutionLimits,
│                         # company watchlist), validation, specific error
│                         # messages
├── countries.py          # ISO country → region lookup (§5)
├── models.py              # every Pydantic model in §2 (Location through
│                          # SourceRun) — one file; see rationale below
├── deduplication.py        # fingerprinting + tiered matching (§8)
├── source_intelligence/
│   ├── __init__.py
│   ├── registry.py          # load/validate source registry YAML
│   ├── planner.py            # §6 — builds SearchExecutionPlan
│   ├── query_planner.py       # §6 — SearchProfile-driven PlannedQuery
│   │                           # generation, added Milestone 2 Deliverable 5
│   │                           # step 3 (decisions.md D-037/D-041)
│   ├── sponsor_registry.py     # §21 — UK sponsor-register import/parsing +
│   │                           # find_sponsor_match, added Milestone 2
│   │                           # Deliverable 5 step 10 (decisions.md D-050)
│   └── compliance.py          # §7 — ComplianceGate.authorize()
├── sources/
│   ├── __init__.py
│   ├── base.py                # SourceAdapter Protocol, RawJobRecord, exceptions
│   ├── adzuna.py                # AdzunaAdapter — the only adapter in M1
│   ├── reed.py                   # ReedAdapter — added Milestone 2 Deliverable 5 step 5
│   ├── greenhouse.py               # GreenhouseAdapter — step 7
│   └── lever.py                     # LeverAdapter — step 8
├── matching/
│   ├── __init__.py
│   ├── hard_filters.py           # Stage 1
│   ├── prefilter.py                # Stage 2
│   ├── scoring.py                   # Stage 5 (score components carry the
│   │                                 # reserved-but-unused Stage 3/4 hooks as
│   │                                 # optional model fields, not extra files)
│   ├── visa.py                       # §21 — assess_visa(), added Milestone 2
│   │                                 # Deliverable 5 step 10 (decisions.md D-050)
│   └── visa_patterns.py               # §21 — shared visa positive/negative
│                                       # regex patterns, added Milestone 2
│                                       # Deliverable 5 step 10 (decisions.md D-050)
├── repository/
│   ├── __init__.py
│   ├── base.py                       # JobRepository Protocol (§4)
│   └── sqlite_repo.py                  # SqliteJobRepository
├── pipeline.py                          # orchestrates the sequence in §11
├── evaluation.py                         # §22 — job-scout evaluate calibration
│                                          # tool, added Milestone 2 Deliverable 5
│                                          # step 11 (decisions.md D-043/D-051)
└── cli.py                                # Typer app: run-once, plan, init,
                                           # version, sources, sponsors import,
                                           # evaluate
```

This structure is implemented as proposed, with two additions:
`source_intelligence/registry.py` re-exposes `config.py`'s registry loader
plus a small `index_by_id` helper (registry-specific structuring, not
duplicate YAML parsing — see the module's own docstring), and `config.py`
also owns `ScoringWeights`/`SourceScoringWeights` (the section 10/6 weight
tables — see decisions.md D-013/D-014).

### Why `models.py` is one file, not a package

An earlier draft of this document split models into an eight-file
`models/` package (one file per domain concept). At Milestone 1's actual
size — roughly fifteen small `BaseModel`/`StrEnum` definitions, most a few
fields each — that split adds `__init__.py` re-export ceremony without a
real benefit: nothing in M1 needs to import "just the job models" without
the rest. A single typed `models.py` (a few hundred lines, organised with
clear section comments matching §2's numbering) is easier to read top to
bottom and easier to keep consistent. Re-split into a package only if/when a
later milestone's model count actually makes one file unwieldy.

### Adapter selection needs no factory

Milestone 1 shipped exactly one adapter (`AdzunaAdapter`); Milestone 2
Deliverable 5 step 5 added a second (`ReedAdapter`, decisions.md D-046).
`pipeline.py::_default_adapter_factory` is still a plain closure with an
`if`/`elif` per `source_id` — not a registry, factory class, or
`adapter_ref`-keyed dynamic-import mechanism. Adding Reed meant adding one
module and one more `if` branch, exactly as this section originally
predicted ("adding a second adapter later means adding a module and one line
in `pipeline.py`, not a loader") — confirmed, not revised. That kind of
plugin-loading indirection remains explicitly out of scope (see below);
revisit only if a much larger number of adapters ever makes the `if`/`elif`
chain itself unwieldy, which two entries does not.

### What Milestone 1 deliberately does not add

None of the following appear anywhere in the layout above, and none should
be introduced while implementing it:

- **Dependency-injection containers** — `SqliteJobRepository`,
  `AdzunaAdapter`, and the planner are constructed directly (plain
  constructor calls) in `cli.py`/`pipeline.py` and passed as arguments. No
  container, no service locator.
- **Abstract factories** — see "Adapter selection" above; a `Protocol` plus
  one concrete implementation is not a factory.
- **Plugin-loading frameworks** — no dynamic import by string, no
  entry-point discovery. Adding a second adapter later means adding a module
  and one line in `pipeline.py`, not a loader.
- **Event buses** — the pipeline is a straight-line function call sequence
  (§11); nothing publishes or subscribes to events. A future milestone's
  always-on poller may need one; M1's `run-once` does not.
- **Database migration frameworks** (Alembic etc.) — M1's SQLite schema is
  created with a single `CREATE TABLE IF NOT EXISTS` set run at startup.
  Migrations become worth it once there's a deployed instance with data to
  preserve across schema changes — not before.
- **A PostgreSQL implementation** — only `SqliteJobRepository` exists;
  `JobRepository` (§4) is the seam a Postgres implementation would later
  plug into, not built now.
- **Unnecessary async** — `run-once` is a single sequential CLI invocation
  against one adapter. `httpx` is used in its synchronous client mode;
  nothing here benefits from `async`/`await` until concurrent multi-source
  polling exists (a later milestone).
- **Complex inheritance hierarchies** — `SourceAdapter` and `JobRepository`
  are `Protocol`s (structural typing, no base-class inheritance required);
  domain models are flat `BaseModel`s composed of one another, not deep
  class trees. `matching/` stages are plain functions taking a `Job` (+
  profile/context) and returning a typed result — no `Stage` base class.

The two interfaces this project does keep are `SourceAdapter` (§3) and
`JobRepository` (§4) — real seams for real, named future work (a second
adapter; a second database backend), not speculative abstraction.

## 13. Unresolved design risks

- **R-1 (Adzuna coverage gap)**: Adzuna's public API does not confirm
  coverage for India or the UAE (`src/job_scout/resources/templates/source_registry.example.yaml`'s
  `adzuna_api.geographic_coverage` deliberately excludes both, and the
  example `strategy-global` profile still requests the UAE so the planner's
  per-source-country exclusion behaviour has a real example to demonstrate —
  see §11a). With M1 shipping only the Adzuna adapter, real-world results
  will skew toward UK/EU/North America/ANZ even though the candidate is
  India-based. This matches the candidate's international intent but means
  "regional source intelligence" is only partially demonstrable until a
  second adapter or email-alert ingestion exists. **Verify Adzuna's actual
  supported country list against their current API docs at implementation
  time** — the registry's coverage list is this project's working assumption,
  not a verified fact. IE was live-verified as unsupported (HTTP 404 on
  `/v1/api/jobs/ie/search/{page}`) and removed from
  `adzuna_api.geographic_coverage` accordingly (decisions.md D-028); the
  remaining entries are still this project's working assumption, not
  individually re-verified.
- **R-2 (Cold-start scoring)**: Several source-scoring factors
  (`historical_match_count`, `duplicate_rate`, adapter health) have no data on
  a fresh install. Neutral-prior defaults (§6) avoid divide-by-zero/None
  errors but mean early plans are less differentiated than the model implies.
- **R-3 (Experience-range parsing)**: Parsing "4-6 years" style ranges out of
  free-text descriptions for Stage 1 is inherently lossy. False negatives
  (rejecting a real match because the range wasn't parsed) are more costly
  than false positives here, so the filter should fail *open* (treat
  unparseable experience text as non-rejecting) — needs explicit test
  coverage.
- **R-4 (Visa assessment strength in M1)**: Without sponsor-registry
  enrichment or LLM extraction, `VisaAssessment.status` in M1 is a regex/
  keyword scan over the job description only — expect a lot of `unknown`.
  This is intentional scope-limiting (Milestone 1 explicitly excludes
  sponsor-registry enrichment) but should be communicated to the user as a
  quality ceiling for M1, not a bug.
- **R-5 (`search_discovery` scope)**: This access mode is easy to misuse as a
  quiet way to scrape search-engine results. The compliance gate treats it as
  never auto-executable under any approval status (§7); this must not be
  loosened without a deliberate terms review per source.
- **R-6 (GitHub Actions + SQLite)**: Not a Milestone 1 concern (M1 is local
  only), but flagged for the roadmap: SQLite state will not survive between
  GitHub Actions runs, so deduplication/notification-history correctness
  requires external persistence *before* any GitHub Actions scheduling is
  enabled (matches the project's own stated constraint).

## 14. Notable requirement tensions (flagged, not silently resolved)

- The requirements list both a fixed example module layout and "do not
  blindly follow this structure... propose the smallest internally consistent
  structure." §12 is a light adaptation of the example (merged `sources/` and
  `source_intelligence/` docstrings only where genuinely redundant were
  avoided) — kept close to the suggestion because it already matches this
  design well, not out of default deference.
- The repository contract is asked to "eventually support" nine entities, but
  Milestone 1 explicitly excludes several of the features that would populate
  them (visa registry enrichment, notifications, feedback, application
  tracking). §4 resolves this by defining the full Protocol now (so the
  interface never breaks) while only implementing the subset M1 actually
  writes to.
- "Dry-run mode" and "must still persist jobs and source-run info" read as
  contradictory at first glance. The "Dry-run semantics" subsection under §11
  resolves this explicitly: dry-run performs real fetching, normalisation,
  deduplication, scoring, and local persistence — it only disables outbound
  notifications and other external write actions. It is not a read-only or
  no-network mode.

## 15. Milestone 1.1: profession-agnostic and locally distributable
foundations

See `MILESTONE_1_1.md` for scope and `decisions.md` D-017 through D-026 for
the reasoning. This section documents what changed structurally; §§1–14
above still describe Milestone 1's pipeline unchanged — 1.1 does not alter
the pipeline shape, it removes hard-coded profession assumptions from it and
adds a proper installed-application path model around it.

### 15.1 `AppPaths` and path resolution (`src/job_scout/paths.py`)

```python
class AppPaths(BaseModel):
    application_data_dir: Path   # platformdirs root for this install
    config_dir: Path             # application_data_dir / "config"
    data_dir: Path                # application_data_dir / "data"
    database_path: Path           # data_dir / "job_scout.sqlite3"
    logs_dir: Path
    cache_dir: Path
    candidate_profile_path: Path
    search_profiles_path: Path
    source_registry_path: Path
    execution_limits_path: Path
    scoring_weights_path: Path
    source_scoring_weights_path: Path
    company_watchlist_path: Path  # Milestone 2 Deliverable 5 step 6
    environment_file_path: Path | None
```

`resolve_app_paths(data_dir_override=None, *, env=None) -> AppPaths`
resolves `application_data_dir` with priority: an explicit `data_dir_override`
argument (the CLI's `--data-dir`) > `JOB_SCOUT_DATA_DIR` environment variable
> `platformdirs.user_data_dir("job-scout", appauthor=False)`. Explicit
per-file CLI flags (`--candidate-profile`, `--db-path`, etc.) take priority
over anything `AppPaths` computes — they are resolved in `cli.py`, not
`paths.py`. The current working directory plays no role in this resolution
(see D-018) except as a documented, secondary convenience for `.env` — see
15.4.

### 15.2 Packaged templates (`src/job_scout/resources/`)

`src/job_scout/resources/templates/` ships the canonical, generic config
templates (candidate profile, search profiles, source registry, execution
limits, scoring weights, source scoring weights, and — as of Milestone 2
Deliverable 5 step 6 — company watchlist) as package data, read via
`importlib.resources` (`job_scout.resources.template_text(name)`). This is
the *only* copy of these templates (D-021) — `config/*.example.yaml` no
longer exists. `pyproject.toml`'s `[tool.hatch.build.targets.wheel]`/
`[tool.hatch.build.targets.sdist]` configuration ensures they're included in
editable installs, wheels, and sdists alike. Unlike execution limits/scoring
weights, `company_watchlist.example.yaml` has no runtime load-time fallback
in `config.py` (§2.14) — it is only ever copied by `job-scout init`, the
same one-time-copy-only treatment as candidate profile/search
profiles/source registry.

### 15.3 `job-scout init` (`src/job_scout/bootstrap.py`)

`run_init(app_paths: AppPaths) -> InitResult` creates `config/`, `data/`,
`logs/`, `cache/` under `application_data_dir`, copies each packaged
template to its real filename (skipping any file that already exists —
never overwrites), and opens the SQLite database once (creating it and
stamping its schema version — §15.6 — if it doesn't exist yet, verifying it
otherwise). It never creates `.env` or any credential value (D-019). It is
idempotent and side-effect-free on a repeated run beyond re-verifying the
database. `cli.py`'s `init` command surfaces `InitResult` as: directories/
files created, files skipped (already present), and guidance on which files
to edit and that credentials are supplied separately. Seven templates are
copied as of Milestone 2 Deliverable 5 step 6 (was six through Milestone 1.1).

### 15.4 Environment (`.env`) resolution

`config.load_env()` resolution order: an explicit `path` argument >
`AppPaths.environment_file_path` (`<application_data_dir>/.env`) if it
exists > a `.env` in the current working directory if it exists (retained
only as a documented development convenience, since a repository checkout
commonly keeps `.env` beside it — never the *primary* default). Real
environment variables always take precedence over anything loaded from a
file, unchanged from Milestone 1.

### 15.5 Generic `CandidateProfile` / `SearchProfile` fields

Both models gained optional, generic fields (skills/qualifications/
certifications/licences/languages, industries/sectors, employment/
relocation preferences, free-text `seniority` — see D-022 for
`seniority_level`'s relaxation to optional). `SearchProfile` also gained
`hard_filters: HardFilterToggles`, a block of boolean opt-in flags; the new
generic hard-filter inputs (required skills/qualifications/certifications/
licences, included/excluded keywords, minimum salary) only reject a job
when their toggle is `True` (D-025). Milestone 1's pre-existing filters are
unchanged. Every new field is optional with a generic default, so every
valid Milestone 1 config still validates.

### 15.6 SQLite schema version

`sqlite_repo.py` checks `PRAGMA user_version` on every connection. A fresh
database and a pre-1.1 Milestone 1 database (never versioned, reads `0`)
are both stamped `1`; a database whose version is greater than this build's
`_SCHEMA_VERSION` raises `SchemaVersionError` and refuses to run. See
D-026 — 1.1 introduces no schema change, so this is purely the versioning
mechanism itself, not a migration. Milestone 2 performs two separate,
purely additive increments, never reusing a version number for two
different schema shapes (decisions.md D-049/D-050):

- Deliverable 5 step 9 (§20) bumps `_SCHEMA_VERSION` from `1` to `2` —
  a new non-unique `CREATE INDEX IF NOT EXISTS` on
  `job_fingerprints.canonical_url`, nothing else. A `1`-stamped database
  upgrades to `2` the same no-op way on next open, with no data loss.
- Deliverable 5 step 10 (§21) bumps `_SCHEMA_VERSION` again, from `2` to
  `3` — the new `sponsor_registry_entries` table plus two new indexed
  columns on `visa_assessments` (see §21's "Schema" bullet for the full
  list). A `2`-stamped database (step-9-only code) upgrades to `3` the
  same no-op, additive way; code that only understands up to `2` still
  correctly refuses a `3`-stamped database via `SchemaVersionError`,
  preserving D-026's guarantee.

`_SCHEMA_VERSION` is `3` as of the end of Milestone 2 Deliverable 5.

### 15.7 Industry/sector/seniority source-selection signal

`SourceRegistryEntry` gained `industry_coverage` / `sector_coverage` /
`seniority_coverage: list[str] = []` (empty = unrestricted, so every
existing registry entry is unaffected by default). The planner's
`sector_relevance` factor (§6's scoring table) now blends real industry+
sector overlap when a candidate/search profile supplies either; its
`seniority_relevance` factor now computes real overlap against
`seniority_coverage`. Both still fall back to `neutral_prior` exactly when
the corresponding data is absent — no change to `SourceScoringWeights`'
schema (D-024).

### 15.8 Module layout additions

```
src/job_scout/
├── __main__.py            # `python -m job_scout` — same Typer app as job-scout
├── paths.py                # AppPaths + resolve_app_paths() (§15.1)
├── bootstrap.py             # run_init() — job-scout init (§15.3)
├── resources/
│   ├── __init__.py            # template_text()/templates_root() (§15.2)
│   └── templates/               # the six canonical *.example.yaml templates
```

`cli.py` gained `init` and `version` commands; `plan`/`run-once` gained
`--data-dir`. No other module in §12's tree changed shape — the pipeline,
planner, compliance gate, adapters, and repository are structurally
untouched by Milestone 1.1.

## 16. Milestone 2 Deliverable 5 step 1: canonical normalization boundary +
`SourceCapabilities` (implemented)

Confirms and formalises what §2.4/§3 already implied (decisions.md D-040):
`Job` is the single canonical, normalized job model. Every `SourceAdapter`
returns `RawJobRecord` only (source-native, pre-normalisation); exactly one
normalizer function per source (`pipeline.py::_NORMALIZERS`, keyed by
`source_id`) converts `RawJobRecord -> Job`; no stage after that lookup —
deduplication, hard filters, pre-filter, Stage 5 scoring, persistence — ever
branches on `source_id`. No new model was introduced; this is a documentation
formalisation of already-implemented, already-tested behaviour, not a code
change.

### 16.1 `SourceCapabilities` (decisions.md D-041)

`SourceRegistryEntry.capabilities: SourceCapabilities = SourceCapabilities()`
— one typed nested object (not scattered top-level booleans) describing what
a source's own adapter contract actually supports: `keyword_search`,
`exact_phrase_search`, `location_filter`, `country_filter`, `city_filter`,
`industry_filter`, `company_filter`, `remote_filter`, `salary_data`,
`structured_description`, `pagination`, `page_size_control`,
`posting_date_filter`, `stable_external_job_id`,
`canonical_application_url` (all `bool`), and
`max_recommended_queries_per_request: int | None`. Defaults reproduce
`AdzunaAdapter`'s own verified contract (D-016/D-031), so every existing
registry entry — none of which have a `capabilities` key today — keeps
validating and behaving identically. `authentication_required` is
deliberately not a field: `SourceRegistryEntry.auth_required` (§2.7) already
means exactly that.

This field is data only as of this step — nothing in the planner, CLI, or
dedup logic reads it yet. It exists so the query planner (capability-gated
query-mode selection), `job-scout plan`/`job-scout sources` (capability
display), and the cross-source dedup tiers (`canonical_application_url`
gating) have a typed source to read from once those specific pieces of work
land (Milestone 2 Deliverable 5, later steps) — see `MILESTONE_2.md` for the
full consumption design.

## 17. Milestone 2 Deliverable 5 step 4: planned-query execution (implemented)

`pipeline.py::run_once` now calls `adapter.fetch()` once per
`SelectedSource.planned_queries` entry (§2.10, populated by step 3's query
planner), instead of once per source. For each executable, non-empty-plan
selected source:

- `SourceSearchParams` is built once as a template (unchanged fields:
  `countries` — already narrowed to the source's `supported_countries` —
  `role_family_hints`, `employment_types`, `min/max_experience_years`,
  `page_size`, `max_pages`), then `model_copy(update={"keywords": ...,
  "keyword_mode": ...})` substitutes each `PlannedQuery.keywords`/`.mode` in
  turn. `keyword_mode` (§3, new field) is a plain, source-agnostic field
  copy — the pipeline never branches on `source_id` or knows any adapter's
  actual request-parameter names. `AdzunaAdapter.fetch()`'s Protocol
  signature and internal per-country pagination/fail-fast behaviour are
  exactly as before — only the pipeline's call cardinality and the
  `keywords`/`keyword_mode` each call carries changed; `AdzunaAdapter
  ._build_query` gained a small, mode-aware branch (see below) to translate
  that generic intent into Adzuna's actual request parameters.
- Raw records from every query for a source are aggregated into one list
  before normalisation/dedup/scoring, which are otherwise unchanged — the
  existing fingerprint/cross-source-duplicate checks already handle
  duplicate raw observations returned by two overlapping planned queries
  (e.g. "Strategy Manager" and "Corporate Strategy" both matching the same
  vacancy) the same way they handle a repeat fetch across separate runs: one
  canonical `Job` row, provenance merged, `SourceRun.jobs_duplicate`
  incremented. `SourceRun.jobs_fetched` counts raw observations (pre-dedup),
  matching its existing single-query-era meaning.
- A source with zero `planned_queries` (an empty `SearchProfile`/
  `CandidateProfile`, or a capability like `company_filter=True`/
  `keyword_search=False` that the planner already turns into zero queries) is
  never executed and produces no `SourceRun` row at all — mirrors the
  existing `executable=False` skip. There is no fallback to
  `CandidateProfile.title_aliases` once `planned_queries` exists.
- If a query's `adapter.fetch()` call raises a `SourceAdapterError`, the
  source stops attempting its remaining planned queries (fail-fast,
  consistent with the adapter's own existing per-country fail-fast behaviour
  inside one `fetch()` call) but keeps whatever raw records earlier queries
  in the same run already returned. If no query for that source produced any
  data, the run is `FAILED` with zero jobs (identical to the pre-M2
  single-query failure outcome); otherwise it is `PARTIAL`, same as the
  existing `hit_cap`-driven `PARTIAL` case.
- `SelectedSource.search_queries` (§2.10) is now rendered from
  `planned_queries` (one string per `PlannedQuery` — the phrase itself for
  `exact_phrase`, an `" OR "`-joined list for `any_of_words`) instead of the
  M1/1.1 `candidate_profile.title_aliases` list, so it never displays a
  different search than the one `planned_queries`/execution actually use.
  `SourceSearchParams.keywords` on `SelectedSource.search_params` stays the
  legacy per-source template value (still `candidate_profile.title_aliases`)
  — no longer what execution actually sends, since `run_once` overrides it
  per query; kept only for the template's other fields (see above) and
  general backward-compatible shape.

### `SourceSearchParams.keyword_mode` (§3, new field; decisions.md D-045)

`SourceSearchParams` gained one new field, `keyword_mode: Literal
["exact_phrase", "any_of_words"] = "any_of_words"` — the same two literal
values and meaning as `PlannedQuery.mode` (§2.10), so the model stays a
plain, adapter-agnostic carrier of query *intent* (never a source-specific
parameter name). Defaults to `"any_of_words"` so every call site that
predates this field — legacy tests, the per-source template
`source_intelligence/planner.py::build_plan` still constructs — keeps its
pre-M2 OR-query behaviour unchanged without needing to set it explicitly.

`AdzunaAdapter._build_query` (the one, narrowly-scoped adapter change this
correction makes) branches on `keyword_mode` to pick which of Adzuna's two
real, documented query parameters a request uses — never both on the same
request, and never a fabricated quoting syntax:
- `keyword_mode="any_of_words"` -> `what_or` (confirmed OR-of-individual-
  words — the M1/1.1 behaviour, byte-for-byte unchanged).
- `keyword_mode="exact_phrase"` -> `what` — Adzuna's stricter of the two
  documented parameters. Evidence available at implementation time
  (`developer.adzuna.com/docs/search`, plus secondary sources) confirms
  `what` and `what_or` are both real parameters and that `what_or` is
  OR-of-words, but does **not** confirm that `what` guarantees literal
  word-adjacency/quoted-phrase matching — a separate `what_phrase` parameter
  also appears in Adzuna's own documentation, which would be the literal-
  phrase option if one is ever needed. Documented here, per this project's
  evidence bar (decisions.md D-016/D-027/D-028/D-031), as "stricter/
  all-terms-required," not overclaimed as guaranteed phrase-adjacency.

The invariant this correction establishes: `exact_phrase` and `any_of_words`
queries for the same keywords no longer render into an identical Adzuna
request (`test_exact_phrase_and_any_of_words_never_render_identical_requests`,
`tests/test_adzuna_adapter.py`).

## 18. Milestone 2 Deliverable 5 step 7: Greenhouse adapter + watchlist
fan-out (implemented)

`sources/greenhouse.py::GreenhouseAdapter` implements Greenhouse's public
Job Board API (`GET /v1/boards/{board_token}/jobs`, no auth, no documented
pagination — see §3). `pipeline.py::run_once` gained a second execution
path, alongside step 4's planned-query fan-out, for any selected source
whose `SourceRegistryEntry.capabilities.company_filter` is `True`:

- For each `CompanyWatchlistEntry` whose `source_id` matches the selected
  source, `run_once` constructs one fresh `SourceAdapter` via a new
  `WatchlistAdapterFactory` (`Callable[[str, CompanyWatchlistEntry],
  SourceAdapter | None]`, `pipeline.py::_default_watchlist_adapter_factory`
  — the same shape as the existing `AdapterFactory`/
  `_default_adapter_factory`, keyed additionally by the watchlist entry
  since a company_filter=True source needs one adapter instance per
  company, not one shared instance per source_id) and calls `.fetch()`
  exactly once, passing through `SelectedSource.search_params` unchanged
  (`GreenhouseAdapter.fetch()` ignores it — see §3).
- Zero matching `CompanyWatchlistEntry` rows produces zero calls and **no**
  `SourceRun` row for that source at all (MILESTONE_2.md R-10) — identical
  in shape to the existing "zero `planned_queries` -> no `SourceRun`"
  convention step 4 already established, just gated on watchlist matches
  instead of query-planner output for this branch.
- Both branches converge immediately after populating `raw_records:
  list[RawJobRecord]` — aggregation, `SourceRun.jobs_fetched` accounting,
  normalisation (`_NORMALIZERS["greenhouse_public_feeds"] =
  normalize_greenhouse_record`), dedup, hard filters, pre-filter, Stage 5
  scoring, and persistence are all the exact same shared code every other
  source already uses; nothing downstream of `raw_records` branches on
  `source_id` or on whether a source is watchlist-scoped.
- The branch selector is `entry.capabilities.company_filter` — read from
  the registry (decisions.md D-041's own stated mechanism), never a
  `source_id` string check — so Lever (Deliverable 5 step 8) reuses this
  same branch unmodified once its registry entry also ships
  `company_filter=True`; only `_default_watchlist_adapter_factory`'s
  per-source-id adapter-construction dispatch gains a new `elif` branch for
  it, mirroring `_default_adapter_factory`'s existing shape.
- `query_planner.py`/`source_intelligence/planner.py::build_plan` are
  **unchanged** by this step — `build_planned_queries` already returned
  zero `PlannedQuery`s for `company_filter=True` sources (step 3);
  `estimated_request_count`'s existing `len(supported) *
  len(planned_queries) * max_pages` arithmetic therefore still reads `0`
  for Greenhouse regardless of watchlist size — a known, pre-existing
  under-report acknowledged in decisions.md D-047, not a regression this
  step introduces or was asked to fix.

`normalize_greenhouse_record` (`pipeline.py`) follows the `_NORMALIZERS`
dict-dispatch pattern unchanged (D-040): `Location.country` is always `""`
(Greenhouse's list-jobs response has no structured country field — see
decisions.md D-047 for why this is never inferred from the freeform
`location.name`, and its hard-filter consequence); `posted_at`,
`employment_type`, and every salary field are always `None` (not documented
on this endpoint, D-047); `company` is read from
`raw_payload["_company_name"]`, stashed by `GreenhouseAdapter._to_raw_record`
from the `CompanyWatchlistEntry` that produced the fetch (Greenhouse's own
response never names the company); `raw_url` is Greenhouse's own
`absolute_url` — a genuine canonical application URL, unlike Reed's.

## 19. Milestone 2 Deliverable 5 step 8: Lever adapter (implemented)

`sources/lever.py::LeverAdapter` implements Lever's public Postings API
(`GET https://api.lever.co/v0/postings/{site}?mode=json`, no auth — see
§3). Reuses §18's watchlist-fan-out branch **unmodified**
(`entry.capabilities.company_filter`-gated, never a `source_id` check, per
§18's own stated design) — the only pipeline changes are a second `elif`
in `_default_watchlist_adapter_factory` (constructing `LeverAdapter` from
`CompanyWatchlistEntry.external_company_key`/`company_name`, mirroring
Greenhouse's branch) and a second `_NORMALIZERS` entry
(`"lever_public_postings": normalize_lever_record`). `query_planner.py`/
`source_intelligence/planner.py::build_plan` are untouched by this step,
same as §18.

`normalize_lever_record` (`pipeline.py`) follows the `_NORMALIZERS`
dict-dispatch pattern unchanged (D-040), but differs from
`normalize_greenhouse_record` in three respects Lever's richer, verified
contract actually supports (decisions.md D-048):
- `Location.country` uses Lever's own documented `country` field (ISO
  alpha-2 or `null` → `""`) directly — real structured data, not inferred
  from free text, unlike Greenhouse.
- `Job.remote_type` is read directly from Lever's documented `workplaceType`
  enum (`remote`/`hybrid`/`on-site`/`unspecified` → `RemoteType.REMOTE`/
  `HYBRID`/`ONSITE`/`UNKNOWN`), not the shared `_guess_remote_type` text
  heuristic every other source (including Greenhouse) falls back to — using
  a source's own authoritative field is more accurate than guessing when
  that data genuinely exists.
- `salary_min`/`salary_max`/`salary_currency` are read from Lever's
  documented, optional `salaryRange` object when present (absent → `None`,
  never fabricated/defaulted to `0`) — unlike Greenhouse, which has no
  salary field on its list endpoint at all.
`posted_at` stays `None` unconditionally: a live response can carry an
undocumented `createdAt` field, but Lever's own `postings-api` issue
tracker (issue #35) reports its values do not parse into sane timestamps —
never treated as a posting date (same "don't guess an unverified field"
discipline as D-046/D-047). `employment_type` reads `categories.commitment`
when present, else `None`. `company` is read from
`raw_payload["_company_name"]`, stashed by `LeverAdapter._to_raw_record`
the same way Greenhouse stashes it (Lever's own response never names the
company). `raw_url` is Lever's own `hostedUrl` — a genuine canonical
posting URL; the separate `applyUrl` field is preserved on `raw_payload`
for potential future use but not surfaced onto any `Job`/`RawJobRecord`
field, since only one URL field exists to populate.

**Known limitation — no pagination.** Lever's Postings API documents real
`skip`/`limit` query parameters (unlike Greenhouse, which has none at all),
but neither the official docs nor a live, unauthenticated check performed
at implementation time expose a total-count/`hasMore` termination signal.
Per this project's evidence bar (D-016/D-027/D-028/D-031/D-046/D-047 —
never build against an unconfirmed contract), `LeverAdapter.fetch()` makes
exactly one HTTP request per call and never sends `skip`/`limit`;
`SourceCapabilities.pagination=False` records this as a deliberate,
acknowledged limitation. A watchlisted company with more open postings than
one unpaginated response returns will have some postings silently
unfetched until this is revisited with a verified termination signal.

## 20. Milestone 2 Deliverable 5 step 9: cross-source deduplication and
provenance (implemented)

`deduplication.py::DedupTier` gains two new members and `match_against_recent`
gains two new tiers (decisions.md D-038; MILESTONE_2.md "Deduplication
implications"), evaluated in this order ahead of the unchanged repost/distinct
fallback — see §8 for the full, current tier list:

- **`EXACT_DUPLICATE`**: a cross-source match on `JobFingerprint
  .canonical_url` alone, ignoring `external_source_id` entirely — deliberately
  *not* gated on `_same_identity` (company+title+location), since the whole
  point is catching the case where an aggregator's redirect URL and an ATS
  feed's own posting URL resolve to the same real apply page even when the
  two sources' free-text company/location parsing didn't normalise
  identically. Gated by `SourceCapabilities.canonical_application_url`
  (decisions.md D-041) on **both** the new job's and the candidate match's
  originating source — derived from each `JobFingerprint.external_source_id`'s
  `source_id` prefix, looked up in a `source_id -> SourceCapabilities` map the
  caller supplies (`match_against_recent(..., source_capabilities=...)`); a
  source absent from that map defaults to `SourceCapabilities()`'s own `True`
  default, the same convention every other capability consumption point in M2
  uses for an unset `capabilities` block. `pipeline.py::run_once` builds this
  map once per run from the loaded registry
  (`{entry.source_id: entry.capabilities for entry in registry}`) and passes
  it through; `deduplication.py` itself never reads a registry or a
  `source_id` literal.
- **`PROBABLE_DUPLICATE`**: generalises the old (pre-step-9)
  `CROSS_SOURCE_DUPLICATE` tier, which required the `_same_identity`
  precondition **and** a byte-identical `description_fingerprint`. The
  precondition is unchanged and still never optional (MILESTONE_2.md risk
  R-8), but the corroborating signal is now any *one* of three: an identical
  `description_fingerprint` (kept, still checked first since it's the
  strongest available), a bounded token-set (Jaccard) similarity of the two
  jobs' `description_text` (via the existing, shared
  `matching/normalize.py::normalize_tokens` — no new tokenisation logic) at or
  above `PROBABLE_DUPLICATE_JACCARD_THRESHOLD` (0.6), or a `posted_date`
  within `PROBABLE_DUPLICATE_POSTED_DATE_WINDOW_DAYS` (±3 days) of each other
  combined with identical `salary_min` **and** `salary_max` when both jobs
  report salary (a `None` on either side simply means that signal doesn't
  fire — same "absence is not an error" convention D-041 established for
  every other capability-conditioned signal). No embeddings, per explicit
  instruction (decisions.md D-038).

Both new tiers, like the pre-existing `CROSS_SOURCE_DUPLICATE` tier they
extend/replace, merge into the pipeline's existing dedup call site
(`pipeline.py::run_once`) identically: `repository.merge_provenance(...)` is
called against the matched job, `run.jobs_duplicate` increments, and no new
`Job` row is written — `pipeline.py` treats `EXACT_DUPLICATE` and
`PROBABLE_DUPLICATE` as one merge-eligible outcome, the same way it
previously treated `CROSS_SOURCE_DUPLICATE`.

**Persistence** (decisions.md D-038's Workstream F conclusion; MILESTONE_2.md
"Persistence implications" — this is the milestone's first schema change,
`_SCHEMA_VERSION` `1`->`2`, §15.6): `sqlite_repo.py` adds a non-unique
`CREATE INDEX IF NOT EXISTS idx_job_fingerprints_canonical_url ON
job_fingerprints(canonical_url)`, backing the new `EXACT_DUPLICATE` tier's
lookup (the table's existing `PRIMARY KEY (canonical_url,
external_source_id)` already serves Tier 1 efficiently but can't serve a
canonical-URL-only lookup). No new table and no new `SourceObservation` model
— auditing `SqliteJobRepository.merge_provenance` confirmed `source_provenance`
is already an append-only fetch-observation log (a fresh row is inserted on
every call, including repeat fetches of the same source/external-id pair);
the only genuine gap was a missing read method, now added:
`JobRepository.list_provenance(job_id) -> list[SourceProvenance]` (§4),
returning every observation for a job in fetch order (`ORDER BY id ASC`, i.e.
insertion order — `first_seen_at`/`last_seen_at` per source are a `MIN`/
`MAX(fetched_at)` computed by the caller over this list, not a stored column).

## 21. Milestone 2 Deliverable 5 step 10: sponsor registry, UK provider, and
visa enrichment (implemented)

`VisaAssessment` (§2.12) is now actually constructed and persisted, per
scored job, instead of existing only as a reserved-but-unwritten model
(decisions.md D-006/D-050; MILESTONE_2.md "Sponsorship/visa enrichment
design"):

- **New module `matching/visa.py`**: `assess_visa(job, candidate, search,
  registry_match, country_regime) -> VisaAssessment`. `candidate`/`search`
  are accepted for signature parity but never read — work-authorisation
  fields are Stage 1 hard-filter inputs (existing
  `requires_work_authorisation_support` behaviour in `hard_filters.py`), not
  evidence about a specific job, and must never feed `status`. Evidence
  precedence (never blended, decisions.md D-050 point 4): start from
  `unknown`; an authoritative sponsor-registry match raises status to
  `employer_eligible` at the match's own (capped, ~0.7) confidence;
  job-text evidence is applied last — positive wording raises to
  `confirmed_yes`, explicit negative wording sets `confirmed_no`
  **regardless** of a registry match. `confidence` on the result is always
  the confidence of whichever evidence source actually set the final
  status.
- **New module `matching/visa_patterns.py`**: the single source of truth for
  `VISA_POSITIVE_PATTERNS`, `VISA_NEGATIVE_PATTERNS` (aliased as
  `NO_SPONSORSHIP_PATTERNS`), and `first_match()` — previously
  `matching/scoring.py`'s `_visa_relocation_component` and
  `matching/hard_filters.py`'s no-sponsorship check each carried a
  byte-identical private copy of these regex lists; both now import from
  here instead. No matching-behaviour change (decisions.md D-050 point 3).
- **New module `source_intelligence/sponsor_registry.py`**:
  `parse_uk_home_office_csv` (validates the real gov.uk "Register of
  licensed sponsors: workers" export header, raising
  `SponsorRegisterParseError` on a mismatch), `import_sponsor_register`
  (parses + calls `repository.replace_sponsor_registry_entries`, replacing
  — never appending to — the rows for a given `(country, register_name)`
  pair), and `find_sponsor_match(repository, company_name, country) ->
  SponsorRegistryMatch | None` (exact-normalized-name + country lookup via
  `deduplication.normalize_company`, the same normaliser cross-source job
  dedup already uses; returns `None` rather than a `matched=False` object
  when nothing is found). UK is the only implemented provider (D-042
  mandatory scope); no fuzzy/alias matching (R-9) and no live
  download/scrape of any kind — every import is a file the user already
  downloaded.
- **`pipeline.py::run_once` wiring**: for every job whose `MatchResult
  .final_score is not None` (i.e. it reached Stage 5 scoring — a job
  rejected at Stage 1 or filtered at Stage 2 never reached Stage 5 and gets
  no `VisaAssessment` either), the pipeline looks up a registry match via
  `find_sponsor_match`, resolves a country-level regime label via
  `countries.py::resolve_work_permit_regime`, calls `assess_visa`, and
  persists the result via `repository.save_visa_assessment`. This is
  orchestration wiring beside Stage 5, not a new pipeline stage — it never
  feeds back into Stage 5's own separate `visa_relocation` `ScoreComponent`
  (`matching/scoring.py`), which is unchanged.
- **`countries.py::resolve_work_permit_regime(country) -> str`**: a
  best-effort, region-granularity (never per-country) structural-context
  label for `VisaAssessment.country_work_permit_regime` — a plain
  module-level dict keyed by the existing region constants, never raising
  (an unrecognised country resolves to a generic placeholder string). Never
  scored on its own.
- **Schema** (`_SCHEMA_VERSION` `2`→`3` — see §15.6 and decisions.md D-050
  for why this is its own increment, not a reuse of Task 9's `2`):
  - New table `sponsor_registry_entries` (`country`, `registered_name`,
    `normalized_name`, `register_name`, `license_status`, `imported_at`),
    indexed on `(country, normalized_name)` for `find_sponsor_match`'s join.
    `job-scout sponsors import` replaces (`DELETE` then `INSERT`) the rows
    for the given `(country, register_name)` pair on every import.
  - `visa_assessments` (reserved since M1 as `(job_id, data)` only) gains
    two columns via an idempotent, `PRAGMA table_info`-checked `ALTER
    TABLE ... ADD COLUMN`, run unconditionally on every open since a plain
    `CREATE TABLE IF NOT EXISTS` can't widen an existing table: `status
    TEXT` (indexed, `idx_visa_assessments_status`, so a future
    query/report command can filter by visa status without deserialising
    every row's JSON) and `employer_registry_match INTEGER` (denormalized
    alongside the JSON blob, same pattern `match_results` already uses for
    `notification_tier`/`final_score`, but not itself indexed — no stated
    filter use case yet).
  - A database stamped `1` or `2` opens under this code and upgrades to `3`
    the same no-op, additive way every prior version bump has; a database
    stamped newer than `3` still refuses to run via the existing
    `SchemaVersionError`.
- **CLI**: `job-scout sponsors import <file> --country <CC> --register
  <name> [--db-path] [--data-dir] [--env-file]` — parses a file the user has
  already downloaded and reports the number of entries imported; never
  fetches anything itself. `<name>` must be a register `import_sponsor_register`
  recognises (`uk_home_office_sponsor_list` in M2); an unknown name or a
  missing file exits non-zero with a message, never a traceback.
- **Config**: `sponsor_registries.yaml` (new template, copied by `job-scout
  init` alongside the existing seven) is metadata only —
  `SponsorRegisterConfig(country, register_name, enabled)` via
  `config.py::load_sponsor_registries_config` — naming which registers an
  installation has set up, never the register data itself (that lives only
  in `sponsor_registry_entries`, written only by `sponsors import`).
  `AppPaths.sponsor_registries_path` follows the same
  never-copied-back-into-the-repo, per-user-data-directory convention as
  `company_watchlist_path` (§15.1).
- **`models.py`**: `SponsorRegistryEntry` (one imported register row) and
  `SponsorRegistryMatch` (a lookup result: `matched`, `registered_name`,
  `register_name`, `confidence`) — new models; `VisaAssessment`/`VisaStatus`
  themselves are unchanged (§2.12's M2 update above documents the new
  *values* they're constructed with, not a schema change).

## 22. Milestone 2 Deliverable 5 step 11: evaluation tooling (implemented)

A repeatable, offline score-calibration tool (decisions.md D-043/D-051;
MILESTONE_2.md "Evaluation dataset and calibration design") — no pipeline,
network, or persistence involvement, and no change to
`notification_thresholds` or any Stage 5 scoring formula.

- **New module `evaluation.py`**: `run_evaluation(dataset:
  list[EvaluationJobFixture], candidate: CandidateProfile, search:
  SearchProfile, weights: ScoringWeights) -> EvaluationReport` is a pure
  function over the existing Stage 1/2/5 callers
  (`matching.hard_filters.evaluate_hard_filters`,
  `matching.prefilter.run_prefilter`, `matching.scoring.build_match_result`)
  — the same three calls `pipeline.py::run_once` already makes, applied to a
  synthetic `Job` per fixture instead of a fetched one. `load_evaluation_dataset(path)
  -> list[EvaluationJobFixture]` is the one I/O boundary (a YAML file,
  `{fixtures: [...]}`), kept separate so `run_evaluation` itself stays a pure
  function; raises `EvaluationDatasetError` (same shape as `config.py`'s
  `ConfigError`) on a missing/malformed/empty/duplicate-`job_id` dataset
  file.
- **Fixture-to-`Job` construction**: `evaluation.py::_fixture_to_job` builds
  the canonical `Job` model (D-040) an `EvaluationJobFixture` represents by
  reusing `deduplication.normalize_title`/`normalize_company`/
  `compute_fingerprint` — the exact same normalisation helpers every real
  adapter's normalizer already uses — rather than a second, parallel
  normalized representation. `Job.collected_at` falls back to a fixed
  constant (`2026-01-01T00:00:00Z`) when a fixture supplies no `posted_at`,
  so report output is reproducible from one run to the next.
- **`EvaluationReport`/`EvaluationFixtureResult`** (defined in
  `evaluation.py`, not `models.py` — decisions.md D-051 point 3):
  `EvaluationFixtureResult(job_id, label, rationale, hard_filter_passed,
  final_score, notification_tier)` is the per-fixture evidence trail
  (CLAUDE.md hard constraint 5); `EvaluationReport(dataset_size,
  label_counts, precision_at_5, precision_at_10, precision_at_20,
  recall_of_strong_matches, false_positive_rate, hard_filter_correctness,
  ranking_inversions, ranking_inversion_pairs, tier_distribution,
  fixture_results)` is `run_evaluation`'s return value and
  `job-scout evaluate --json`'s payload verbatim (`.model_dump(mode="json")`).
- **Ranking treatment of `final_score is None`** (decisions.md D-051 point
  2): a fixture rejected at Stage 1 or filtered at Stage 2 never receives an
  invented score — `_effective_score`/`_rank_sort_key` sort it strictly
  below every real `[0, 100]` `final_score` (a `-1.0` sentinel used only for
  ranking, never stored back onto the result) for precision@k and the
  ranking-inversions metric. Every other metric reads `notification_tier`/
  `hard_filter_result.passed` directly, which `build_match_result` already
  sets correctly for a `None`-score job with no special-casing needed here.
- **Metrics** (MILESTONE_2.md "Evaluation dataset and calibration design",
  decisions.md D-043): precision@5/@10/@20 (fraction of the top-`min(k, n)`
  ranked fixtures labelled `strong_match`/`adjacent_match`), recall of
  labelled strong matches (fraction of `strong_match` fixtures whose
  `notification_tier` is `priority`/`digest`; vacuously `1.0` when a dataset
  has none), false-positive rate (fraction of `deceptive_false_positive`
  fixtures landing `priority`/`digest`; vacuously `0.0` when a dataset has
  none), hard-filter correctness (fraction of `hard_filter_reject` fixtures
  where `HardFilterResult.passed is False`; vacuously `1.0` when a dataset
  has none), ranking inversions (count + the actual `(higher_label_job_id,
  lower_label_job_id)` pairs, using the label order `strong_match(0) <
  adjacent_match(1) < weak_match(2) < {hard_filter_reject,
  deceptive_false_positive}(3)` from D-043), and threshold-tier distribution
  (`label -> notification_tier -> count`). Every value is described as a
  **relevance score**, never a probability or confidence percentage — this
  wording rule applies to `job-scout evaluate`'s own printed output too, not
  only source code comments.
- **CLI**: `job-scout evaluate --profile <search-profile-id> --dataset
  <path> [--candidate-profile <path>] [--search-profiles <path>]
  [--scoring-weights <path>] [--data-dir <path>] [--json]` (decisions.md
  D-051 point 1) — reuses `plan`/`run-once`'s existing flag convention
  exactly (`--profile` is the search-profile id; `--candidate-profile`/
  `--search-profiles` are AppPaths-resolved-default file paths), not
  MILESTONE_2.md's earlier-drafted, differently-shaped `--search-profile
  <id>` wording. Never calls a source adapter, never touches the network or
  the database (no `SqliteJobRepository` import in `evaluation.py` or the
  CLI command's own code path) — a read-only calibration report.
- **`models.py`**: `EvaluationLabel` (`StrEnum`: `strong_match |
  adjacent_match | weak_match | hard_filter_reject |
  deceptive_false_positive`) and `EvaluationJobFixture(job_id, title,
  description, company, location: Location, employment_type, posted_at,
  label, rationale)` — new models, used only by `evaluation.py`/
  `job-scout evaluate`, never by the core matching pipeline.
- **Fixture dataset** (`tests/fixtures/evaluation/`, decisions.md D-051):
  two self-contained groups, each its own generic `candidate_profile.yaml` +
  `search_profiles.yaml` + `dataset.yaml` (15 fixtures, 3 per
  `EvaluationLabel`) — `strategy_chief_of_staff/` (the shipped example
  profile's own role family, CLAUDE.md/decisions.md D-017) and
  `software_engineering/` (a materially different profession, proving no
  profession-specific code path per CLAUDE.md hard constraint 10). No real
  employer, school, or biographical data in either (hard constraint 8).
  Each dataset includes a deliberately-inserted ranking-inversion pair (one
  `deceptive_false_positive` fixture engineered to clear the Stage 2
  pre-filter while several `weak_match` fixtures in the same dataset do
  not), documented in that fixture's own `rationale`, so the
  ranking-inversions metric has a real pair to detect in normal test runs.

## 23. Milestone 3 D3, Phase 1: semantic backend boundary (implemented)

Implements only the dependency/config/backend boundary decisions.md D-057
finalized — no Stage 5 integration, no `SemanticResult` redesign yet. A
later Milestone 3 D3 phase builds `SemanticResult`/`SemanticMatch`
(decisions.md D-057 point 4), the deterministic chunking contract (point
5), and the rescue-only Stage 5 wiring (point 6) on top of this boundary.

- **New optional dependency**: `fastembed>=0.8.0` under a new
  `[semantic]` extra (`pyproject.toml`), never a core dependency —
  `job-scout run-once`/`evaluate` import and run correctly with it absent,
  the same optionality discipline `[llm]`/`ANTHROPIC_MODEL` already
  established (CLAUDE.md hard constraint 3, decisions.md D-052).
  `[[tool.mypy.overrides]]` adds `ignore_missing_imports` for
  `fastembed.*`/`onnxruntime.*` (no bundled type stubs) — scoped to those
  two module globs only, so `mypy --strict` stays fully strict for every
  first-party module, including `matching/semantic.py` itself.
- **New module `matching/semantic.py`**: the `Embedder` protocol
  (`embed(texts: list[str]) -> list[list[float]]`), `SemanticBackendUnavailable`,
  `FastEmbedBackend`, and `get_default_embedder(model_name, cache_dir) ->
  Embedder`. `fastembed`/`onnxruntime` types never leak past this module
  (decisions.md D-052) — `from fastembed import TextEmbedding` happens
  lazily inside `FastEmbedBackend._loaded_model`, so importing
  `matching/semantic.py`, constructing `FastEmbedBackend`, or calling
  `get_default_embedder` triggers no model load and no network call; only
  a backend's first real `.embed()` call does (decisions.md D-057 point
  2). Any import failure, model-load failure, or embedding-computation
  failure raises `SemanticBackendUnavailable` — no Stage 3 call site
  exists yet in this phase to catch it (that lands with the next D3
  phase).
- **New config surface `SemanticConfig`** (`config.py`): `enabled: bool`,
  `model_name: str = "BAAI/bge-small-en-v1.5"`, `similarity_threshold:
  float` and `rescue_cap: float` (both validated to `[0, 1]`) — loaded by
  `config.load_semantic_config()`, following the exact
  explicit-path-else-`AppPaths`-default-else-packaged-template fallback
  `_load_with_template_fallback` already implements for
  `ExecutionLimits`/`ScoringWeights`/`SourceScoringWeights` (§15.2,
  decisions.md D-013/D-014/D-021). Kept as its own file/model, not folded
  into `ScoringWeights` — Milestone 3 D4 re-tunes `scoring_weights.yaml`
  weights only (`MILESTONE_3.md` D4's own constraint).
- **New packaged template `semantic_matching.example.yaml`**
  (`src/job_scout/resources/templates/`, added to `TEMPLATE_NAMES`):
  `enabled: false` by default; `similarity_threshold`/`rescue_cap` values
  are documented as uncalibrated starting points (decisions.md D-057's
  calibration finding), not a validated default — Milestone 3 D4 tunes
  them empirically via `job-scout evaluate`. Not yet added to
  `bootstrap.py`'s `job-scout init` copy list — `load_semantic_config`'s
  packaged-template fallback already makes the config surface work with
  no local override, so `job-scout init`'s created-file set is unchanged
  in this phase; a later phase adds the copy once Stage 5 integration
  gives a user a reason to hand-edit it.
- **New `AppPaths` fields**: `semantic_matching_path` (`config_dir /
  "semantic_matching.yaml"`, same fallback treatment as
  `scoring_weights_path`) and `embeddings_cache_dir` (`cache_dir /
  "embeddings"`) — the local embedding model's on-disk cache, passed to
  `fastembed` as `cache_dir=`, under the existing per-user cache
  directory, never inside the repository and never fastembed's own
  default OS cache path (decisions.md D-057 point 9). `FastEmbedBackend`
  creates this directory lazily (`Path.mkdir(parents=True,
  exist_ok=True)`) inside `_loaded_model`, not at construction time.
- **Tests**: `tests/test_semantic.py` (Embedder protocol/`StubEmbedder`
  conformance, lazy-construction-triggers-no-import/no-directory
  assertions, `SemanticBackendUnavailable` on a forced `fastembed` import
  failure via `monkeypatch.setitem(sys.modules, "fastembed", None)`);
  `tests/test_config.py`/`tests/test_paths.py` extended for
  `SemanticConfig` loading (template fallback, explicit override, range
  validation) and the two new `AppPaths` fields. The default suite never
  imports `fastembed` and never downloads a model (decisions.md D-057
  point 10) — verified by `test_constructing_default_embedder_triggers_no_model_load_or_import`
  asserting `"fastembed" not in sys.modules` after construction, run
  against a dev environment with the `[semantic]` extra not installed.
