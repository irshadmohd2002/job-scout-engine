"""Shared visa/sponsorship regex evidence patterns (Milestone 2 Deliverable 5
step 10; MILESTONE_2.md "Evidence precedence").

Before this module existed, `matching/scoring.py`'s `_visa_relocation_component`
and `matching/hard_filters.py`'s `reject_on_explicit_no_sponsorship` check each
maintained their own copy of the negative-sponsorship pattern list
(byte-identical). This module is the single source of truth for both, plus
`matching/visa.py::assess_visa`'s job-text evidence scan — the same three
lists, imported everywhere they're used, never redefined locally. Moving
these here changes no matching behaviour: the compiled patterns are unchanged
from their pre-consolidation form in either module.
"""

from __future__ import annotations

import re

VISA_POSITIVE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"visa sponsorship (?:is )?(?:available|provided|offered)",
        r"relocation (?:assistance|support|package)",
        r"we (?:will |can )?sponsor",
        r"work permit sponsorship",
    ]
]

VISA_NEGATIVE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"(?:not able|unable) to (?:offer|provide) sponsorship",
        r"cannot sponsor",
        r"no (?:visa )?sponsorship (?:is )?(?:available|offered|provided)",
        r"does not offer (?:visa )?sponsorship",
    ]
]

# hard_filters.py's `_NO_SPONSORSHIP_PATTERNS` was byte-identical to
# VISA_NEGATIVE_PATTERNS above — kept as one shared name, not a separate list,
# now that both call sites import from here.
NO_SPONSORSHIP_PATTERNS = VISA_NEGATIVE_PATTERNS


def first_match(patterns: list[re.Pattern[str]], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None
