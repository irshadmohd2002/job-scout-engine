"""Opt-in integration test — real network, real Adzuna credentials.

Skipped by default (pyproject.toml addopts = "-m 'not integration'"). Run
explicitly with `pytest -m integration`; requires ADZUNA_APP_ID/
ADZUNA_APP_KEY in .env or the environment (MILESTONE_1.md test plan).
"""

from __future__ import annotations

import pytest

from job_scout.config import load_env
from job_scout.models import SourceSearchParams
from job_scout.sources.adzuna import AdzunaAdapter

pytestmark = pytest.mark.integration


def test_adzuna_live_fetch_returns_well_formed_record() -> None:
    env = load_env()
    if not env.adzuna_app_id or not env.adzuna_app_key:
        pytest.skip("ADZUNA_APP_ID/ADZUNA_APP_KEY not set; skipping live Adzuna integration test.")

    adapter = AdzunaAdapter(app_id=env.adzuna_app_id, app_key=env.adzuna_app_key)
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
    assert record.source_id == "adzuna_api"
    assert record.external_id
    assert record.raw_url.startswith("http")
    assert isinstance(record.raw_payload, dict)
    assert "title" in record.raw_payload
