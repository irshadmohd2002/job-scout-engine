import pytest

from job_scout.models import AccessMode, ApprovalStatus, SourceCapabilities
from job_scout.source_intelligence.compliance import ComplianceConfigError, authorize
from tests.factories import make_source_entry

AUTO_EXECUTABLE = {AccessMode.PUBLIC_API, AccessMode.PUBLIC_ATS_FEED, AccessMode.RSS_OR_SITEMAP}
ALL_ACCESS_MODES = list(AccessMode)
ALL_APPROVAL_STATUSES = list(ApprovalStatus)


@pytest.mark.parametrize("approval_status", ALL_APPROVAL_STATUSES)
@pytest.mark.parametrize("access_mode", ALL_ACCESS_MODES)
def test_full_truth_table(approval_status: ApprovalStatus, access_mode: AccessMode) -> None:
    entry = make_source_entry(access_mode=access_mode, approval_status=approval_status)

    if access_mode == AccessMode.SEARCH_DISCOVERY:
        decision = authorize(entry, access_mode)
        assert decision.allowed is False
        return

    if approval_status == ApprovalStatus.APPROVED:
        if access_mode in AUTO_EXECUTABLE:
            decision = authorize(entry, access_mode)
            assert decision.allowed is True
        else:
            with pytest.raises(ComplianceConfigError):
                authorize(entry, access_mode)
        return

    # every other approval_status is never auto-executable, regardless of access_mode
    decision = authorize(entry, access_mode)
    assert decision.allowed is False


def test_search_discovery_never_executable_even_when_approved() -> None:
    entry = make_source_entry(
        access_mode=AccessMode.SEARCH_DISCOVERY, approval_status=ApprovalStatus.APPROVED
    )
    decision = authorize(entry, AccessMode.SEARCH_DISCOVERY)
    assert decision.allowed is False


def test_approved_with_non_executable_mode_raises_not_silently_passes() -> None:
    entry = make_source_entry(
        access_mode=AccessMode.PERMITTED_HTML, approval_status=ApprovalStatus.APPROVED
    )
    with pytest.raises(ComplianceConfigError):
        authorize(entry, AccessMode.PERMITTED_HTML)


@pytest.mark.parametrize(
    "approval_status",
    [
        ApprovalStatus.ALERT_ONLY,
        ApprovalStatus.REQUIRES_AUTHORISATION,
        ApprovalStatus.MANUAL_REVIEW,
        ApprovalStatus.BLOCKED,
        ApprovalStatus.DEPRECATED,
    ],
)
def test_non_approved_statuses_never_executable(approval_status: ApprovalStatus) -> None:
    entry = make_source_entry(access_mode=AccessMode.PUBLIC_API, approval_status=approval_status)
    decision = authorize(entry, AccessMode.PUBLIC_API)
    assert decision.allowed is False


def test_capabilities_never_affect_the_compliance_decision() -> None:
    """decisions.md D-041 step 1 non-goal: capabilities are metadata only —
    the compliance gate must reach the same decision regardless of what a
    source's capabilities block declares."""
    baseline = make_source_entry(
        access_mode=AccessMode.PUBLIC_API, approval_status=ApprovalStatus.MANUAL_REVIEW
    )
    fully_capable = make_source_entry(
        access_mode=AccessMode.PUBLIC_API,
        approval_status=ApprovalStatus.MANUAL_REVIEW,
        capabilities=SourceCapabilities(
            keyword_search=True,
            exact_phrase_search=True,
            company_filter=True,
            industry_filter=True,
        ),
    )
    baseline_decision = authorize(baseline, AccessMode.PUBLIC_API)
    capable_decision = authorize(fully_capable, AccessMode.PUBLIC_API)
    assert baseline_decision.allowed is False
    assert capable_decision.allowed is False
    assert baseline_decision.reason == capable_decision.reason
