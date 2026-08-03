# Milestone 1.1 — Profession-Agnostic and Locally Distributable Foundations

Status: **implemented**. Builds directly on Milestone 1 (`MILESTONE_1.md`,
status: implemented) without altering its acceptance criteria — every
Milestone 1 behaviour and test keeps working exactly as before. This is not
Milestone 2 (`ROADMAP.md`); no broader source collection, no new adapters, no
notification channel, no scheduler.

## Goal

Two structural gaps in Milestone 1 blocked anyone but the original candidate
from using this engine:

1. **Profession lock-in.** `CandidateProfile.seniority_level` was a fixed
   consulting-ladder enum, Stage 5's education component hard-coded MBA/
   management keywords, and every example config described one
   management-consultant profile. None of that is a hard constraint of the
   *architecture* — the matching pipeline is already profile-driven — but
   nothing forced a second profession's config to actually exercise it.
2. **Repo lock-in.** Every config default (`config/candidate_profile.yaml`,
   `./data/job_scout.sqlite3`, `.env`) was resolved relative to the current
   working directory, and the only way to get a starter config was to `cp`
   files out of a checked-out git repository. A second person could not
   install this engine on their own machine without cloning the repo and
   hand-copying files into it.

Milestone 1.1 closes both gaps. The product remains exactly what
`CLAUDE.md` says it is: local, single-user, CLI-based, config-driven,
synchronous, with no LLM processing, no notifications, no browser scraping,
no scheduling, no GUI.

## In scope

- **Platform-independent application paths** (`src/job_scout/paths.py`):
  `AppPaths`, a small typed model naming every path the engine reads or
  writes, resolved via `platformdirs` with priority explicit-CLI-file >
  explicit `--data-dir` > `JOB_SCOUT_DATA_DIR` > platform user-data
  directory. The current working directory is never the implicit default for
  config, database, log, or cache locations.
- **Packaged templates** (`src/job_scout/resources/templates/`): one
  canonical, generic copy of each of the six config files, shipped as
  package data and loaded via `importlib.resources`. `config/*.example.yaml`
  no longer exists as a second, hand-maintained copy — see D-017.
- **`job-scout init` / `job-scout init --data-dir <path>`**: resolves the
  target data directory, creates `config/`, `data/`, `logs/`, `cache/`,
  copies the six templates to their real filenames (never overwriting an
  existing file), initialises the SQLite database file, and prints exactly
  what it created vs. what it left alone. Idempotent, non-interactive, never
  writes a populated `.env`.
- **`job-scout version`** (reads the installed package version via
  `importlib.metadata`, never hard-coded) and **`python -m job_scout`**
  (`src/job_scout/__main__.py`) as a documented fallback entry point — see
  D-019 for why this matters on this development machine specifically.
- **Generic `CandidateProfile` / `SearchProfile` fields**: profession-neutral
  optional fields (skills, qualifications, certifications, licences,
  languages, industries, sectors, seniority as free text, employment/
  relocation preferences, and — on `SearchProfile` — a `HardFilterToggles`
  block so a new hard filter only ever rejects a job when a profile
  explicitly turns it on). All additive; every valid Milestone 1 config
  still validates unchanged.
- **Profession-agnostic Stage 5 education scoring**: the fixed MBA/
  management keyword list is gone. The education component now matches
  against the *configured candidate's own* degrees, fields, qualifications,
  certifications, and licences — never a hard-coded universal list. No
  configured education data and no textual match both still resolve to the
  same documented neutral contribution Milestone 1 already used.
- **Industry/sector/seniority source-selection signal**: `SourceRegistryEntry`
  gained optional `industry_coverage` / `sector_coverage` /
  `seniority_coverage` (default empty = unrestricted, fully backward
  compatible with every existing registry entry). The planner computes a
  real overlap score when a candidate/search profile actually supplies
  industry, sector, or seniority data, and falls back to the existing
  neutral prior only when that data is absent — exactly the "neutral prior
  only when data is absent" rule `architecture.md` section 6 already states.
- **SQLite schema-version marker** (`PRAGMA user_version`): a new database is
  stamped `1`; an existing Milestone 1 database (schema-identical, but never
  stamped) is stamped `1` on first open under 1.1; a database from a future,
  higher schema version refuses to run rather than risk silent corruption.
  No migration framework — see the explicit exclusion list below.
- Tests for all of the above, including cross-profession deterministic
  matching fixtures (strategy/transformation, software engineering, nursing)
  and an opt-in packaging smoke test that builds a wheel, installs it into a
  clean venv, and runs `python -m job_scout` from outside the repository.

## Explicitly out of scope for Milestone 1.1

Everything Milestone 1 already excluded, plus (per this milestone's own
brief): Anthropic/LLM calls, semantic embeddings, email/WhatsApp/Gmail
ingestion, browser scraping, a scheduler, GitHub Actions, PostgreSQL, a
dashboard or GUI, an installer (PyInstaller/Docker/etc.), multi-user
accounts or authentication, cloud hosting, a `job-scout doctor` command,
auto-update, a plugin system, a dependency-injection framework, complex
migrations, and profession-specific Python subclasses. `AppPaths` and the
packaged templates are **distribution foundations**, not an installer — the
user still runs `pip install` (or `pip install -e .`) themselves; nothing in
this milestone produces a standalone executable or a package-manager
artifact beyond the wheel/sdist that already existed.

## Compatibility contract

- Every field Milestone 1.1 adds to `CandidateProfile` and `SearchProfile`
  is optional with a generic default. A `config/candidate_profile.yaml` that
  validated against Milestone 1's schema still validates unchanged.
- `CandidateProfile.seniority_level` (the fixed consulting-ladder enum) is
  relaxed from required to optional rather than removed — see D-018.
  Existing configs that set it keep working unchanged; new profession-
  agnostic configs use the new free-text `seniority` field instead.
- `config.load_execution_limits()` / `load_scoring_weights()` /
  `load_source_scoring_weights()` keep working with no argument: instead of
  falling back to a CWD-relative `config/*.example.yaml`, they now fall back
  to the packaged template (see D-017) — same numeric defaults, no behaviour
  change for anyone already relying on the fallback.
- The Milestone 1 acceptance command
  (`job-scout run-once --profile strategy-global --dry-run`) still works
  unchanged for a repository checkout with local `config/*.yaml` files
  passed explicitly or resolved the old way, as long as those files exist
  somewhere `AppPaths` or an explicit CLI flag points at.

## Test plan additions

See `decisions.md` D-017 through D-022 for the specific trade-offs below,
and the `tests/` modules added under this milestone (`test_paths.py`,
`test_resources.py`, `test_init.py`, `test_version.py`,
`test_cwd_independence.py`, `test_cross_profession.py`, and the opt-in
`test_packaging_smoke.py`) for coverage detail. All 158 Milestone 1 tests
continue to pass unmodified in behaviour (only their setup, where it relied
on package-relative example fallback, is now exercised through the packaged
template instead of a repo-relative file — no test assertions changed).

## Open items before implementation

None outstanding — this document, the ADRs in `decisions.md`, and the
consistency edits to `ROADMAP.md`/`architecture.md`/`CLAUDE.md` were written
first, per this milestone's own working instructions, before any code
changed.
