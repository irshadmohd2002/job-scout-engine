# Architecture Decision Records

Short-form ADRs. Each one names the decision, the alternatives considered, and
why the chosen option won. Update this file when a decision changes — don't
delete superseded entries, mark them superseded.

---

### D-001: SQLite for Milestone 1, repository interface abstracts storage
**Decision**: Use SQLite (stdlib `sqlite3`) for all Milestone 1 persistence,
behind a `JobRepository` Protocol.
**Alternatives**: Postgres from day one; an ORM (SQLAlchemy).
**Why**: The spec calls for zero unnecessary infrastructure in Milestone 1 and
explicit repository abstraction for a later swap. A hand-rolled repository
over stdlib `sqlite3` is enough to prove the interface without pulling in an
ORM before there's a second backend to justify one.

### D-002: Only the Adzuna adapter ships in Milestone 1
**Decision**: `AdzunaAdapter` is the only `SourceAdapter` implementation in M1.
**Alternatives**: Also implement Greenhouse/Lever feeds since they're
low-friction public JSON.
**Why**: The spec explicitly caps M1 to "an Adzuna adapter." Greenhouse/Lever
need a priority-company watchlist to be useful (a specific board token per
company), which is itself out of scope for M1. They're modeled in
`source_registry.example.yaml` as `manual_review`/not-yet-adapted to keep the
registry realistic without building the watchlist early. See R-1 in
`architecture.md` for the coverage consequence.

### D-003: Compliance gate is a static rule table, not a scored model
**Decision**: `ComplianceGate.authorize()` is a deterministic lookup
(`approval_status` × `access_mode` → allow/deny), not part of the weighted
source-selection score.
**Alternatives**: Fold compliance into the scoring model as a heavily-weighted
factor.
**Why**: Compliance is a hard boundary ("may this run at all"), not a
preference ("how good is this source"). Mixing them risks a high relevance
score numerically overriding a compliance concern. Keeping them separate also
makes the gate trivially testable in isolation (pure function, small truth
table) — see test plan in `MILESTONE_1.md`.

### D-004: Sources can be "selected" but not "executable"
**Decision**: The planner includes relevant-but-not-yet-approved sources in
`SearchExecutionPlan.selected_sources` with `executable=False`, rather than
moving them straight to `excluded_sources`.
**Alternatives**: Only ever list sources that can run right now.
**Why**: The spec requires the plan to show "required setup actions" per
source, which only makes sense if the plan acknowledges a relevant source
that isn't running yet. `excluded_sources` is reserved for sources that are
irrelevant (no geographic/role overlap) or explicitly disqualified
(deprecated/blocked/redundant), which is a meaningfully different signal to
show the user than "relevant but not wired up yet."

### D-005: Stage 1 (hard filters) is the only rejecting stage
**Decision**: Stages 2 and 5 can score a job arbitrarily low but never set
`notification_tier: rejected`; only Stage 1 rejects.
**Alternatives**: Let the Stage 2 pre-filter threshold hard-reject too.
**Why**: The spec is explicit that missing one secondary skill must not reject
a job, and more generally that scoring should stay transparent rather than
silently dropping jobs a human might have kept. A low pre-filter score still
gets persisted with `store_only`-equivalent treatment; only Stage 1's
enumerated hard-eligibility rules (country, citizenship, explicit
no-sponsorship, etc.) are allowed to reject outright, each with evidence.

### D-006: No sponsor-registry enrichment in Milestone 1
**Decision**: `VisaAssessment.employer_registry_match` stays `None` throughout
M1; visa status comes only from job-text evidence.
**Alternatives**: Do a minimal UK sponsor-register lookup since it's a public
CSV, since the spec calls out several countries with real registries.
**Why**: Spec explicitly excludes "sponsor-registry enrichment" from M1's
scope list. Building the model to support it now (full `VisaAssessment` shape)
without wiring the registry lookup keeps the later milestone additive rather
than a schema change. See R-4.

