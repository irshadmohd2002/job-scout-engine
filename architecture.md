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
`duplicate_rate: float | None`, `last_successful_run: datetime | None`.

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
D-029): `config_status` stays exactly what §4 describes below — static,
user-maintained registry metadata, never written back by the engine.
`effective_config_status` is a live view computed by `build_plan` from an
optional `EnvConfig`: for `adzuna_api` it's `configured` when
`ADZUNA_APP_ID`/`ADZUNA_APP_KEY` are both present, else `needs_credentials`,
regardless of what the registry YAML declares; every other source_id (no
adapter/credential rule implemented yet) falls back to its declared
`config_status` unchanged. `build_plan(..., env=None)` (the default) leaves
`effective_config_status == config_status` for every source — existing
callers that don't pass `env` see no behaviour change. Never derived from or
displaying a secret value, only a boolean-derived enum.

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

### 2.13 `SourceRun`
`run_id`, `source_id`, `search_profile_ref`, `started_at`, `completed_at`,
`status` (enum: `success|partial|failed`), `jobs_fetched`, `jobs_new`,
`jobs_duplicate`, `errors: list[str]`.

### 2.14 Reserved for later milestones (schema defined now, not written to in M1)
`NotificationRecord`, `UserFeedback`, `ApplicationStatus`, `SourcePerformance`,
`CompanyWatchlistEntry`. Defining these now means the repository interface
(§4) does not need a breaking change when they're implemented.

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
`approved`).

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

    # Defined for interface stability; no-op or NotImplementedError-free stub
    # in M1's SQLite implementation until the relevant milestone lands.
    def save_visa_assessment(self, assessment: VisaAssessment) -> None: ...
    def save_notification(self, record: NotificationRecord) -> None: ...
    def save_feedback(self, feedback: UserFeedback) -> None: ...
    def save_application_status(self, status: ApplicationStatus) -> None: ...
    def save_source_performance(self, perf: SourcePerformance) -> None: ...
```

M1 ships `SqliteJobRepository` implementing the first six methods fully
(`jobs`, `source_provenance`, `source_runs`, `match_results`,
`job_fingerprints` tables) with real schema. The remaining methods exist on the
Protocol and have a table reserved in the SQLite schema, but the schema
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

Matching tiers, applied in order at ingestion:
1. **Exact**: same `canonical_url` + `external_source_id` → same job. Merge
   provenance, keep the earliest `posted_at`.
2. **High-confidence cross-source duplicate**: same `normalized_company` +
   `normalized_title` + `normalized_location` + identical
   `description_fingerprint` → same underlying job posted to multiple sources.
   Merge provenance.
3. **Repost**: same company+title+location, *different* description
   fingerprint, and existing job's `posted_at` is older than a configurable
   gap (default 21 days) → new `Job` row, linked via `previous_job_id`, subject
   to the repost notification policy (do not re-notify unless materially
   changed or repost policy allows).
4. Anything else → distinct job.

`find_by_fingerprint` in the repository only implements tier 1 lookup
efficiently (indexed exact match); tiers 2–3 are pipeline-level comparisons
against a recent-jobs window (`list_recent_jobs`), acceptable at Milestone 1
volumes.

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

### Stage 3 — Semantic similarity (not in M1)
Interface reserved (`SemanticResult` field on `MatchResult`). Will use
embeddings to catch equivalents like "Head of Special Projects" ↔ strategic
initiatives.

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

**Best-match title/role-family scoring (decisions.md D-032, Parts 1–3)**: the
original formula divided every matched phrase by the *entire* configured
title/role-family vocabulary, which mechanically diluted an exact
target-title match by however many *other* titles a profile happened to
configure. `title_role_family`'s raw score is now
`(best_title_match_strength + best_role_family_match_strength) / 2`, where
each "best match" is the single strongest configured phrase match — never
an average or ratio over the full vocabulary, so configuring additional
unrelated titles/role-families can never lower an existing exact match's
score. Matching itself reuses Stage 2's `matching.normalize.match_phrase`
(exact normalised phrase, or token-coverage ≥ the same
`PrefilterWeights.strong_title_coverage` threshold for multi-word phrases) —
the same function in both stages, so a job that clears Stage 2 on
token-coverage title evidence is guaranteed non-zero credit for that same
evidence at Stage 5 (previously Stage 5 used exact-substring-only matching,
so some jobs passed the Stage 2 gate on evidence Stage 5 then scored as
zero). Each match is further scaled by: field (a title-field match always
outweighs a description-only match, mirroring Stage 2's
`desc_only_damping`) and provenance tier (an active `SearchProfile` signal —
`target_titles`/`title_aliases`/`role_families` — outweighs a
`CandidateProfile` signal of otherwise-equal match quality, and
`CandidateProfile.previous_titles` ranks lowest of all, so a candidate's
purely historical job titles cannot automatically outrank this run's actual
search targets).

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
│                         # search profiles, source registry, ExecutionLimits),
│                         # validation, specific error messages
├── countries.py          # ISO country → region lookup (§5)
├── models.py              # every Pydantic model in §2 (Location through
│                          # SourceRun) — one file; see rationale below
├── deduplication.py        # fingerprinting + tiered matching (§8)
├── source_intelligence/
│   ├── __init__.py
│   ├── registry.py          # load/validate source registry YAML
│   ├── planner.py            # §6 — builds SearchExecutionPlan
│   └── compliance.py          # §7 — ComplianceGate.authorize()
├── sources/
│   ├── __init__.py
│   ├── base.py                # SourceAdapter Protocol, RawJobRecord, exceptions
│   └── adzuna.py                # AdzunaAdapter — the only adapter in M1
├── matching/
│   ├── __init__.py
│   ├── hard_filters.py           # Stage 1
│   ├── prefilter.py                # Stage 2
│   └── scoring.py                   # Stage 5 (score components carry the
│                                     # reserved-but-unused Stage 3/4 hooks as
│                                     # optional model fields, not extra files)
├── repository/
│   ├── __init__.py
│   ├── base.py                       # JobRepository Protocol (§4)
│   └── sqlite_repo.py                  # SqliteJobRepository
├── pipeline.py                          # orchestrates the sequence in §11
└── cli.py                                # Typer app: run-once, plan
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

Milestone 1 has exactly one adapter (`AdzunaAdapter`). `pipeline.py` imports
it directly and calls it for any `SelectedSource` where
`source_id == "adzuna_api"` and `executable` is true. There is no adapter
registry, factory, or `adapter_ref`-keyed dynamic-import mechanism — that
kind of plugin-loading indirection is explicitly out of scope (see below) and
has nothing to abstract yet with a single concrete adapter. Revisit only when
a second adapter actually exists.

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

`src/job_scout/resources/templates/` ships the six canonical, generic config
templates (candidate profile, search profiles, source registry, execution
limits, scoring weights, source scoring weights) as package data, read via
`importlib.resources` (`job_scout.resources.template_text(name)`). This is
the *only* copy of these templates (D-021) — `config/*.example.yaml` no
longer exists. `pyproject.toml`'s `[tool.hatch.build.targets.wheel]`/
`[tool.hatch.build.targets.sdist]` configuration ensures they're included in
editable installs, wheels, and sdists alike.

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
to edit and that credentials are supplied separately.

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
mechanism itself, not a migration.

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
