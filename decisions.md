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
