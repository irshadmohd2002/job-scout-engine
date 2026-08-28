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

### D-029: Stage 2 pre-filter reads SearchProfile signals and gates on a
single strong title/role match, not just CandidateProfile ratio averages
**Decision**: A live UK Adzuna run (150 jobs fetched, only 1 scored)
diagnosed a matching-quality bug, not a source or dedup bug: `run_prefilter`
only ever read `CandidateProfile.title_aliases`/`role_families`/skills,
never `SearchProfile.target_titles`/`title_aliases`/`role_families`/
required-preferred-skills/`included_keywords`; matching was strict
contiguous-substring containment with no punctuation normalisation
(`&`, `/`, hyphens all broke otherwise-correct matches); and the four
ratio-averaged categories meant one perfectly-matched signal (e.g. a single
`title_aliases` hit out of 4 configured aliases) capped below the
`prefilter_threshold` on its own. Fixed by: (1) a new shared
`matching/normalize.py::normalize_text` used consistently by both the
pre-filter and Stage 5 scoring's `keyword_overlap`; (2) `run_prefilter` now
takes `(job, candidate, search, weights: PrefilterWeights)` and folds in
`SearchProfile`'s title/role/skill/keyword/industry-sector signals alongside
`CandidateProfile`'s; (3) a strong title/role evidence gate — a configured
target title/title alias/role family that exactly matches the normalised
job title, or covers >= `PrefilterWeights.strong_title_coverage` (0.75) of
its own tokens in the title — now passes Stage 2 on that evidence alone,
independent of the weighted ratio score; the pre-existing weighted score
remains as an additive fallback for weaker/description-only evidence.
`PrefilterWeights` is a new small type (`matching/prefilter.py`), not a YAML
schema change — only its `threshold` field is sourced from the existing
user-tunable `scoring_weights.yaml: prefilter_threshold`
(`PrefilterWeights.from_scoring_weights`); the category sub-weights keep the
same "not independently config-tunable" status the original module-private
constants had. Stage 5's `title_role_family` component was extended to
match: it now also reads `SearchProfile.target_titles`/`title_aliases`/
`role_families`, tagging evidence by provenance (`search_target_title:`,
`search_role_family:`, …) so a job that passed Stage 2 on search-profile
evidence doesn't have that evidence silently dropped at Stage 5.
**Alternatives**: Lowering `prefilter_threshold` alone (rejected — most of
the audited failures scored exactly `0.0` evidence, so a lower threshold
alone would not have helped, and would have widened recall indiscriminately
rather than fixing the actual evidence gap); token-set (bag-of-words, order
-independent) matching everywhere including the weighted score (rejected —
kept the existing substring-containment semantics for the *additive* score
to minimise behaviour change/risk there, and reserved the more permissive
token-coverage rule for the new strong gate specifically, where its
structural safeguard — requiring most/all of a multi-word phrase's own
tokens — is what keeps single generic words like "consultant" from
completing a match by themselves); changing `SourceSearchParams`/Adzuna
`what_or` query construction in the same change (rejected — out of scope for
this fix per explicit instruction; the noisy broad query is a separate,
documented next tuning item, see below).
**Why**: CLAUDE.md hard constraint 10 (profession-agnostic — no hard-coded
vocabulary) and constraint 6 (no hard-coded single source list) both point
the same direction here: the fix has to come from *reading more of the
already-configured profile data* and *normalising comparison structurally*,
never from adding profession-specific terms to `src/job_scout/`. The
strong-gate token-coverage ratio is deliberately a pure structural
calculation (matched-tokens / phrase-tokens), not a stopword list, so it
stays profession-agnostic while still refusing to let one common word stand
in for an entire configured multi-word phrase.
**Known next tuning item (explicitly not fixed here)**: `SourceSearchParams
.keywords`/Adzuna's `what_or` query is still built from
`candidate_profile.title_aliases` only (`planner.py`), which Adzuna's
OR-word semantics turn into an OR over individual words — this is what
produces most of the noisy recruitment/sales/travel-consultant volume in the
fetched set. This fix only changes which *already-fetched* jobs reach Stage
5 scoring; it deliberately does not touch retrieval-query construction, per
explicit instruction, so the live result distribution can be inspected
before redesigning the query (e.g. sourcing `what_or`/multiple queries from
`SearchProfile.target_titles` instead).

