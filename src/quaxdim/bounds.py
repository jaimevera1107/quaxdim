"""Partial-identification bounds for pass rates with unknown units.

When some units cannot be classified (missing or unparseable inputs), the
pass rate is only *partially identified*: every resolution of the unknown
units yields a rate inside ``[lower, upper]``. These are identification
bounds, not confidence intervals; they say nothing about sampling error.

The worst case depends on whether resolving one unknown unit can change the
outcome of *other* units:

* ``ROW_LOCAL``: each unit is judged on its own row (presence, ranges,
  allowed values, regex...). An unknown unit can at worst fail itself, so
  ``lower = passed / eligible``.
* ``PAIRWISE_COLLISION``: units are judged against each other (uniqueness).
  An unknown key may equal a currently unique key, turning *both* rows into
  failures. Each unknown can therefore remove at most one current pass, so
  ``lower = max(passed - unknown, 0) / eligible``. This is conservative for
  composite keys where only part of the key is missing.

In both models ``upper = (passed + unknown) / eligible``: unknowns resolve to
passes (new, distinct keys); resolving an unknown never repairs a failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BoundModel(str, Enum):
    """How an unknown unit can affect the outcome of other units."""

    ROW_LOCAL = "row_local"
    PAIRWISE_COLLISION = "pairwise_collision"


@dataclass(frozen=True)
class RateBounds:
    """Identification bounds of a pass rate.

    Attributes:
        lower: Worst-case pass rate, or ``None`` with no eligible units.
        upper: Best-case pass rate, or ``None`` with no eligible units.
    """

    lower: float | None
    upper: float | None


def rate_bounds(
    passed: int, failed: int, unknown: int, *, model: BoundModel
) -> RateBounds:
    """Compute worst/best-case pass rates when unknowns may take any value.

    Args:
        passed: Units known to pass.
        failed: Units known to fail.
        unknown: Units whose outcome cannot be determined.
        model: How an unknown unit can affect other units.

    Returns:
        The identification bounds; both ``None`` when nothing is eligible.

    Raises:
        ValueError: If a count is negative or the model is unsupported.
    """
    if min(passed, failed, unknown) < 0:
        raise ValueError("Counts must be non-negative")
    eligible = passed + failed + unknown
    if eligible == 0:
        return RateBounds(None, None)
    upper = (passed + unknown) / eligible
    if model is BoundModel.ROW_LOCAL:
        lower = passed / eligible
    elif model is BoundModel.PAIRWISE_COLLISION:
        lower = max(passed - unknown, 0) / eligible
    else:  # pragma: no cover - guarded by the Enum type
        raise ValueError(f"Unsupported bound model: {model}")
    return RateBounds(lower, upper)