### D-007: Deterministic proxies stand in for Stage 3/4 in Milestone 1's Stage 5
**Decision**: Stage 5 scoring runs in M1 using keyword/phrase-overlap proxies
for components that the spec ultimately wants semantic/LLM-backed
(responsibilities, sector relevance).
**Alternatives**: Skip those score components entirely until Stage 3/4 exist;
or delay Stage 5 to a later milestone.
**Why**: The M1 acceptance command requires a "basic deterministic score" with
visible components today. Building the component *slots* now (with a clearly
weaker M1 computation) means Stage 3/4 land as an upgrade to an existing
component's calculation, not a new scoring dimension bolted on later.

### D-008: Fingerprint-first deduplication, fuzzy match as a second tier
**Decision**: Exact fingerprint match is the primary dedup path; cross-source
fuzzy matching (same company+title+location+description hash) is a secondary
tier evaluated against a recent-jobs window, not the whole table.
**Alternatives**: A single fuzzy-matching pass over everything (e.g.
similarity search from day one).
**Why**: At Milestone 1 volumes (one adapter, one country set) an O(recent
window) fuzzy pass is cheap and sufficient; a full similarity index is
infrastructure the spec says to avoid until it's needed.

### D-009: Search profiles and the source registry stay YAML-first through M1
**Decision**: `CandidateProfile`, `SearchProfile`, and `SourceRegistryEntry`
are loaded from YAML files at run time; the repository does not persist or
mutate them in Milestone 1.
**Alternatives**: Load the registry into SQLite so `last_successful_run` etc.
can be updated in place.
**Why**: There's no dashboard or discovery-write-back process yet, so a
database copy of config would just be a second source of truth to keep in
sync for no consumer. `SourceRun` records (which *do* go to SQLite) are enough
to reconstruct "when did this source last succeed" for now.

### D-010: `search_discovery` is never auto-executable
**Decision**: Regardless of `approval_status`, the compliance gate treats
`access_mode: search_discovery` as non-executable for collection; it exists
only to support the discovery process producing candidate registry entries.
**Alternatives**: Allow it under `approved` like other read-only modes.
**Why**: This mode typically implies search-engine-result scraping, which
carries its own terms-of-service exposure distinct from a stable feed/API. The
project's ground rules require permission clarity before automation; a mode
whose *definition* is "discovery, not verified access" cannot itself satisfy
that bar. See R-5.

### D-012: Flat `models.py`, no adapter factory, no premature infrastructure
**Decision**: Collapsed the originally-proposed 8-file `models/` package into
a single `models.py`; confirmed no dependency-injection container, abstract
factory, plugin-loading mechanism, event bus, migration framework, Postgres
implementation, async processing, or deep inheritance hierarchy appears
anywhere in Milestone 1's module layout (`architecture.md` §12).
**Alternatives**: Keep the package-per-concept split for "future-proofing";
add an adapter registry/factory now so a second adapter is "just config"
later.
**Why**: At M1's actual size (~15 small models, one adapter), that structure
was organisational overhead with no present consumer — nothing needs to
import a subset of models, and there is nothing to abstract over with a
single concrete adapter. The project's own ground rules ("do not add
unnecessary frameworks," "don't design for hypothetical future
requirements") apply directly here. `SourceAdapter` and `JobRepository`
remain as `Protocol`s because they're real seams for named, planned future
work (a second adapter in Milestone 2; a Postgres backend later) — that's
the difference between a justified interface and speculative abstraction.
Confirmed with the user on 2026-08-03 as an explicit "keep M1 small" pass.

### D-013: `config/scoring_weights.yaml` added at implementation time
**Decision**: Added `config/scoring_weights.example.yaml` (and the matching
`config/scoring_weights.yaml` local-override slot, loaded by `config.py`)
to hold Stage 5's eight component weights plus the Stage 2 pre-filter
threshold. Falls back to the tracked `.example` file when no local override
exists, following the same pattern `architecture.md` section 11 already
defines for `execution_limits.yaml`.
**Alternatives**: Hard-code the weights table from `architecture.md` section
10 directly in `matching/scoring.py`.
**Why**: Section 10 already states "Weights are config, not code
(`config/scoring_weights.yaml` — to be added at implementation time)" — this
file was referenced but never created. It contains no personal data (pure
algorithm tuning, like `execution_limits.yaml`), so it gets the same
example-fallback treatment rather than the no-fallback treatment used for
`candidate_profile.yaml`/`search_profiles.yaml`/`source_registry.yaml`.
Notification tier thresholds (priority/digest score cutoffs) were
deliberately *not* added here — they already live in
`CandidateProfile.notification_thresholds` / `SearchProfile.notification_thresholds`
(section 2.2/2.3), so duplicating them in the weights file would create two
sources of truth for the same value.

