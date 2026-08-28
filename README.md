# Job Scout Engine

A deterministic, **profession-agnostic** job-monitoring and matching engine
— with optional LLM enrichment, never a dependency. Configure it for your
own profession, skills, and experience; the example configuration
(international strategy/transformation/chief-of-staff/program-management
roles) is one worked example, not the engine's scope.

It identifies newly published public job opportunities early, matches them
against your configurable candidate profile, assesses visa/relocation
signals with evidence (never a simple yes/no), deduplicates across sources,
and — in later milestones — notifies you.

Job Scout Engine is **locally installable**: `pip install` it, run
`job-scout init`, and it runs entirely on your own machine with your own
profile, credentials, and database — no access to this repository required
after installation.

## Status

**Milestone 1 and Milestone 1.1 implemented.** The local vertical slice
(`MILESTONE_1.md`) and the profession-agnostic/locally-distributable
foundations built on top of it (`MILESTONE_1_1.md`) are both built under
`src/job_scout/` with a full `pytest` suite (see "Testing" below). The
design (`architecture.md`), scope contracts, decision records
(`decisions.md`), longer-term plan (`ROADMAP.md`), and packaged config
templates (`src/job_scout/resources/templates/`) remain the authoritative
contract this code was built against. Read `architecture.md` before writing
or reviewing any code against this repo. Milestone 2+ work has not been
started — see `ROADMAP.md`.

## Documents

| File | Purpose |
|---|---|
| `architecture.md` | System design: domain models, adapter/repository contracts, planner, compliance gate, deduplication, scoring, paths/packaging (section 15) — the contract implementation must satisfy |
| `MILESTONE_1.md` | Scope of the first implementation milestone (local vertical slice), acceptance criteria, test plan |
| `MILESTONE_1_1.md` | Scope of the profession-agnostic/locally-distributable foundations milestone |
| `decisions.md` | Why non-obvious choices were made (ADR-style) |
| `ROADMAP.md` | What comes after Milestone 1.1, and what's explicitly deferred |
| `CLAUDE.md` | Working conventions and hard constraints for AI-assisted development in this repo |

## Design principles (see `architecture.md` for detail)

- Deterministic job-monitoring engine with *optional* LLM enrichment — not an
  autonomous browser agent.
- Profession-agnostic: no title, skill, role family, industry, or scoring
  keyword is hard-coded — every one comes from your own configuration.
- Only official APIs, public ATS feeds, government sources, RSS/sitemaps,
  permitted structured data, official career pages, or alert-email ingestion.
  No scraping where terms are unclear or prohibited; no bypassing auth,
  CAPTCHAs, or rate limits.
- Source selection is data-driven per search (country, region, role family,
  seniority, sector, industry, skills, visa needs) — never a single
  hard-coded source list.
- Visa/sponsorship signal is a multi-field assessment with evidence, never a
  boolean.
- Every match score and visa conclusion is explainable component-by-component.
- Local, single-user, config-driven: your profile, credentials, and database
  are yours alone, kept outside this repository (see "Installing and
  configuring" below).

## Installing and configuring

Job Scout Engine keeps two things strictly separate (architecture.md section
15.1; decisions.md D-018): the **installed application** (this repository's
code, and the generic, profession-agnostic templates packaged inside it —
`src/job_scout/resources/templates/`), and **your own data** (config,
database, logs, cache), which lives in a per-user data directory outside the
repository, resolved via [`platformdirs`](https://pypi.org/project/platformdirs/).
You never manually copy files out of this repository — `job-scout init`
does it for you, from packaged templates, not from any file checked into
git.

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -e ".[dev]"

job-scout init
```

`job-scout init` (optionally `job-scout init --data-dir <path>` to choose
where) creates your config/data/logs/cache directories, copies eight starter
config files (candidate profile, search profiles, source registry,
execution limits, scoring weights, source-scoring weights, company
watchlist, sponsor registries) to your data directory, and initialises a
local SQLite database. It's idempotent — safe to run again — and never
overwrites a file you've already edited, and never generates a populated
`.env` or any credential.

Then, in your own data directory (`job-scout init`'s output tells you
exactly where):
- **`candidate_profile.yaml`** — replace every placeholder with your real
  background, skills, qualifications/certifications/licences, and role
  targets. Every field is generic — there is no profession-specific schema
  to fight; see the comments in the generated file.
- **`search_profiles.yaml`** — define one or more named searches narrowing
  your candidate profile (countries, employment types, experience range,
  and any opt-in hard filters via `hard_filters` — see the generated file's
  comments).
- **`source_registry.yaml`** — adjust to taste; only entries with
  `approval_status: approved` and an auto-executable `access_mode` are ever
  collected automatically.
- **`execution_limits.yaml`** — adjust API quota/execution guardrails to
  your own risk tolerance (see "API quota and execution guardrails" below);
  the shipped defaults are deliberately conservative.
- **Credentials** — set real environment variables (`ADZUNA_APP_ID`,
  `ADZUNA_APP_KEY`, etc.) or create a `.env` file yourself in your data
  directory (or copy `.env.example` from this repo as a reference for which
  variables exist). `job-scout init` never creates or populates this file —
  every user supplies their own (decisions.md D-019).

None of this ever touches the repository — your real config, database, and
credentials live entirely in your own data directory and are never
committed (there is nothing under `config/` in this repo any more; the
tracked templates live at `src/job_scout/resources/templates/`, which are
generic and contain no real personal data — CLAUDE.md hard constraint 8).

## Quick start

```bash
job-scout init
# edit the files job-scout init just told you about, then:
job-scout plan --profile <your-profile-id>       # sanity-check source selection, no API quota spent
job-scout run-once --profile <your-profile-id> --dry-run
```

`run-once` performs real, permitted HTTP calls to your configured sources
(see "Dry-run semantics" below) — make sure your real credentials are set
first. If the `job-scout` console script isn't usable on your machine (see
"Windows note" below), use `python -m job_scout <command>` instead — it's
fully equivalent.

### Windows note

On this development machine, Windows Application Control policy blocks the
generated `job-scout.exe` console-script entry point (decisions.md D-023).
`python -m job_scout <command>` is the documented, fully-equivalent
fallback — it never depends on the blocked `.exe` — and works everywhere
`job-scout` does: `python -m job_scout init`, `python -m job_scout plan`,
`python -m job_scout run-once`, `python -m job_scout version`.

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

## Testing

```bash
pytest                 # default: no network, no real credentials needed
pytest -m integration  # opt-in: hits the real Adzuna API, needs .env credentials
pytest -m packaging    # opt-in: builds a wheel + throwaway venv, needs network (PyPI)
```

Every default-suite test that touches a data-directory path uses an
explicit `tmp_path`/`data_dir` override — none of them read or write your
real per-user data directory (see `tests/test_paths.py`).

## License

Proprietary — personal project, not for redistribution.
