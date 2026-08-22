"""ISO country -> region resolution (architecture.md section 5).

A plain static lookup table, not a service. Used by the planner to match
SearchProfile countries against SourceRegistryEntry.geographic_coverage when
coverage is expressed at region granularity, and by VisaAssessment's
country_work_permit_regime context slot.
"""

from __future__ import annotations

REGION_UK = "uk"
REGION_EUROPE = "europe"
REGION_MIDDLE_EAST = "middle_east"
REGION_SOUTH_ASIA = "south_asia"
REGION_SOUTHEAST_ASIA = "southeast_asia"
REGION_NORTH_AMERICA = "north_america"
REGION_ANZ = "anz"

# ISO 3166-1 alpha-2 -> region tag(s). The "uk" region is deliberately
# distinct from "europe" (architecture.md section 5: "uk (subset of
# europe-adjacent-but-distinct"), so a source whose geographic_coverage lists
# only "europe" does not automatically cover GB.
COUNTRY_REGIONS: dict[str, list[str]] = {
    "GB": [REGION_UK],
    "DE": [REGION_EUROPE],
    "NL": [REGION_EUROPE],
    "IE": [REGION_EUROPE],
    "FR": [REGION_EUROPE],
    "ES": [REGION_EUROPE],
    "IT": [REGION_EUROPE],
    "PT": [REGION_EUROPE],
    "BE": [REGION_EUROPE],
    "CH": [REGION_EUROPE],
    "AT": [REGION_EUROPE],
    "SE": [REGION_EUROPE],
    "DK": [REGION_EUROPE],
    "NO": [REGION_EUROPE],
    "FI": [REGION_EUROPE],
    "PL": [REGION_EUROPE],
    "AE": [REGION_MIDDLE_EAST],
    "SA": [REGION_MIDDLE_EAST],
    "QA": [REGION_MIDDLE_EAST],
    "KW": [REGION_MIDDLE_EAST],
    "BH": [REGION_MIDDLE_EAST],
    "OM": [REGION_MIDDLE_EAST],
    "SG": [REGION_SOUTHEAST_ASIA],
    "MY": [REGION_SOUTHEAST_ASIA],
    "TH": [REGION_SOUTHEAST_ASIA],
    "ID": [REGION_SOUTHEAST_ASIA],
    "PH": [REGION_SOUTHEAST_ASIA],
    "VN": [REGION_SOUTHEAST_ASIA],
    "CA": [REGION_NORTH_AMERICA],
    "US": [REGION_NORTH_AMERICA],
    "MX": [REGION_NORTH_AMERICA],
    "AU": [REGION_ANZ],
    "NZ": [REGION_ANZ],
    "IN": [REGION_SOUTH_ASIA],
    "PK": [REGION_SOUTH_ASIA],
    "BD": [REGION_SOUTH_ASIA],
    "LK": [REGION_SOUTH_ASIA],
    "NP": [REGION_SOUTH_ASIA],
}


class UnknownCountryError(ValueError):
    """Raised when a country code has no known region mapping.

    Fails loudly rather than silently defaulting, per architecture.md
    section 5 and MILESTONE_1.md's test plan ("unknown code fails loudly
    rather than silently defaulting").
    """

    def __init__(self, country: str) -> None:
        self.country = country
        super().__init__(
            f"Unknown country code '{country}' — no region mapping is defined in "
            "job_scout/countries.py. Add it before using this country in a "
            "search profile or source registry entry."
        )


def resolve_regions(country: str) -> list[str]:
    """Resolve an ISO 3166-1 alpha-2 country code to its region tag(s).

    Raises UnknownCountryError for any code not present in COUNTRY_REGIONS.
    """
    try:
        return COUNTRY_REGIONS[country.upper()]
    except KeyError as exc:
        raise UnknownCountryError(country) from exc


def is_known_country(country: str) -> bool:
    return country.upper() in COUNTRY_REGIONS


# --- Work-permit regime labels (Milestone 2 Deliverable 5 step 10) ---------
# A small, region-granularity (never per-country) context label for
# VisaAssessment.country_work_permit_regime — a generic structural prior
# only, never scored on its own (MILESTONE_2.md "Evidence precedence" #3;
# architecture.md section 5). Kept as a plain module-level dict, the same
# shape as COUNTRY_REGIONS above, rather than a new YAML surface — this is
# static architecture-lookup data, not per-run candidate/search config.
_UNKNOWN_REGIME = "unspecified_work_permit_regime"

REGION_WORK_PERMIT_REGIMES: dict[str, str] = {
    REGION_UK: "uk_points_based_system",
    REGION_EUROPE: "eu_or_efta_national_visa_regime",
    REGION_MIDDLE_EAST: "gcc_employer_sponsored_regime",
    REGION_SOUTH_ASIA: "south_asia_general_work_permit_regime",
    REGION_SOUTHEAST_ASIA: "southeast_asia_general_work_permit_regime",
    REGION_NORTH_AMERICA: "north_america_employer_sponsored_regime",
    REGION_ANZ: "anz_points_based_system",
}


def resolve_work_permit_regime(country: str) -> str:
    """Best-effort region-level regime label for `country`. Never raises —
    an unrecognised country code (or a region with no label configured)
    resolves to a generic `_UNKNOWN_REGIME` string rather than aborting a
    visa assessment; this is a low-confidence context string, not a value
    correctness depends on."""
    try:
        regions = resolve_regions(country)
    except UnknownCountryError:
        return _UNKNOWN_REGIME
    for region in regions:
        label = REGION_WORK_PERMIT_REGIMES.get(region)
        if label is not None:
            return label
    return _UNKNOWN_REGIME
