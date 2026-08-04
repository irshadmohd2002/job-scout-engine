"""Cross-profession deterministic matching (decisions.md D-017; architecture.md
section 15). Job Scout Engine has no profession-specific code — these tests
prove three materially different professions (strategy/transformation,
software engineering, and a regulated profession, nursing) each exercise the
same pipeline: profile loading, search-profile loading, hard filters,
pre-filtering, scoring, and evidence generation — driven entirely by
configuration loaded through config.py, not by any profession-aware code
path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from job_scout import config
from job_scout.matching.hard_filters import evaluate_hard_filters
from job_scout.matching.prefilter import PrefilterWeights, run_prefilter
from job_scout.matching.scoring import compute_score_components
from job_scout.models import Job, Location
from job_scout.source_intelligence.planner import build_plan
from tests.factories import make_fingerprint, make_provenance, make_source_entry

_STRATEGY_CANDIDATE_YAML = """
candidate_id: strategy-candidate
years_experience: 8.0
current_location: {country: IN, region: null, city: null}
nationality: IN
seniority: "Associate Director"
seniority_level: null
education:
  - {level: postgraduate, degree: MBA, field: "General Management", institution: null}
role_families: [strategy_and_planning, transformation]
title_aliases: ["Strategy Manager", "Transformation Manager"]
primary_skills: [strategy_development, transformation]
secondary_skills: [financial_modelling]
industries: [financial_services]
sectors: [private_sector]
requires_work_authorisation_support: true
open_to_relocation: true
notification_thresholds: {priority_score: 85, digest_score: 70}
"""

_STRATEGY_SEARCH_YAML = """
profiles:
  - profile_id: strategy-role
    candidate_profile_ref: strategy-candidate
    included_countries: [GB]
    security_clearance_allowed: false
    reject_on_explicit_no_sponsorship: true
    notification_thresholds: {priority_score: 85, digest_score: 70}
    polling_frequency_minutes: 720
"""

_SOFTWARE_CANDIDATE_YAML = """
candidate_id: software-candidate
years_experience: 6.0
current_location: {country: IN, region: null, city: null}
nationality: IN
seniority: "Senior Software Engineer"
seniority_level: null
education:
  - {level: undergraduate, degree: "BSc Computer Science", field: "Computer Science",
     institution: null}
role_families: [software_engineering, backend_development]
title_aliases: ["Senior Software Engineer", "Backend Engineer"]
primary_skills: [python, distributed_systems]
secondary_skills: [kubernetes]
tools_and_technologies: [python, postgresql, kubernetes]
industries: [technology]
sectors: [private_sector]
requires_work_authorisation_support: true
open_to_relocation: true
notification_thresholds: {priority_score: 85, digest_score: 70}
"""

_SOFTWARE_SEARCH_YAML = """
profiles:
  - profile_id: software-role
    candidate_profile_ref: software-candidate
    included_countries: [GB]
    required_skills: [python]
    security_clearance_allowed: false
    reject_on_explicit_no_sponsorship: true
    notification_thresholds: {priority_score: 85, digest_score: 70}
    polling_frequency_minutes: 720
    hard_filters:
      enforce_required_skills: true
"""

_NURSING_CANDIDATE_YAML = """
candidate_id: nursing-candidate
years_experience: 5.0
current_location: {country: IN, region: null, city: null}
nationality: IN
seniority: "Senior Staff Nurse"
seniority_level: null
education:
  - {level: undergraduate, degree: "BSc Nursing", field: "Adult Nursing", institution: null}
role_families: [nursing, clinical_care]
title_aliases: ["Registered Nurse", "Staff Nurse"]
primary_skills: [patient_care, clinical_assessment]
secondary_skills: [wound_care]
licences: ["Registered Nurse License"]
certifications: ["Basic Life Support"]
languages: ["English"]
industries: [healthcare]
sectors: [public_sector]
requires_work_authorisation_support: true
open_to_relocation: true
notification_thresholds: {priority_score: 85, digest_score: 70}
"""

_NURSING_SEARCH_YAML = """
profiles:
  - profile_id: nursing-role
    candidate_profile_ref: nursing-candidate
    included_countries: [GB]
    required_languages: [English]
    required_licences: ["Registered Nurse License"]
    security_clearance_allowed: false
    reject_on_explicit_no_sponsorship: true
    notification_thresholds: {priority_score: 85, digest_score: 70}
    polling_frequency_minutes: 720
    hard_filters:
      enforce_required_licences: true
