"""Single auditable evaluator catalogue used by planners, execution and
suggestions.

Backend support is explicit: a registered concept is not assumed executable on
Spark.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Capability:
    """What one evaluator supports: levels, operators and field counts."""

    evaluator: str
    levels: frozenset[str]
    shapes: frozenset[str]
    operators: frozenset[str]
    min_fields: int
    max_fields: int | None
    evidence_required: bool = False
    temporal: bool = False
    scalar_only: bool = False


@dataclass(frozen=True)
class EvaluatorSpec:
    """A capability together with its backends and dimensions."""

    capability: Capability
    backends: frozenset[str]
    dimension_ids: tuple[str, ...]
    note: str = ""


def _spec(
    name: str,
    levels: Iterable[str],
    operators: Iterable[str],
    nmin: int,
    nmax: int | None,
    dims: Iterable[str],
    backends: Iterable[str] = ("pandas",),
    **kw: Any,
) -> EvaluatorSpec:
    return EvaluatorSpec(
        Capability(
            name,
            frozenset(levels),
            frozenset({"table"}),
            frozenset(operators),
            nmin,
            nmax,
            **kw,
        ),
        frozenset(backends),
        tuple(dims),
    )


REGISTRY: dict[str, EvaluatorSpec] = {
    x.capability.evaluator: x
    for x in (
        _spec(
            "builtin.presence",
            ("value", "field", "record"),
            ("not_null",),
            1,
            None,
            ("completeness",),
            ("pandas", "spark"),
        ),
        _spec(
            "builtin.composite_uniqueness",
            ("table", "entity"),
            ("composite_unique",),
            1,
            None,
            ("uniqueness",),
            ("pandas", "spark"),
            scalar_only=True,
        ),
        _spec(
            "builtin.max_delay",
            ("record", "dataset"),
            ("max_delay",),
            2,
            2,
            ("timeliness",),
            ("pandas", "spark"),
            temporal=True,
        ),
        _spec(
            "reference.lookup_temporal",
            ("value", "field"),
            ("reference_lookup",),
            1,
            1,
            ("validity",),
            ("pandas", "spark"),
            evidence_required=True,
            scalar_only=True,
        ),
        _spec(
            "builtin.allowed_values",
            ("value", "field"),
            ("in_set",),
            1,
            1,
            ("validity",),
            ("pandas", "spark"),
            scalar_only=True,
        ),
        _spec(
            "builtin.value_range",
            ("value", "field"),
            ("min", "max", "between"),
            1,
            1,
            ("validity",),
            ("pandas", "spark"),
            scalar_only=True,
        ),
        _spec(
            "builtin.regex_fullmatch",
            ("value", "field"),
            ("regex",),
            1,
            1,
            ("validity",),
            ("pandas",),
            scalar_only=True,
        ),
        _spec(
            "builtin.column_compare",
            ("record",),
            ("expression",),
            2,
            2,
            ("consistency",),
            ("pandas",),
            scalar_only=True,
        ),
    )
}


def capabilities() -> dict[str, Capability]:
    """Return the capability of every registered evaluator, by name."""
    return {k: x.capability for k, x in REGISTRY.items()}


def supports_backend(name: str, backend: str) -> bool:
    """Return whether evaluator ``name`` is implemented on ``backend``."""
    return backend in REGISTRY[name].backends if name in REGISTRY else False


def list_evaluators() -> list[dict[str, object]]:
    """List the registered evaluators with their supported backends."""
    return [
        {
            "id": name,
            "backends": sorted(s.backends),
            "operators": sorted(s.capability.operators),
            "levels": sorted(s.capability.levels),
            "dimensions": list(s.dimension_ids),
        }
        for name, s in sorted(REGISTRY.items())
    ]
