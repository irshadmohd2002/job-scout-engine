"""Sponsor-register import and lookup (Milestone 2 Deliverable 5 step 10;
MILESTONE_2.md "Sponsor registers").

Import-only, never a live download (CLAUDE.md hard constraint 1 and this
milestone's explicit instruction): `import_sponsor_register` parses a file
the user has already downloaded themselves and replaces the stored rows for
that (country, register_name) pair via `JobRepository` — this module never
opens its own SQLite connection (repository/base.py owns persistence, per
this project's existing architecture).

Employer-name normalisation reuses `deduplication.normalize_company` — the
same normaliser cross-source job dedup uses — as the join key for both
`Job.normalized_company` and `SponsorRegistryEntry.normalized_name`
(MILESTONE_2.md "Employer-name normalisation"). Matching is exact
normalized-name + country only; no fuzzy/Levenshtein/alias matching is
implemented (R-9, explicitly deferred).
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

from job_scout.deduplication import normalize_company
from job_scout.models import SponsorRegistryEntry, SponsorRegistryMatch
from job_scout.repository.base import JobRepository

# Registry-match confidence is capped below "confirmed" — exact-normalized-
# name matching still carries real subsidiary/trading-name false-positive
# risk (MILESTONE_2.md "Evidence precedence" #1; R-9).
SPONSOR_REGISTRY_MATCH_CONFIDENCE = 0.7

UK_HOME_OFFICE_SPONSOR_LIST = "uk_home_office_sponsor_list"

# Confirmed directly against a live gov.uk-published export at implementation
# time (decisions.md D-050, same evidence bar as D-016/D-027/D-028/D-031) —
# "Register of Worker and Temporary Worker licensed sponsors":
# https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers
# Header, verbatim: Organisation Name,Town/City,County,Type & Rating,Route
_UK_REQUIRED_COLUMNS = ("Organisation Name", "Town/City", "County", "Type & Rating", "Route")


class SponsorRegisterParseError(ValueError):
    """Raised when a sponsor-register file doesn't match the expected shape
    for its named register — never guessed/silently coerced."""


def parse_uk_home_office_csv(
    csv_text: str,
    *,
    country: str = "GB",
    register_name: str = UK_HOME_OFFICE_SPONSOR_LIST,
) -> list[SponsorRegistryEntry]:
    """Parses the UK Home Office "Register of licensed sponsors: workers"
    CSV export. Only `Organisation Name` and `Type & Rating` map onto
    SponsorRegistryEntry's fixed fields (`registered_name`/`license_status`)
    — `Town/City`/`County`/`Route` are validated as present (confirming this
    really is the expected export shape) but have no corresponding
    SponsorRegistryEntry field to populate; MILESTONE_2.md's domain model
    reserves exactly `country, registered_name, normalized_name,
    register_name, license_status, imported_at`, no location/route fields.
    Rows with a blank organisation name are skipped (defensive against a
    trailing blank line in a real export)."""
    reader = csv.DictReader(io.StringIO(csv_text))
    fieldnames = [name.strip() for name in (reader.fieldnames or [])]
    missing = [col for col in _UK_REQUIRED_COLUMNS if col not in fieldnames]
    if missing:
        raise SponsorRegisterParseError(
            f"Sponsor register CSV is missing expected column(s): {', '.join(missing)}. "
            f"Expected header: {', '.join(_UK_REQUIRED_COLUMNS)}."
        )

    imported_at = datetime.now(UTC)
    country_code = country.upper()
    entries: list[SponsorRegistryEntry] = []
    for row in reader:
        registered_name = (row.get("Organisation Name") or "").strip()
        if not registered_name:
            continue
        license_status = (row.get("Type & Rating") or "").strip() or None
        entries.append(
            SponsorRegistryEntry(
                country=country_code,
                registered_name=registered_name,
                normalized_name=normalize_company(registered_name),
                register_name=register_name,
                license_status=license_status,
                imported_at=imported_at,
            )
        )
    return entries


def import_sponsor_register(
    repository: JobRepository, csv_text: str, *, country: str, register_name: str
) -> int:
    """Parses and imports a sponsor-register CSV snapshot, replacing (never
    appending to) any existing rows for (country, register_name) — the
    documented refresh path for a re-downloaded snapshot. Returns the number
    of entries imported. UK is the only implemented provider in M2 (D-042
    mandatory scope); provider selection is a plain if/elif on
    register_name, not a plugin/loader mechanism, matching this project's
    existing fixed, small-provider-count discipline (CLAUDE.md hard
    constraint 9)."""
    if register_name == UK_HOME_OFFICE_SPONSOR_LIST:
        entries = parse_uk_home_office_csv(csv_text, country=country, register_name=register_name)
    else:
        raise SponsorRegisterParseError(
            f"Unknown register_name '{register_name}'. Supported registers: "
            f"{UK_HOME_OFFICE_SPONSOR_LIST}."
        )
    repository.replace_sponsor_registry_entries(country.upper(), register_name, entries)
    return len(entries)


def find_sponsor_match(
    repository: JobRepository, company_name: str, country: str
) -> SponsorRegistryMatch | None:
    """Exact normalized-name + country lookup only. Returns None when no
    match is found (never a `matched=False` object) — MILESTONE_2.md's own
    `find_sponsor_match(company_name, country) -> SponsorRegistryMatch |
    None` signature."""
    normalized = normalize_company(company_name)
    entry = repository.find_sponsor_registry_entry(normalized, country.upper())
    if entry is None:
        return None
    return SponsorRegistryMatch(
        matched=True,
        registered_name=entry.registered_name,
        register_name=entry.register_name,
        confidence=SPONSOR_REGISTRY_MATCH_CONFIDENCE,
    )
