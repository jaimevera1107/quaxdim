"""Attach governance metadata to results and order them by attention.

Internal module: the engines call it, users see its effect on the report.

Ordering rule, applied only when the contract declares governance (without
the block the report keeps the contract's order, exactly as before):

1. Results that need attention come before ``PASS`` and ``NOT_APPLICABLE``.
2. Among those, more critical data first: a ``regulatory_reporting`` rule
   that could not be measured outranks an ``analytical`` failure, because
   in regulatory reporting "could not measure it" is worse news than
   "it failed" on secondary data.
3. Within the same criticality: errors, then unknown outcomes
   (``INDETERMINATE`` and other unmeasured states), then ``FAIL``.
4. Ties, and all settled results, keep the contract's order.
"""

from __future__ import annotations

import dataclasses
import warnings
from collections.abc import Sequence
from typing import Any, TypeVar, cast

from .governance import Governance

__all__ = ["StaleContractWarning", "apply_governance", "warn_if_overdue"]

T = TypeVar("T")

_SETTLED: frozenset[str] = frozenset({"PASS", "NOT_APPLICABLE"})

# Lower is more urgent. Statuses not listed are treated as "not measured".
_STATUS_RANK: dict[str, int] = {
    "ERROR": 0,
    "INVALID_CONTRACT": 0,
    "INDETERMINATE": 1,
    "NEEDS_EVIDENCE": 1,
    "UNSUPPORTED": 1,
    "NO_ELIGIBLE_DATA": 1,
    "FAIL": 2,
}
_UNMEASURED_RANK = 1


class StaleContractWarning(UserWarning):
    """The contract's review cycle has elapsed since it was approved.

    A warning, never a failure: the verdicts are still valid measurements,
    but the rules they measure may no longer reflect what was agreed. Turn
    it into an error where a stale contract must stop a pipeline::

        warnings.filterwarnings("error", category=StaleContractWarning)
    """


def _attention_key(
    result: Any, position: int, governance: Governance
) -> tuple[int, int, int, int]:
    status = str(result.status)
    if status in _SETTLED:
        # Nothing to act on: keep the contract's order.
        return (1, 0, 0, position)
    return (
        0,
        governance.criticality_rank_for(str(result.rule_id)),
        _STATUS_RANK.get(status, _UNMEASURED_RANK),
        position,
    )


def apply_governance(
    results: Sequence[T], governance: Governance
) -> tuple[T, ...]:
    """Return the results with their governance metadata, in attention order.

    Each result gains ``steward`` and ``on_failure`` from
    ``governance.rules`` and ``criticality`` from the rule (falling back to
    the contract). Results are dataclasses and are copied, never mutated.

    Args:
        results: Rule results in contract order.
        governance: The contract's parsed governance.

    Returns:
        The enriched results. When nothing is declared they are returned
        unchanged and in the same order.
    """
    if not governance.declared:
        return tuple(results)
    enriched: list[T] = []
    for result in results:
        rule_id = str(cast(Any, result).rule_id)
        meta = governance.for_rule(rule_id)
        values = {
            "steward": meta.steward,
            "on_failure": meta.on_failure,
            "criticality": governance.criticality_for(rule_id),
        }
        names = {f.name for f in dataclasses.fields(cast(Any, result))}
        changes = {k: v for k, v in values.items() if k in names}
        enriched.append(
            cast(T, dataclasses.replace(cast(Any, result), **changes))
        )
    order = sorted(
        range(len(enriched)),
        key=lambda i: _attention_key(enriched[i], i, governance),
    )
    return tuple(enriched[i] for i in order)


def warn_if_overdue(governance: Governance) -> None:
    """Emit :class:`StaleContractWarning` if the review cycle has elapsed."""
    if not governance.is_overdue():
        return
    warnings.warn(
        f"The contract was approved {governance.days_since_approval()} days "
        f"ago and its review cycle is '{governance.review_cycle}'; review "
        "and re-approve it.",
        StaleContractWarning,
        stacklevel=3,
    )
