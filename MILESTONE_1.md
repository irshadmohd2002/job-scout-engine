# Milestone 1 — Local Vertical Slice

Status: **not started**. This document is the scope contract; `architecture.md`
is the design that satisfies it; `decisions.md` explains the trade-offs.

## Goal

One command, run locally, that goes from YAML config to ranked, deduplicated,
scored job matches from one real source (Adzuna), with full transparency into
source selection and scoring — with all external notification/write actions
disabled under `--dry-run` (see "Dry-run semantics" below).

## Configuration required before running

The acceptance command reads real, local, gitignored config — never the
tracked `.example.yaml` files directly. See README.md "Configuration
bootstrap and privacy" for the exact commands. In short:
`config/candidate_profile.yaml`, `config/search_profiles.yaml`,
`config/source_registry.yaml`, `config/execution_limits.yaml`, and `.env`
must all exist locally (copied from their `.example` counterparts and
edited) and must never be committed.

## Acceptance command

```bash
job-scout run-once --profile strategy-global --dry-run
```

Must, in order (cross-reference `architecture.md` §11):
1. Load and validate configuration.
2. Load the candidate profile.
3. Load the requested search profile.
4. Resolve countries and regions.
5. Generate a source execution plan.
6. Explain selected and excluded sources (with reasons).
7. Fetch jobs from the configured Adzuna adapter.
8. Normalise jobs.
9. Store source provenance.
10. Deduplicate jobs.
11. Apply hard filters.
12. Calculate a basic deterministic score.
13. Print ranked results and scoring evidence.
14. Persist jobs and source-run information locally.
15. Under `--dry-run`, disable notification dispatch and any other external
    write action — see "Dry-run semantics" below for the full definition.
16. Exit with a useful, specific error when configuration or credentials are
    invalid.

## Dry-run semantics

`--dry-run` is **not a read-only database mode**. The authoritative
definition lives in `architecture.md` §11 ("Dry-run semantics"); in short:

- Fetching (real, permitted HTTP calls), normalisation, deduplication,
  scoring, and local database writes (jobs, source runs, match results) all
  happen exactly as they would without the flag.
- The only thing `--dry-run` turns off is outbound notification dispatch
  (email, and any other channel added later) and any other external write
  action.
- This document, `README.md`, and `architecture.md` must not define
  `--dry-run` differently from each other — if you're changing this behaviour,
  update all three.

## API quota and execution guardrails

M1's acceptance checklist requires that a broad search profile (e.g. the
example `strategy-global` profile spanning nine countries) cannot generate an
uncontrolled number of API requests. The full guardrail set
(`max_countries_per_run`, `max_pages_per_source_country`, `results_per_page`,
`request_timeout_seconds`, `max_retries`, optional
`max_jobs_processed_per_run`) is defined in `architecture.md` §11a and is
in scope for M1 — these are config values read at startup, not deferred to a
later milestone. The planner's per-source-country support check (same
section) is also in scope: an unsupported `(source, country)` pair must
appear in the plan with an exclusion/non-executable reason and must never
result in an API call.

## In scope

- `CandidateProfile` / `SearchProfile` YAML schema + loader + validation.
- `SourceRegistryEntry` schema + example registry YAML covering every
  `AccessMode`/`ApprovalStatus` combination (not every real source — see
  `config/source_registry.example.yaml`).
- Country → region resolution (static lookup).
- Deterministic source planning (`SearchExecutionPlan`) with transparent
  scoring and inclusion/exclusion reasons.
- Compliance gate enforcing `approved` + auto-executable `access_mode` before
  any adapter call.
- `AdzunaAdapter` (only adapter in M1).
- Normalised `Job` model + `SourceProvenance`.
- `SqliteJobRepository` covering jobs, provenance, source runs, match results,
  fingerprints.
- URL- and content-based deduplication (fingerprint tiers 1–3, see
  `architecture.md` §8).
- Stage 1 hard filters + Stage 2 pre-filter + Stage 5 deterministic scoring
  (using proxies where Stage 3/4 would normally contribute — see D-007).
- API quota and execution guardrails (`max_countries_per_run`,
  `max_pages_per_source_country`, `results_per_page`,
  `request_timeout_seconds`, `max_retries`, optional
  `max_jobs_processed_per_run`) and the planner's per-source-country support
  check — see `architecture.md` §11a.
- Console output: source plan explanation, ranked matches with score
  component evidence.
- `SourceRun` tracking.
- `run-once` CLI command with `--dry-run`.
- `plan` CLI command (`job-scout plan --profile X`): prints the
  `SearchExecutionPlan` without calling any adapter — confirmed in scope,
  see `decisions.md` D-011.
- Unit tests for every stage above; mocked HTTP for Adzuna; one opt-in
  integration test (pytest marker `integration`, skipped by default per
  `pyproject.toml`'s `addopts`).

## Explicitly out of scope for Milestone 1

- Anthropic/LLM calls (Stage 4) — interfaces reserved, not implemented.
- Semantic similarity (Stage 3) — interface reserved, not implemented.
- WhatsApp/Telegram/push notification channels.
- Any real notification dispatch (email included) — no notification channel
  is built in M1, so `--dry-run`'s notification-suppression behaviour has
  nothing to suppress yet. The flag's full semantics (fetch/normalise/dedupe/
  score/persist all still happen) are defined now so a later milestone can
  add a real channel without redefining what dry-run means.
