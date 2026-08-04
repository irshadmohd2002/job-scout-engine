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