"""


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def _make_job(*, title: str, description_text: str) -> Job:
    fingerprint = make_fingerprint(
        canonical_url=f"https://example.com/jobs/{abs(hash(title))}",
        normalized_title=title.lower(),
    )
    return Job(
        job_id="22222222-2222-2222-2222-222222222222",
        title=title,
        normalized_title=title.lower(),
        company="Example Employer",
        normalized_company="example employer",
        location=Location(country="GB", city="London"),
        description_raw=description_text,
        description_text=description_text,
        collected_at="2026-01-01T00:00:00+00:00",
        source_provenance=[make_provenance()],
        fingerprint=fingerprint,
    )


@pytest.mark.parametrize(
    "candidate_yaml,search_yaml,job_title,job_text,expect_pass",
    [
        (
            _STRATEGY_CANDIDATE_YAML,
            _STRATEGY_SEARCH_YAML,
            "Strategy Manager",
            "Strategy and transformation role. MBA required. 6-10 years experience.",
            True,
        ),
        (
            _SOFTWARE_CANDIDATE_YAML,
            _SOFTWARE_SEARCH_YAML,
            "Senior Software Engineer",
            "Backend engineering role using Python and distributed systems, 5-8 years.",
            True,
        ),
        (
            _NURSING_CANDIDATE_YAML,
            _NURSING_SEARCH_YAML,
            "Registered Nurse",
            "Adult ward nursing role. Requires Registered Nurse License. English required.",
            True,
        ),
    ],
    ids=["strategy_transformation", "software_engineering", "nursing_regulated"],
)
def test_full_pipeline_stages_for_each_profession(
    tmp_path: Path,
    candidate_yaml: str,
    search_yaml: str,
    job_title: str,
    job_text: str,
    expect_pass: bool,
) -> None:
    """Profile loading -> search-profile loading -> hard filters ->
    prefilter -> scoring -> evidence, all through config.py's real loaders
    (not just in-memory factories), for a matching job in each profession."""
    candidate_path = _write(tmp_path, "candidate_profile.yaml", candidate_yaml)
    search_path = _write(tmp_path, "search_profiles.yaml", search_yaml)

    candidate = config.load_candidate_profile(candidate_path)
    profiles = config.load_search_profiles(search_path)
    search = next(iter(profiles.values()))

    job = _make_job(title=job_title, description_text=job_text)

    hard_filter_result = evaluate_hard_filters(job, candidate, search)
    assert hard_filter_result.passed is expect_pass

    weights = config.load_scoring_weights(data_dir=tmp_path / "no-such-data-dir")
    prefilter_result = run_prefilter(
        job, candidate, search, PrefilterWeights.from_scoring_weights(weights)
    )
    assert prefilter_result.score > 0
    assert prefilter_result.evidence  # some overlap evidence produced

    components = compute_score_components(job, candidate, search, weights)
    assert len(components) == 8
    for component in components:
        assert 0.0 <= component.raw_value <= 1.0


def test_nursing_job_missing_licence_is_rejected(tmp_path: Path) -> None:
    """At least one required-certification/licence/language test: the
    opt-in required_licences hard filter actually rejects a job that omits
    the licence, once enforce_required_licences is true."""
    candidate_path = _write(tmp_path, "candidate_profile.yaml", _NURSING_CANDIDATE_YAML)
    search_path = _write(tmp_path, "search_profiles.yaml", _NURSING_SEARCH_YAML)
    candidate = config.load_candidate_profile(candidate_path)
    search = next(iter(config.load_search_profiles(search_path).values()))

    job = _make_job(
        title="Registered Nurse",
        description_text="Adult ward nursing role. English required.",  # no licence mention
    )
    result = evaluate_hard_filters(job, candidate, search)
    assert result.passed is False
    assert any(r.rule == "missing_required_licence" for r in result.rejections)


def test_mba_has_no_special_value_for_software_engineer_profile(tmp_path: Path) -> None:
    """Reinforces test_scoring.py's dedicated MBA test in a different
    profession's context: a software-engineering candidate profile (no MBA
    anywhere in it) must not score a job mentioning "MBA" any differently
    for education than one that doesn't."""
    candidate_path = _write(tmp_path, "candidate_profile.yaml", _SOFTWARE_CANDIDATE_YAML)
    search_path = _write(tmp_path, "search_profiles.yaml", _SOFTWARE_SEARCH_YAML)
    candidate = config.load_candidate_profile(candidate_path)
    search = next(iter(config.load_search_profiles(search_path).values()))
    weights = config.load_scoring_weights(data_dir=tmp_path / "no-such-data-dir")

    with_mba = _make_job(
        title="Senior Software Engineer",
        description_text="Backend role using Python. MBA preferred but not required.",
    )
    without_mba = _make_job(
        title="Senior Software Engineer",
        description_text="Backend role using Python and distributed systems.",
    )

    education_with_mba = next(
        c for c in compute_score_components(with_mba, candidate, search, weights)
        if c.name == "education"
    )
    education_without_mba = next(
        c for c in compute_score_components(without_mba, candidate, search, weights)
        if c.name == "education"
    )
    assert education_with_mba.raw_value == 0.5
    assert education_without_mba.raw_value == 0.5
    assert education_with_mba.raw_value == education_without_mba.raw_value


