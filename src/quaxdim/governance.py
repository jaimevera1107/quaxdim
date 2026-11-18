"""Governance metadata attached to quality contracts.

The ``governance`` block in a contract is entirely optional. When it is
absent, every engine produces the same report it produced before this module
existed: same results, same order.

The block follows the DQ01 and DQ04 processes of Karkošková (2023):

- ``data_owner``, ``approved_by`` and ``approved_at`` record who is
  accountable for the contract and who approved it.
- ``criticality`` classifies the data (``regulatory_reporting`` is the most
  critical); a rule may override it for itself.
- ``review_cycle`` together with ``approved_at`` tells whether the contract
  is due for review.
- ``rules`` maps rule IDs to a steward and a recommended ``on_failure``
  action.

Nothing here changes a verdict. The metadata travels with the report so that
the systems which handle incidents (DQ03 to DQ05) do not have to re-parse
the contract.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .contract_validation import ContractError

__all__ = [
    "CRITICALITY_ORDER",
    "FAILURE_ACTIONS",
    "REVIEW_CYCLE_DAYS",
    "Governance",
    "GovernanceError",
    "RuleGovernance",
]

CRITICALITY_ORDER: tuple[str, ...] = (
    "regulatory_reporting",
    "operational",
    "analytical",
    "exploratory",
)
"""Criticality levels, most critical first."""

FAILURE_ACTIONS: frozenset[str] = frozenset(
    {"block_publication", "open_ticket", "notify", "record_only"}
)
"""Recommended actions on failure. QuaxDim records them and acts on none."""

REVIEW_CYCLE_DAYS: Mapping[str, int] = {
    "monthly": 31,
    "quarterly": 92,
    "biannual": 184,
    "annual": 366,
}
"""Upper bound, in days, of each named review cycle."""

_SCALAR_FIELDS: tuple[str, ...] = (
    "data_owner",
    "approved_by",
    "approved_at",
    "review_cycle",
    "criticality",
)


class GovernanceError(ContractError):
    """Raised when a contract's governance block is inconsistent.

    It is a :class:`~quaxdim.ContractError`, so code that already handles
    invalid contracts handles this one too.
    """


def _rank(criticality: str | None) -> int:
    """Sort key of a criticality: 0 is the most critical, unknown last."""
    if criticality in CRITICALITY_ORDER:
        return CRITICALITY_ORDER.index(criticality)
    return len(CRITICALITY_ORDER)


def _check_criticality(value: Any, where: str) -> str | None:
    if value is not None and value not in CRITICALITY_ORDER:
        raise GovernanceError(
            f"{where} must be one of {list(CRITICALITY_ORDER)}, got {value!r}"
        )
    return value


@dataclass(frozen=True)
class RuleGovernance:
    """Governance metadata of one rule.

    Attributes:
        steward: Team or person who answers for the rule.
        on_failure: Recommended action when the rule fails; one of
            :data:`FAILURE_ACTIONS`.
        criticality: Overrides the contract-level criticality for this rule.
    """

    steward: str | None = None
    on_failure: str | None = None
    criticality: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the fields that are set, as a JSON-ready dictionary."""
        return {
            name: value
            for name in ("steward", "on_failure", "criticality")
            if (value := getattr(self, name)) is not None
        }


