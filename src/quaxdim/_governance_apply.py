"""Apply governance metadata to evaluation results.

This module is an internal implementation detail.  Public callers should
access it through :func:`quaxdim.evaluate_optimized` or
:func:`quaxdim.evaluate_contract`.

Responsibilities
----------------
1. Enrich each result with its per-rule governance fields (steward,
   on_failure) and the contract-level criticality.
2. Sort results so that the most actionable items appear first:
   - Within each status tier, higher-criticality items precede lower ones.
   - Original evaluation order is the tiebreaker.
3. Emit :class:`StaleContractWarning` when the review cycle has elapsed.
"""

from __future__ import annotations

import dataclasses
import warnings
from collections.abc import Sequence
from typing import Any, TypeVar, cast

from .governance import Governance

__all__ = ["StaleContractWarning", "apply_governance", "warn_if_overdue"]

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Warning type
# ---------------------------------------------------------------------------


class StaleContractWarning(UserWarning):
    """Emitted when a contract's review cycle has elapsed since approval.

    The warning carries the number of days since approval and the review
    cycle name so that callers can surface it in their own UIs.

    Example::

        import warnings
        warnings.filterwarnings("error", category=StaleContractWarning)
    """


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Lower index = show first (most urgent).
_ATTENTION_ORDER: dict[str, int] = {
    "ERROR": 0,
    "INVALID_CONTRACT": 0,
    "INDETERMINATE": 1,
    "FAIL": 2,
    "UNSUPPORTED": 3,
    "NO_ELIGIBLE_DATA": 4,
    "PASS": 5,
}

_DEFAULT_ATTENTION = max(_ATTENTION_ORDER.values()) + 1


def _status_rank(result: object) -> int:
    status = getattr(result, "status", None)
    if status is None:
        return _DEFAULT_ATTENTION
    return _ATTENTION_ORDER.get(str(status), _DEFAULT_ATTENTION)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def apply_governance(
    results: Sequence[T], governance: Governance
) -> tuple[T, ...]:
    """Return *results* enriched with governance metadata and sorted.

    For each result the function calls :func:`dataclasses.replace` to attach:

    - ``steward`` — from the matching ``governance.rules[rule_id].steward``,
      falling back to ``None`` when the rule has no entry.
    - ``on_failure`` — from ``governance.rules[rule_id].on_failure``,
      falling back to ``None``.
    - ``criticality`` — the contract-level ``governance.criticality``
      (same value for every result in this report).

    If a result dataclass does not have one of those fields the enrichment
    is skipped silently, so pre-governance results are accepted without error.

    Sorting key: ``(status_rank, criticality_rank, original_position)``.

    Args:
        results: Sequence of result objects (must be dataclasses supporting
            :func:`dataclasses.replace`).
        governance: Parsed governance for the contract being evaluated.

    Returns:
        A tuple of enriched, sorted results.
    """
    enriched: list[tuple[T, int, int]] = []  # (result, status_rank, pos)

    for pos, result in enumerate(results):
        rule_id: str | None = getattr(result, "rule_id", None)
        rg = governance.for_rule(rule_id) if rule_id else None

        replacements: dict[str, object] = {}

        result_fields = {f.name for f in dataclasses.fields(result)}  # type: ignore[arg-type]

        if "steward" in result_fields:
            replacements["steward"] = rg.steward if rg else None
        if "on_failure" in result_fields:
            replacements["on_failure"] = rg.on_failure if rg else None
        if "criticality" in result_fields:
            replacements["criticality"] = governance.criticality

        if replacements:
            result = cast(
                T, dataclasses.replace(cast(Any, result), **replacements)
            )

        enriched.append((result, _status_rank(result), pos))

    enriched.sort(key=lambda t: (t[1], governance.criticality_rank, t[2]))
    return tuple(item[0] for item in enriched)


def warn_if_overdue(governance: Governance) -> None:
    """Emit :class:`StaleContractWarning` when the review cycle has elapsed.

    Does nothing when governance is not declared or when ``approved_at`` /
    ``review_cycle`` are absent.

    Args:
        governance: Parsed governance for the contract being evaluated.
    """
    if not governance.declared:
        return
    if not governance.is_overdue():
        return
    elapsed = governance.days_since_approval()
    msg = (
        f"This contract was approved {elapsed} day(s) ago but its "
        f"review_cycle is '{governance.review_cycle}'. "
        "Consider re-approving the contract to reflect current data."
    )
    warnings.warn(msg, StaleContractWarning, stacklevel=4)