def test_industry_and_sector_relevance_affects_source_planning() -> None:
    """At least one test showing industry/sector relevance affecting
    planning or scoring: a source restricted to a matching sector/industry
    outranks an otherwise-identical source that claims no data, once the
    candidate/search profile actually supplies sector/industry signal."""
    from job_scout.config import ExecutionLimits, SourceScoringWeights
    from job_scout.models import SourceType
    from tests.factories import make_candidate_profile, make_search_profile

    weights = SourceScoringWeights(
        country_region_relevance=0.2,
        role_family_relevance=0.2,
        sector_relevance=0.2,
        seniority_relevance=0.1,
        historical_matching_jobs=0.1,
        freshness=0.1,
        visa_international_usefulness=0.05,
        source_reliability=0.025,
        duplicate_rate_inverted=0.025,
        technical_quality=0.0,
        neutral_prior=0.5,
        diversity_duplicate_rate_threshold=0.6,
    )
    limits = ExecutionLimits(
        max_countries_per_run=6,
        max_pages_per_source_country=3,
        results_per_page=50,
        request_timeout_seconds=15,
        max_retries=2,
    )
    candidate = make_candidate_profile(industries=["healthcare"], sectors=["public_sector"])
    search = make_search_profile(
        included_countries=["GB"],
        included_industries=["healthcare"],
        included_sectors=["public_sector"],
    )

    # Distinct, non-aggregator source_type on both so the diversity rule
    # (which only collapses same-country generic *aggregator* sources) never
    # excludes either one — this test is about the sector_relevance factor,
    # not the diversity rule.
    matching_source = make_source_entry(
        source_id="healthcare_matched",
        geographic_coverage=["GB"],
        source_type=SourceType.JOB_PORTAL,
        industry_coverage=["healthcare"],
        sector_coverage=["public_sector"],
    )
    unrelated_source = make_source_entry(
        source_id="unrelated_industry",
        geographic_coverage=["GB"],
        source_type=SourceType.JOB_PORTAL,
        industry_coverage=["retail"],
        sector_coverage=["private_sector"],
        priority=50,
    )

    plan = build_plan(candidate, search, [matching_source, unrelated_source], limits, weights)
    scores = {s.source_id: s.score for s in plan.selected_sources}
    assert scores["healthcare_matched"] > scores["unrelated_industry"]
