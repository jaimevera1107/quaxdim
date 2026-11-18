"""Governance metadata attached to quality contracts.

The ``governance`` block in a contract is entirely optional.  When absent,
every public function behaves exactly as before this module was introduced.

Design follows the DQ01 / DQ04 processes from Karkošková (2023):
  - data_owner / approved_by / approved_at encode the accountability chain.
  - criticality classifies data elements (regulatory_reporting > operational >
    analytical > exploratory).
  - review_cycle + approved_at let callers detect stale contracts.
  - rules maps each rule_id to a RuleGovernance with a steward and an
    on_failure action.

Nothing in this module changes verdicts.  It enriches results so that
downstream consumers (dashboards, ticketing systems) can apply DQ03–DQ05
without reimplementing the parsing.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "CRITICALITY_ORDER",
    "FAILURE_ACTIONS",
    "REVIEW_CYCLE_DAYS",
    "GovernanceError",
    "RuleGovernance",
    "Governance",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CRITICALITY_ORDER: tuple[str, ...] = (
    "regulatory_reporting",
    "operational",
    "analytical",
    "exploratory",
)
"""Ordered from highest to lowest importance.

``CRITICALITY_ORDER.index(x)`` gives the sort key; lower index = higher
priority.  Callers that do not want to depend on the tuple directly should
use :attr:`Governance.criticality_rank`.
"""

FAILURE_ACTIONS: frozenset[str] = frozenset(
    {"block_publication", "open_ticket", "notify", "record_only"}
)
"""Allowed values for ``RuleGovernance.on_failure``.

