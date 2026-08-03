# CLAUDE.md

Guidance for Claude Code sessions working in this repository.

## What this project is

Job Scout Engine: a deterministic job-monitoring and matching engine (optional
LLM enrichment only) for a management-consultant candidate seeking
international strategy/transformation/chief-of-staff/program-management roles.
Read `architecture.md` first — it is the design contract. `MILESTONE_1.md`
defines current scope; `decisions.md` explains why non-obvious choices were
made; `ROADMAP.md` shows what comes after and, just as importantly, what
doesn't happen yet.

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
7. **Stay within the current milestone.** Check `MILESTONE_1.md`'s "in scope"
   / "explicitly out of scope" lists before adding anything — this project
   has a documented tendency to over-scope (see the original requirements'
   own regional-source breadth), and the milestone boundary is intentional.
   Don't start Milestone 2+ work without the user explicitly asking.
8. **Never put real personal data into a tracked file.** Every
   `config/*.example.yaml` must stay a generic placeholder — no real
   employer names, schools, or biographical details. The user's real profile
   lives only in `config/candidate_profile.yaml` (and the other non-`.example`
   config files), which are gitignored and must never be committed. Don't
   create the user's real `config/candidate_profile.yaml` unless explicitly
   asked to.
9. **Keep Milestone 1 small.** No dependency-injection container, abstract
   factory, plugin-loading mechanism, event bus, migration framework,
   Postgres implementation, unnecessary async, or deep inheritance hierarchy —
   see `architecture.md` §12 ("What Milestone 1 deliberately does not add")
   and `decisions.md` D-012. `SourceAdapter` and `JobRepository` are the only
   interfaces; everything else is plain functions and flat typed models.

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

See README.md "Configuration bootstrap and privacy" for the exact `cp`
commands. Short version: real config is `config/candidate_profile.yaml`,
`config/search_profiles.yaml`, `config/source_registry.yaml`,
`config/execution_limits.yaml`, and `.env` — all copied from their
`.example` counterparts, all gitignored, none ever committed.

## Where things live

See `architecture.md` §12 for the full module layout under `src/job_scout/`,
implemented as described there (Milestone 1 is complete; see
`MILESTONE_1.md` status and `decisions.md` D-013 through D-016 for the small
corrections found along the way).

## Before implementing beyond Milestone 1

Don't. Check `ROADMAP.md` and ask the user first.