@dataclass(frozen=True)
class Governance:
    """Contract-level governance metadata.

    Every field is optional. ``Governance()`` means "nothing declared" and
    :attr:`declared` is ``False``.
    """

    data_owner: str | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    review_cycle: str | None = None
    criticality: str | None = None
    rules: Mapping[str, RuleGovernance] = field(default_factory=dict)

    @property
    def declared(self) -> bool:
        """Whether the contract declared any governance at all."""
        return bool(self.rules) or any(
            getattr(self, name) is not None for name in _SCALAR_FIELDS
        )

    @property
    def criticality_rank(self) -> int:
        """Sort key of the contract criticality (0 = most critical)."""
        return _rank(self.criticality)

    def for_rule(self, rule_id: str) -> RuleGovernance:
        """Return the rule's metadata, or an empty one if it has none."""
        return self.rules.get(rule_id, RuleGovernance())

    def criticality_for(self, rule_id: str) -> str | None:
        """Return the rule's own criticality, else the contract's."""
        return self.for_rule(rule_id).criticality or self.criticality

    def criticality_rank_for(self, rule_id: str) -> int:
        """Sort key of the rule's effective criticality."""
        return _rank(self.criticality_for(rule_id))

    def days_since_approval(
        self, today: datetime.date | None = None
    ) -> int | None:
        """Days elapsed since ``approved_at``; ``None`` if it is not set."""
        if self.approved_at is None:
            return None
        approved = datetime.date.fromisoformat(self.approved_at)
        return ((today or datetime.date.today()) - approved).days

    def is_overdue(self, today: datetime.date | None = None) -> bool:
        """Whether the review cycle has elapsed since approval.

        ``False`` when either ``approved_at`` or ``review_cycle`` is absent:
        without both there is no deadline to miss.
        """
        if self.review_cycle is None:
            return False
        elapsed = self.days_since_approval(today)
        if elapsed is None:
            return False
        return elapsed > REVIEW_CYCLE_DAYS[self.review_cycle]

    def to_dict(self) -> dict[str, Any]:
        """Return the fields that are set, as a JSON-ready dictionary."""
        result: dict[str, Any] = {
            name: value
            for name in _SCALAR_FIELDS
            if (value := getattr(self, name)) is not None
        }
        if self.rules:
            result["rules"] = {
                rule_id: meta.to_dict()
                for rule_id, meta in self.rules.items()
            }
        return result

    @classmethod
    def from_contract(cls, contract: Mapping[str, Any]) -> Governance:
        """Parse and check the ``governance`` block of a contract.

        The JSON Schema already restricts the shape and the enumerations;
        this also checks what the schema cannot express: that ``approved_at``
        is a real calendar date and that every rule ID in
        ``governance.rules`` exists in the contract.

        Args:
            contract: A loaded contract.

        Returns:
            The parsed governance; empty when the block is absent.

        Raises:
            GovernanceError: If the block is inconsistent.
        """
        raw = contract.get("governance")
        if raw is None:
            return cls()
        if not isinstance(raw, Mapping):
            raise GovernanceError("governance must be a mapping")

        approved_at = raw.get("approved_at")
        if approved_at is not None:
            try:
                datetime.date.fromisoformat(str(approved_at))
            except ValueError as exc:
                raise GovernanceError(
                    "governance.approved_at must be a date (YYYY-MM-DD), "
                    f"got {approved_at!r}"
                ) from exc
            approved_at = str(approved_at)

        review_cycle = raw.get("review_cycle")
        if review_cycle is not None and review_cycle not in REVIEW_CYCLE_DAYS:
            raise GovernanceError(
                "governance.review_cycle must be one of "
                f"{list(REVIEW_CYCLE_DAYS)}, got {review_cycle!r}"
            )

        criticality = _check_criticality(
            raw.get("criticality"), "governance.criticality"
        )
        rules = _parse_rules(raw.get("rules") or {}, contract)
        return cls(
            data_owner=raw.get("data_owner"),
            approved_by=raw.get("approved_by"),
            approved_at=approved_at,
            review_cycle=review_cycle,
            criticality=criticality,
            rules=rules,
        )


def _parse_rules(
    raw_rules: Mapping[str, Any], contract: Mapping[str, Any]
) -> dict[str, RuleGovernance]:
    """Parse ``governance.rules`` and reject references to unknown rules."""
    known = {
        str(rule.get("id"))
        for rule in contract.get("rules", [])
        if isinstance(rule, Mapping)
    }
    orphans = sorted(str(r) for r in raw_rules if r not in known)
    if orphans:
        raise GovernanceError(
            f"governance.rules refers to rules not in the contract: {orphans}"
        )
    parsed: dict[str, RuleGovernance] = {}
    for rule_id, meta in raw_rules.items():
        where = f"governance.rules[{rule_id!r}]"
        if not isinstance(meta, Mapping):
            raise GovernanceError(f"{where} must be a mapping")
        on_failure = meta.get("on_failure")
        if on_failure is not None and on_failure not in FAILURE_ACTIONS:
            raise GovernanceError(
                f"{where}.on_failure must be one of "
                f"{sorted(FAILURE_ACTIONS)}, got {on_failure!r}"
            )
        parsed[str(rule_id)] = RuleGovernance(
            steward=meta.get("steward"),
            on_failure=on_failure,
            criticality=_check_criticality(
                meta.get("criticality"), f"{where}.criticality"
            ),
        )
    return parsed
