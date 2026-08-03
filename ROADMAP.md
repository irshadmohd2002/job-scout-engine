# Roadmap

Milestones are sequential and each depends on the previous one being real and
working — not aspirational. Do not start a milestone's implementation without
explicit instruction, per project ground rules.

## Milestone 1 — Local vertical slice (implemented)
See `MILESTONE_1.md`. One adapter (Adzuna), deterministic matching only,
SQLite, console output, `run-once --dry-run`. Milestone 2 has not been
started — do not begin it without the user explicitly asking, per this
project's ground rules.

## Milestone 2 — Broader deterministic collection
- Priority-company watchlist (schema exists from M1; build the
  Greenhouse/Lever/Ashby adapters against it).
- Email-alert ingestion (Naukri, iimjobs, foundit, Indeed alerts, Naukrigulf,
  GulfTalent, Bayt) — parse forwarded/ingested alert emails into
  `RawJobRecord`s through the same normalisation path as any adapter.
- Additional approved public sources per region as terms review clears them
  (Reed API for UK, The Muse as optional, government job boards where a real
  feed exists).
- Source-discovery process: a distinct, human-reviewed workflow that proposes
  new `SourceRegistryEntry` rows (never auto-approved — see `architecture.md`
  §9).
- Sponsor-registry enrichment for countries with a public register (starting
  with the UK sponsor register), feeding `VisaAssessment.employer_registry_match`.

## Milestone 3 — Semantic similarity
- Embedding-based Stage 3 matching to catch role equivalents that keyword
  overlap misses ("Head of Special Projects" ↔ strategic initiatives, "Office
  of the Managing Director" ↔ chief of staff, etc.).
- Re-tune Stage 5 weights now that responsibilities/sector components have a
  stronger signal than the M1 keyword proxy.

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