### D-014: `config/source_scoring_weights.yaml` added at implementation time
**Decision**: Added `config/source_scoring_weights.example.yaml` holding the
ten source-selection factors from `architecture.md` section 6's scoring
table, plus `neutral_prior` and `diversity_duplicate_rate_threshold`. Same
example-fallback pattern as `execution_limits.yaml`/`scoring_weights.yaml`
(D-013).
**Why**: Section 6 states directly: "All weights and the neutral-prior value
live in config (not hard-coded)." This is a distinct scoring system from
Stage 5's job-match scoring (`scoring_weights.yaml`) — the planner uses it to
rank *sources*, not jobs — so it gets its own file rather than overloading
`scoring_weights.yaml` with unrelated fields.

### D-015: Guardrail country truncation preserves profile order, not a
computed score
**Decision**: When a search profile's resolved country list exceeds
`max_countries_per_run`, the planner keeps the first N countries in the
order the search profile lists them, and records the rest as skipped (via a
`diversity_notes` entry per country) — it does not attempt to re-rank
countries by a "source-selection score."
**Alternatives**: `architecture.md` section 11a's prose says the planner
"truncates to the first `max_countries_per_run` countries **by
source-selection score**." Taken literally that's underspecified —
source-selection scores are computed per (source, country-set), not per
individual country, so there is no single well-defined per-country score to
sort by before any source has been evaluated.
**Why**: The project's own more detailed planner requirements are explicit
and unambiguous here: "Preserve the exact country priority and order defined
in that profile" and, on hitting the limit, "process countries according to
the configured profile priority... include all remaining countries in the
plan... mark them as skipped." Taking "the first N countries" literally (in
profile-listed order) satisfies both that explicit instruction and the
literal words "first N countries" in section 11a, without inventing a
per-country scoring scheme the schema doesn't support. Treated as a minimal
documented correction per this session's working instructions (resolve
ambiguity with the smallest change, log it here) rather than a redesign.

### D-016: R-1 (Adzuna coverage) investigated, not resolved — example
registry left unchanged
**Decision**: Verified the Adzuna API's real endpoint contract during
implementation (`GET /v1/api/jobs/{country}/search/{page}`, `app_id`/`app_key`
query auth, `results[]` response shape with `id`/`title`/`company.display_name`/
`location`/`redirect_url`/`created`/`description`/`salary_min`/`salary_max`/
`contract_time`) via Adzuna's own developer docs. Country coverage itself
could not be fully confirmed from a single authoritative source — secondary
sources disagree on whether the API covers 12 or ~18 countries, and whether
`IN`/`IE` specifically are included (one source lists `IN` among Adzuna's
core countries, contradicting `source_registry.example.yaml`'s comment that
excludes it). Left `config/source_registry.example.yaml`'s
`geographic_coverage` list unchanged rather than editing it against
unconfirmed secondary sources.
**Why**: `architecture.md` R-1 already flags this as "verify against Adzuna's
current docs at implementation time" and explicitly frames the registry's
list as "this project's working assumption, not a verified fact" — so this
is expected residual uncertainty, not a defect. The registry is real user
config data the user edits themselves (`config/source_registry.yaml`, not
committed); the example file's exact country list is illustrative, not
load-bearing for correctness (the planner's per-source-country exclusion
logic is what's actually under test, and it's covered regardless of which
countries are in the fixture). The adapter itself does not hard-code or
validate a country whitelist — it only queries whatever countries the
planner already resolved as supported via the registry's
`geographic_coverage`, so this uncertainty has no code-correctness impact.
Flagging here so the user can verify their real `config/source_registry.yaml`
against current Adzuna docs before relying on it.

