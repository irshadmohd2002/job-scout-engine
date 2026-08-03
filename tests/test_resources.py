"""Packaged config templates (architecture.md section 15.2; decisions.md
D-021) — job_scout.resources."""

from __future__ import annotations

import yaml

from job_scout.resources import TEMPLATE_NAMES, template_text, templates_root

# Substrings that must never appear in a generic packaged template — a
# cheap, deliberately blunt guard against personal data or credentials
# creeping into a tracked template file (CLAUDE.md hard constraint 8).
_FORBIDDEN_SUBSTRINGS = (
    "ADZUNA_APP_KEY=",  # a populated credential value, not just the key name
    "sk-ant-",  # Anthropic API key prefix
)


def test_every_template_name_is_accessible() -> None:
    for name in TEMPLATE_NAMES:
        text = template_text(name)
        assert text.strip()


def test_templates_root_lists_the_same_files_on_disk() -> None:
    root = templates_root()
    on_disk = {entry.name for entry in root.iterdir()}  # type: ignore[attr-defined]
    assert on_disk == set(TEMPLATE_NAMES)


def test_unknown_template_name_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        template_text("does-not-exist.yaml")


def test_templates_contain_no_populated_secrets_or_forbidden_strings() -> None:
    for name in TEMPLATE_NAMES:
        text = template_text(name)
        for forbidden in _FORBIDDEN_SUBSTRINGS:
            assert forbidden not in text, f"{name} contains forbidden substring {forbidden!r}"


def test_yaml_templates_parse_as_valid_yaml() -> None:
    for name in TEMPLATE_NAMES:
        parsed = yaml.safe_load(template_text(name))
        assert isinstance(parsed, dict)


def test_candidate_profile_template_is_a_generic_placeholder() -> None:
    text = template_text("candidate_profile.example.yaml").lower()
    # Placeholder markers, not a real person's details.
    assert "xx" in text  # placeholder ISO country/nationality
    assert "example" in text


def test_source_registry_template_has_no_credential_values() -> None:
    parsed = yaml.safe_load(template_text("source_registry.example.yaml"))
    for source in parsed["sources"]:
        # auth_required is a boolean flag, never a credential value itself.
        assert isinstance(source["auth_required"], bool)
