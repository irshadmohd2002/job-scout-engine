# Milestone 3 — Regional Source Expansion, Source Discovery, and Semantic Matching

Status: **Scope defined and approved; not implemented.** This document was
written as a scope contract before any Milestone 3 code changed, per this
project's ground rules ("Before implementing beyond Milestone 2: Don't.
Check `ROADMAP.md` and ask the user first."). It formalises the Milestone 3
planning report and the scope decisions the user approved on top of it.
Milestone 1, Milestone 1.1, and Milestone 2 are all implemented and
accepted — see `MILESTONE_1.md`/`MILESTONE_1_1.md`/`MILESTONE_2.md` and
`decisions.md` D-001 through D-051. Baseline at the time this document was
written: `pytest` 624 passed / 1 skipped / 4 deselected, `ruff check .`
clean, `mypy --strict src` clean.

## Goal

Extend Job Scout's discovery breadth with the same evidence-verified,
compliance-gated adapter pattern Milestone 2 established (D1), give a
future session a repeatable, human-reviewed way to evaluate new candidate
sources instead of a one-off manual matrix (D2), and close the single
biggest gap keyword/phrase matching leaves open — role equivalents that
share no configured vocabulary at all (D3) — then use Milestone 2's own
`job-scout evaluate` tool to measure whether that new signal actually
improves ranking quality, and re-tune Stage 5's weights accordingly (D4).

Email-alert ingestion and a general LLM/generative extraction stage are
**not** part of this milestone — see "Explicitly out of scope" below.

## User outcome

Running `job-scout plan`/`run-once` after Milestone 3 can pull from three
additional regional public sources (once each one's terms are verified and
a user promotes it out of `manual_review`), exactly as Reed/Greenhouse/
Lever work today. A new `job-scout discover` command turns a human's own
research into well-formed candidate `SourceRegistryEntry` YAML for review —
it never adds anything to a real registry automatically. Ranked results can
surface a genuine role equivalent that shares no configured title/skill
vocabulary with the search profile at all, with visible evidence naming
which configured signal it matched and against what job-text field — never
a bare similarity number. `job-scout evaluate`'s existing metrics (`ruff`,
`mypy`, and the report itself) demonstrate, against a documented pre-M3
baseline, whether the re-tuned Stage 5 weights measurably improved ranking
quality. None of this requires editing Python; new sources are configured
exactly like Reed/Greenhouse/Lever, and semantic matching/re-tuned weights
apply automatically to every existing profile.

## In scope

### D1 — Additional regional public-source adapters
UK Find a Job, EURES, Canada Job Bank.

- **Purpose**: Extend discovery breadth using the exact, already-proven M2
  pattern (verify the real contract/terms → build the adapter → normalizer
  → registry entry), the lowest-risk deliverable in this milestone.
- **Files**: `sources/uk_find_a_job.py`, `sources/eures.py`,
  `sources/canada_job_bank.py` (fewer, if a terms review disqualifies one),
  `pipeline.py` (`_NORMALIZERS`/`_default_adapter_factory` additions),
  `source_intelligence/planner.py` (`_effective_config_status`, only if a
  source needs credentials), registry template updates.
- **Dependencies**: None within M3.
- **Tests**: Per-adapter unit tests (`respx`-mocked), normalization
  fixture tests, pipeline integration tests, an opt-in `integration` test
  per adapter that needs one — mirroring
  `test_reed_*`/`test_greenhouse_*`/`test_lever_*`.
- **Acceptance**: Each adapter ships `manual_review` by default;
  `job-scout plan`/`run-once` show it as selected-but-not-yet-approved
  until a user promotes it; full suite green, `ruff`/`mypy --strict` clean.
- **Non-goals**: No change to `ComplianceGate`'s rule table, no new
  `AccessMode`/`ApprovalStatus` values, no scoring change, no
  auto-promotion to `approved`. **Only implement a source after its real
  contract/terms are verified** — the same evidence bar as
  D-016/D-027/D-028/D-031/D-046/D-047/D-048; a source whose contract can't
  be confirmed stays undone, not guessed at.

### D2 — General, human-reviewed source-discovery workflow
`job-scout discover`.

- **Purpose**: A repeatable *tool* that turns a human's own research into
  well-formed candidate `SourceRegistryEntry` rows for review, replacing
  M2's one-off manual source-priority-matrix with something reusable.
- **Files**: New module (e.g. `source_intelligence/discovery.py`), a new
  CLI command (`job-scout discover`). No change to `ComplianceGate` or to
  `access_mode: search_discovery`'s permanent non-executability (D-010).