- Browser scraping of any kind.
- Reverse-engineering Workday or any other ATS's non-public endpoints.
- Dashboard / web UI.
- Continuous scheduler / always-on deployment.
- GitHub Actions deployment (comes after this local slice works, per the
  project's own deployment direction).
- Every regional portal in the requirements' regional-examples section — only
  Adzuna is wired; the rest exist as registry *examples* to prove the schema.
- Email-alert ingestion implementation (the registry can *describe* an
  `email_alert` source; nothing parses an inbox in M1).
- Sponsor-registry enrichment.
- Company watchlist implementation (schema may be sketched, not built).
- Production hosting.

## Test plan

**Unit tests** (mocked HTTP throughout, no network):
- Config loading: valid YAML loads correctly; missing required field raises a
  specific, named error; missing `.env` credential raises a specific, named
  error (not a generic exception).
- Country/region resolution: known codes resolve; unknown code fails loudly
  rather than silently defaulting.
- Planner: given a fixed candidate/search profile + registry fixture,
  produces expected `selected_sources`/`excluded_sources` with correct
  `executable` flags; diversity rule excludes a redundant fixture source;
  scoring weights sum to a stable, documented total.
- Guardrails: a search profile with more countries than
  `max_countries_per_run` is truncated to the configured limit, with the
  truncation recorded on the plan; a `(source, country)` pair the registry
  doesn't cover produces an exclusion/non-executable reason on the plan and
  the fixture adapter asserts zero calls for that pair (not merely a caught
  failure); a fixture with a very broad country list still results in a
  bounded, predictable number of mocked HTTP calls (pages × countries ×
  sources, all capped) — no network calls escape the ceiling.
- Compliance gate: full truth table over `ApprovalStatus` × `AccessMode`
  (§7 of `architecture.md`) — every combination asserted, including that
  `search_discovery` is never executable and that a non-executable
  `access_mode` on an `approved` entry raises rather than silently passing.
- Adzuna adapter: `respx`-mocked HTTP responses → correct `RawJobRecord`
  list; auth failure → `SourceAuthError`; rate-limit response →
  `SourceRateLimitError`; adapter never fires without `is_configured()`
  passing first.
- Normalisation: raw Adzuna payload fixture → expected `Job` fields,
  including `description_text` HTML-stripping.
- Deduplication: identical URL with different tracking params → same
  fingerprint; same job from two source fixtures → provenance merge, not two
  rows; genuinely different jobs at the same company → not merged; repost
  fixture (same company/title/location, changed description, dated later)
  → linked via `previous_job_id`, not silently duplicated or silently merged.
- Hard filters: one fixture per rule (excluded country, citizenship
  restriction, explicit no-sponsorship phrase, missing mandatory
  qualification, etc.) — each produces the expected `RejectionReason` with
  evidence text; a job with a missing *secondary* skill must not be rejected
  anywhere in the pipeline.
- Pre-filter: title-alias match scores higher than no match; role-family
  keyword overlap contributes; below-threshold jobs are persisted but not
  scored further.
- Scoring: each `ScoreComponent` computed independently and asserted against
  a fixture with known expected values; total respects configured weights;
  notification tier boundaries (85 / 70) hit exactly at the configured
  thresholds.
- Repository: SQLite round-trip for jobs/provenance/source runs/match
  results; `find_by_fingerprint` returns `None` for a novel fingerprint and
  the correct row for a known one.
- CLI: `run-once --dry-run` end-to-end against a fully mocked adapter +
  temp SQLite file — asserts steps 1–16 all occurred (via captured output
  and repository state), explicitly including that jobs/source-run/match
  rows *were* written to the database (proving dry-run is not read-only) and
  that no notification path was invoked; invalid config → non-zero exit with
  the expected message substring.

**Integration test (opt-in, marked `integration`, real network + real
credentials from `.env`)**:
- One test that runs `AdzunaAdapter.fetch()` against the live Adzuna API with
  a narrow, cheap query and asserts it returns at least one well-formed
  `RawJobRecord`. Skipped unless `ADZUNA_APP_ID`/`ADZUNA_APP_KEY` are present
  and the `integration` marker is explicitly selected
  (`pytest -m integration`).

## Acceptance checklist

- [ ] `job-scout run-once --profile strategy-global --dry-run` runs end to end
      against real Adzuna credentials in `.env` and exits 0.
- [ ] Console output shows the source plan (selected + excluded, with
      reasons) before any results.
- [ ] Console output shows ranked matches with visible score-component
      evidence.
- [ ] A second run against the same query does not duplicate previously
      seen jobs (dedup verified via repository row count).
- [ ] Removing `ADZUNA_APP_KEY` from `.env` produces a clear, named error and
      non-zero exit — not a stack trace.
- [ ] Running with the example `strategy-global` profile (9 countries) makes
      a bounded, predictable number of Adzuna requests — capped by
      `max_countries_per_run` × `max_pages_per_source_country` — never one
      request per country with unlimited pagination.
- [ ] A country in the search profile that Adzuna's registry entry doesn't
      cover appears in the plan as excluded/non-executable with a reason,
      and produces zero HTTP requests for that country.
- [ ] `pytest` (default, no `integration` marker) passes with no network
      access required.
- [ ] `pytest -m integration` passes when real credentials are present.

## Open items before implementation

None outstanding. `plan` command scope confirmed (D-011). Implementation is
paused at the user's request pending doc review — see `README.md` status.
