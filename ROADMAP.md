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

## Milestone 2 — Multi-source discovery & sponsorship intelligence (scope defined, not started)
See `MILESTONE_2.md` for the full scope contract. Query-planning quality
(`SearchProfile`-driven retrieval instead of a single candidate-history
OR-query), two to three new compliant adapters (Reed, Greenhouse, Lever —
watchlist-backed where applicable), cross-source deduplication beyond
single-source fingerprinting, and real `VisaAssessment` construction with
optional UK/NL sponsor-register corroboration. Still local, single-user,
synchronous, deterministic — no notification delivery, no scheduler. This
supersedes the previous draft of this section (see below); do not begin
implementation without the user explicitly asking, per this project's
ground rules.

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

## Milestone 3 — Semantic similarity & email-alert ingestion
- Embedding-based Stage 3 matching to catch role equivalents that keyword
  overlap misses ("Head of Special Projects" ↔ strategic initiatives, "Office
  of the Managing Director" ↔ chief of staff, etc.).
- Re-tune Stage 5 weights now that responsibilities/sector components have a
  stronger signal than the M1 keyword proxy — and now that Milestone 2's
  `job-scout evaluate` tool exists to measure the effect empirically rather
  than by inspection alone.
- Email-alert ingestion (Naukri, iimjobs, foundit, Indeed alerts, Naukrigulf,
  GulfTalent, Bayt) — parse forwarded/ingested alert emails into
  `RawJobRecord`s through the same normalisation path as any adapter.
  Re-sequenced here from the original Milestone 2 draft — see above.
- A general, human-reviewed source-discovery workflow (`architecture.md`
  §9) — also re-sequenced here from the original Milestone 2 draft.
- Additional approved public sources per region as terms review clears them
  (UK Find a Job, EURES, Canada Job Bank — all marked `requires verification`
  in `MILESTONE_2.md`'s source priority matrix, not yet confirmed to have a
  real programmatic interface).

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