### D-011: CLI ships both `run-once` and `plan` in Milestone 1
**Decision**: Confirmed. In addition to the required `run-once` acceptance
command, M1 includes `job-scout plan --profile X`, which prints the
`SearchExecutionPlan` without calling any adapter.
**Alternatives**: `run-once` only, exactly matching the acceptance command
literally.
**Why**: Cheap to build (it's a strict subset of `run-once`'s first six
steps), directly unit-testable in isolation from the Adzuna adapter, and lets
the user sanity-check source selection/compliance decisions without spending
API quota. Confirmed with the user on 2026-08-03 before implementation
started.

---

## Milestone 1.1 — Profession-agnostic and locally distributable foundations

See `MILESTONE_1_1.md` for the full scope contract. The ADRs below cover the
decisions the milestone's own working instructions required to be documented
explicitly before implementation started.

### D-017: The engine's job-matching logic is profession-agnostic; every
profession-specific input is configuration, not code
**Decision**: Nothing under `src/job_scout/` may hard-code a title, skill,
role family, industry, sector, qualification, or scoring keyword that only
makes sense for one profession (e.g. the old fixed MBA/management education
keyword list in `matching/scoring.py`). Every such input comes from the
loaded `CandidateProfile`/`SearchProfile`/`SourceRegistryEntry` for that run.
**Alternatives**: Ship a `StrategyConsultantProfile` subclass or a
profession-specific scoring module alongside a generic one.
**Why**: The whole point of Milestone 1.1 is that a nurse, a software
engineer, and a management consultant configure the *same* engine
differently — not that the engine ships parallel code paths per profession.
Subclassing or parallel modules is exactly the "profession-specific Python
subclass" this milestone's brief explicitly excludes; a flat, profile-driven
pipeline was already Milestone 1's architecture (`architecture.md` section
10), so this is a correction of one leftover hard-coded list
(`_EDUCATION_KEYWORDS`), not a redesign. See `MILESTONE_1_1.md` "Profession
lock-in."

### D-018: Job Scout Engine is a locally installable, single-user
application with resources and mutable user data kept separate
**Decision**: `AppPaths` (`src/job_scout/paths.py`) formalises a hard split
between *installed application resources* (the package itself — code,
packaged templates under `src/job_scout/resources/`, both read-only at
runtime) and *mutable user data* (a per-user data directory resolved via
`platformdirs`, containing that person's real config, SQLite database,
logs, and cache — all writable, all outside the package/repo). No installed
codepath ever writes into its own package directory, and no default path
assumes the process's current working directory is the repository root or
even a writable location.
**Alternatives**: Keep Milestone 1's model (config beside the repo,
database at a CWD-relative `./data/` path) and just document that installed
users should `cd` into a writable directory first.
**Why**: The requirement is explicit — "another person can install it on
their own computer and use their own profiles, credentials and database
without access to this repository." A CWD-relative default cannot satisfy
that: it silently breaks the moment the installed command is invoked from
anywhere other than a specific directory, and it gives an installed user no
principled place to look for "where did my data go." `platformdirs` is the
standard, cross-platform (Windows/macOS/Linux) way to answer that question
without hand-rolling per-OS path logic. See `MILESTONE_1_1.md` "Repo
lock-in."