### D-030: `SelectedSource.effective_config_status` — a computed runtime
view of credential availability, not a change to the registry's static
`config_status`
**Decision**: The live run above also showed `config_status=needs_credentials`
printed even though the same run successfully used real
`ADZUNA_APP_ID`/`ADZUNA_APP_KEY` — because `config_status` is (correctly,
per D-009) static YAML the user must hand-maintain, and nothing cross-checked
it against actual credential presence. Added `SelectedSource
.effective_config_status`, computed by a new `planner._effective_config_status
(entry, env: EnvConfig | None)`: for `adzuna_api` specifically, `configured`
when both env vars are present, else `needs_credentials`, regardless of the
declared value; every other `source_id` (no adapter/credential rule yet)
falls back to its declared `config_status` unchanged. `build_plan` gained an
`env: EnvConfig | None = None` keyword parameter (default preserves prior
behaviour exactly for every existing caller/test that doesn't pass one — no
behaviour change without opt-in). The CLI's `plan` command now also loads
`.env` (a new `--env-file` option, mirroring `run-once`'s) purely to compute
this display value — `load_env` only reads a file and `os.environ`, it never
performs a network call, so `plan`'s "never touches a source adapter or API
quota" guarantee (MILESTONE_1.md) is unaffected; a dedicated test
(`test_plan_command_never_makes_http_call`) already covers this and
continues to pass. Both `config_status` (declared) and
`effective_config_status` (runtime) are printed side by side, labelled, in
`job-scout plan`/`run-once` human output; neither ever prints the credential
value itself.
**Alternatives**: Writing the computed status back into the registry YAML
(rejected outright — directly contradicts D-009's "no runtime mutation of
YAML-first config" and would require a `SourceRegistryRepository` that
architecture.md §4 explicitly defers to a future milestone); a fully generic
"does this source have credentials" mechanism keyed off new registry fields
like `required_env_vars` (rejected as scope creep for a narrowly-scoped fix
— Milestone 1 has exactly one adapter, decisions.md D-002, so a single
`if source_id == "adzuna_api"` check is the smallest change that solves the
actual live problem, consistent with the existing `source_id == "adzuna_api"`
special-case already present in `pipeline.py`'s `_default_adapter_factory`).
**Why**: The mismatch was a genuine reporting gap the user could not resolve
by editing their own config correctly (there is no "runtime" value to hand
-author), unlike this session's other private-config recommendation
(D-029's audit — updating the registry's *declared* `config_status` by hand
remains valid and is still shown, just no longer the only signal).

### D-031: Adzuna "full description" request parameter investigated —
confirmed not to exist; documented as a known limitation, not implemented
**Decision**: Checked `developer.adzuna.com`'s Search endpoint documentation
directly (not merely re-reading D-016's earlier note) specifically for a
parameter to request the untruncated job description. The docs list
`results_per_page`, `what`, `what_or`, `what_exclude`, `where`, `sort_by`,
`salary_min`, `full_time`, `permanent`, `content-type`, and explicitly state
(twice) that the API "currently only provide[s] a snippet of the job
description in the response" — no `full_description` parameter or
equivalent is documented anywhere found. Per this session's explicit
instruction ("only add such a parameter if confirmed by the existing
official API contract or authoritative documentation... if not confirmed,
do not guess, retain current behaviour"), `AdzunaAdapter._build_query` was
**not** changed. Documented the truncation as a known limitation in
`architecture.md` §3 instead (see the AdzunaAdapter section).
**Alternatives**: Adding an unconfirmed `full_description=1` parameter
speculatively (rejected — this is exactly the class of unverified-secondary
-source mistake D-016 already flagged and declined to repeat; an
undocumented parameter could silently do nothing, error, or (worse) change
billing/quota behaviour with no way to verify from this codebase alone).
**Why**: Matches this project's established evidence bar for source-contract
claims (D-016, D-027, D-028 — verify against the source itself, don't guess
from unclear/secondary information) and the explicit instruction for this
task. Title matching (this fix's actual scope) does not depend on full
descriptions — Stage 2's strong gate and the title-vs-description weighting
in the weighted fallback score are both title-field-first specifically
because the description is known to be truncated.

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

### D-027: A first live Adzuna run's HTTP 404 was not a URL-construction
bug — `_get_page` already matched Adzuna's documented endpoint exactly
**Decision**: Investigated a live-run report of `Adzuna returned unexpected
HTTP 404` against `adzuna.py`'s URL builder. Re-derived the constructed URLs
for GB/IE directly (`https://api.adzuna.com/v1/api/jobs/gb/search/1`,
`.../jobs/ie/search/1`), confirmed they match `GET /v1/api/jobs/{country}/
search/{page}` exactly (same contract D-016 already verified against
Adzuna's docs), and confirmed all ten existing `test_adzuna_adapter.py`
cases pass against that URL shape — so no path/casing/duplication bug
exists. Rather than rewrite already-correct URL logic, added a distinct
`SourceNotFoundError` (`sources/base.py`) so a 404 is no longer lumped into
the generic "unexpected HTTP status" branch of `SourceUnavailableError`, and
added `source_id`/country/page context to every adapter error message
(never the query params or response body, since `SourceRun.errors`
persists these strings and credentials travel in the query string).
**Alternatives**: Assume the report implied a real path bug and rewrite the
URL builder anyway; silently swallow 404s per-country instead of raising.
**Why**: D-016 already flagged that Adzuna's actual supported-country list
couldn't be confirmed from a single authoritative source, specifically
naming `IE` as disputed — a 404 on an otherwise well-formed request is the
expected symptom if a `geographic_coverage` entry (here, the example
registry's `adzuna_api` listing `IE`) includes a country Adzuna's API
doesn't actually route. Editing that unconfirmed list again without a
authoritative source would repeat the mistake D-016 already declined to
make; giving the error message enough context (which country, which page)
lets whoever runs it next diagnose a per-country 404 immediately instead of
re-deriving it from a bare status code.

### D-028: Live execution confirmed the D-027 hypothesis — removed `IE`
from `adzuna_api.geographic_coverage` in the packaged registry template
**Decision**: A live `run-once` against the real Adzuna API reproduced
`Adzuna endpoint not found (HTTP 404) [source_id=adzuna_api country=IE
page=1]` — the exact `SourceNotFoundError` D-027 anticipated for an
unsupported market, now backed by a live response instead of disputed
secondary docs. Removed `IE` from `adzuna_api.geographic_coverage` in
`src/job_scout/resources/templates/source_registry.example.yaml`, keeping
`GB` (and the other previously-listed countries, none of which have a live
404 report against them) untouched. Did not touch `included_countries` on
any search profile — Ireland stays a valid country for a candidate to
search, it's just no longer routed through Adzuna specifically; the
planner's existing per-source-country exclusion logic (§6/§11a,
`unsupported_countries` with reason `not_in_geographic_coverage`) already
reports `IE` as unsupported for `adzuna_api` and already narrows
`SourceSearchParams.countries` to only the supported subset before any
adapter call, so `GB` executes normally in the same run even when the
profile also requests `IE` — no pipeline or planner code change was needed,
only the registry data. Added regression coverage in `test_planner.py` and
`test_pipeline.py` for a combined `GB`+`IE` profile against an
Adzuna-shaped entry with `geographic_coverage: [GB]`.
**Alternatives**: Leave the template list unedited per D-027's original
reasoning (no longer applicable — that reasoning was explicitly about
*unconfirmed* secondary sources, and this is now a first-party live
result); have the adapter catch 404 and silently drop the country
per-request instead of fixing the registry (rejected — it would hide a
config error behind a runtime catch, and the registry is the documented
source of truth for coverage, not the adapter).
**Why**: A live HTTP 404 from Adzuna itself is the authoritative
confirmation D-016/D-027 were waiting for — it's no longer "unconfirmed
secondary sources disagree," it's a direct response from the API in
question for this exact source_id/country pair. Fixing coverage at the
registry layer (rather than in `adzuna.py` or `pipeline.py`) matches
CLAUDE.md hard constraint 6 (source selection goes through the registry,
never a hard-coded list) and keeps the country/source concerns properly
separated: which countries a *candidate* wants is profile data; which
countries a *source* can actually serve is registry data; the planner's
existing intersection logic is what's supposed to reconcile the two, and

### D-032: Stage 5 final-scoring calibration fix — best-match title/role
scoring, search-profile-aware skills, real sector relevance, no
unconditional score floor
**Decision**: Following a deterministic audit of a live run (150 jobs
fetched, 25 scored, relevant strategy roles clustering at 15–21 with weak
data/clinical/trainee roles scoring within 1–5 points of them), rewrote
`matching/scoring.py`'s component formulas — weights and component names
unchanged, so no `scoring_weights.yaml` schema change:
1. **Title/role-family** (Part 1/2): replaced `matches / total configured
   vocabulary` with a best-single-match strength (`_best_phrase_match`),
   scaled by field (title > description) and provenance tier (active
   SearchProfile signal > CandidateProfile signal > CandidateProfile
   `previous_titles`). Configuring more target titles can no longer dilute
   an existing exact match — the winning match's score only ever depends on
   itself, not on how many other (non-matching) phrases exist.
2. **Stage 2/5 consistency** (Part 3): both stages now call the same
   `matching.normalize.match_phrase` (moved out of `prefilter.py`'s
   previously module-private `_phrase_token_coverage`/inline exact-match
   logic). A job can no longer pass Stage 2's strong-title gate on
   token-coverage evidence and then score zero Stage 5 title credit for
   that identical evidence (the live-audit's Equifax/JAGGAER/Michael Page
   cases — token-coverage-only title matches that previously fell through
   to Stage 5's exact-substring-only matching and landed on the score
   floor).
3. **Search-profile-aware skills** (Part 4): `required_skills` and
   `transferable_skills` now read `SearchProfile.required_skills`/
   `preferred_skills`+`transferable_skills` as the primary signal, with the
   corresponding `CandidateProfile` fields as supplemental (when a
   search-profile signal exists) or a capped fallback (when it doesn't) —
   `_SUPPLEMENTAL_CANDIDATE_SKILL_WEIGHT`/`_FALLBACK_CANDIDATE_SKILL_WEIGHT`.
   `responsibilities` (which has no SearchProfile-side field to draw a
   primary signal from) is always treated as fallback-only, at the same
   reduced weight, using the same capped-denominator `_bounded_coverage`
   Part 1/2 use for titles — otherwise candidate-history-only signal
   spread across three components could still out-score a bare exact-title
   match (verified by
   `test_generic_candidate_skill_overlap_does_not_outrank_exact_title_match`).
4. **Sector/industry relevance** (Part 5): `_sector_relevance_component`
   was hard-coded to always return neutral 0.5, with a code comment
   claiming "no sector field exists on CandidateProfile or SearchProfile
   yet" — stale since Milestone 1.1 added exactly those fields
   (`industries`/`sectors`/`included_industries`/`included_sectors`/
   `excluded_industries`/`excluded_sectors`). Now reads them, matched via a
   new word-boundary-safe `contains_phrase_tokens` (token-sequence
   containment, not substring containment) so a short configured term (the
   live audit's "ai" matching inside "Trainee") can't false-positive.
   `search.excluded_industries`/`excluded_sectors` reduce the component
   (never a hard rejection from here — that only happens at Stage 1 when
   the profile's own `hard_filters` toggle is on).
5. **No unconditional score floor** (Part 6): `seniority_experience`,
   `sector_relevance`, `education`, `visa_relocation` no longer default to
   neutral 0.5 when no evidence exists — they default to 0
   (`not_evaluable`, always recorded in evidence). This was the direct
   cause of the audit's flat 15-point floor (0.10+0.10+0.05+0.05 weight ×
   0.5 raw = 0.15 for every job, confirmed against three live jobs that
   scored exactly 15.00 with zero evidence in every other component too).
   `sector_relevance` keeps 0.5 for the one case Part 5 explicitly
   documents as genuinely neutral: nothing configured at all (as opposed to
   "configured but no evidence found," which is 0). `final_score` is
   clamped to `[0, 100]` in `build_match_result` since negative-evidence
   components (seniority mismatch, explicit no-sponsorship) can now push
   the raw weighted sum below zero.
6. **Entry-level seniority safeguard** (Part 7): a small hard-coded,
   generic (not profession-specific) list — trainee, graduate, internship,
   intern, junior, entry level — matched word-boundary-safe against the
   job text. Fires only when `SearchProfile.min_experience_years >= 3`
   (this run targets an experienced hire; a threshold on existing
   configuration, not an invented rule), giving `seniority_experience`
   explicit negative evidence rather than neutral "no evidence."
**Alternatives**: Renormalising component weights when a component is
`not_evaluable` (rejected — the task scope explicitly asked for the
smallest correction that preserves transparency; weight renormalisation is
a bigger behavioural change than "no evidence contributes zero," and
`scoring_weights.yaml`'s existing "weights sum to 1.0" contract would need
new documentation either way). Excluding
`CandidateProfile.excluded_industries`/`excluded_role_families` from the
sector-relevance soft-negative vocabulary was deliberate, not an oversight
— those already reject unconditionally at Stage 1 whenever non-empty, so a
job matching them can never reach Stage 5 to begin with; including them
here would be dead code. Changing `notification_thresholds` (85/70) or
Adzuna's `what_or` query construction were both explicitly out of scope for
this task and untouched.
**Why**: Matches CLAUDE.md hard constraint 10 (profession-agnostic — every
new signal is an existing `CandidateProfile`/`SearchProfile` field, no new
profession-specific keyword) and hard constraint 5 (every component still
carries its evidence). Confirmed against the audit's own strong/weak
examples: `test_strong_strategy_group_outranks_weak_data_and_trainee_group`
asserts the full strong-vs-weak ranking inversion the audit's live data
exhibited is now corrected under a synthetic profile shaped like the real
one (no dependency on the user's private database or config files, per
this task's explicit instruction).
this incident is exactly the case it exists for.

### D-033: Connector-word-aware token coverage + title/role-family
aggregation no longer halves an exact title match
**Decision**: Two narrowly-scoped follow-on corrections to D-032's Stage
2/5 matching, after confirming five specific jobs still scored/ranked
wrong post-calibration (Business Strategy Consultant 38.79, Business
Strategy Analyst/Consultant 38.79, Strategy Manager 25.00, People
Reporting and Analytics Data Partner 28.59, Model Build & Data Analytics
Manager 25.26):
1. **Meaningful-token coverage** (`matching/normalize.py`): added a fixed,
   profession-agnostic set of English connector/function words (`and`,
   `or`, `of`, `the`, `for`, `to`, `in`, `on`, `with`, `a`, `an` —
   `_CONNECTOR_TOKENS`) and a `meaningful_tokens()` helper that strips them
   from a phrase's tokens (falling back to the original tokens if nothing
   would remain, so a phrase can never be filtered to empty).
   `phrase_token_coverage` now computes its ratio over a phrase's
   meaningful tokens only, instead of every raw token — so "Data &
   Analytics Associate" (tokens `data, and, analytics, associate`) needs
   its 3 content tokens present, not 3-of-4 raw tokens padded by "and".
   This fixed the false positive where "People Reporting and Analytics
   Data Partner" shared `data`, `and`, `analytics` (3/4 = 0.75, exactly at
   the gate) with the configured phrase, even though the phrase's
   occupational head term `associate` was entirely absent; under
   meaningful-token coverage the same job shares only `data`+`analytics`
   out of 3 content tokens (0.67), below the gate. `normalize_text`/
   `normalize_tokens` (destructive storage/display normalisation) are
   untouched — connector filtering applies only inside
   `phrase_token_coverage`, shared unchanged by Stage 2's strong-title gate
   and Stage 5's best-match scoring (both already called the same
   `match_phrase`, per D-032 Part 3).
2. **Title/role-family aggregation** (`matching/scoring.py`): replaced the
   unconditional `(best_title + best_role_family) / 2` with
   `max(best_title, (best_title + best_role_family) / 2)`
   (`_combine_title_role_family`). The averaging formula halved an exact
   active target-title match (best_title=1.0) down to 0.5 whenever no
   *separate* role-family phrase also happened to match — title score is
   now the primary signal, and the title/role-family average is only ever
   used when it says more than title score alone: a weaker or absent
   role-family match can never drag an existing title match down (the
   average is then <= best_title, so the max() falls back to best_title
   unchanged), a *stronger* role-family match can raise the combined score
   above title alone, and role-family evidence with no title match at all
   still contributes at exactly its pre-D-033 value (`best_role_family /
   2`, since the average with a zero title score is unchanged) — so
   role-family-only jobs already covered by D-032's live-case regression
   (`test_strong_strategy_group_outranks_weak_data_and_trainee_group`)
   keep the exact same score, and only the specific halved-exact-title-match
   case is corrected.
**Alternatives**: A hand-maintained profession-specific stopword list
(rejected outright — CLAUDE.md hard constraint 10 explicitly forbids
hard-coding "strategy", "consultant", "data", "analyst", "manager",
"associate", etc.); filtering connector words out of `available_tokens`
(the job title's own token set) as well as the phrase (rejected as
unnecessary — only the phrase's own tokens ever get looked up in the
title's token set, so a connector word appearing in the title was never
capable of matching anything by itself); the task's own illustrative
`max(best_title, 0.75 * best_title + 0.25 * best_role_family)` formula
(tried first, then rejected — it also caps *any* role-family-only match at
a quarter weight instead of half, which silently re-scored several
already-correct role-family-only jobs from D-032's own live-case
regression test downward, dropping "Consulting Project Team Lead -
Corporate Strategy" from 25.0 to 6.25 and inverting its ranking against a
weaker trainee-role job; `max(best_title, average)` fixes only the
specific reported defect — an exact title match losing credit to a missing
role-family signal — without touching the already-validated role-family-only
calibration, which is the smaller, more conservative change).
**Why**: Both fixes are structural (a fixed closed-class connector-word
list, and a bounded max/blend formula), not new profession vocabulary, so
CLAUDE.md hard constraint 10 stays satisfied. Verified against synthetic
fixtures shaped like the confirmed regressions in
`tests/test_normalization.py` and `tests/test_scoring.py`
(`test_strong_strategy_group_outranks_weak_data_and_trainee_group` and the
new title-role aggregation regressions) — no dependency on the user's
private database or config files. `notification_thresholds`, Adzuna
querying, and Milestone 2 scope were all explicitly out of bounds for this
task and untouched.

### D-034: Active-search-intent evidence classification and role-family
aggregation correction
**Decision**: A read-only audit against the live database (161 stored
match results, real `candidate_profile.yaml`/`search_profiles.yaml`)
confirmed two ranking defects surviving D-032/D-033: (1) "Data Business
Analyst" (candidate-history title alias only, plus an incidental
`search.included_industries` sector match on the generic token "ai") scored
31.25, above "Strategy Manager" (exact active `SearchProfile.target_titles`
match, no sector evidence) at 25.0 — a job with *zero* active search-profile
title/role-family evidence outranking one with the strongest possible
active evidence; (2) "Consulting Project Team Lead - Corporate Strategy"
(two distinct active `search.role_families` matches on the title —
`strategy` and `corporate_strategy`) scored only 12.5, identical to a
single weaker active role-family match and below candidate-history-only
jobs whose fallback skill/responsibilities components stacked on top,
because `_combine_title_role_family`'s role-alone case was always exactly
`role_score / 2` regardless of whether the match was this run's actual
active ask or only the candidate's own history, and regardless of how many
distinct active phrases matched. Two narrowly-scoped corrections in
`matching/scoring.py`, both provenance-structural (never new profession
vocabulary):
1. **Active-search-intent classification** (`_EvidenceTier`,
   `_classify_evidence_tier`): a small internal `StrEnum` —
   `active_target_title` / `active_title_alias` / `active_role_family` /
   `candidate_history_only` / `no_title_or_role_evidence` — derived
   directly from `_best_phrase_match`'s structured `_PhraseScore` results
   (label/field/score), never by re-parsing rendered evidence strings.
   `_best_phrase_match` was refactored to return `list[_PhraseScore]`
   instead of a `(float, list[str])` tuple, with a separate
   `_render_phrase_evidence` step producing byte-identical evidence text to
   the pre-D-034 format — the structured data is now the single source of
   truth for both the human-readable evidence and the classification/
   aggregation logic. The classification is recorded in
   `title_role_family`'s own evidence as `active_search_intent_tier:<value>`
   — a transparency label, not a new public `ScoreComponent` (CLAUDE.md hard
   constraint 9: no new abstraction) or `ScoringWeights` schema field
   (constraint explicitly given for this task).
2. **Role-family-alone credit** (`_role_family_alone_credit`, folded into
   `_combine_title_role_family` via `max(title_score, average,
   role_alone_credit)`): replaces the flat `role_score / 2` with three
   provenance-aware factors — a single active `search_role_family` match
   alone now earns `role_score * 0.70` (`_ACTIVE_ROLE_FAMILY_ALONE_CREDIT`);
   two or more *distinct* active role-family phrases matched in the job's
   *title* field (never a single incidental description-only mention — the
   reinforcement count is filtered to `field == "title"`) earn
   `role_score * 0.85` (`_ACTIVE_ROLE_FAMILY_REINFORCED_CREDIT`);
   candidate-only role-family evidence keeps its pre-existing, unchanged
   `role_score * 0.5` (`_CANDIDATE_ROLE_FAMILY_ALONE_CREDIT`, numerically
   identical to the old blanket factor, so candidate-only role-family
   scores are byte-for-byte unchanged by this fix). Active and
   candidate-only evidence are evaluated independently (not "whichever
   phrase scored highest overall") so a weaker active match is never
   shadowed by a stronger candidate-only match sharing the same phrase
   list. Folding this in via `max()` alongside D-033's existing
   `max(title_score, average)` means it can only ever raise the combined
   score, never lower an existing title or averaged score — candidate
   history still never reduces active evidence. `candidate_title_alias`/
   `candidate_previous_title`'s provenance tiers (`_TITLE_TIER_WEIGHTS`)
   were also lowered from 0.85/0.7 to 0.55/0.4 — the gap between them and
   the active tiers (still 1.0, unchanged) was too narrow for a
   candidate-alias-only title match plus one incidental sector/skill hit to
   reliably stay below a bare exact active target-title match; `search_
   target_title`/`search_title_alias`/`search_role_family`'s tiers, the
   D-033 `max(title_score, average)` blend, and every other Stage 5
   component (`sector_relevance`, `required_skills`, `transferable_skills`,
   `responsibilities`, `seniority_experience`, `education`,
   `visa_relocation`) are untouched.
**Verification**: all pre-existing `tests/test_scoring.py` assertions
(55 tests) pass unchanged against the new formula — none hard-coded the old
absolute tier/credit values, only relational orderings the fix preserves or
strengthens. Seven new tests cover the classification tags, the
active-alone/reinforced/candidate-only credit ordering
(`0.70`/`0.85` > `0.5`), the `candidate_title_alias`/`previous_title`
ordering, and — the concrete regressions — a synthetic mirror of "Data
Business Analyst" vs "Strategy Manager" and a dedicated
`test_reinforced_active_role_family_outranks_candidate_history_only_job`
reproducing "Consulting Project Team Lead - Corporate Strategy" vs "Senior
Manager - Model Build & Data Analytics". The latter required closing a gap
the audit found in the pre-existing `_live_like_search()` fixture (it
omitted `financial_modelling` from `preferred_skills`, unlike the real
profile, so `test_strong_strategy_group_outranks_weak_data_group` never
actually exercised the live near-miss) — `financial_modelling` was added to
the fixture and to the "Senior Manager" job's synthetic description,
faithfully reproducing the real ~17.29 near-miss score the fix must (and
now does) rank below the reinforced-role-family job's 21.25. Re-run against
the real live database/profile (read-only, no config or DB writes):
"Data Business Analyst" 31.25 → 23.75 (now below "Strategy Manager"'s
unchanged 25.0); "Consulting Project Team Lead - Corporate Strategy"
12.5 → 21.25 (now above "Senior Manager - Model Build & Data Analytics"'s
unchanged 17.29); "Location Strategy Analyst"/"Assistant Director -
Operational Strategy" (single active role-family match) 12.5 → 17.50;
"Management Consultant" (candidate-title-alias-only) 21.25 → 13.75.
**Alternatives**: A new public `search_intent_alignment` `ScoreComponent`
(rejected — would require a `ScoringWeights` schema change and rebalancing
every existing `scoring_weights.yaml`, including the real one, for no
benefit the existing five-tier provenance system doesn't already
structurally provide; the task explicitly scoped this out); a cross-
component final-score cap/multiplier gating on "no active search-profile
evidence at all" (considered — would further address sector/skill-fallback
stacking against a *candidate-history-only* job, but was out of this task's
explicit scope, which named only the title/role-family classification and
aggregation; left for a follow-up if the live re-run above still shows
stacking defects after this fix); reducing `candidate_role_family`'s
per-phrase tier weight (0.85) directly instead of adding a separate
role-alone credit factor (rejected — would also shrink the D-033 `average`
blend path for jobs where a candidate role family combines with a
candidate title alias, an unrelated case this task did not confirm as
defective; the added `role_alone_credit` term changes only the specific
role-family-only aggregation path the audit identified).
**Why**: Every change is provenance-structural (which configured list a
phrase came from, and how many distinct ones matched), never new
profession-specific vocabulary, so CLAUDE.md hard constraint 10 stays
satisfied. `notification_thresholds`, Adzuna query construction, Milestone
2 scope, and all private configuration files were explicitly out of bounds
for this task and untouched — this ADR and its code change only ever
*read* the private database/config to verify the fix, never wrote to
either.

---

## Milestone 2 — Planning (scope defined, not implemented)

The ADRs below record decisions made while writing `MILESTONE_2.md`, before
any Milestone 2 code changed — same discipline `MILESTONE_1_1.md`'s "Open
items before implementation: None outstanding... written first" used.
Milestone 1/1.1's baseline (288 passed / 1 skipped / 3 deselected, `ruff`/
`mypy --strict` clean) was re-verified unchanged during this planning pass;
no application code was modified.

### D-035: Email-alert ingestion and the general source-discovery workflow
are re-sequenced out of Milestone 2, to Milestone 3
**Decision**: The pre-existing `ROADMAP.md` draft listed "email-alert
ingestion" (Naukri, iimjobs, foundit, Indeed alerts, Naukrigulf, GulfTalent,
Bayt) and a general human-reviewed source-discovery workflow under
"Milestone 2." This planning pass's own task brief scoped Milestone 2 as
"multi-source discovery, query quality, and visa/sponsorship enrichment"
and its Workstream B evaluation list named only API/feed/government sources
(Adzuna, Greenhouse, Lever, UK Find a Job, EURES, Canada Job Bank,
SEEK/ANZ) — never the email-alert portals the old draft named, and its
explicit out-of-scope list separately excludes "email notifications." Both
items are moved to Milestone 3 in the `ROADMAP.md` update accompanying this
ADR.
**Alternatives**: Keep both in Milestone 2 as originally drafted, since
`architecture.md` §9 and the `email_alert`/`search_discovery` access modes
already exist in the schema; treat the task brief's silence on them as
oversight rather than intentional narrowing.
**Why**: Email-alert ingestion is a materially different capability from
API/feed adapter work — it needs mailbox authentication and per-portal
parsing heuristics, neither of which any part of this planning pass's
Workstream A–F investigated or designed. Building it "along the way" inside
a milestone scoped and reviewed for adapter/query/dedup/visa work would be
exactly the kind of over-scoping CLAUDE.md hard constraint 7 warns this
project has a documented tendency toward. A general, automatic
source-discovery *workflow* is a distinct, larger capability from this
milestone's own *manual* discovery (the source priority matrix in
`MILESTONE_2.md` Deliverable 4) — conflating "we manually evaluated some
sources this session" with "we built a tool that does this automatically
and repeatably" would overstate what's actually in scope. Flagged
explicitly as an open decision for the user in the preparation report
accompanying this ADR, since it changes a previously-stated milestone
boundary.

### D-036: `VisaAssessment`/`VisaStatus` keep their existing shape for
Milestone 2; the task brief's alternative nine-value enum is not adopted
**Decision**: Milestone 2's sponsorship-enrichment design (`MILESTONE_2.md`
Workstream D) wires the *existing* `VisaAssessment` model (architecture.md
§2.12, unchanged since Milestone 1) into the pipeline for the first time,
rather than replacing `VisaStatus`'s six values (`confirmed_yes|likely|
employer_eligible|unknown|confirmed_no|not_required`) with the flatter
nine-value enum the planning task's own brief sketched as an illustrative
option (`confirmed_sponsorship_available`, `sponsor_registered_employer`,
`citizenship_restriction`, `relocation_available`, etc.).
**Alternatives**: Adopt the brief's proposed enum directly, since it was
offered as a concrete suggestion.
**Why**: The task brief itself said to "use a smaller enum if the current
architecture already defines a suitable one," and it does: `VisaStatus`
already separates the *sponsorship* dimension (its six values) from
*independent* evidence dimensions the brief's flat enum would collapse
together — `citizenship_restrictions: list[str]`,
`existing_work_authorisation_required: bool | None`, and
`relocation_support_evidence: list[str]` are already their own fields on
`VisaAssessment`. A job can simultaneously be `employer_eligible` *and*
citizenship-restricted *and* have relocation evidence — three independent
facts a single flat status enum cannot represent at once, but the existing
model already can. Replacing it would also be a breaking schema change to
a model `save_visa_assessment`'s reserved SQLite table already assumes the
shape of, for no representational gain.

### D-037: Query planner is a bounded hybrid of per-target-title exact
queries and one grouped fallback, not a single broad OR or one query per
configured phrase
**Superseded in part by D-041**: the `SourceQueryCapabilities` model this
ADR introduces was replaced by the broader `SourceCapabilities` model
(same `exact_phrase_search`/`keyword_search` fields, renamed, plus twelve
more capability signals) — see D-041. The query-planning *design* described
below (bounded hybrid, per-title exact queries, capped grouped fallback) is
otherwise unchanged and still current.
**Decision**: `MILESTONE_2.md`'s query-planning design builds one
exact-phrase `PlannedQuery` per `SearchProfile.target_titles` entry (capped
by a new `max_queries_per_source_country` execution limit, profile order
preserved, same truncation-with-recorded-note pattern as the existing
country cap, D-015), plus at most one additional grouped OR-fallback query
built from `title_aliases`/`role_families`/`required_skills` when budget
remains or no `target_titles` are configured. `SourceAdapter.fetch()`'s
Protocol and `AdzunaAdapter` itself are unchanged — the pipeline calls
`fetch()` once per planned query instead of once per source.
**Alternatives**: Keep the current single broad OR query (rejected — this
is the exact D-029-flagged "known next tuning item" this milestone exists
partly to fix); one query per every configured title/alias/role-family
phrase with no grouping (rejected — unbounded request-count growth,
`architecture.md` §11a's guardrail philosophy requires every fan-out
dimension to have an explicit, config-visible cap); grouped role-family-only
queries with no per-title precision (rejected — re-creates a milder version
of the current dilution problem, since an exact `target_titles` phrase gets
folded back into a family-level OR instead of getting its own precise
query).
**Why**: `target_titles` is the run's most explicit, highest-intent
configured signal (the same precedence Stage 5 scoring's `_TITLE_TIER_
WEIGHTS` already gives it over `title_aliases`/role families, D-032/D-034)
— it deserves the most precise query type a source supports, and CLAUDE.md
hard constraint 6/10 requires the mechanism to come from reading more
already-configured profile data, not new hard-coded vocabulary. Capping by
a new, explicit `ExecutionLimits` field (not a module-private constant)
matches every existing guardrail's own "config, not hard-coded" treatment
(§11a).

### D-038: Cross-source deduplication adds an exact-canonical-URL tier and a
bounded token-overlap "probable duplicate" tier; no new `SourceObservation`
model
**Decision**: `MILESTONE_2.md` Workstream C adds two deterministic
dedup signals — an exact cross-source match on `canonical_url` alone
(ignoring `external_source_id`, unlike Tier 1), and a "probable duplicate"
tier requiring the existing company+title+location identity match *plus*
at least one of: identical `description_fingerprint` (kept from M1), bounded
token-set (Jaccard) similarity above a conservative threshold, or a close
`posted_date` combined with matching salary fields. No embeddings, per
explicit instruction. Separately, Workstream F concludes **no new
`SourceObservation` model** is needed: auditing `SqliteJobRepository
.merge_provenance` found it already inserts a fresh `source_provenance` row
on every call (including repeat fetches), making the existing table already
an append-only fetch-observation log in practice — the only real gap is a
missing read method (`JobRepository.list_provenance`), not a missing model.
**Alternatives**: A dedicated `SourceObservation` model/table distinct from
`SourceProvenance` (considered, per the task brief's own suggestion);
embedding-based near-duplicate detection (explicitly excluded by the task);
treating any single one of the new corroborating signals (Jaccard alone, or
posted-date alone) as sufficient without the company+title+location
precondition (rejected — too high a false-merge risk, see `MILESTONE_2.md`
risk R-8).
**Why**: `architecture.md` §8 already earmarks a future swap of
`description_fingerprint`'s exact-hash approach for something more
tolerant ("A future milestone may switch to SimHash for near-duplicate
detection... the field is a plain string so that swap doesn't change the
schema") — the bounded-Jaccard tier is exactly that anticipated swap,
scoped to Milestone 2 without embeddings. The task's own explicit
instruction — "do not add abstraction unless cross-source deduplication
actually requires it" — applies directly to the `SourceObservation`
question: the audit found the existing schema already does the job once a
read method exists, so adding a parallel model would be exactly the
unjustified abstraction CLAUDE.md hard constraint 9 and `architecture.md`
§12 already rule out project-wide.

### D-039: Sponsor-register enrichment is import-only (UK, NL); no live
government-register downloading, no fuzzy name matching, in Milestone 2
**Refined by D-042**: UK and NL are no longer treated as equally mandatory —
UK is mandatory for M2, NL is optional/stretch and must not block
completion. The import-only/no-live-download/no-fuzzy-matching decisions
below are otherwise unchanged and still current.
**Decision**: `MILESTONE_2.md` Workstream D designs `job-scout sponsors
import <file> --country <CC> --register <name>` to parse a
user-already-downloaded register snapshot (UK Home Office licensed-sponsor
register; Netherlands IND recognised-sponsors register) into a new
`sponsor_registry_entries` SQLite table, joined to jobs via exact
normalized-employer-name matching (reusing `deduplication.normalize_company`
— one normalisation function for both cross-source dedup and sponsor
joining, not two). No other country's register is assumed to exist; no
code in this design ever downloads or scrapes a government site itself; no
fuzzy/alias name matching is attempted.
**Alternatives**: Automating the register download (rejected outright —
explicit task instruction, and CLAUDE.md hard constraint 1's "no scraping
without clear permission" applies to government sites exactly as it does to
job boards); fuzzy/Levenshtein company-name matching to catch subsidiaries
and trading names (rejected for M2 — real false-positive risk with no
mitigation designed yet, `MILESTONE_2.md` risk R-9; deferred, not solved,
listed explicitly in `ROADMAP.md`'s "Explicitly not planned until asked
for"); inventing a register for countries without a confirmed public one
(rejected — directly contradicts CLAUDE.md hard constraint 4, "visa/
sponsorship is never a boolean... a sponsor-registry match means 'may be
eligible to sponsor,' never 'will sponsor'" — the same caution applies to
inventing register coverage that doesn't exist).
**Why**: Matches the task's explicit instruction ("do not implement live
government-register downloading during this planning pass") and this
project's evidence bar for source-contract claims already established by
D-016/D-027/D-028/D-031 — verify against the source itself or a user-
supplied artifact, never guess. Registry-match confidence is deliberately
capped below "confirmed" in the evidence-precedence design (`MILESTONE_2.md`
"Sponsorship/visa enrichment design") specifically because exact-name
matching alone cannot rule out a subsidiary/trading-name collision.

---

## Milestone 2 — Planning refinement pass (2026-08-08)

The ADRs below record a second planning pass over `MILESTONE_2.md`/
`ROADMAP.md`, still before any Milestone 2 code changed. This pass refines
five things the user explicitly approved: the canonical normalized job
model, source capability metadata, sponsor-provider scope, the evaluation
dataset design, and a set of previously-open M2 decisions. No application
code was modified; the M1/1.1 baseline was not re-run (no code changed to
re-run it against).

### D-040: `Job` is already the canonical normalized job model every adapter
must emit; no new `NormalizedJob` model
**Decision**: Confirmed by auditing the current codebase (not by design
intent alone) that `Job` (`models.py`, architecture.md §2.4) already is the
single canonical, normalized representation every source's data passes
through before reaching the generic pipeline. `RawJobRecord`'s own docstring
already states the boundary explicitly — "Source-native shape,
pre-normalisation. Adapters return this, never Job." — and `pipeline.py`
already implements exactly the rule this ADR formalises:
`normalize_adzuna_record(RawJobRecord) -> Job` is the only function that
reads `RawJobRecord.raw_payload`, and it is looked up through a plain dict,
`_NORMALIZERS: dict[str, Callable[[RawJobRecord], Job]]`, keyed by
`source_id` — every stage downstream of that one lookup (deduplication, hard
filters, pre-filter, Stage 5 scoring, visa assessment, persistence) operates
purely on `Job`/`MatchResult`/`VisaAssessment` and contains zero
`source_id`-conditional branching anywhere. **Source adapters normalize
external records into `Job`** — no new model is introduced for Milestone 2;
a second model would duplicate `Job` for no representational gain and
contradict CLAUDE.md hard constraint 9 (no unjustified new abstraction).
**Architectural rule (formalised, not new)**: `External source payload ->
source-specific adapter -> Job (canonical normalized model) -> generic
pipeline`. Concretely:
- Every `SourceAdapter.fetch()` implementation (Adzuna today; Reed/
  Greenhouse/Lever in M2) returns `list[RawJobRecord]` only — the
  source-native payload, untouched, per `architecture.md` §3's existing
  contract.
- Exactly one normalizer function per source (`normalize_reed_record`,
  `normalize_greenhouse_record`, `normalize_lever_record`, mirroring
  `normalize_adzuna_record`'s existing shape and location) converts
  `RawJobRecord -> Job`. M2 keeps normalizers colocated in `pipeline.py`
  next to `_NORMALIZERS`, matching the established M1 pattern, rather than
  moving them into each `sources/*.py` module — a location change with no
  behavioural benefit, and not what this task asked for.
- `_NORMALIZERS` grows by three entries (dict data, not new `if`/`elif`
  branches) — the single place in the whole codebase where a `source_id`
  string selects source-specific behaviour for normalization. This is the
  one, intentionally narrow "branch point" the architecture allows; every
  other stage must keep operating on `Job` alone.
- No stage after normalization — `deduplication.py`,
  `matching/hard_filters.py`, `matching/prefilter.py`, `matching/scoring.py`,
  `matching/visa.py` (new in M2), `repository/sqlite_repo.py` — may read
  `source_id` to change *how* it evaluates a job (it may of course *record*
  `source_id` as data, e.g. in `SourceProvenance`/`ScoreComponent` evidence,
  which is not branching).
**Required normalization fields** (every M2 normalizer must populate the
same fields `normalize_adzuna_record` already does, degrading to
`None`/empty/`RemoteType.UNKNOWN` rather than fabricating a value when a
source doesn't provide the underlying data — never a per-source special case
downstream): `job_id` (fresh UUID4), `external_ids` (one
`SourceExternalId`), `title`, `normalized_title`/`normalized_company` (via
the shared `compute_fingerprint`, never hand-rolled per adapter), `company`,
`location`, `remote_type` (inferred from description text via the existing
shared `_guess_remote_type`, not a per-source heuristic), `employment_type`
(`None` if the source doesn't expose one), `description_raw`/
`description_text` (HTML-stripped via the shared `strip_html`), `posted_at`
(`None` if unparseable/absent — never guessed), `collected_at`
(`datetime.now(UTC)` at normalization time), `salary_min`/`salary_max`/
`salary_currency` (`None` when the source's payload has no salary field —
see D-041's `salary_data` capability, which documents this rather than
working around it), `source_provenance` (one `SourceProvenance` entry,
`access_mode` read from the adapter's own declared `access_mode`, never
hard-coded per call site), `fingerprint` (`compute_fingerprint`, shared,
unchanged), `role_family_hints` (`[]` — Stage 2 populates this later, no
normalizer sets it directly today).
**Source provenance**: unchanged from Workstream F's existing conclusion
(D-038) — every normalizer constructs exactly one `SourceProvenance` row per
fetched record, `merge_provenance` already appends rather than overwrites on
repeat/cross-source fetches, and `JobRepository.list_provenance()` (new in
M2) is the read path. Nothing about D-040 changes that.
**Alternatives**: A new `NormalizedJob` model distinct from `Job`, kept
adapter-boundary-only and mapped into `Job` at a later pipeline stage
(rejected — audited `Job` itself and found no persistence- or
matching-only concern baked into it that would make it unsuitable as the
adapter boundary; every field on `Job` is either raw normalized data or a
value every stage after normalization already needs, so a second model
would be a pure pass-through with no field it owns that `Job` doesn't
already carry); moving normalizer functions into each `sources/*.py` module
(considered — arguably tidier ownership, but a real location change to
already-implemented M1 code for a stylistic reason only, not requested by
this task and not required to satisfy the architectural rule, since the
dict-dispatch boundary already fully contains the "branch point" regardless
of which file the function bodies live in).
**Why**: This is exactly the audit the task asked for — check whether `Job`
already serves the canonical-normalized-model purpose before inventing a
new one. It does, and the evidence is the existing code, not just intent:
the docstring, the dict-dispatch pattern, and the absence of any
`source_id` check anywhere in `matching/`, `deduplication.py`, or
`repository/sqlite_repo.py`. CLAUDE.md hard constraint 9 (no unjustified new
abstraction) applies directly: formalise the rule, don't add a redundant
model.

### D-041: A single `SourceCapabilities` model, not scattered booleans,
added to `SourceRegistryEntry`; consolidates and supersedes the draft
`SourceQueryCapabilities`
**Decision**: `MILESTONE_2.md`'s pre-existing draft (D-037) added a narrow
`SourceQueryCapabilities` (four fields: `supports_exact_phrase`,
`supports_or_terms`, `supports_industry_filter`,
`max_recommended_queries_per_request`) to answer only the query-planner's
needs. This ADR replaces it with a broader `SourceCapabilities` model
(still nested on `SourceRegistryEntry`, still one typed object, not many
top-level booleans) covering every capability signal M2's design actually
needs across query construction, filtering, result-shape, and dedup — not
just query mode selection: `keyword_search`, `exact_phrase_search`,
`location_filter`, `country_filter`, `city_filter`, `industry_filter`,
`company_filter`, `remote_filter`, `salary_data`, `structured_description`,
`pagination`, `page_size_control`, `posting_date_filter`,
`stable_external_job_id`, `canonical_application_url` (all `bool`), plus
`max_recommended_queries_per_request: int | None` carried over unchanged
from the draft it replaces. **`authentication_required` is deliberately not
added** — `SourceRegistryEntry.auth_required: bool` (architecture.md §2.7)
already means exactly this; adding a second field would create two sources
of truth for the same fact.
`SourceRegistryEntry.capabilities: SourceCapabilities = SourceCapabilities()`
defaults to Adzuna's actual documented behaviour (per D-016/D-031's
already-verified contract: `keyword_search=True`, `exact_phrase_search=True`,
`location_filter=True`, `country_filter=True`, `city_filter=True`,
`industry_filter=False`, `company_filter=False`, `remote_filter=False`,
`salary_data=True`, `structured_description=False`, `pagination=True`,
`page_size_control=True`, `posting_date_filter=False`,
`stable_external_job_id=True`, `canonical_application_url=True`) so every
existing registry entry — which has no `capabilities` key today — keeps
validating and behaving unchanged.
**How the query planner consumes it**: `exact_phrase_search`/
`keyword_search` gate `PlannedQuery.mode` selection exactly as
`SourceQueryCapabilities.supports_exact_phrase`/`supports_or_terms` were
already designed to (D-037) — no behaviour change there, just a
renamed/relocated field. `company_filter=True` (Greenhouse/Lever's actual
shape — one fetch is implicitly scoped to one watchlisted company) tells the
planner to skip keyword-`PlannedQuery` generation entirely for that source
and rely on `CompanyWatchlistEntry` fan-out instead, making the
already-designed "Greenhouse/Lever don't use keyword queries at all"
behaviour data-driven (read from the registry) rather than an unstated
adapter-specific assumption. `industry_filter` gates whether an
industry/sector term is folded into a query's OR terms or silently dropped
for that source.
**How unsupported filters/fields are handled**: never a special case
downstream — a source with `salary_data=False` simply normalizes
`salary_min`/`salary_max`/`salary_currency` to `None` (per D-040's
normalization-field rule), and any code that conditionally uses salary (e.g.
the new dedup Tier 2 salary-corroboration signal, D-038) already treats
"both sources report salary" as a precondition, so a `None` simply means
that corroborating signal doesn't fire — no `if source_id == ...` anywhere.
The same pattern applies to every other capability: absence means "this
signal is unavailable for this source," never an error and never a
fabricated value.
**How `job-scout plan` exposes this**: extends the already-planned
`planned_queries`/`estimated_request_count` output (D-037) to show, per
selected source, which capability determined each query's mode (e.g.
`exact_phrase_search: unsupported -> degraded to any_of_words for "Chief of
Staff"`), and the new `job-scout sources` command (`MILESTONE_2.md` CLI
changes, registry-only view) lists each entry's full `capabilities` block —
this is the natural home for capability inspection since it already exists
to show registry/credential state independent of a chosen profile.
**Real consumption points wired into M2 logic** (the rest are recorded as
data for now, deliberately not wired into scoring/matching logic this
milestone — see below): `canonical_application_url=True` is a precondition
for a source to participate in the new cross-source exact-URL dedup tier
(D-038) — a source without a stable canonical URL must not be compared on
URL alone, since its URLs could be session-scoped or otherwise
non-canonical, and a false match there would silently merge two distinct
jobs.
**Deliberately not wired into any scoring/dedup logic this milestone**:
`stable_external_job_id=False` and `structured_description=False` are
recorded (so `job-scout sources`/a future `job-scout evaluate` pass can see
them) but do not change Tier 1 fingerprinting confidence or Jaccard-dedup
thresholds in M2 — doing so would be a new, untested scoring dimension
outside this milestone's own acceptance criteria. Left as an explicit
forward-looking signal for a later milestone, not a gap.
**Alternatives**: Many scattered top-level booleans directly on
`SourceRegistryEntry` (rejected — the task's own instruction prefers one
typed object, and sixteen more top-level fields would make an already-large
model harder to read); keeping `SourceQueryCapabilities` as a second,
narrower model alongside the new broader one (rejected — two overlapping
capability models on the same entry is exactly the kind of
duplicated-source-of-truth risk this ADR is trying to avoid, since
`exact_phrase_search`/`supports_exact_phrase` would mean the same thing in
two places); adding `authentication_required` for symmetry with the task's
suggested field list (rejected — direct duplicate of the already-existing
`auth_required`, see above).
**Why**: Matches the task's explicit preference for one typed capability
object, keeps every new signal config-driven (`SourceRegistryEntry`,
YAML-editable, per CLAUDE.md hard constraint 6/10), and avoids inventing a
new scoring/dedup behaviour this milestone doesn't need — the two fields M2
actually branches on (`exact_phrase_search`/`keyword_search` for query
mode, `company_filter` for query-vs-watchlist fetch strategy,
`canonical_application_url` for dedup-tier eligibility) get concrete
wiring; the rest are honest, inspectable metadata that costs nothing to
record now and saves a schema change later.

### D-042: UK licensed-sponsor register is mandatory for Milestone 2; the
Netherlands recognised-sponsors register is optional/stretch and must not
block M2 completion
**Decision**: Refines D-039 (which scoped sponsor-register enrichment as
"import-only, UK and NL, no live download, no fuzzy matching" without
ranking the two countries against each other). For M2, mandatory scope is:
the generic sponsor-registry import framework (`job-scout sponsors import`,
`SponsorRegistryEntry` persistence — country/register-agnostic, a plain
CSV-parsing function per register, not a plugin/loader mechanism per
CLAUDE.md hard constraint 9), the UK Home Office licensed-sponsor register
provider/parser specifically, deterministic employer-name normalization
(reusing `deduplication.normalize_company`, unchanged from D-039), full
evidence provenance (`SponsorRegistryMatch`/
`VisaAssessment.employer_registry_match*`/`registry_source`), and its
integration into `assess_visa()`'s evidence precedence (`MILESTONE_2.md`
"Evidence precedence"). The Netherlands IND recognised-sponsors provider
stays in the design (kept, not deleted — the "Sponsor registers" section of
`MILESTONE_2.md` still documents it) but is explicitly optional/stretch: M2
is complete without it, and it must not block the milestone's Definition of
Done if any of the following hold at implementation time — (a) the IND
register's published file format proves difficult to parse reliably (e.g.
an unstable or undocumented column layout), (b) authoritative access to a
current snapshot cannot be verified the same way D-016/D-027/D-028/D-031
required for other source contracts, (c) the register's schema changes
materially from what this planning pass assumed, or (d) implementing it
would expand M2's scope disproportionately to its value (e.g. requiring new
normalization logic beyond what the UK provider already established). No
live government-register downloading for either country is authorized in
M2 regardless of this refinement — unchanged from D-039 — and would require
separate, explicit user approval in a future milestone.
**Alternatives**: Treat UK and NL as equally mandatory, as D-039's original
framing implied (rejected — the task explicitly asks for a mandatory/
optional split, and a two-country mandatory bar is a real completion risk
if the Dutch register's format turns out to be harder to parse reliably
than the UK one, which this planning pass has not yet verified to the same
evidence bar D-016 established for Adzuna); drop the Netherlands provider
from the design entirely now that it's optional (rejected — explicit
instruction to keep it as a design proving the import framework generalizes
beyond one country, just not required for completion).
**Why**: Matches the task's explicit instruction and this project's
existing evidence discipline (D-016/D-027/D-028/D-031: don't commit to a
source contract before it's actually verified) — the UK register's
CSV/ODS shape is well-documented and publicly stable; the Dutch register's
has not yet been evaluated to the same bar during this planning pass, so
gating M2 completion on it would repeat the mistake those ADRs already
declined to make. Keeping the NL design (not deleting it) preserves the
"the import framework is register-agnostic, UK is just the first concrete
instance" property the mandatory framework requirement is there to prove.

### D-043: Evaluation dataset expanded to five label categories across
multiple professions, including a profession-agnostic "deceptive false
positive" category; metric set expanded accordingly
**Decision**: `EvaluationLabel` (`MILESTONE_2.md` domain-model changes,
Workstream E) changes from four values (`strong_match | adjacent_match |
weak_match | reject`) to five: `strong_match | adjacent_match | weak_match |
hard_filter_reject | deceptive_false_positive`. `reject` is renamed
`hard_filter_reject` for clarity (a fixture a real Stage 1 hard filter
should reject — visa/location/citizenship, etc.) and does not change
meaning otherwise. `deceptive_false_positive` is new: a fixture that
plausibly *looks* like a match on shallow keyword overlap (shares a generic
word with a configured title/skill) but a human reviewer would not consider
it the same role family — the exact class of near-miss this project's own
live-run audits have already found in practice (D-029/D-032/D-033/D-034's
"Data Business Analyst" vs. "Strategy Manager"-shaped confusions). This is a
distinct failure mode from `weak_match` (a genuinely weaker but still real
match) and from `hard_filter_reject` (fails an explicit hard-eligibility
rule) — a deceptive false positive typically *passes* Stage 1 and often
Stage 2, and only a human label (or, going forward, this evaluation tool)
can catch it. `EvaluationJobFixture`'s shape is unchanged (`job_id, title,
description, company, location, employment_type, posted_at, label,
rationale`) — `rationale` is exactly where a labeller explains *why* a
`deceptive_false_positive` fixture is deceptive, which doubles as
documentation for whoever maintains the fixture set later.
The labelled fixture set (`tests/fixtures/evaluation/`) must span
**multiple professions**, not only the shipped example profile's
strategy/transformation/chief-of-staff domain — at minimum one additional
fixture group shaped like a different profession (e.g. a nursing-,
software-engineering-, or sales-shaped group), each with all five labels
represented. Concrete deceptive-false-positive *pattern examples* the task
named (Business Analyst vs. Data Analyst vs. HR Analyst; Software Engineer
vs. Sales Engineer; Registered Nurse vs. Nurse Recruiter; Mechanical
Engineer vs. Sales Engineer; Strategy Analyst vs. Investment Analyst;
Product Manager vs. Product Marketing Manager) are illustrative guidance for
authoring the fixture set — they live only in `tests/fixtures/evaluation/`
test data and `MILESTONE_2.md`'s own prose, never as a hard-coded list in
`src/job_scout/` (CLAUDE.md hard constraint 10 — the matching engine itself
must stay profession-agnostic; only the *test fixtures* get
profession-specific, and only to prove the scoring engine handles them
without profession-specific code).
Metrics reported by `job-scout evaluate` expand from `precision@10/@20,
recall of labelled strong matches, false-positive rate, tier-vs-label
cross-tab` to: **precision@5**, precision@10, precision@20 (precision@k =
fraction of the top-k ranked fixtures, by `final_score`, whose label is
`strong_match` or `adjacent_match`), **recall of labelled strong matches**
(unchanged), **false-positive rate** (fraction of
`deceptive_false_positive`-labelled fixtures that land in `priority`/
`digest` tiers — this is the metric that directly measures whether the
milestone's namesake risk is actually caught), **hard-filter correctness**
(fraction of `hard_filter_reject`-labelled fixtures Stage 1 actually rejects
— a direct pass/fail count against `HardFilterResult.passed`, distinct from
the score-based metrics), **ranking inversions** (count of labelled-fixture
pairs where a lower-ranked label scores a strictly higher `final_score`
than a higher-ranked label, e.g. any `deceptive_false_positive` outscoring
any `strong_match` — directly generalises the ranking-order regressions
D-032/D-033/D-034 found and fixed by hand, into a repeatable, automated
check), and **threshold-tier distribution** (count of labelled fixtures
landing in each `notification_tier`, cross-tabbed by label — supersedes/
renames the earlier "tier-vs-label cross-tab" language, same underlying
computation).
`final_score`/`ScoreComponent.raw_value`/`weighted_value` continue to be
documented and printed as **relevance scores** — a deterministic, weighted-
sum ranking signal — never as a probability or confidence percentage;
`job-scout evaluate`'s own output and any future documentation must not
describe them as "probability of being a match."
**Alternatives**: Folding "deceptive false positive" into the existing
`weak_match` label (rejected — a weak-but-real match and a plausible-but-
wrong match need different remediation: a weak match is a
scoring-sensitivity question, a deceptive false positive is a
precision/false-positive-rate question, and conflating them would blunt
exactly the metric — false-positive rate — this expansion exists to add); a
single combined strategy-only fixture set with inline comments noting
"imagine this were a different profession" (rejected — doesn't actually
exercise the profession-agnostic matching code against different
vocabulary, which is the whole point of requiring multiple real
profession-shaped fixture groups).
**Why**: Directly matches the task's explicit instruction, and closes a
real gap this project's own history exposes — every deceptive-false-
positive-shaped bug found in Milestone 1/1.1 (D-029, D-032 through D-034)
was caught by manual live-run inspection, not by a repeatable tool.
`job-scout evaluate` existed already to calibrate thresholds (Workstream E);
expanding its label/metric set to explicitly target this exact failure mode
turns "we happened to notice this in a live run" into "the evaluation tool
would have caught this automatically," which is the tool's actual purpose
per `MILESTONE_2.md`'s own stated goal.

### D-044: Milestone 2 open-scope questions confirmed approved by the user
(2026-08-08); adapter set fixed at exactly three (Reed, Greenhouse, Lever)
**Decision**: The following M2 scope questions — previously stated as
decisions in earlier ADRs but in some cases still phrased with residual
ambiguity or flagged as "open decisions for the user" in `MILESTONE_2.md`'s
own preparation report — are confirmed **approved**, not open, as of this
planning refinement pass:
1. **Email-alert ingestion** stays re-sequenced to Milestone 3 (D-035) —
   confirmed, unchanged.
2. **The general, automatic source-discovery workflow** stays re-sequenced
   to Milestone 3 (D-035) — confirmed, unchanged.
3. **M2's adapter set is fixed at exactly three: Reed, Greenhouse, and
   Lever**, in addition to the existing Adzuna adapter — this replaces the
   earlier "two to three new... adapters" phrasing (`MILESTONE_2.md` "In
   scope", and the Deliverable 5/acceptance-criteria wording that only
   required "Reed + one of Greenhouse/Lever, minimum"). All three are in
   scope; none is optional. `MILESTONE_2.md`'s acceptance criteria and
   Deliverable 5 are updated accordingly (see the implementation-sequence
   update accompanying this ADR).
4. **`VisaAssessment`/`VisaStatus` keep their existing six-value-enum
   shape** (D-036) — confirmed, unchanged, unless a concrete M2
   implementation finding proves it insufficient (in which case a new ADR
   would document that finding before any schema change, per this
   project's existing discipline).
5. **UK sponsor-register provider is mandatory; Netherlands is
   optional/stretch** (D-042) — confirmed.
6. **No notification delivery and no scheduler in M2** (already stated in
   `MILESTONE_2.md`'s "Explicitly out of scope") — confirmed, unchanged.
**Alternatives**: Leave these as they were — some already effectively
decided in prose but never formally marked closed, one (the adapter count)
genuinely ambiguous between "2" and "3" depending which part of the
document was read. Leaving ambiguity here risks a real implementation-time
scope question re-litigating a decision the user has already made.
**Why**: The task explicitly asked for these to be recorded as approved
decisions in `decisions.md`, not left as open questions the next session
would need to re-ask the user about. Fixing the adapter count at exactly
three (rather than "2 to 3") removes a genuine, previously-unresolved
ambiguity between `MILESTONE_2.md`'s "In scope" prose and its own
acceptance criteria, which — if left inconsistent — would have let an
implementation pass legitimately claim done-ness after building only two
adapters.

### D-045: Milestone 2 Deliverable 5 step 4 (planned-query execution) —
fail-fast per source on a query failure; zero-planned-queries sources
produce no `SourceRun`; `search_queries` reconciled; `PlannedQuery.mode`
threaded to the Adzuna adapter boundary via a new, source-agnostic
`SourceSearchParams.keyword_mode` field
**Decision**: `pipeline.py::run_once` now calls `adapter.fetch()` once per
`SelectedSource.planned_queries` entry (architecture.md §17), resolving
several execution-semantics questions `MILESTONE_2.md`'s Deliverable 5 step 4
description left implicit:
1. **Failure handling is fail-fast per source, not best-effort-continue.** If
   one planned query's `fetch()` raises a `SourceAdapterError`, the remaining
   planned queries for that source are not attempted; data already returned
   by earlier, successful queries in the same run is kept. This mirrors
   `AdzunaAdapter.fetch()`'s own existing internal behaviour (one country's
   failure already aborts the rest of that call, §3) rather than inventing a
   new "keep trying after an error" policy the task explicitly discouraged
   ("do not invent a complex retry framework"). If a source's queries produce
   zero raw records and at least one error, the run is `FAILED` with zero
   jobs — byte-identical to the pre-M2 single-query failure outcome, so `N=1`
   planned query reduces exactly to prior behaviour. If at least one query
   succeeded before a later one failed, the run is `PARTIAL`, reusing the
   existing `PARTIAL if (hit_cap or run.errors) else SUCCESS` status logic
   unchanged.
2. **A source with zero planned queries produces no `SourceRun` row at all**,
   mirroring the pre-existing `not selected.executable: continue` skip
   (`pipeline.py`) rather than logging an empty/vacuous run. Chosen as the
   closer precedent already established in the same function, over inventing
   a new "ran but had nothing to search for" run state this task's own scope
   didn't ask for.
3. **`SelectedSource.search_queries` is now rendered from `planned_queries`**
   (one string per query — the phrase for `exact_phrase`, an `" OR "`-joined
   list for `any_of_words`), replacing the M1/1.1
   `candidate_profile.title_aliases` list `source_intelligence/planner.py`
   populated it with through step 3. `planner.py`'s own step-3 comment
   ("reconciling or retiring this field is Task 4's concern") assigned this
   to step 4; `MILESTONE_2.md`'s Deliverable 5 step 4 file list names only
   `pipeline.py`, but leaving `search_queries` stale would violate this
   task's own explicit "no field may misleadingly claim a different search
   was executed" instruction and the acceptance criterion that `job-scout
   plan`'s displayed queries match what `run-once` actually executes. This
   is a one-line, additive rendering change in `planner.py` — no query
   generation logic (step 3, untouched) or `AdzunaAdapter` code moved.
   `SourceSearchParams.keywords` on `SelectedSource.search_params` is left
   as-is (still `candidate_profile.title_aliases`), now documented in a code
   comment as a legacy template value for the *non-keyword* fields only
   (`countries`/`employment_types`/paging) — `run_once` overrides `.keywords`
   per query via `model_copy`, so the stale value is never actually sent to
   an adapter.
4. **`PlannedQuery.mode` now reaches the Adzuna adapter boundary, via a new,
   source-agnostic `SourceSearchParams.keyword_mode` field, not an
   Adzuna-specific one.** The original version of this step left
   `AdzunaAdapter._build_query` unchanged (per `MILESTONE_2.md` Deliverable 5
   step 4's literal "`AdzunaAdapter` itself is **not** modified"), which meant
   `exact_phrase` and `any_of_words` queries silently rendered into the
   identical Adzuna request (`what_or` in both cases) — a genuine semantic
   mismatch between what `SourceCapabilities.exact_phrase_search=True` (D-041)
   declares Adzuna supports and what the shipped adapter actually did. Fixed
   as a follow-on correction, explicitly authorised by the user, with the
   smallest typed change: `SourceSearchParams` gains `keyword_mode:
   Literal["exact_phrase", "any_of_words"] = "any_of_words"` — the same two
   values and meaning as `PlannedQuery.mode`, so the model stays a generic,
   adapter-agnostic carrier of query intent (never `what`/`what_or` or any
   other adapter-specific parameter name). `pipeline.py` copies
   `query.mode -> keyword_mode` alongside `query.keywords -> keywords` in the
   same `model_copy(update={...})` call already used for keywords — still a
   plain field copy, no `if source_id == ...` branching anywhere in
   `pipeline.py`. `AdzunaAdapter._build_query` gained one small,
   `keyword_mode`-aware branch: `"any_of_words"` -> `what_or` (unchanged,
   confirmed OR-of-words); `"exact_phrase"` -> `what`, Adzuna's stricter of
   the two documented parameters — never both parameters on the same
   request. Evidence gathered at implementation time
   (`developer.adzuna.com/docs/search` and secondary sources) confirms `what`
   and `what_or` are both real, documented parameters, and confirms
   `what_or` is OR-of-words, but could **not** confirm that `what` guarantees
   literal word-adjacency/quoted-phrase matching — Adzuna's own docs also
   reference a separate `what_phrase` parameter, which would be the literal-
   phrase option if a future task needs one. Per this project's evidence bar
   (D-016/D-027/D-028/D-031), `what` is documented here as "stricter/
   all-terms-required," not overclaimed as guaranteed phrase-adjacency; no
   quote characters or other unverified quoting syntax are added to either
   parameter's value. `SourceCapabilities.exact_phrase_search`'s meaning is
   unchanged; the frozen `PlannedQuery.mode` name/values (Task 3) are
   unchanged; no query-generation rule changed. The invariant this correction
   establishes: the two modes no longer render into the same Adzuna request
   for the same keywords (`test_exact_phrase_and_any_of_words_never_render_
   identical_requests`, `tests/test_adzuna_adapter.py`).
**Alternatives**: Continuing to attempt every planned query even after one
fails (rejected — no precedent elsewhere in this codebase for "ignore an
error and keep going" at the source-adapter boundary; the adapter's own
per-country behaviour is fail-fast, and matching that is the smaller,
more consistent change); logging a `SUCCESS`, zero-job `SourceRun` for a
zero-planned-queries source instead of skipping it entirely (rejected —
the `not executable` precedent in the same function already establishes
"nothing happened, no row" as this codebase's convention); leaving
`exact_phrase`/`any_of_words` collapsed to the same `what_or` request
(rejected — the user explicitly authorised this follow-on correction rather
than accepting the mismatch, which is what this final version of D-045
implements); mapping `exact_phrase` to the undocumented-in-depth
`what_phrase` parameter instead of `what` (rejected — the task's own
explicit "expected architectural intent" directs `exact_phrase -> what`, and
`what_phrase`'s own semantics could be confirmed even less than `what`'s from
available evidence; using it would trade one unverified claim for another,
less-evidenced one); adding literal quote characters around multi-word
`what` values to force phrase-adjacency (rejected — not confirmed as
supported/required syntax by any source consulted, and the task explicitly
forbids inventing unverified quoting syntax).
**Why**: Each choice reuses an existing, already-established pattern in this
codebase (fail-fast adapter behaviour, the `not executable` skip precedent,
the `PARTIAL`/`FAILED` status logic, `model_copy`-based field substitution
already used for `keywords`) rather than inventing new policy, per this
project's "smallest behaviour consistent with existing error handling"/
"smallest typed change" instructions; the `keyword_mode` field keeps the
adapter-boundary rule (`architecture.md` §3: pipeline code stays
adapter-agnostic, translation happens inside the adapter) intact while
finally giving `exact_phrase`/`any_of_words` distinguishable real requests,
and the `what` semantics claim is calibrated to exactly what the available
evidence supports, consistent with this project's established evidence bar.

### D-046: `ReedAdapter` (Milestone 2 Deliverable 5 step 5) — Search API
only, no Details API; explicit `SourceCapabilities` derived from the
official docs' own prose "Returns" tables (no literal JSON example
published); `_effective_config_status`/`_default_adapter_factory` extended
by the smallest safe if/elif addition, not a generic mechanism
**Decision**: `sources/reed.py::ReedAdapter` implements Reed's documented
Jobseeker API (`https://www.reed.co.uk/developers/jobseeker`, verified at
implementation time), following `AdzunaAdapter`'s exact structural shape
(pure HTTP-in/`RawJobRecord`-out, `is_configured()`, typed exceptions,
per-country loop). Several implementation questions the frozen
`MILESTONE_2.md` task description left to be resolved against the verified
API:
1. **Search endpoint only (`GET /api/1.0/search`); the Details endpoint
   (`GET /api/1.0/jobs/{jobId}`) is not called.** The Details endpoint
   exposes richer fields (contract type, job type, expiration date, an
   external application URL, and a reed.co.uk listing URL) that the Search
   endpoint's response does not include, but calling it once per search
   result would add an unbounded per-result HTTP fan-out that neither
   `ExecutionLimits` nor `SelectedSource.estimated_request_count` (Task 3/4)
   account for. Deferred, not implemented, per the task's own explicit
   preference for the Search-response-only shape when it satisfies the
   `RawJobRecord`/`Job` contract — which it does, once every field this
   adapter can't provide degrades to `None`/absent rather than being
   fabricated (point 3 below).
2. **HTTP Basic Auth via `httpx.BasicAuth(api_key, "")`**, passed per
   request (not baked into a client-level default), so the mechanism stays
   visible and testable at the same call site as every other request
   detail; the API key never appears in the URL, in a query parameter, or
   in any raised exception message (mirrors `AdzunaAdapter`'s existing
   "context string, never the query dict" convention).
3. **Response field-name mapping is a documented, best-effort inference,
   not a literally-quoted contract** — a genuine gap in Reed's own
   published docs, not a shortcut taken here. The docs page's "Returns"
   sections for both endpoints list only human-readable row labels (`Job
   Id`, `Employer Name`, `Job Title`, `Description`, `Location Name`,
   `Minimum Salary`, `Maximum Salary`, …) inside a parameters/returns table
   — no literal JSON response example or field-casing sample is published
   anywhere on the page (confirmed by three separate targeted fetches at
   implementation time, including one that asked explicitly for verbatim
   table cell text). `ReedAdapter`/`normalize_reed_record` therefore use
   this project's direct camelCase translation of those documented labels
   (`jobId`, `employerId`, `employerName`, `employerProfileId`, `jobTitle`,
   `description`, `locationName`, `minimumSalary`, `maximumSalary`) —
   standard REST/JSON convention, not a fabricated guess from nothing, but
   explicitly flagged (in `sources/reed.py`'s module docstring and the
   Task 5 final report) as needing live confirmation via the new opt-in
   `tests/test_reed_integration.py` before a real run is relied on. Every
   field read from the payload uses `.get()` (never a bare index) except
   the required identity field `jobId`, which uses `item["jobId"]` and
   therefore raises uncaught on a malformed record — the same
   "required-field KeyError propagates, optional fields degrade cleanly"
   philosophy `AdzunaAdapter._to_raw_record`'s `item["id"]` already
   establishes, not a new error-handling policy.
4. **Explicit `SourceCapabilities` derived field-by-field from the verified
   docs, deliberately not the Adzuna-shaped default**: `keyword_search=True`
   (`keywords` param documented); `exact_phrase_search=False` (`keywords` is
   a single generic term parameter with no documented literal-phrase/quote
   syntax — per D-041's own warning against overclaiming, and per this
   task's explicit instruction not to fabricate phrase semantics);
   `location_filter=True`/`city_filter=True` (`locationName` is a
   documented free-text location parameter — the same
   one-parameter-covers-city-and-location precedent already accepted for
   `adzuna_api`'s undocumented `where`-equivalent, D-016/D-031);
   `country_filter=False` (Reed's Search API has **no** country-scoping
   request parameter at all — it is inherently a single UK-wide job board,
   unlike Adzuna's per-country endpoint path; geographic coverage is a
   registry/planner concept, never conflated here with an API-side filter,
   per this task's explicit instruction to keep the two distinct);
   `industry_filter=False`/`remote_filter=False`/`posting_date_filter=False`
   (no such parameters documented — never invented); **`company_filter=False`
   even though `employerId`/`employerProfileId` are real, documented
   parameters** — per D-041's explicit warning, `company_filter=True` means
   a watchlist-scoped fetch model that suppresses keyword-`PlannedQuery`
   generation entirely (Greenhouse/Lever's actual shape), and Reed is
   deliberately kept a normal keyword-search source, not reclassified into
   that shape merely because an employer-id parameter exists;
   `salary_data=True` (`minimumSalary`/`maximumSalary` are genuinely part of
   the Search endpoint's own documented Returns, not Details-only);
   `structured_description=False` (the Search response's description is a
   single freeform text field, the same "not structured" classification
   already given to Adzuna's own truncated-snippet description);
   `pagination=True`/`page_size_control=True` (`resultsToTake`/
   `resultsToSkip` are real, documented offset-pagination parameters, capped
   at Reed's own documented 100-result maximum —
   `sources/reed.py::MAX_RESULTS_TO_TAKE`); `stable_external_job_id=True`
   (`jobId` is a stable per-listing identifier, same convention as Adzuna's
   `id`); **`canonical_application_url=False`** — the Search response
   documents no application/job URL field at all (only the unused Details
   endpoint does, per point 1), so this adapter's `RawJobRecord.raw_url` is
   always `""`; per this task's explicit instruction, this capability
   reflects what the adapter's *chosen acquisition endpoint* actually
   provides, not a hypothetical "Reed has URLs somewhere" claim — no URL
   format is fabricated or derived from a job ID;
   `max_recommended_queries_per_request=None` (no Reed-specific request-count
   limit is documented; not invented).
5. **`_effective_config_status` (`source_intelligence/planner.py`) and
   `_default_adapter_factory` (`pipeline.py`) both gained one narrow
   `elif entry.source_id == "reed_api"` / `if source_id == "reed_api"`
   branch each, mirroring the existing `adzuna_api` branch exactly** —
   not a generalised per-adapter credential-mapping/registration mechanism.
   `_effective_config_status`'s own docstring already documented this as the
   deliberate scope boundary ("every other source_id's effective status is
   its declared status until it gets its own adapter and a matching
   credential rule here"); generalising it into a table/registry keyed by
   `source_id` would be a reasonable follow-on refactor once a third
   credentialed adapter exists, but is out of this task's scope per its own
   "smallest Task-5-safe extension, document the debt" instruction — tracked
   here as acknowledged debt, not silently deferred.
6. **Employment-type translation reuses the one existing generic value this
   project already checks, not a new filter surface**: `AdzunaAdapter`
   already reads exactly one `SourceSearchParams.employment_types` value
   ("full_time") to set its own request parameter; `ReedAdapter` maps that
   same, already-established generic value onto Reed's own documented
   `fullTime` boolean parameter. Reed's other documented boolean filters
   (`permanent`/`contract`/`temp`/`partTime`/`postedByRecruitmentAgency`/
   `postedByDirectEmployer`/`graduate`) and `distanceFromLocation` are not
   wired — no existing generic model field carries that intent, and adding
   one would be inventing a new filter surface this task explicitly
   discourages ("do not implement every possible parameter simply because
   the API exposes it").
**Alternatives**: Calling the Details endpoint once per search result to get
a real canonical URL/currency/job-type (rejected — unbounded per-result
fan-out, see point 1); treating `employerId`/`employerProfileId` as grounds
for `company_filter=True` (rejected — explicitly contradicts D-041's own
stated warning); guessing at real-world Reed JSON field casing from prior
general knowledge of the API rather than the verified docs fetched at
implementation time (rejected — this project's evidence bar, D-016/D-027/
D-028/D-031, requires citing what was actually checked; the camelCase
mapping is disclosed as an inference, not presented as confirmed fact);
generalising `_effective_config_status`/`_default_adapter_factory` into a
credential-mapping registry now (rejected — a disproportionate refactor for
a second adapter, deferred with the debt explicitly recorded per this task's
own instruction, not silently absorbed).
**Why**: Every choice here follows the same evidence-bar-first, smallest-
safe-change discipline this project has already established for Adzuna
(D-016/D-027/D-028/D-031) and for `SourceCapabilities` itself (D-041) —
verify against the actual official documentation, degrade honestly to
`None`/`False`/absent rather than fabricate when the documentation doesn't
support a claim, and extend existing narrow mechanisms by the smallest
matching increment rather than generalising ahead of a second real need.

### D-047: `GreenhouseAdapter` (Milestone 2 Deliverable 5 step 7) — one
adapter instance per watchlisted board, not one shared adapter looping
internally; `pipeline.py` gains a second, narrow watchlist-fan-out path
gated on `SourceCapabilities.company_filter` (data-driven, not a
`source_id` string check); `Location.country` stays `""` (honest unknown),
never parsed from Greenhouse's freeform `location.name`
**Decision**: `sources/greenhouse.py::GreenhouseAdapter` implements
Greenhouse's public Job Board API (`GET https://boards-api.greenhouse.io/
v1/boards/{board_token}/jobs`, verified against the official docs —
`grnhse/greenhouse-api-docs`, "Jobs" -> "List jobs" — at implementation
time), following `AdzunaAdapter`/`ReedAdapter`'s structural shape
(`is_configured()`, typed exceptions from `sources/base.py`) with two
genuine, documented differences from both:
1. **No pagination.** The verified docs document no `page`/`per_page`/
   `offset`/`cursor` parameter at all — the endpoint returns every open
   job post for a board in one response (`{"jobs": [...], "meta":
   {"total": N}}`). `fetch()` therefore makes exactly one HTTP request,
   never a page loop; `?content=true` is always sent (a real, documented,
   zero-extra-request flag) so the response actually includes each job's
   description — omitting it would silently make every fetched job
   unscoreable.
2. **One adapter instance per watchlisted company, constructed with
   `board_token`/`company_name`, not one shared adapter called per query.**
   `MILESTONE_2.md`'s own query-planning design (`query_planner.py::
   build_planned_queries`) already returns zero `PlannedQuery`s for any
   source whose `SourceCapabilities.company_filter=True` — Greenhouse's
   real shape (decisions.md D-041) — so `pipeline.py::run_once`'s existing
   step-4 "one `adapter.fetch()` call per `PlannedQuery`" loop can never
   drive it; every company_filter=True source would silently fetch nothing,
   forever, without a second execution path. `run_once` therefore gained a
   second branch, selected by `entry.capabilities.company_filter` (read
   from the registry, exactly D-041's own stated mechanism — never a
   `source_id == "greenhouse_public_feeds"` string check, so step 8's Lever
   adapter reuses this same branch unmodified once it also ships
   `company_filter=True`): for each `CompanyWatchlistEntry` whose
   `source_id` matches the selected source, `pipeline.py` constructs one
   fresh adapter via a new `WatchlistAdapterFactory` (mirrors the existing
   `AdapterFactory`'s per-source-id shape, but keyed by
   `(source_id, CompanyWatchlistEntry)` instead of `source_id` alone — a
   second, narrow, typed factory rather than generalising the first one
   into something pluggable, CLAUDE.md hard constraint 9) and calls
   `.fetch()` exactly once. Both branches converge back into the same
   aggregation/normalisation/dedup/scoring code immediately after —
   `raw_records: list[RawJobRecord]` is populated by whichever branch ran,
   and everything downstream of that point is unchanged and shared. Zero
   matching watchlist entries produces zero calls and no `SourceRun` row at
   all (R-10, MILESTONE_2.md) — the exact same "nothing happened, no row"
   convention `run_once` already used for zero `planned_queries`.
   `SourceAdapter.fetch(params: SourceSearchParams)`'s Protocol signature is
   unchanged; `GreenhouseAdapter.fetch()` accepts and ignores `params`
   entirely (documented in its own module docstring) rather than the
   alternative of adding a new field to `SourceSearchParams` to carry the
   board token through the existing single-adapter-per-source path (see
   Alternatives).
**Company name**: Greenhouse's list-jobs response never includes a company
name at all (one board is implicitly one company) — the only source of
truth is the `CompanyWatchlistEntry.company_name` that produced the fetch.
`GreenhouseAdapter.__init__` takes `company_name` alongside `board_token`
and `_to_raw_record` stashes it onto `raw_payload["_company_name"]`, the
same non-API-native-context-stash pattern `AdzunaAdapter`/`ReedAdapter`
already use for `_query_country` — real, known-correct context describing
which fetch produced this record, not fabricated job data.
**`Location.country` stays `""` (never parsed from `location.name`)**:
Greenhouse's list-jobs response provides exactly one freeform location
signal per job, `location.name` (e.g. "London", "Remote - US") — no
structured country code anywhere in the documented response, even with
`?content=true`. `Location.country: str` is a required field, so some
value must be supplied; this task's own explicit instruction ("do not
fabricate... or other fields when the source does not provide them", "be
explicit about ... uncertainty rather than silently guessing") rules out
inferring an ISO country from free text via a name-matching heuristic, even
though `countries.py` has region data that could technically back one — a
heuristic string match against values like "Remote - US"/"EMEA"/"NYC" is
exactly the class of unreliable inference the project's evidence bar (D-016/
D-027/D-028/D-031, restated in D-046) already forbids. Consequence,
documented rather than silently absorbed: `matching/hard_filters.py::
evaluate_hard_filters` treats a falsy `Location.country` as unknown and
rejects the job whenever `SearchProfile.included_countries` is non-empty —
so a Greenhouse job is only ever hard-filtered on country when the running
search profile actually scopes by country, which is common. This is an
honest reflection of what Greenhouse's public API genuinely does not tell
this project, not a bug this task silently works around; a future,
separately-scoped improvement could derive a coarser signal from the
`offices[].location` array (present under `?content=true`) via a verified
name/code mapping, but that is new inference logic this task was not asked
to build and is left as acknowledged debt.
**Fields left `None`, and why**: `posted_at` — only `updated_at`
(last-modified) is present; never conflated with a posting date (D-040's
rule, same treatment D-046 already gave Reed's missing posted-date field).
`employment_type` — no such field is documented anywhere on this endpoint.
`salary_min`/`salary_max`/`salary_currency` — `pay_input_ranges` exists only
on the single-job detail endpoint behind `?pay_transparency=true`, which
this adapter never calls, for the same "no unbounded per-result endpoint
fan-out" reason D-046 already established for Reed's Details endpoint.
**Malformed job entries**: a job post missing its own `id` field is skipped
individually (cannot become a valid `RawJobRecord.external_id`), rather than
crashing the whole board's fetch over one bad record or fabricating an
identifier.
**`SourceCapabilities`** (packaged template, `source_registry.example.yaml`):
`company_filter: true`, `keyword_search: false`, `exact_phrase_search:
false`, `location_filter: false`, `country_filter: false`, `city_filter:
false`, `industry_filter: false`, `remote_filter: false` (no such
parameters documented, `geographic_coverage` stays a registry/planner
concept only, same precedent D-046 already established for `reed_api`),
`salary_data: false`, `structured_description: false` (`content` is one
freeform HTML string), `pagination: false`, `page_size_control: false` (no
such parameters documented at all — a first for this project's three real
adapters), `posting_date_filter: false`, `stable_external_job_id: true`
(the docs themselves state `id` is the stable per-post identifier),
`canonical_application_url: true` (`absolute_url` is a genuine documented
link, unlike Reed's `reed_api`). `adapter_ref: greenhouse`; `approval_status`
stays `manual_review` (CLAUDE.md hard constraint 1 — never approved by
default).
**Estimated request count under-reports for company_filter=True sources**:
`source_intelligence/planner.py::build_plan`'s existing
`estimated_request_count = len(supported) * len(planned_queries) *
max_pages` arithmetic yields `0` for Greenhouse regardless of watchlist
size, because `len(planned_queries)` is always `0` for a company_filter=True
source (query_planner.py, unchanged by this task). This is a pre-existing
consequence of step 3's design, not introduced by this task, and fixing it
would require touching either `query_planner.py` or `planner.py`'s
arithmetic — both explicitly out of this task's scope (the task instruction
says not to modify `query_planner.py`, and `planner.py`'s own
`estimated_request_count` formula is untouched precedent this task does not
own). Documented here as acknowledged debt, not silently absorbed; `job-scout
plan`'s displayed estimate for a watchlist-populated Greenhouse entry will
read `0` even though `run-once` will actually issue one request per
watchlisted company.
**Alternatives**: Adding a `company_key`/`board_token`-shaped optional field
to `SourceSearchParams` and keeping one shared adapter instance per source,
routed through the existing single-arg `AdapterFactory` with per-call
`model_copy` substitution (the same mechanism step 4 already uses for
`keywords`/`keyword_mode`) — considered, and structurally closer to the
existing query-fan-out shape, but rejected: it would grow the shared,
adapter-agnostic `SourceSearchParams` model with a field only two adapters
(Greenhouse, Lever) ever read, and — unlike `keyword_mode`, which every
adapter already receives and either uses or ignores by design — a
board-token-shaped field has no meaning for keyword-search sources at all,
so it reads as a company_filter-source-specific parameter smuggled into a
supposedly source-agnostic model. Constructing one adapter instance per
company, the same way `board_token`/`company_name` already have to reach
the adapter as constructor config (there is no other channel for them),
keeps `SourceSearchParams` exactly as `MILESTONE_2.md`'s own "Architecture
changes" section says it should stay for this step
("`SourceAdapter.fetch()`'s Protocol signature ... unchanged — the fan-out
happens at the pipeline call site, not inside the adapter"). Hardcoding
`if selected.source_id == "greenhouse_public_feeds"` in `pipeline.py`
instead of reading `entry.capabilities.company_filter` (rejected — CLAUDE.md
hard constraint 6 explicitly warns against a role/source assuming one fixed
set of sources, and D-041 already established `company_filter` as exactly
this generic, data-driven signal; a `source_id` string check would also mean
step 8's Lever adapter needs its own near-duplicate branch instead of
falling into the same one). Parsing/guessing `Location.country` from
`location.name` or the `offices[]` array now (rejected — see "`Location
.country` stays `""`" above). Mapping `updated_at` -> `posted_at` (rejected
— a last-modified timestamp is not a posting date; conflating the two would
misrepresent job freshness for any listing edited after its original post
date).
**Why**: Same evidence-bar-first, smallest-safe-change discipline as D-046
— verify the actual documented contract, degrade honestly to `None`/`""`/
skipped rather than fabricate or infer when the documentation doesn't
support a claim, and extend existing narrow mechanisms (a second factory
shape, a capability-gated branch) by the smallest matching increment rather
than generalising `AdapterFactory` itself or reaching for a new
`SourceAdapter` method.

### D-048: `LeverAdapter` (Milestone 2 Deliverable 5 step 8) — reuses
D-047's watchlist branch unmodified; no pagination shipped; `country` and
`workplaceType` used directly since Lever's contract is genuinely more
structured than Greenhouse's
**Decision**: `sources/lever.py::LeverAdapter` implements Lever's public
Postings API (`GET https://api.lever.co/v0/postings/{site}?mode=json`, no
authentication, verified against the official docs —
`lever/postings-api`, https://github.com/lever/postings-api — at
implementation time), following `GreenhouseAdapter`'s exact structural
shape (`is_configured()`, typed exceptions from `sources/base.py`, one
instance per `CompanyWatchlistEntry`) with genuine, documented differences
in three places:
1. **Bare-array response, not a wrapped object.** Unlike Greenhouse's
   `{"jobs": [...]}`, a live, unauthenticated request against a real
   Lever-hosted site (`https://api.lever.co/v0/postings/lever?mode=json`,
   checked at implementation time — returned `[]`) confirmed the
   list-postings endpoint returns a bare JSON array at the top level. This
   was not fully confirmed by the official README text alone (which
   describes per-posting fields but shows no literal example response),
   so the live check is the evidence this project's bar requires
   (D-016/D-027/D-028/D-031). `_get_postings` treats any other top-level
   shape as zero postings, never guessed at — same discipline as
   Greenhouse's `payload.get("jobs", [])` fallback for its analogous case.
2. **No pagination, deliberately.** Lever's docs document real `skip`/
   `limit` query parameters — unlike Greenhouse, which has no pagination
   parameters at all — but neither the docs nor the live check above
   expose a total-count/`hasMore` termination signal. Per this project's
   evidence bar, `fetch()` makes exactly one HTTP request and never sends
   `skip`/`limit`; `SourceCapabilities.pagination=False` records this as a
   deliberate, acknowledged limitation, not an oversight. A watchlisted
   company with more open postings than one unpaginated response returns
   will have the remainder silently unfetched until a reliable stop
   condition is confirmed in a future task.
3. **`country` and `workplaceType` are real, structured fields Greenhouse's
   feed does not have** — Lever's docs document `country` as ISO 3166-1
   alpha-2 or `null`, and `workplaceType` as a closed enum
   (`unspecified|on-site|remote|hybrid`). `Location.country` is populated
   directly from `country` (null/absent → `""`, the same honest-unknown
   convention every adapter already uses for a missing required string) —
   genuinely more accurate than Greenhouse's forced `""` (D-047), since
   real data exists here. `Job.remote_type` is read directly from
   `workplaceType` via a fixed `_LEVER_WORKPLACE_TYPE_MAP`
   (`pipeline.py`), **not** the shared `_guess_remote_type` text heuristic
   every other source (Adzuna/Reed/Greenhouse) falls back to — a source's
   own authoritative structured field is more accurate than guessing from
   description text when that field genuinely exists; an unrecognised or
   missing value maps to `RemoteType.UNKNOWN`, the same "no evidence"
   sentinel `_guess_remote_type` already returns (never `None` —
   `Job.remote_type` is a required field, not optional).
**`createdAt` is never used for `posted_at`.** A live response can carry
an undocumented `createdAt` field, but Lever's own `postings-api` issue
tracker (`github.com/lever/postings-api` issue #35, "`createdAt` field not
documented and no correct (v0)") reports its values do not parse into sane
timestamps and the field has no documented meaning. Treating an
unreliable, unofficial field as a posting date would be exactly the
fabricated-certainty this project's evidence bar forbids — `posted_at`
stays `None` unconditionally, same treatment D-046/D-047 already gave
Reed's/Greenhouse's missing posted-date signal.
**`salaryRange`** (`currency`/`min`/`max`, all optional per Lever's docs)
maps directly to `salary_currency`/`salary_min`/`salary_max`; a missing
`salaryRange` (or a missing sub-field) normalizes to `None`, never `0` or
inferred from the separate freeform `salaryDescription` text.
**`applyUrl` is preserved but not surfaced.** Lever documents two distinct
links per posting — `hostedUrl` (the posting page) and `applyUrl` (the
application form). `raw_url`/`Job`'s canonical URL uses `hostedUrl` only
(the same "one canonical URL field" shape every adapter already has);
`applyUrl` is kept on `raw_payload` (via the same `{**item, ...}` spread
`_to_raw_record` already uses) for potential future use, not discarded,
but no new `Job`/`RawJobRecord` field was added to carry it — adding one
now would be schema growth for a value nothing downstream reads yet.
**Watchlist branch reused unmodified.** `pipeline.py::run_once`'s
`is_watchlist_source = entry.capabilities.company_filter` branch (D-047)
required zero changes — it already reads `SourceCapabilities.company_filter`
from the registry, never a `source_id` string check, exactly so Lever could
land here. The only `pipeline.py` changes are a second `elif` in
`_default_watchlist_adapter_factory` (constructing `LeverAdapter` from
`CompanyWatchlistEntry.external_company_key`/`company_name`, mirroring
Greenhouse's branch byte-for-byte in shape) and a second `_NORMALIZERS`
entry. `query_planner.py`/`source_intelligence/planner.py` are untouched,
same as D-047.
**`SourceCapabilities`** (packaged template,
`source_registry.example.yaml`): `company_filter: true`,
`keyword_search: false`, `exact_phrase_search: false`, `location_filter:
false`, `country_filter: false`, `city_filter: false`, `industry_filter:
false`, `remote_filter: false` (Lever documents `location`/`team`/
`department`/`commitment`/`level` filter parameters, but this adapter
deliberately never sends them — same "one fetch per watchlisted company,
unfiltered" shape as Greenhouse, per this task's frozen scope), `salary_data:
true` (a genuine difference from Greenhouse's `false` — `salaryRange` is
real, documented, optional data), `structured_description: false`
(`description`/`descriptionPlain` etc. are freeform HTML/plaintext),
`pagination: false`, `page_size_control: false` (see point 2 above),
`posting_date_filter: false`, `stable_external_job_id: true` (`id` is
confirmed a stable per-posting identifier by the docs), `canonical_
application_url: true` (`hostedUrl` is a genuine documented link).
`adapter_ref: lever`; `approval_status` stays `manual_review` (CLAUDE.md
hard constraint 1 — never approved by default).
**Alternatives**: Sending `skip`/`limit` and looping until a response page
returns fewer than `limit` results (considered, rejected — without a
documented or live-confirmed default/max for `limit`, "fewer than
requested" is itself an unverified stop-condition assumption, the same
class of guess D-016/D-027 already declined to make for Adzuna's country
coverage). Using `_guess_remote_type` for consistency with every other
source instead of `workplaceType` (considered, rejected per this task's
explicit instruction and this project's general "prefer real structured
data over a heuristic when it genuinely exists" principle — Greenhouse and
Reed use the heuristic only because they have no better field to read, not
because consistency across sources is itself a goal). Hardcoding
`if selected.source_id == "lever_public_postings"` anywhere in `pipeline.py`
beyond the one factory `elif` (rejected — the shared `is_watchlist_source`
branch already generalizes correctly via `capabilities.company_filter`,
per D-047's own design intent).
**Why**: Same evidence-bar-first, smallest-safe-change discipline as
D-046/D-047 — verify the actual documented (and, where docs were
insufficient, live-checked) contract, degrade honestly to `None`/`""`
rather than fabricate or infer when evidence doesn't support a claim, reuse
the existing generic watchlist-execution architecture rather than building
a parallel one, and use a source's own real structured data in preference
to a shared heuristic when — and only when — that data is genuinely more
reliable than the heuristic it would replace.

### D-049: Cross-source deduplication (Milestone 2 Deliverable 5 step 9) —
`DedupTier.CROSS_SOURCE_DUPLICATE` renamed/split into `EXACT_DUPLICATE`/
`PROBABLE_DUPLICATE`; capability gating passed as a plain `source_id ->
SourceCapabilities` map, never a registry dependency inside `deduplication.py`
**Decision**: Implements D-038's design exactly as specified (exact
cross-source canonical-URL tier + Jaccard/posted-date+salary-corroborated
probable-duplicate tier; thresholds unchanged from the design pass:
`PROBABLE_DUPLICATE_JACCARD_THRESHOLD = 0.6`,
`PROBABLE_DUPLICATE_POSTED_DATE_WINDOW_DAYS = 3`), with two implementation
choices the design pass left open:
1. **`DedupTier.CROSS_SOURCE_DUPLICATE` is renamed to `EXACT_DUPLICATE` and
   `PROBABLE_DUPLICATE`, not kept as one enum value covering both new
   tiers.** MILESTONE_2.md's own "Deduplication implications" section
   defines exactly these two named categories ("Exact duplicate: Tier 1...
   or the new cross-source canonical-URL match — no reasonable doubt";
   "Probable duplicate: the new Tier 2..."), so the code now says what the
   spec already says. The value is not persisted anywhere (`DedupResult` is
   an in-memory pipeline signal only, never written to SQLite or exposed on
   a `Job`/`MatchResult`), and exactly one existing test referenced the old
   name — a safe, in-scope rename rather than a breaking schema/API change.
   `pipeline.py`'s dedup call site now checks membership in
   `(EXACT_DUPLICATE, PROBABLE_DUPLICATE)` where it previously checked one
   value; both are merge-eligible outcomes with identical downstream
   handling (`merge_provenance`, `jobs_duplicate += 1`, no new `Job` row).
2. **Capability gating takes a `source_capabilities: Mapping[str,
   SourceCapabilities] | None` parameter on `match_against_recent`, not a
   `SourceRegistryEntry` list or a registry lookup inside
   `deduplication.py`.** `deduplication.py` has never imported
   `SourceRegistryEntry` and this task does not start — `pipeline.py::run_once`
   (which already holds the loaded registry) builds the
   `source_id -> SourceCapabilities` map once per run and passes it through,
   the same "pass data in, don't reach out for it" shape `evaluate_hard_filters`/
   `run_prefilter` already use for `CandidateProfile`/`SearchProfile`. Each
   fingerprint's own `source_id` (parsed from `external_source_id`'s
   `"{source_id}:{external_id}"` prefix, the same format `compute_fingerprint`
   already constructs) is the lookup key; a `source_id` absent from the map
   falls back to `SourceCapabilities()`'s own `True` default for
   `canonical_application_url` — identical to how an omitted `capabilities`
   block on a real `SourceRegistryEntry` already behaves (D-041), so a caller
   that doesn't care about capability gating (most existing unit tests) needs
   to pass nothing.
**Real-registry regression coverage**: `reed_api`'s packaged registry entry
already ships `canonical_application_url: false` (D-046 — Reed's Search API
returns no application/job URL at all) — `test_deduplication.py` exercises
the capability gate against this real, already-verified capability value
rather than only an invented fixture, and a new
`tests/test_cross_source_dedup_pipeline.py` proves the `EXACT_DUPLICATE`
tier end-to-end through `pipeline.py::run_once` across two real source_ids
(`adzuna_api` keyword-query path, `greenhouse_public_feeds` watchlist path)
collapsing into one canonical `Job` with two `source_provenance` rows,
readable back via the new `JobRepository.list_provenance`.
**Alternatives**: Keeping one `CROSS_SOURCE_DUPLICATE` enum value for both
new tiers (rejected — loses the "no reasonable doubt" vs. "short of
certainty" distinction MILESTONE_2.md's terminology section explicitly asks
for, for no simplification benefit since `pipeline.py`'s two call sites
already need to check "is this a duplicate at all" as a set membership test
either way). Passing the whole `list[SourceRegistryEntry]` into
`match_against_recent` (rejected — `deduplication.py` would then depend on
`models.SourceRegistryEntry`'s full shape for one boolean field, and every
other capability-consuming function in this codebase already takes the
narrower `SourceCapabilities`/a plain mapping, not the entry itself).
**Why**: Matches this task's own instruction to reuse the milestone
document's stated design and terminology exactly rather than inventing a
parallel vocabulary, keeps `deduplication.py` free of any registry/`source_id`
branching per D-040's architectural rule (the module only ever *reads*
`source_id` as data, via the capabilities map, never branches its own logic
on a literal `source_id` string), and prefers a real, already-shipped
capability difference (Reed's `canonical_application_url: false`) for
regression coverage over an invented one wherever the codebase already has
one available.

### D-050: Sponsor registry + UK provider + visa enrichment (Milestone 2
Deliverable 5 step 10) — `_SCHEMA_VERSION` `2`→`3`; shared
`matching/visa_patterns.py`; exact normalized-name + country matching only

**Decision**: Implements MILESTONE_2.md's "Sponsorship/visa enrichment
design" as specified:
1. **`_SCHEMA_VERSION` bumps `2`→`3`, not a second use of `2`.** Task 9
   already claimed `2` for the canonical-URL index alone; Task 10 adds a
   materially different schema shape (a new `sponsor_registry_entries`
   table plus two new indexed `visa_assessments` columns), so it takes the
   next version number, exactly as "Persistence implications" requires. A
   database stamped `1` or `2` upgrades to `3` the same no-op, additive way
   (`CREATE TABLE`/`INDEX IF NOT EXISTS` plus an idempotent, `PRAGMA
   table_info`-checked `ALTER TABLE ... ADD COLUMN` for the two
   `visa_assessments` columns, since that table predates step 10 as
   `(job_id, data)` only and `CREATE TABLE IF NOT EXISTS` alone can't widen
   an existing table).
2. **Only `status` gets its own index (`idx_visa_assessments_status`);
   `employer_registry_match` does not.** The milestone document's own
   stated reason for adding these columns is so "a future `job-scout`
   query/report command can filter by visa status" — a single-column enum
   filter is the concrete use case, mirroring `match_results` (which
   likewise stores `notification_tier`/`final_score` as denormalized
   columns without indexing either). `employer_registry_match` is stored
   denormalized for the same future-report reason but has no stated filter
   use case yet, so it isn't indexed until one exists.
3. **`matching/visa_patterns.py` consolidates three regex-pattern lists**
   (`VISA_POSITIVE_PATTERNS`, `VISA_NEGATIVE_PATTERNS`/
   `NO_SPONSORSHIP_PATTERNS`) that `matching/scoring.py`'s
   `_visa_relocation_component` and `matching/hard_filters.py`'s
   no-sponsorship check previously each maintained as byte-identical private
   copies. Both call sites now import from the shared module; the compiled
   patterns and matching behaviour are unchanged.
4. **Evidence precedence is never blended**: `assess_visa` starts from
   `unknown`; an `employer_eligible` registry match sets both status and
   confidence to the registry match's own (capped, ~0.7) confidence;
   job-text evidence is applied last and can raise to `confirmed_yes` or
   override down to `confirmed_no` *regardless* of a registry match — a
   specific "we cannot sponsor this role" statement in a job posting is
   more specific than a general employer-eligibility signal and must win.
   `confidence` on the returned `VisaAssessment` is always the confidence of
   whichever evidence source actually set the final status, never an
   average of the two.
5. **UK Home Office "Register of licensed sponsors: workers" is the only
   implemented provider** (D-042 mandatory scope); the Netherlands IND
   Recognised Sponsors register is deferred and kept only as a disabled,
   present placeholder in `sponsor_registries.example.yaml` and as a
   `SponsorRegisterParseError` branch in
   `import_sponsor_register`/`load_sponsor_registries_config`'s config
   surface — proving the import framework is register-agnostic without
   building a second parser this milestone.
6. **Exact normalized-name + country matching only** — `find_sponsor_match`
   reuses `deduplication.normalize_company` (the same normaliser
   cross-source job dedup already uses) as the join key, with no
   fuzzy/Levenshtein/alias matching (R-9, explicitly deferred). A registry
   match means "may be eligible to sponsor in general," never "will
   sponsor this specific vacancy" (CLAUDE.md hard constraint 4); confidence
   is capped below "confirmed" (`SPONSOR_REGISTRY_MATCH_CONFIDENCE = 0.7`)
   to reflect real subsidiary/trading-name false-positive risk.
7. **`job-scout sponsors import <file> --country <CC> --register <name>`**
   parses a file the user has already downloaded and replaces (DELETE +
   INSERT, never appends) the stored rows for that `(country,
   register_name)` pair — the only way `sponsor_registry_entries` is ever
   written; nothing in this module opens a live connection to a government
   register (CLAUDE.md hard constraint 1).
8. **`sponsor_registries.yaml` is metadata only** (`SponsorRegisterConfig`:
   `country`, `register_name`, `enabled`) — which registers an installation
   has set up, never the register data itself, matching D-009's YAML-first/
   no-database-copy-of-config principle in the other direction (the
   register *data* lives only in SQLite, never YAML). `job-scout` never
   writes back to this file after an import.
**Alternatives**: A single shared `_SCHEMA_VERSION` bump combining Task 9 and
Task 10's schema objects (rejected — MILESTONE_2.md's "Persistence
implications" explicitly requires each task's schema change to own its own
increment, so a database stamped `2` unambiguously means exactly Task 9's
shape, never Task 9 plus an unknown subset of Task 10's). Indexing both new
`visa_assessments` columns (rejected for now — no stated filter use case for
`employer_registry_match` yet; trivial to add a second `CREATE INDEX IF NOT
EXISTS` in a later task if one emerges, same low-risk-to-add-later posture
this project already takes with schema growth). Fuzzy/alias sponsor-name
matching (rejected — explicitly out of scope per R-9 and the task's own
instruction; a subsidiary/trading-name miss is an accepted false negative
this milestone, not a bug).
**Why**: Same evidence-bar-first, additive-only, no-two-shapes-one-version
discipline as D-038/D-049 for the schema work; same "reuse the existing
normaliser rather than inventing a second one" discipline as D-040 for
`normalize_company`; same "evidence precedence, never blended" discipline
CLAUDE.md hard constraint 4/5 already requires for every visa/match
conclusion in this codebase.

### D-051: Evaluation tooling (Milestone 2 Deliverable 5 step 11) —
`--profile`/`--candidate-profile`/`--search-profiles` reused rather than
MILESTONE_2.md's earlier-drafted `--search-profile <id>` wording; a
`final_score is None` fixture ranks below every scored fixture, never
invents a score; `EvaluationReport` lives in `evaluation.py`, not
`models.py`
**Decision**: Three implementation questions MILESTONE_2.md's CLI/domain-model
prose left ambiguous (the document predates any M2 code, per its own status
line) are resolved as follows:
1. **CLI flags reuse `plan`/`run-once`'s existing convention exactly**:
   `job-scout evaluate --profile <search-profile-id> --dataset <path>
   [--candidate-profile <path>] [--search-profiles <path>]
   [--scoring-weights <path>] [--data-dir <path>] [--json]` — `--profile`
   is the search-profile *id* (looked up via `get_search_profile`, same as
   `plan`/`run-once`), `--candidate-profile`/`--search-profiles` are file
   *paths* with the same AppPaths-resolved-default behaviour every other
   command already uses. This is **not** MILESTONE_2.md's literal
   originally-drafted `--candidate-profile <ref> --search-profile <id>`
   wording (singular `--search-profile` naming a *profile id*), which would
   have introduced a second, inconsistent flag-naming convention alongside
   the one `plan`/`run-once` already established (`--search-profiles` as a
   *path*, `--profile` as the *id*) — `--search-profile <id>` and
   `--search-profiles <path>` differing only by a trailing "s" is exactly
   the kind of near-miss this project's own scoring-calibration history
   (D-029/D-032–034) warns against inventing a second time, this time in
   the CLI surface instead of the matching engine. `--dataset <path>` is
   the one genuinely new flag, since no existing command has an evaluation
   dataset to load.
2. **A fixture whose `MatchResult.final_score` is `None` (Stage 1
   hard-filter-rejected, or Stage 2 pre-filter-rejected) is never assigned
   an invented numeric score for ranking purposes.** `evaluation.py`'s
   `_effective_score`/`_rank_sort_key` treat `None` as sorting strictly
   below every real `[0, 100]` `final_score` (via a `-1.0` sentinel, never
   stored back onto the fixture result itself) for both precision@k ranking
   and the ranking-inversions metric. Every other metric
   (`recall_of_strong_matches`, `false_positive_rate`,
   `hard_filter_correctness`, `tier_distribution`) is computed from
   `notification_tier`/`hard_filter_result.passed` directly, which
   `build_match_result` already sets correctly (`REJECTED`/`STORE_ONLY`)
   for a `None`-score job without any special-casing needed in
   `evaluation.py`. This mirrors exactly how a real `run-once` pipeline
   already treats such a job — never surfaced above `store_only`/`rejected`
   regardless of any hypothetical score — so the evaluation tool's ranking
   behaviour matches production behaviour instead of inventing a separate
   rule for offline calibration.
3. **`EvaluationReport`/`EvaluationFixtureResult` are defined in
   `evaluation.py`, not `models.py`.** `MILESTONE_2.md`'s "Architecture
   changes" section names `EvaluationReport` as `run_evaluation`'s return
   type but never lists it under "Domain-model changes" (`models.py`) —
   only `EvaluationLabel`/`EvaluationJobFixture` are listed there, and
   Deliverable 5 step 11's own file list matches that split exactly
   (`models.py` for the label/fixture, `evaluation.py` new). This follows
   the existing precedent of `pipeline.py::RunOnceResult` (an
   orchestration-function's own return-aggregation type, defined beside
   `run_once`, not in `models.py`) and `config.py::SponsorRegisterConfig`
   (a Pydantic `BaseModel` that still lives in its owning module rather
   than `models.py`) — `models.py` holds cross-module domain models
   (`Job`, `MatchResult`, `EvaluationJobFixture`, ...), not every
   function's own output-aggregation shape. `EvaluationReport` is still a
   Pydantic `BaseModel` (not a plain `@dataclass` like `RunOnceResult`) so
   `job-scout evaluate --json` can call `.model_dump(mode="json")` directly
   without hand-building a payload dict, matching `plan`'s existing
   `--json` handling for its own Pydantic `SearchExecutionPlan` result.
**Alternatives**: Adopting MILESTONE_2.md's literal `--search-profile <id>`
flag name verbatim (rejected — reason 1 above: a second, confusable
profile-flag convention). Treating a `None` `final_score` as `0.0` for
ranking (rejected — `0.0` is a real, valid Stage 5 raw score a job that
reached Stage 5 and scored maximally negative on every component could
still receive, so `0.0` would not reliably sort below every scored fixture;
`-1.0` is guaranteed to, since Stage 5's own clamp bounds every real
`final_score` to `[0.0, 100.0]`). Defining `EvaluationReport` in
`models.py` for uniformity with every other M2 domain-model addition
(rejected — reason 3 above; would also make `models.py` responsible for a
type nothing outside `evaluation.py` ever needs to import, the same
"import just the job models" non-need `architecture.md` section 12 already
gives as its reason `models.py` doesn't need splitting the other way).
**Dataset composition**: two self-contained fixture groups under
`tests/fixtures/evaluation/` (`strategy_chief_of_staff/`,
`software_engineering/`), each its own generic `candidate_profile.yaml` +
`search_profiles.yaml` + `dataset.yaml` (15 labelled fixtures, 3 per
`EvaluationLabel`) — materially different vocabulary per CLAUDE.md hard
constraint 10, no real personal data per hard constraint 8. Each dataset
includes a deliberately-inserted ranking-inversion pair (one
`deceptive_false_positive` fixture engineered to clear the Stage 2
pre-filter and reach a real, if low, Stage 5 score, while several
`weak_match` fixtures in the same dataset do not clear Stage 2 at all) so
the ranking-inversions metric has a real, reproducible pair to detect
rather than only ever reporting zero — every fixture's `rationale` field
explains why its label applies, including why the inversion fixtures were
constructed that way, per D-043.
**Why**: Consistency with this project's own established CLI/module-layout
conventions outweighs matching a planning document's literal
before-any-code-existed wording once implementation reveals a naming
collision (the same category of correction D-045 already made for
`SelectedSource.search_queries`); "never invent a score for a job Stage
1/2 already rejected" keeps the evaluation tool's ranking behaviour
consistent with what a real `run-once` pipeline actually surfaces to a
user, rather than adding a second, offline-only ranking rule a future
maintainer would need to remember exists.

---

## Milestone 3 — Planning (scope defined, not implemented)

The ADRs below record decisions made while writing `MILESTONE_3.md` and the
Milestone 3 planning report that preceded it, before any Milestone 3 code
changed — same discipline the Milestone 2 planning ADRs (D-035 through
D-044) used. Milestone 2's baseline (624 passed / 1 skipped / 4 deselected,
`ruff check .`/`mypy --strict src` clean) was confirmed unchanged during
this planning pass. No application code, configuration, or test file was
modified — only `MILESTONE_3.md` (new), `ROADMAP.md`, `MILESTONE_2.md`,
`CLAUDE.md`, and `architecture.md` §12 changed, and only to formalise this
scope/these decisions and correct already-identified documentation
staleness (M2's status text and module-layout tree lagging behind its own
finalize commit).

### D-052: Stage 3 semantic similarity uses a local embedding backend, not
an API provider; kept behind a narrow, replaceable interface
**Decision**: Milestone 3's Stage 3 semantic-similarity component computes
embeddings using a local model (no outbound network call for the embedding
computation itself), never an API-based embedding provider (e.g.
Anthropic, OpenAI, Voyage). The embedding computation is isolated behind a
narrow, single-purpose interface (a small `Protocol` or a single function
boundary, e.g. `embed(texts: list[str]) -> list[list[float]]`) in its own
module, so a future milestone could substitute a different backend
(local or API) without touching `matching/scoring.py`'s consumption of the
result.
**Alternatives**: An API-based embedding provider (rejected — introduces a
new credential/network dependency and a new per-request cost/latency/
availability surface for a milestone whose own `ROADMAP.md` explicitly
separates "semantic similarity" from "LLM enrichment" specifically to avoid
conflating the two; would also front-run M4's optional-`[llm]`-extra
pattern before M4 has been scoped for embeddings specifically). Leaving
Stage 3 unimplemented until M4 (rejected — the user has explicitly approved
D3 as in-scope for M3).
**Why**: Matches the spirit of CLAUDE.md hard constraint 3 (no mandatory
external-model dependency baked into the core pipeline) even though that
constraint is written specifically about the Anthropic model id — a local
embedding backend keeps `job-scout run-once`/`evaluate` runnable offline,
with no new credential category, consistent with this project's "local,
single-user, synchronous" framing repeated at every milestone boundary. The
replaceable-interface requirement follows `SourceAdapter`/`JobRepository`'s
own precedent (`architecture.md` §12's "real seams for real, named future
work") — this is a real, named future need (a later milestone might swap in
a better/API backend once M4's LLM groundwork exists), not speculative
abstraction.
**Open follow-up (not resolved by this ADR)**: the specific local embedding
library/model and its packaging (a new required dependency vs. a new
optional extra) is an implementation-time decision, to be verified and
documented against the library's real behaviour (mirroring D-016's evidence
bar) when D3 is actually implemented — this ADR fixes the *category* of
backend (local, not API/LLM), not the specific library.

### D-053: Stage 3's semantic-similarity evidence must name the specific
matched signal, never a bare similarity score
**Decision**: `SemanticResult`'s evidence must identify, for a positive
semantic match, at least the specific configured phrase (a title, title
alias, role family, or skill from `CandidateProfile`/`SearchProfile`) and
the specific job-text field/span it was compared against — not only a raw
cosine-similarity float. The exact evidence shape (fields on
`SemanticResult`, and how it renders into `ScoreComponent.evidence`
strings) must be designed and written down (in `MILESTONE_3.md` or an
implementation-time follow-up ADR) before D3's code is written, not decided
ad hoc while coding.
**Alternatives**: Reporting only the similarity score with no
matched-phrase context (rejected outright — CLAUDE.md hard constraint 5,
"every match/visa conclusion carries evidence... don't collapse to a bare
number," applies to Stage 3 exactly as it already applies to every Stage 5
`ScoreComponent`). Deferring the evidence-design question to implementation
time with no upfront ADR (rejected — the user's own instruction for this
planning pass is explicit: evidence representation must be designed and
documented before implementation).
**Why**: This is the Stage-3-specific requirement most likely to be skipped
by accident — embeddings are the first scoring signal in this project that
doesn't naturally come with a human-readable "why" the way phrase matching
does, so the discipline has to be stated explicitly rather than assumed to
follow automatically from the existing pattern.

### D-054: The M3 source-discovery workflow is a structured, human-driven
tool; no autonomous search-engine querying or scraping
**Decision**: `job-scout discover` (D2) takes structured input a human
already has in hand (e.g. a list of candidate URLs/names/regions to
evaluate, or a small, explicit checklist the human fills in) and produces
well-formed candidate `SourceRegistryEntry` YAML for review — it does not
itself query a search engine, crawl the web, or scrape any external site to
*find* candidates. `access_mode: search_discovery` remains permanently
non-executable (unchanged from D-010); this ADR does not touch the
compliance gate.
**Alternatives**: An automated discovery mechanism that queries a search
engine or crawls for candidate job-source URLs (rejected for M3 — this
re-raises exactly the terms-of-service caution CLAUDE.md hard constraint 1
already applies to adapters, and "how do we find sources" deserves the same
explicit, source-by-source terms review this project already requires
before any adapter is built, not an automated shortcut around it).
**Why**: Matches the user's explicit instruction for this planning pass
("prefer a structured/manual workflow; do NOT implement autonomous
search-engine scraping or another mechanism that could bypass the project's
terms/compliance discipline") and keeps D2 additive to, not a replacement
for, the human judgement M2's own manual source-priority-matrix work
already relied on (`MILESTONE_2.md` Deliverable 4).

### D-055: Email-alert ingestion is removed from Milestone 3 and deferred
to its own, not-yet-scoped future milestone
**Decision**: Email-alert ingestion (Naukri, iimjobs, foundit, Indeed
alerts, Naukrigulf, GulfTalent, Bayt) — previously re-sequenced from M2 to
M3 by D-035/D-044 — is removed from Milestone 3's approved scope. It
remains a real, intended capability, deferred to its own future milestone
(tentatively numbered M4.5, or a standalone milestone — not yet finalized),
not folded into M3 and not assigned a firm number by this ADR.
**Alternatives**: Keeping it in M3 as D-035/D-044 originally re-sequenced
(rejected — the M3 planning report identified it as architecturally
uncoupled from M3's other three deliverables — no adapter, no
`SourceSearchParams`, no query-planner involvement; an ingestion path, not
a fetch path — and as introducing a genuinely new risk/credential category
(mailbox OAuth/IMAP, reading a user's real inbox) this project has not
handled before; bundling it into M3 anyway would repeat the exact
over-scoping pattern D-035 itself was written to correct).
**Why**: Directly matches the user's explicit instruction approving this
split. Supersedes D-035/D-044 specifically on this one point (both ADRs
otherwise stand — the general source-discovery workflow they also
re-sequenced to M3 is retained in M3 as D2). `ROADMAP.md`'s Milestone 3
section is updated accordingly.

### D-056: M3's Stage 5 re-tune uses the existing two-profession evaluation
dataset by default; a third profession group is added only if needed to
expose a real regression or validate generality, not as a general
dataset-growth effort
**Decision**: D4 (Stage 5 weight re-tuning) re-tunes weights against the
existing `tests/fixtures/evaluation/` dataset (`strategy_chief_of_staff/`,
`software_engineering/`, 15 fixtures each, D-043) by default. A third
profession-shaped fixture group may be added during D4's implementation
only if the existing two groups don't exercise a real regression the
re-tune needs to guard against, or don't demonstrate the new Stage 3 signal
generalising across professions — not as a standing effort to grow the
dataset toward statistical robustness (that remains R-11's explicitly
deferred, real-usage-driven concern, `MILESTONE_2.md`).
**Alternatives**: Committing to a larger, synthetic multi-profession
dataset expansion as part of D4 (rejected — the user's own instruction is
explicit: do not attempt to solve the project's long-term
real-usage/statistical-evaluation problem with a large synthetic dataset).
**Why**: Keeps D4 scoped to what it actually needs (re-tuning weights,
measured against the existing evaluate tool) rather than re-opening R-11
under cover of a weight change — the same "smallest change that satisfies
the actual requirement" discipline this project applied to D-015, D-033,
and others.
