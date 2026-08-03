# Job Scout Engine

A deterministic job-monitoring and matching engine — with optional LLM
enrichment, never a dependency — built for a management-consultant candidate
targeting international strategy, transformation, chief-of-staff, CEO-office,
business-planning, performance, commercial-strategy, and program-management
roles.

It identifies newly published public job opportunities early, matches them
against a configurable candidate profile, assesses visa/relocation signals
with evidence (never a simple yes/no), deduplicates across sources, and — in
later milestones — notifies the user.

## Status

**Architecture and planning phase.** No application code has been written
yet. This repository currently contains the design (`architecture.md`), the
Milestone 1 scope contract (`MILESTONE_1.md`), decision records
(`decisions.md`), the longer-term plan (`ROADMAP.md`), and example
configuration (`config/*.example.yaml`). Read `architecture.md` before writing
or reviewing any code against this repo.

## Documents

| File | Purpose |
|---|---|
| `architecture.md` | System design: domain models, adapter/repository contracts, planner, compliance gate, deduplication, scoring — the contract implementation must satisfy |
| `MILESTONE_1.md` | Exact scope of the first implementation milestone, acceptance criteria, test plan |
| `decisions.md` | Why non-obvious choices were made (ADR-style) |
| `ROADMAP.md` | What comes after Milestone 1, and what's explicitly deferred |
| `CLAUDE.md` | Working conventions and hard constraints for AI-assisted development in this repo |

## Design principles (see `architecture.md` for detail)

- Deterministic job-monitoring engine with *optional* LLM enrichment — not an
  autonomous browser agent.
- Only official APIs, public ATS feeds, government sources, RSS/sitemaps,
  permitted structured data, official career pages, or alert-email ingestion.
  No scraping where terms are unclear or prohibited; no bypassing auth,
  CAPTCHAs, or rate limits.
- Source selection is data-driven per search (country, region, role family,
  seniority, sector, skills, visa needs) — never a single hard-coded source
  list.
- Visa/sponsorship signal is a multi-field assessment with evidence, never a
  boolean.
- Every match score and visa conclusion is explainable component-by-component.

## Configuration bootstrap and privacy

Every tracked `config/*.example.yaml` file is a **generic placeholder** —
structurally complete but containing no real personal, employment, or
education details. Your real configuration is a set of local, gitignored
files that you create by copying the examples and then editing them:

```bash
# Git Bash
cp .env.example .env
cp config/candidate_profile.example.yaml config/candidate_profile.yaml
cp config/search_profiles.example.yaml config/search_profiles.yaml
cp config/source_registry.example.yaml config/source_registry.yaml
cp config/execution_limits.example.yaml config/execution_limits.yaml
```

Then edit the copies:
- `.env` — fill in real credentials (`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, etc.).
- `config/candidate_profile.yaml` — replace every placeholder with your real
  background, skills, and role targets.
- `config/search_profiles.yaml` / `config/source_registry.yaml` — adjust to
  taste.
- `config/execution_limits.yaml` — adjust API quota/execution guardrails to
  your own risk tolerance (see "API quota and execution guardrails" below);
  the shipped defaults are deliberately conservative.

**These five local files (`.env`, and the four non-`.example` files under
`config/`) must never be committed.** `.gitignore` enforces this:
`.env` is ignored outright, and `config/*.yaml` is ignored with an explicit
exception for `config/*.example.yaml` (so the placeholder examples stay
tracked while your real files never do). Run `git status` after editing them
to confirm they don't appear as trackable changes.

## Quick start (once Milestone 1 is implemented)

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -e ".[dev]"

job-scout run-once --profile strategy-global --dry-run
```

This command is not runnable yet — see "Status" above. It assumes you've
already completed the configuration bootstrap above.

## Dry-run semantics

`--dry-run` is **not a read-only or no-network mode.** It still performs real,
permitted job fetching, normalisation, deduplication, scoring, and writes
jobs/source-run/match records to the local database. The only thing it
disables is outbound notification dispatch (email, and any other channel
added later) and any other external write action. See `architecture.md` §11
("Dry-run semantics") for the authoritative definition — `MILESTONE_1.md`
and this file both restate it and must not diverge from it.

## API quota and execution guardrails

Because Milestone 1 talks to a real, quota-metered external API, the engine
enforces configurable limits — max countries per run, max pages per
source/country, results per page, request timeout, retry limit, and an
optional max-jobs-processed ceiling — so a broad search profile can never
generate an uncontrolled number of requests. See `architecture.md` §11a for
the full guardrail table and how unsupported source/country combinations are
excluded from the plan without ever making a failing API call.

## Testing (once implemented)

```bash
pytest                 # default: no network, no real credentials needed
pytest -m integration  # opt-in: hits the real Adzuna API, needs .env credentials
```

## License

Proprietary — personal project, not for redistribution.
