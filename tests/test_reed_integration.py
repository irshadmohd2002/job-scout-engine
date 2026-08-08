"""Opt-in integration test — real network, real Reed credentials.

Skipped by default (pyproject.toml addopts = "-m 'not integration'"). Run
explicitly with `pytest -m integration`; requires REED_API_KEY in .env or the
environment (mirrors test_adzuna_integration.py). Makes a single, tiny,
bounded request — one page, one result — and never prints the API key.
"""

from __future__ import annotations

import pytest

from job_scout.config import load_env
from job_scout.models import SourceSearchParams
from job_scout.sources.reed import ReedAdapter

pytestmark = pytest.mark.integration


def test_reed_live_fetch_returns_well_formed_record() -> None:
    env = load_env()
    if not env.reed_api_key:
        pytest.skip("REED_API_KEY not set; skipping live Reed integration test.")

    adapter = ReedAdapter(api_key=env.reed_api_key)
    params = SourceSearchParams(
        countries=["GB"],
        keywords=["manager"],
        role_family_hints=[],
        employment_types=[],
        min_experience_years=None,
        max_experience_years=None,
        page_size=1,
        max_pages=1,
    )

    records = adapter.fetch(params)

    assert len(records) >= 1
    record = records[0]
    assert record.source_id == "reed_api"
    assert record.external_id
    assert isinstance(record.raw_payload, dict)
