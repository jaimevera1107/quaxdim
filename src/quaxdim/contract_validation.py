"""Structural contract checks shared by every execution engine.

Every planner (pandas, native/optimized and Spark) must reject the same
malformed contracts. Keeping these checks in one dependency-free module
prevents engines from silently diverging, e.g. one engine rejecting duplicate
rule IDs while another overwrites one rule with the other in a dictionary.

The module imports neither pandas nor any optional engine.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from functools import lru_cache
from importlib.resources import files
from typing import Any

CONTRACT_SECTIONS: tuple[str, ...] = (
    "assets",
    "dimensions",
    "metrics",
    "rules",
    "references",
)
_SCHEMA_FILE = "quality-contract.schema.json"


class ContractError(ValueError):
    """Raised when a contract is structurally invalid."""


@lru_cache(maxsize=1)
def _load_schema() -> dict[str, Any]:
    """Load the bundled JSON Schema once per process."""
    text = files("quaxdim").joinpath("schemas").joinpath(_SCHEMA_FILE)
    schema: dict[str, Any] = json.loads(text.read_text(encoding="utf-8"))
    return schema


def validate_schema(contract: Mapping[str, Any]) -> None:
    """Validate ``contract`` against the bundled JSON Schema.

    Args:
        contract: Contract to validate.

    Raises:
        ContractError: If the schema cannot be loaded or is violated.
    """
    try:
        import jsonschema
    except ImportError as exc:
        raise ContractError(
            "JSON Schema validation requires jsonschema>=4.18"
        ) from exc
    try:
        schema = _load_schema()
    except (OSError, ValueError) as exc:
        raise ContractError(f"Cannot load contract schema: {exc}") from exc
    validator = jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.FormatChecker()
    )
    try:
        validator.validate(dict(contract))
    except jsonschema.ValidationError as exc:
        raise ContractError(
            f"Contract v0.2 schema violation at {list(exc.path)}: "
            f"{exc.message}"
        ) from exc


def duplicate_ids(items: Sequence[Mapping[str, Any]]) -> list[str]:
    """Return identifiers that appear more than once, in first-seen order."""
    seen: set[Any] = set()
    repeated: list[str] = []
    for item in items:
        identifier = item.get("id")
        if identifier in seen and str(identifier) not in repeated:
            repeated.append(str(identifier))
        seen.add(identifier)
    return repeated


def validate_unique_ids(contract: Mapping[str, Any]) -> None:
    """Reject repeated identifiers inside any contract section.

    Raises:
        ContractError: If a section contains a repeated identifier.
    """
    for section in CONTRACT_SECTIONS:
        repeated = duplicate_ids(contract.get(section, []))
        if repeated:
            raise ContractError(f"Duplicate IDs in {section}: {repeated}")


def validate_contract_structure(
    contract: Mapping[str, Any], *, check_schema: bool = True
) -> None:
    """Run the structural preflight common to every engine.

    Args:
        contract: Contract to validate.
        check_schema: Also validate against the bundled JSON Schema.

    Raises:
        ContractError: If the contract is structurally invalid.
    """
    if not isinstance(contract, Mapping):
        raise ContractError("Contract must be a mapping")
    if check_schema:
        validate_schema(contract)
    validate_unique_ids(contract)


def identity_key_problem(rule: Mapping[str, Any]) -> str | None:
    """Explain why a uniqueness rule's key declaration is inconsistent.

    The evaluated key (``target.fields``) must be exactly the declared key
    (``expectation.key_fields``). Otherwise a rule declaring uniqueness of
    ``ID`` could silently be evaluated on ``(ID, PERIOD)`` and pass.

    Args:
        rule: A ``builtin.composite_uniqueness`` rule.

    Returns:
        A human-readable problem, or ``None`` when the declaration is valid.
    """
    fields = list(rule.get("target", {}).get("fields", []))
    keys = rule.get("expectation", {}).get("key_fields")
    if not isinstance(keys, list):
        return "Uniqueness requires expectation.key_fields"
    if len(set(fields)) != len(fields):
        return "Target fields must not repeat"
    if keys != fields:
        return (
            "Target fields and declared key fields must match exactly "
            f"(target={fields}, key_fields={keys})"
        )
    return None


def unknown_dimension_ids(
    metric: Mapping[str, Any], dimensions: Mapping[str, Any]
) -> list[str]:
    """Return the metric's dimension identifiers that are not declared."""
    return [
        str(d) for d in metric.get("dimension_ids", []) if d not in dimensions
    ]
