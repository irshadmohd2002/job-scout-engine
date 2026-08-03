"""Compliance gate (architecture.md section 7).

A deterministic rule table, not a scored model (decisions.md D-003):
approval_status x access_mode -> allow/deny. Enforced twice — once by the
planner (sets `executable` on SelectedSource) and once inside the pipeline
immediately before invoking SourceAdapter.fetch() (defence in depth,
registry state can change between plan generation and execution).
"""

from __future__ import annotations

from pydantic import BaseModel

from job_scout.models import AccessMode, ApprovalStatus, SourceRegistryEntry

AUTO_EXECUTABLE_ACCESS_MODES: frozenset[AccessMode] = frozenset(
    {AccessMode.PUBLIC_API, AccessMode.PUBLIC_ATS_FEED, AccessMode.RSS_OR_SITEMAP}
)


class ComplianceConfigError(Exception):
    """Raised when an `approved` registry entry declares an access_mode that
    is not auto-executable. This is a registry authoring error — the gate
    never silently downgrades an approved-but-misconfigured entry to
    non-executable; it fails loudly so the registry gets fixed."""


class ComplianceDecision(BaseModel):
    allowed: bool
    reason: str


def authorize(entry: SourceRegistryEntry, requested_mode: AccessMode) -> ComplianceDecision:
    # search_discovery is never auto-executable regardless of approval_status
    # (architecture.md section 7; decisions.md D-010; risk R-5).
    if requested_mode == AccessMode.SEARCH_DISCOVERY:
        return ComplianceDecision(
            allowed=False,
            reason="search_discovery is never auto-executable for collection; it exists only "
            "to support the source-discovery process, not job collection.",
        )

    if entry.approval_status == ApprovalStatus.APPROVED:
        if requested_mode in AUTO_EXECUTABLE_ACCESS_MODES:
            return ComplianceDecision(
                allowed=True,
                reason=f"approved with auto-executable access_mode '{requested_mode}'",
            )
        raise ComplianceConfigError(
            f"Source '{entry.source_id}' has approval_status=approved but access_mode="
            f"'{requested_mode}' is not auto-executable. This is a registry configuration "
            "error — fix the registry entry (either the access_mode or the approval_status), "
            "the gate will not silently downgrade it."
        )

    if entry.approval_status == ApprovalStatus.ALERT_ONLY:
        return ComplianceDecision(
            allowed=False,
            reason="alert_only — requires email-alert ingestion, not adapter execution",
        )

    if entry.approval_status == ApprovalStatus.REQUIRES_AUTHORISATION:
        return ComplianceDecision(
            allowed=False,
            reason="requires_authorisation — pending required setup action before execution",
        )

    if entry.approval_status == ApprovalStatus.MANUAL_REVIEW:
        return ComplianceDecision(allowed=False, reason="manual_review — pending human review")

    if entry.approval_status == ApprovalStatus.BLOCKED:
        return ComplianceDecision(allowed=False, reason="blocked — never selected for execution")

    if entry.approval_status == ApprovalStatus.DEPRECATED:
        return ComplianceDecision(allowed=False, reason="deprecated")

    raise AssertionError(f"Unhandled approval_status: {entry.approval_status}")  # pragma: no cover