- **Dependencies**: None on D1/D3; may use D1's adapters as worked examples
  of a well-formed entry.
- **Design constraint (`decisions.md` D-054)**: structured/manual input
  only — a human supplies the candidate (a URL, a name, a region, or a
  filled-in checklist); the tool never queries a search engine, crawls the
  web, or scrapes any site on its own to *find* candidates.
- **Tests**: Unit tests over whatever structured input the tool accepts; a
  test asserting no discovered entry is ever `approved` or executable, and
  that nothing is written to a real registry file automatically.
- **Acceptance**: Running the tool against a human-supplied input produces
  YAML a person can review and paste into their own `source_registry.yaml`
  after independently confirming terms; it writes nothing back
  automatically and never touches `ComplianceGate`.
- **Non-goals**: No automatic promotion to `approved`, no automatic
  persistence into any registry file, no autonomous search-engine querying
  or scraping (`decisions.md` D-054).

### D3 — Embedding-based Stage 3 semantic similarity
- **Purpose**: Catch role equivalents that keyword/phrase matching misses
  ("Head of Special Projects" ↔ strategic initiatives, "Office of the
  Managing Director" ↔ chief of staff, etc.).
- **Files**: A new module (e.g. `matching/semantic.py`) implementing a
  narrow, replaceable embedding interface (`decisions.md` D-052);
  `MatchResult.semantic_result` (already reserved on the model — the
  existing integration point, no schema change needed to add the field
  itself); `matching/scoring.py` (a new `ScoreComponent` reading semantic
  similarity, or a blend into an existing one — the exact choice is an
  implementation-time design decision, not fixed by this document).
- **Dependencies**: None on D1/D2. Implementation may not start until the
  evidence-representation design required below is written down.
- **Backend constraint (`decisions.md` D-052)**: a **local** embedding
  backend only. No API-based embedding provider (Anthropic, OpenAI,
  Voyage, or otherwise). No LLM/generative call of any kind. No vector
  database — a small, local similarity computation over already-fetched
  jobs needs no persistent vector infrastructure at this data volume. The
  embedding computation must sit behind a narrow, single-purpose interface
  so a future milestone could substitute a different backend without
  touching `matching/scoring.py`'s consumption of the result. The specific
  local library/model is an implementation-time decision, verified against
  its real, documented behaviour before being relied on (the same evidence
  bar D-016 established for Adzuna).
- **Evidence constraint (`decisions.md` D-053)**: a bare cosine-similarity
  number is not sufficient. `SemanticResult`'s evidence must name, for a
  positive match, the specific configured phrase (a title, title alias,
  role family, or skill) and the specific job-text field/span it was
  compared against. **The exact evidence shape must be designed and
  documented — in a dedicated implementation-time follow-up ADR — before
  D3's code is written.**
- **Tests**: Deterministic unit tests against fixed/stubbed embeddings (no
  real model call in the default suite); evidence-carrying assertions
  (hard constraint 5); a test confirming `final_score` stays in `[0, 100]`
  and is still described as a relevance score, never a probability.
- **Acceptance**: `job-scout evaluate`'s ranking-inversion metric shows
  measurable improvement on the existing fixture datasets' "deceptive
  false positive vs. adjacent match" pairs without regressing
  precision@5/@10/@20, measured against the documented pre-M3 baseline
  (see "Definition of done").
- **Non-goals**: No LLM generative call (that stays M4's Stage 4, a
  categorically different mechanism); no change to
  `notification_thresholds`; no vector database.

### D4 — Stage 5 weight re-tuning
- **Purpose**: Recalibrate Stage 5 weights now that D3 gives
  `responsibilities`/`sector_relevance` a stronger real signal, measured
  empirically via `job-scout evaluate` rather than by hand (as M1.1/M2's
  D-029/D-032/D-033/D-034 audits were forced to do).
- **Files**: `config/scoring_weights.yaml` template (weight values only,
  never code — the same discipline D-013 already established); possibly
  `tests/fixtures/evaluation/` (see the dataset-scope constraint below).
- **Dependencies**: Hard dependency on D3 (there is no new signal to
  re-tune around otherwise) and on `job-scout evaluate` (already exists,
  M2).
- **Dataset-scope constraint (`decisions.md` D-056)**: re-tune against the
  existing two-profession dataset (`strategy_chief_of_staff/`,
  `software_engineering/`, 15 fixtures each) by default. A third
  profession-shaped fixture group may be added **only if** the existing two
  don't exercise a real regression the re-tune needs to guard against, or
  don't demonstrate D3's signal generalising across professions — not as a
  general effort to grow the dataset toward statistical robustness (that
  stays R-11's deferred, real-usage-driven concern).
- **Tests**: `test_evaluation.py`/`test_cli_evaluate.py` extensions; a
  regression test asserting the new weights don't invert any of the M2
  audit's already-fixed orderings (D-032/D-033/D-034's own regression tests
  must keep passing).
- **Acceptance**: `job-scout evaluate --json` shows improved
  ranking-inversion count and/or false-positive rate on the fixture set
  versus the documented pre-M3 baseline; weights still sum to 1.0; no
  `ScoreComponent`/`ScoringWeights` schema change unless a genuine
  architectural need is demonstrated and separately approved by the user.
- **Non-goals**: No change to `notification_thresholds` (85/70) —
  `ROADMAP.md` reserves that recalibration for M5, using real-usage data,
  not this milestone's synthetic fixtures.

## Explicitly out of scope

Everything Milestone 1/1.1/2 already excluded, plus, for this milestone
specifically:

- **Email-alert ingestion** (Naukri, iimjobs, foundit, Indeed alerts,
  Naukrigulf, GulfTalent, Bayt) and any mailbox OAuth/IMAP credential
  handling — removed from M3 (`decisions.md` D-055); deferred to its own,
  not-yet-scoped future milestone (tentatively M4.5, or standalone).
- **Notification delivery** of any kind — stays Milestone 5 territory.
- **Scheduling** — a continuous scheduler, GitHub Actions/cron runs, any
  always-on deployment — stays Milestone 6 territory.
- **M4 LLM/generative extraction** — Stage 4, or any generative model call
  anywhere in the matching pipeline.
- **A vector database** of any kind.
- **An API-based embedding provider** for D3 — local backend only
  (`decisions.md` D-052).
- **Fuzzy/alias sponsor-name matching** — still `MILESTONE_2.md` risk R-9,
  unchanged, not reopened by this milestone.
- **The Netherlands sponsor registry** — still optional/stretch per
  D-042, not assigned to this milestone.
- **A dashboard or web UI** — CLI only.
- **Any change to `notification_thresholds`** (85/70).
- **Automatic source approval** — every new/discovered source stays
  `manual_review` until a human promotes it in their own registry.
- **A broad synthetic evaluation-dataset expansion** — see D4's
  dataset-scope constraint (`decisions.md` D-056).
- Everything else already listed under `ROADMAP.md`'s "Explicitly not
  planned until asked for."

## Sequencing

D1, D2, and D3 are independent of each other and may be implemented in any
order, or in parallel, once each one's own contract/design questions are
resolved. D4 depends on D3.

```
D1 (regional adapters)   ─┐
D2 (discover workflow)   ─┼── independent, any order/parallel
D3 (semantic Stage 3)    ─┘
        │
        ▼
D4 (Stage 5 re-tune, using job-scout evaluate)
```

A detailed, task-by-task implementation sequence (mirroring
`MILESTONE_2.md` Deliverable 5's twelve-step breakdown) is intentionally
**not** written here — that level of task decomposition is a follow-up
planning pass once implementation is authorized, the same way Milestone
2's own Deliverable 5 sequence was added during a later refinement pass on
top of its initial scope contract.

## Architectural decisions

See `decisions.md` D-052 through D-056 for the full reasoning. Summary:

- **D-052**: D3 uses a local embedding backend only, behind a narrow,
  replaceable interface — no API embedding provider, no LLM call, no
  vector database.
- **D-053**: D3's evidence must name the specific matched configured
  phrase and job-text field, never a bare similarity number; the exact
  representation is designed and documented before D3's code is written.
- **D-054**: D2's discovery technique is structured and human-driven —
  no autonomous search-engine querying or scraping.
- **D-055**: Email-alert ingestion is removed from M3, deferred to its own
  future milestone (supersedes D-035/D-044 on this one point only).
- **D-056**: D4 re-tunes against the existing two-profession evaluation
  dataset by default; a third group is added only if genuinely needed, not
  as general dataset growth.

## Acceptance criteria

- [ ] Each D1 adapter (UK Find a Job, EURES, Canada Job Bank — or fewer, if
      a terms review disqualifies one) is built only after its real
      contract/terms are verified, ships `manual_review` by default, and
      passes a `respx`-mocked test suite mirroring
      `test_reed_adapter.py`'s rigor.
- [ ] `job-scout discover` produces well-formed `SourceRegistryEntry` YAML
      from human-supplied input; a discovered entry is never `approved`,
      never executable, and never written to a real registry file
      automatically.
- [ ] D3's embedding backend is local only, verified against no API call
      being made for the embedding computation itself; the interface
      boundary is narrow enough that swapping the backend would not
      require changing `matching/scoring.py`'s consumption of the result.
- [ ] D3's evidence representation is designed and documented (a dedicated
      implementation-time ADR) before any D3 code exists, and every
      positive semantic match's evidence names a specific configured
      phrase and job-text field, never a bare similarity number.
- [ ] `final_score` remains in `[0, 100]` and is documented as a relevance
      score, never a probability, after D3/D4.
- [ ] `job-scout evaluate --json`, run against a documented pre-M3
      baseline and the post-D3/D4 code, shows the measured effect
      (ranking inversions, false-positive rate, precision@5/@10/@20) —
      whatever that effect turns out to be, reported honestly.
- [ ] `notification_thresholds` (85/70) are byte-for-byte unchanged.
- [ ] Full `pytest` suite (existing 624 + all new tests) passes; `ruff
      check .` clean; `mypy --strict src` clean; `git diff --check` clean —
      same bar as the M1/1.1/M2 baseline, no regression to Stage 1/2
      behaviour.
- [ ] No hard-coded profession-specific vocabulary introduced into
      `src/job_scout/` (CLAUDE.md hard constraint 10).

## Definition of done

- `MILESTONE_3.md` (this document) and the `decisions.md` D-052 through
  D-056 updates are merged before any Milestone 3 code is written, per this
  project's ground rules.
- Every acceptance-criteria checkbox above is checked.
- `decisions.md` carries a new ADR for every non-obvious M3 design choice
  actually implemented (the specific local embedding library, D3's final
  evidence shape, any per-adapter contract findings for D1) — same
  discipline as D-016/D-046/D-047/D-048.
- A documented pre-M3 `job-scout evaluate` baseline (dataset, weights,
  metrics) is captured before D3/D4 implementation starts, so D4's
  acceptance criterion has something concrete to compare against.
- No Milestone 4+ item (email-alert ingestion, LLM/generative extraction,
  notification delivery, scheduling) was started under cover of this
  milestone.
- The full baseline (`pytest`, `ruff check .`, `mypy --strict src`) is
  green at the same bar this document's own preparation pass captured
  (624 passed / 1 skipped / 4 deselected).

## Risks

- **R-13 (embedding backend evidence gap)**: a local embedding library's
  actual behaviour (dimensionality, language coverage, performance on
  short job-title-length text vs. long descriptions) is unverified until
  implementation time. Mitigated by D-052's "verify before relying on it"
  requirement, the same evidence bar D-016 established for Adzuna.
- **R-14 (semantic evidence readability)**: a matched-phrase-plus-field
  evidence string may still be harder for a human to sanity-check than an
  exact keyword match. Mitigated by requiring the evidence design to be
  written down and reviewed (D-053) before code exists, not discovered
  after the fact.
- **R-15 (D3/D4 coupling risk)**: if D3's signal turns out weak or noisy,
  D4 has nothing meaningful to re-tune around. Mitigated by sequencing
  (D4 strictly after D3) and by D4's acceptance criterion requiring an
  honestly-reported `job-scout evaluate` comparison rather than an assumed
  improvement.
- Carried forward, unchanged, not this milestone's concern: R-9 (sponsor
  false matches), R-11 (small evaluation samples, still real-usage-driven
  and deferred), R-12 (query-construction changes are scoring-adjacent) —
  see `MILESTONE_2.md`.

## Explicitly deferred (not this milestone, not pulled in)

- **Email-alert ingestion** — its own future milestone (`decisions.md`
  D-055).
- **The Netherlands sponsor registry** — still optional/stretch (D-042),
  available to pick up independently whenever, not bound to M3.
- **M2 technical debt** not touched by this milestone: the
  `_effective_config_status`/`_default_adapter_factory` per-source
  `if`/`elif` chains (D-030/D-046 — revisit only if adapter count makes
  the chain genuinely unwieldy, which D1's three adapters may approach but
  do not, by themselves, cross); Greenhouse's `estimated_request_count`
  under-reporting for watchlist-scoped sources (D-047); Lever's
  no-pagination limitation (§19); R-11's real-usage-derived evaluation
  robustness question.
- **M4's LLM/generative extraction**, **M5's notification delivery and
  threshold recalibration**, and **M6's always-on deployment** — all
  unchanged, all later.
