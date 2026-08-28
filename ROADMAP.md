# Roadmap

Milestones are sequential and each depends on the previous one being real and
working — not aspirational. Do not start a milestone's implementation without
explicit instruction, per project ground rules.

## Milestone 1 — Local vertical slice (implemented)
See `MILESTONE_1.md`. One adapter (Adzuna), deterministic matching only,
SQLite, console output, `run-once --dry-run`.

## Milestone 1.1 — Profession-agnostic and locally distributable foundations (implemented)
See `MILESTONE_1_1.md`. Removes the remaining profession-specific hard-coding
(fixed education-scoring keywords, a consulting-ladder-only seniority enum)
and the remaining repo/CWD lock-in (config, database, and template defaults
now resolve through `AppPaths`/`platformdirs` and `job-scout init`, not a
path relative to the current working directory). Still local, single-user,
CLI-based, config-driven, synchronous — no new source, no notification
channel, no scheduler. Milestone 1 and Milestone 1.1 were both implemented,
tested (288 passed / 1 skipped / 3 deselected, `ruff`/`mypy --strict` clean),
committed, and released as `v0.1.0`. A subsequent round of matching-quality
fixes (`decisions.md` D-028 through D-034) landed on top of that release
without changing either milestone's acceptance criteria.

## Milestone 2 — Multi-source discovery & sponsorship intelligence (implemented)
See `MILESTONE_2.md` for the full scope contract, refined 2026-08-08
(`decisions.md` D-040 through D-044). A formalised canonical-normalization
boundary (`Job` confirmed as the model every adapter normalizes into) and a
typed `SourceCapabilities` model underpin the rest of the milestone.
Query-planning quality (`SearchProfile`-driven retrieval instead of a single
candidate-history OR-query), **exactly three** new compliant adapters —
Reed, Greenhouse, and Lever, all mandatory — watchlist-backed where
applicable, cross-source deduplication beyond single-source fingerprinting,
and real `VisaAssessment` construction with mandatory UK sponsor-register
corroboration (a Netherlands provider is designed but optional/stretch,
non-blocking). A multi-profession, five-label evaluation dataset (including
a `deceptive_false_positive` category) backs the `job-scout evaluate`
calibration tool. Still local, single-user, synchronous, deterministic — no
notification delivery, no scheduler. This superseded the previous draft of
this section (see below); implementation proceeded task-by-task through
`MILESTONE_2.md` Deliverable 5's twelve-step sequence, and Task 12
(end-to-end acceptance/remediation) is complete — see `MILESTONE_2.md`'s
status line and `decisions.md` D-035 through D-051.

**Superseded from the original M2 draft** (see `decisions.md` D-035 for the
full reasoning):
- *Email-alert ingestion* (Naukri, iimjobs, foundit, Indeed alerts,
  Naukrigulf, GulfTalent, Bayt) — re-sequenced to Milestone 3. It's a
  materially different capability (mailbox auth, per-portal parsing
  heuristics) from the API/feed-based adapter work `MILESTONE_2.md`'s
  Workstream B actually scoped, and the 2026-08-08 planning pass's own
  evaluation list never named these portals.
- *A general, human-reviewed source-discovery workflow* (`architecture.md`
  §9, proposing new `SourceRegistryEntry` rows automatically) — deferred
  past Milestone 2. `MILESTONE_2.md`'s source priority matrix performs this
  milestone's source discovery manually instead; a repeatable discovery
  *tool* is a separate, later capability.
- *"The Muse as optional"* — dropped from the named-source list; not
  evaluated in the 2026-08-08 planning pass (insufficient evidence to
  classify it against the same bar the other candidates were held to,
  `MILESTONE_2.md` Deliverable 4). May be reconsidered in a future
  source-discovery pass.
- Sponsor-registry enrichment and the Reed API are **retained** from the
  original draft — both are now precisely scoped in `MILESTONE_2.md`
  (Workstream D and the source priority matrix, respectively) rather than
  just named.

