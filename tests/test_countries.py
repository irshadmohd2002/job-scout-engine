import pytest

from job_scout.countries import UnknownCountryError, is_known_country, resolve_regions


def test_known_country_resolves() -> None:
    assert resolve_regions("GB") == ["uk"]
    assert resolve_regions("de") == ["europe"]  # case-insensitive


def test_unknown_country_raises_loudly() -> None:
    with pytest.raises(UnknownCountryError):
        resolve_regions("ZZ")


def test_is_known_country() -> None:
    assert is_known_country("US") is True
    assert is_known_country("ZZ") is False


def test_uk_is_distinct_from_europe() -> None:
    assert "europe" not in resolve_regions("GB")
