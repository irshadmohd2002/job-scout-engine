# CLAUDE.md

Guidance for Claude Code sessions working in this repository.

## What this project is

Job Scout Engine: a deterministic, profession-agnostic job-monitoring and
matching engine (optional LLM enrichment only), locally installable so any
job seeker can configure it for their own profession, skills, and
experience. The originally-built example profile (international
strategy/transformation/chief-of-staff/program-management roles) is one
configuration of the engine, not the engine's scope — see `decisions.md`
D-017. Read `architecture.md` first — it is the design contract.
`MILESTONE_1.md`, `MILESTONE_1_1.md`, and `MILESTONE_2.md` describe what's
implemented (Milestone 1, Milestone 1.1, and Milestone 2 — including Task
12 end-to-end acceptance — are all implemented, accepted, and committed;
see "Milestone 2 status and implementation discipline" below);
`MILESTONE_3.md` defines the active milestone's scope (defined, not yet
implemented — see "Milestone 3 status and implementation discipline"
below); `decisions.md` explains why non-obvious choices were made;
`ROADMAP.md` shows what comes after and, just as importantly, what doesn't
happen yet (Milestone 3+ is not authorized to implement).

## Hard constraints — do not violate these

1. **No scraping without clear permission.** Never add a source adapter that
   bypasses authentication, CAPTCHAs, access controls, robots restrictions, or
   published rate limits. If a source's terms are unclear, its
   `SourceRegistryEntry.approval_status` must stay `manual_review` or
   `requires_authorisation`, never `approved`.
2. **The compliance gate is load-bearing.** Every adapter call must go through
   `ComplianceGate.authorize()` immediately before the HTTP call, not just at
   plan-generation time. See `architecture.md` §7.
3. **No hard-coded Anthropic model id.** Always read `ANTHROPIC_MODEL` from
   config/env. The Anthropic dependency is optional (`[llm]` extra); the
   pipeline must run correctly with it absent.
4. **Visa/sponsorship is never a boolean.** Use the `VisaAssessment` status
   enum (`confirmed_yes|likely|employer_eligible|unknown|confirmed_no|
   not_required`) with evidence, always. A sponsor-registry match means
   "may be eligible to sponsor," never "will sponsor this vacancy."
5. **Every match/visa conclusion carries evidence.** `ScoreComponent` and
   `VisaAssessment` always include the evidence strings that produced them —
   don't collapse to a bare number.
6. **Don't hard-code a single global source list.** Source selection goes
   through the registry + planner; a role/region should never silently assume
   one fixed set of sources.