## Milestone 3 — Regional source expansion, source discovery, and semantic matching (scope defined, not started)
See `MILESTONE_3.md` for the full scope contract (`decisions.md` D-052
through D-056). Four deliverables, D1–D3 independent of each other and D4
depending on D3:
- **D1** — additional regional public-source adapters (UK Find a Job,
  EURES, Canada Job Bank), each built only after its real contract/terms
  are verified — the same evidence bar Reed/Greenhouse/Lever were held to
  (`decisions.md` D-016/D-027/D-028/D-031/D-046/D-047/D-048). All marked
  `requires verification` in `MILESTONE_2.md`'s source priority matrix; not
  yet confirmed to have a real programmatic interface.
- **D2** — a general, human-reviewed source-discovery workflow
  (`architecture.md` §9), exposed as `job-scout discover`: a structured,
  human-driven tool that proposes candidate `SourceRegistryEntry` rows for
  manual review — never automatic search-engine querying/scraping, never
  auto-approved, never auto-executable (`decisions.md` D-054;
  `access_mode: search_discovery` stays permanently non-executable,
  unchanged from D-010).
- **D3** — embedding-based Stage 3 semantic matching, to catch role
  equivalents that keyword overlap misses ("Head of Special Projects" ↔
  strategic initiatives, "Office of the Managing Director" ↔ chief of
  staff, etc.), using a **local** embedding backend only (no API embedding
  provider, no LLM/generative call, no vector database) behind a narrow,
  replaceable interface, with an explicit, documented evidence
  representation so a semantic match stays human-interpretable, not a bare
  similarity number (`decisions.md` D-052/D-053).
- **D4** — re-tune Stage 5 weights now that Stage 3 gives
  responsibilities/sector components a stronger real signal, measured
  empirically via Milestone 2's `job-scout evaluate` tool against a
  documented pre-M3 baseline, not by inspection alone. Weights only —
  `notification_thresholds` and the `ScoreComponent`/`ScoringWeights`
  schema are unchanged (`decisions.md` D-056).

Still local, single-user, synchronous, deterministic — no notification
delivery, no scheduler, no LLM/generative extraction. Do not begin
implementation without the user explicitly asking, per this project's
ground rules.

**Removed from M3** (`decisions.md` D-055): email-alert ingestion (Naukri,
iimjobs, foundit, Indeed alerts, Naukrigulf, GulfTalent, Bayt), previously
re-sequenced here from the original Milestone 2 draft, is no longer part of
Milestone 3. It remains a real, intended capability, deferred to its own
future milestone (tentatively numbered M4.5, or a standalone milestone —
not yet finalized), given its materially different risk profile (mailbox
OAuth/IMAP credential handling, reading a user's real inbox) and its lack
of architectural coupling to M3's other three deliverables.

## Milestone 4 — Optional Anthropic enrichment
- Stage 4 LLM extraction for shortlisted jobs only, behind the optional `llm`
  extra (`pip install .[llm]`), model id from `ANTHROPIC_MODEL` env var.
- Structured extraction only (skills, responsibilities, visa/relocation
  evidence, match reasons, gaps, ambiguities) — LLM never sets the final
  score.

## Milestone 5 — Notification delivery
- Real email delivery (priority alert + scheduled digest formats per spec).
- Notification history + repost/re-notify policy enforcement.
- GitHub Actions scheduled runs — only after external persistence (not local
  SQLite) is in place, since Actions runners don't retain state between runs.
- Digest/priority notification thresholds recalibrated empirically using
  Milestone 2's `job-scout evaluate` tooling and a larger, real-usage-derived
  labelled dataset — not guessed, per `MILESTONE_2.md` Workstream E.

## Milestone 6 — Always-on deployment
- Continuous poller (10–15 min cadence for priority sources), immediate
  alerts for exceptional matches, morning/evening digests otherwise.
- WhatsApp / Telegram / push notification channels.
- User feedback + application-status tracking, source-performance feedback
  loop back into source-selection scoring (closing the loop described in
  `architecture.md` §6).

## Explicitly not planned until asked for
- Dashboard / web UI.
- Full regional source registry buildout beyond what each milestone above
  names — the registry schema supports it; implementation is demand-driven.
- Fuzzy/alias sponsor-name matching (subsidiary/trading-name resolution) —
  Milestone 2 does exact normalized-name matching only; see `MILESTONE_2.md`
  risk R-9.
- Raw source-payload persistence (auditing/re-normalisation tooling) — not
  needed by any currently-scoped milestone's deduplication or provenance
  design; revisit only if a concrete consumer emerges.