### D-019: Every user supplies their own profile and credentials; the
engine never ships or bundles either
**Decision**: `job-scout init` copies only generic, profession-agnostic
placeholder templates (see D-020) into a user's data directory — never a
real candidate profile, never a populated `.env`, never any credential
value. `CandidateProfile`/`SearchProfile`/`SourceRegistryEntry` keep
Milestone 1's no-auto-fallback behaviour (`config.py`'s
`load_candidate_profile`/`load_search_profiles`/`load_source_registry` still
raise `ConfigError.missing_file` rather than silently substituting a
placeholder — decisions.md's original config.py comments already establish
this for candidate data specifically).
**Alternatives**: Have `init` prompt interactively for basic profile details
so the user gets a partially-filled starter file.
**Why**: Interactive prompts are explicitly excluded ("Avoid interactive
prompts"), and CLAUDE.md hard constraint 8 already forbids real personal
data in any tracked file — extending that principle, no code path in this
project should *originate* a real credential or profile value on the user's
behalf either. The user always supplies both explicitly, by editing the
copied template or setting real environment variables.

### D-020: Milestone 1.1 ships distribution *foundations*, not an installer
**Decision**: `AppPaths`, packaged templates, and `job-scout init` make the
engine installable and runnable from anywhere on a user's machine via
`pip install` (editable or not). Milestone 1.1 does **not** produce a
standalone executable, a PyInstaller/Docker artifact, or any package-manager
listing beyond the wheel/sdist Python packaging already produced.
**Alternatives**: Bundle a PyInstaller build of `job-scout.exe` now that
distribution is otherwise in scope.
**Why**: Explicitly excluded by this milestone's brief. It's also the right
scope boundary: "can a second person `pip install` this and run it with
their own data" is a materially smaller, more tractable claim than "can a
non-technical person double-click an installer," and conflating the two
would pull in packaging-format decisions (PyInstaller vs. Docker vs. an OS
package) this milestone was never asked to make. See D-024 for a concrete
consequence of this boundary on this development machine.

### D-021: Packaged templates under `src/job_scout/resources/templates/`
are the single canonical copy; `config/*.example.yaml` is retired
**Decision**: The six example config files (candidate profile, search
profiles, source registry, execution limits, scoring weights, source
scoring weights) now live in exactly one place — package data loaded via
`importlib.resources` — and are the templates `job-scout init` copies.
`config/*.example.yaml` no longer exists as a second, hand-maintained copy;
`config.py`'s existing example-fallback behaviour for
`execution_limits`/`scoring_weights`/`source_scoring_weights` (D-013/D-014)
now reads the same packaged template in-memory instead of a CWD-relative
`config/*.example.yaml` file.
**Alternatives**: Keep `config/*.example.yaml` for git-repo browsing/dev
convenience and add the packaged copies alongside them as a second set.
**Why**: The milestone's own instruction is explicit — "Use one canonical
copy of each template. Avoid two copies that can diverge." Two physically
separate files with the same intended content are exactly the drift risk
that instruction is naming; deleting the repo-local copy and sourcing
`README.md`'s bootstrap instructions from `job-scout init` instead removes
the duplication entirely rather than managing it. This is a superset of
D-013/D-014's fallback behaviour, not a reversal of them — the *fallback
still exists*, only its source changed from a repo-relative file to package
data, which is also what makes the fallback work correctly for an installed
user whose current working directory has no `config/` directory at all.

### D-022: `seniority_level` (the Milestone 1 consulting-ladder enum) is
relaxed to optional rather than removed; a free-text `seniority` field is
added alongside it
**Decision**: `CandidateProfile.seniority_level: SeniorityLevel` (required
in Milestone 1) becomes `seniority_level: SeniorityLevel | None = None`.
A new `seniority: str | None = None` free-text field is added for
profession-agnostic use (e.g. "Senior Staff Nurse", "Senior Software
Engineer") — scoring/hard-filter code prefers `seniority` when set, and
falls back to `seniority_level`'s value when it isn't, matching Milestone
1's existing behaviour exactly for any config that still sets it.
**Alternatives**: Remove `SeniorityLevel` entirely and replace it with a
free-text field; or add a fully separate profession-specific enum per
domain.
**Why**: `SeniorityLevel`'s values (`associate|manager|senior_manager|
associate_director|director`) are consulting-career-ladder terminology —
not universally applicable (a nurse or a software engineer doesn't hold an
"associate director" title), so it cannot stay the *only* option without
violating D-017. But every valid Milestone 1 `candidate_profile.yaml`
already sets this field, and relaxing (not removing) a required field to
optional is additive/non-breaking, while removing it would be a breaking
schema change for no compatibility benefit. A second profession-specific
enum per domain is exactly the parallel-code-path pattern D-017 rules out.

### D-023: `python -m job_scout` is documented as the supported fallback
entry point because Windows Application Control blocks the generated
`job-scout.exe` on this development machine
**Decision**: `src/job_scout/__main__.py` makes `python -m job_scout <cmd>`
fully equivalent to the `job-scout` console-script entry point for every
command. `README.md` documents it as the supported fallback, not merely an
implementation detail.
**Alternatives**: Treat this as a local machine quirk not worth documenting;
try to work around the OS policy from inside the application.
**Why**: This project's own ground rules are explicit — "Do not bypass or
alter Windows security policy." The correct response to a generated
`.exe` being blocked by this machine's Application Control policy is a
supported code path that never produces or depends on an `.exe` at all, not
a workaround of the policy. `python -m <package>` is a standard, portable
Python mechanism (not a bypass of anything), so documenting it as the
primary supported path for this environment costs nothing and unblocks
real usage here without touching OS policy.

### D-024: Industry/sector/seniority source-selection relevance is blended
into the existing `SourceScoringWeights` factors, not added as new weighted
dimensions
**Decision**: `SourceRegistryEntry` gains `industry_coverage` /
`sector_coverage` / `seniority_coverage` (all `list[str] = []`, empty =
unrestricted). The planner's existing `sector_relevance` factor now blends
real industry+sector overlap when a candidate/search profile supplies
either, and the existing `seniority_relevance` factor (previously always
`neutral_prior`) now computes real overlap against `seniority_coverage`.
No new top-level weight field is added to `SourceScoringWeights`.
**Alternatives**: Add a dedicated `industry_relevance` weight field
(`architecture.md` section 6's table only reserves `sector_relevance`, not
a separate industry slot).
**Why**: Adding a new weighted dimension forces every existing
`config/source_scoring_weights.yaml` a user already has to be edited to
re-sum to 1.0, or the config load fails — a breaking change to real user
config for a feature that's additive by nature. Blending industry data into
the already-reserved `sector_relevance` slot delivers the same practical
outcome (source ranking now responds to industry/sector data when present)
without forcing that edit. `role_family_relevance` already plays an
equivalent "profession relevance" role per `architecture.md` section 6, so
no separate slot was needed there either.

### D-025: New `SearchProfile` hard filters are opt-in via
`HardFilterToggles`; Milestone 1's existing unconditional filters are
unchanged
**Decision**: The new generic hard-filter inputs this milestone adds
(required skills/qualifications/certifications/licences, included/excluded
keywords, minimum salary) only reject a job when the matching
`SearchProfile.hard_filters.<toggle>` is explicitly `True`. Milestone 1's
pre-existing filters (`mandatory_qualifications`, `required_languages`,
`excluded_industries`, `excluded_role_families`, explicit-no-sponsorship,
etc.) keep their Milestone 1 behaviour exactly — they still reject
unconditionally whenever the relevant list is non-empty, with no new toggle
gating them.
**Alternatives**: Gate every hard filter, old and new, behind the same
toggle mechanism for consistency.
**Why**: The milestone's own instruction is explicit and scoped to the new
inputs: "Only explicitly enabled hard filters may reject a job" appears in
the section introducing the *new* generic fields (task F), not as a
retroactive change to Milestone 1's already-shipped, already-tested filter
behaviour. Retrofitting toggles onto the existing filters would be a
behaviour change to working, accepted Milestone 1 functionality with no
request behind it, and would require every existing `search_profiles.yaml`
to be edited to keep working as before.

### D-026: An existing Milestone 1 SQLite database is stamped schema
version 1 on first open under Milestone 1.1, not treated as version 0
needing migration
**Decision**: `PRAGMA user_version` is checked on every connection. A
freshly created database and a pre-1.1 Milestone 1 database (which has
never set `user_version`, so it reads `0`) are both stamped `1` the first
time they're opened by 1.1 code, distinguished only by whether the `jobs`
table already exists (both cases are schema-identical either way — 1.1 adds
no new tables/columns to `sqlite_repo.py`'s schema). A database whose
`user_version` is *greater* than the version this build understands raises
a clear, named error and refuses to run.
**Alternatives**: Require every pre-1.1 database to be deleted and
recreated; add a real migration framework now.
**Why**: "Never overwrite or delete an existing database during init" is an
explicit requirement, and a migration framework is an explicit exclusion
("Do not add ... complex migrations"). Since 1.1 doesn't actually change
the SQLite schema, the only thing `user_version` needs to do yet is exist
and be checkable — stamping both "never versioned" and "freshly created" as
the current version 1 is correct in both cases today, and the
greater-than-supported check is what makes the mechanism meaningful for the
future without building anything migration-shaped now.