7. **Stay within the current milestone.** Check `MILESTONE_1.md`'s,
   `MILESTONE_1_1.md`'s, `MILESTONE_2.md`'s, and `MILESTONE_3.md`'s "in
   scope" / "explicitly out of scope" lists before adding anything — this
   project has a documented tendency to over-scope (see the original
   requirements' own regional-source breadth), and the milestone boundary
   is intentional. Milestone 2 (including Deliverable 5's Task 12
   acceptance) is fully implemented; Milestone 3's scope is defined but
   **not** authorized to implement. Don't start Milestone 3 work, or any
   Milestone 4+ work (email-alert ingestion, LLM/generative extraction,
   notification delivery, scheduling), without the user explicitly asking.
   See "Milestone 2 status and implementation discipline" and "Milestone 3
   status and implementation discipline" below.
8. **Never put real personal data into a tracked file.** The packaged
   templates under `src/job_scout/resources/templates/` (and, historically,
   `config/*.example.yaml` — see `decisions.md` D-021) must stay generic
   placeholders — no real employer names, schools, or biographical details.
   The user's real profile lives only in their own data-directory config
   (created by `job-scout init`, gitignored, never inside this repo) and
   must never be committed. Don't create the user's real
   `config/candidate_profile.yaml` unless explicitly asked to.
9. **Keep Milestone 1 small.** No dependency-injection container, abstract
   factory, plugin-loading mechanism, event bus, migration framework,
   Postgres implementation, unnecessary async, or deep inheritance hierarchy —
   see `architecture.md` §12 ("What Milestone 1 deliberately does not add")
   and `decisions.md` D-012. `SourceAdapter` and `JobRepository` are the only
   interfaces; everything else is plain functions and flat typed models.
   Milestone 1.1 keeps the same discipline for its own additions — see
   `decisions.md` D-020 ("distribution foundations, not an installer") and
   the explicit exclusion list in `MILESTONE_1_1.md`.
10. **The engine stays profession-agnostic.** No profession-specific title,
    skill, role family, industry, or scoring keyword may be hard-coded in
    `src/job_scout/` — it belongs in `CandidateProfile`/`SearchProfile`/
    `SourceRegistryEntry` config instead, and no profession gets its own
    Python subclass. See `decisions.md` D-017.

## Working conventions

- Python 3.12, fully typed, Pydantic v2 models, Typer CLI, HTTPX for I/O.
- SQLite for local dev, always behind the `JobRepository` Protocol
  (`architecture.md` §4) — never call `sqlite3` directly outside
  `repository/sqlite_repo.py`.
- YAML config is the source of truth for candidate profile, search profiles,
  and the source registry through at least Milestone 1 (`decisions.md`
  D-009) — don't introduce a database copy of these without checking that
  decision first.
- Tests: `pytest`, HTTP mocked via `respx` for anything hitting `httpx`. Real
  network tests are marked `integration` and excluded by default
  (`pyproject.toml` `addopts`). Run the default suite with `pytest`; run the
  opt-in Adzuna integration test with `pytest -m integration` (requires real
  credentials in `.env`).
- Every new stage/model documented in `architecture.md` needs a corresponding
  update to that file — it's the contract, not just a snapshot.

## Configuration bootstrap

See README.md "Configuration bootstrap and privacy" for the full
explanation. Short version: run `job-scout init` (optionally
`--data-dir <path>`) to create starter config files in your own per-user
data directory (`src/job_scout/paths.py::AppPaths`, resolved via
`platformdirs`), then edit them and supply your own credentials via a real
`.env` or real environment variables — nothing is ever copied from or
written back into this repository. `config/*.example.yaml` no longer exists
(`decisions.md` D-021); the canonical templates live at
`src/job_scout/resources/templates/`.

## Where things live

See `architecture.md` §12 for the full module layout under `src/job_scout/`,
implemented as described there (Milestone 1 is complete; see
`MILESTONE_1.md` status and `decisions.md` D-013 through D-016 for the small
corrections found along the way; Milestone 1.1 is complete — see
`MILESTONE_1_1.md` and `decisions.md` D-017 through D-026 for its additions,
including `paths.py` (`AppPaths`), `resources/` (packaged templates), and
`bootstrap.py` (`job-scout init`)).

## Milestone 2 status and implementation discipline

Milestone 2 (`MILESTONE_2.md`, `decisions.md` D-035 through D-051) is
**implemented and accepted**: Deliverable 5's twelve-step sequence —
canonical normalization/`SourceCapabilities`, the `SearchProfile`-driven
query planner, the Reed/Greenhouse/Lever adapters, the company watchlist,
cross-source deduplication, sponsor-registry + visa enrichment, and the
`job-scout evaluate` calibration tool — was built and committed
task-by-task, in order. Task 12 (end-to-end acceptance) found no BLOCKER
defects; the small set of REQUIRED remediation items it surfaced (a missing
`sources` CLI command, a stale packaging-test assertion, a missing `init`
next-steps message, and doc-consistency corrections) has been closed out
and committed (`chore: finalize Milestone 2 acceptance`). Nothing above
changes because M2 is implemented: constraints 1–10 apply to M2's code
exactly as they applied to M1/1.1, and continue to apply to any future work
that touches M2's code.

- Milestone 2 implementation is done and accepted; do not re-implement or
  redesign an already-shipped M2 task. If you find what looks like a gap,
  verify it against `MILESTONE_2.md`'s acceptance criteria and Deliverable 5
  first, rather than assuming it's unbuilt.
- Do not begin Milestone 3+ implementation until the user explicitly asks.
  Being in this section of CLAUDE.md is not that ask.
- Do not start Milestone 3+ work (email-alert ingestion, automatic
  source-discovery, semantic/embedding matching, notification delivery,
  scheduling) under cover of an M2 fix. See `ROADMAP.md`.
- No source becomes `approved`/executable without the same compliance-gate
  discipline hard constraint 1 already requires — every M2 adapter (Reed,
  Greenhouse, Lever) ships `manual_review` by default; a user promotes an
  entry in their own registry only after confirming current terms
  themselves.
- No notification delivery and no scheduler in Milestone 2 — both stay
  Milestone 5/6 territory (`ROADMAP.md`).
- Deterministic matching stays the core of M2. `job-scout evaluate`
  measures relevance-score ranking quality against a labelled fixture
  dataset; it does not add LLM/embedding scoring, and it does not by itself
  change `notification_thresholds`.

## Milestone 3 status and implementation discipline

Milestone 3 (`MILESTONE_3.md`, `decisions.md` D-052 through D-056) is the
**active milestone** — its scope is defined (exactly four deliverables:
D1 additional regional adapters, D2 the `job-scout discover`
source-discovery workflow, D3 embedding-based Stage 3 semantic similarity,
D4 a Stage 5 weight re-tune) but **not yet implemented**. Email-alert
ingestion is explicitly **not** part of Milestone 3 (`decisions.md` D-055)
— it is deferred to its own, not-yet-scoped future milestone. Nothing
above changes because M3 is scoped: constraints 1–10 apply to M3 exactly
as they applied to M1/1.1/M2.

- Do not begin Milestone 3 implementation until the user explicitly asks.
  Being in this section of CLAUDE.md is not that ask.
- When implementation is authorized, treat D1/D2/D3 as independent (any
  order, or in parallel) and D4 as depending on D3 — see `MILESTONE_3.md`'s
  sequencing section — not as one large, unreviewable change.
- D3's embedding backend must be **local only** — no API embedding
  provider, no LLM/generative call, no vector database (`decisions.md`
  D-052) — and its evidence representation must be designed and documented
  before code is written, never a bare similarity number (`decisions.md`
  D-053).
- D2's discovery technique must stay a structured, human-driven workflow —
  no autonomous search-engine querying or scraping (`decisions.md` D-054);
  `access_mode: search_discovery` stays permanently non-executable
  (unchanged from D-010).
- Do not start email-alert ingestion, notification delivery, scheduling, or
  M4 LLM/generative extraction under cover of an M3 task. See `ROADMAP.md`.
- No source becomes `approved`/executable without the same compliance-gate
  discipline hard constraint 1 already requires — every new M3 adapter
  ships `manual_review` by default, only after its real contract/terms are
  verified.
- D4 changes scoring weights only — no change to `notification_thresholds`
  or the `ScoreComponent`/`ScoringWeights` schema, and no large synthetic
  evaluation-dataset expansion (`decisions.md` D-056).

## Before implementing beyond Milestone 3

Don't. Check `ROADMAP.md` and ask the user first.