QuaxDim records these values but does not act on them itself; the action is
the responsibility of the calling system.
"""

REVIEW_CYCLE_DAYS: Mapping[str, int] = {
    "monthly": 31,
    "quarterly": 92,
    "biannual": 184,
    "annual": 366,
}
"""Approximate number of days for each named review cycle."""


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GovernanceError(ValueError):
    """Raised when a governance block violates its schema constraints."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleGovernance:
    """Per-rule governance metadata.

    Args:
        steward: Identifier of the team or person responsible for this rule.
        on_failure: What the calling system should do when this rule fails.
            Must be one of :data:`FAILURE_ACTIONS` when given.
    """

    steward: str | None = None
    on_failure: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary of non-None fields."""
        result: dict[str, Any] = {}
        if self.steward is not None:
            result["steward"] = self.steward
        if self.on_failure is not None:
            result["on_failure"] = self.on_failure
        return result


@dataclass(frozen=True)
class Governance:
    """Contract-level governance metadata.

    All fields are optional.  An instance with all fields ``None`` (the
    default) is equivalent to "no governance declared" and
    :attr:`declared` returns ``False``.

    Args:
        data_owner: Name or identifier of the data owner.
        approved_by: Who approved this contract (name or username).
        approved_at: ISO-8601 date string of approval (``YYYY-MM-DD``).
        review_cycle: One of ``monthly``, ``quarterly``, ``biannual``,
            ``annual``.  Informative only; QuaxDim does not schedule reviews.
        criticality: One of :data:`CRITICALITY_ORDER`.
        rules: Mapping from rule IDs to their :class:`RuleGovernance`.
    """

    data_owner: str | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    review_cycle: str | None = None
    criticality: str | None = None
    rules: Mapping[str, RuleGovernance] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def criticality_rank(self) -> int:
        """Sort key for criticality: 0 = most critical, len = unknown.

        Unknown or absent criticality sorts after all known levels.
        """
        if self.criticality is None:
            return len(CRITICALITY_ORDER)
        try:
            return CRITICALITY_ORDER.index(self.criticality)
        except ValueError:
            return len(CRITICALITY_ORDER)

    @property
    def declared(self) -> bool:
        """``True`` when at least one governance field is set."""
        return any(
            [
                self.data_owner,
                self.approved_by,
                self.approved_at,
                self.review_cycle,
                self.criticality,
                self.rules,
            ]
        )

    # ------------------------------------------------------------------
    # Methods
    # ------------------------------------------------------------------

    def for_rule(self, rule_id: str) -> RuleGovernance:
        """Return the :class:`RuleGovernance` for *rule_id*, or empty."""
        return self.rules.get(rule_id, RuleGovernance())

    def days_since_approval(
        self, today: datetime.date | None = None
    ) -> int | None:
        """Number of calendar days since ``approved_at``, or ``None``.

        Returns ``None`` when ``approved_at`` is not set or cannot be parsed.
        """
        if not self.approved_at:
            return None
        try:
            approved = datetime.date.fromisoformat(self.approved_at)
        except ValueError:
            return None
        reference = today or datetime.date.today()
        return (reference - approved).days

    def is_overdue(self, today: datetime.date | None = None) -> bool:
        """``True`` when the review cycle has elapsed since approval.

        Returns ``False`` when either ``approved_at`` or ``review_cycle`` is
        absent, or when the cycle name is unrecognised.
        """
        if not self.review_cycle or not self.approved_at:
            return False
        allowed_days = REVIEW_CYCLE_DAYS.get(self.review_cycle)
        if allowed_days is None:
            return False
        elapsed = self.days_since_approval(today)
        if elapsed is None:
            return False
        return elapsed > allowed_days

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict of non-None / non-empty fields."""
        result: dict[str, Any] = {}
        scalar_attrs = (
            "data_owner", "approved_by", "approved_at",
            "review_cycle", "criticality",
        )
        for attr in scalar_attrs:
            value = getattr(self, attr)
            if value is not None:
                result[attr] = value
        if self.rules:
            result["rules"] = {
                rid: rg.to_dict() for rid, rg in self.rules.items()
            }
        return result

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_contract(cls, contract: Mapping[str, Any]) -> Governance:
        """Parse a governance block from a loaded contract dictionary.

        Validates all fields against :data:`CRITICALITY_ORDER` and
        :data:`FAILURE_ACTIONS`.  Raises :exc:`GovernanceError` on any
        violation.

        A contract without a ``governance`` key returns an empty
        :class:`Governance` instance with :attr:`declared` == ``False``.
        """
        raw = contract.get("governance")
        if not raw:
            return cls()

        # --- scalar fields ------------------------------------------------
        data_owner: str | None = raw.get("data_owner")
        approved_by: str | None = raw.get("approved_by")
        approved_at: str | None = raw.get("approved_at")

        review_cycle: str | None = raw.get("review_cycle")
        if review_cycle is not None and review_cycle not in REVIEW_CYCLE_DAYS:
            raise GovernanceError(
                f"governance.review_cycle must be one of "
                f"{sorted(REVIEW_CYCLE_DAYS)!r}, got {review_cycle!r}."
            )

        criticality: str | None = raw.get("criticality")
        if criticality is not None and criticality not in CRITICALITY_ORDER:
            raise GovernanceError(
                f"governance.criticality must be one of "
                f"{list(CRITICALITY_ORDER)!r}, got {criticality!r}."
            )

        if approved_at is not None:
            try:
                datetime.date.fromisoformat(approved_at)
            except ValueError as exc:
                raise GovernanceError(
                    f"governance.approved_at must be an ISO-8601 date "
                    f"(YYYY-MM-DD), got {approved_at!r}."
                ) from exc

        # --- per-rule metadata --------------------------------------------
        rules_raw: Mapping[str, Any] = raw.get("rules") or {}
        rules: dict[str, RuleGovernance] = {}
        for rule_id, rule_raw in rules_raw.items():
            on_failure: str | None = rule_raw.get("on_failure")
            if on_failure is not None and on_failure not in FAILURE_ACTIONS:
                raise GovernanceError(
                    f"governance.rules[{rule_id!r}].on_failure must be one of "
                    f"{sorted(FAILURE_ACTIONS)!r}, got {on_failure!r}."
                )
            rules[rule_id] = RuleGovernance(
                steward=rule_raw.get("steward"),
                on_failure=on_failure,
            )

        return cls(
            data_owner=data_owner,
            approved_by=approved_by,
            approved_at=approved_at,
            review_cycle=review_cycle,
            criticality=criticality,
            rules=rules,
        )
