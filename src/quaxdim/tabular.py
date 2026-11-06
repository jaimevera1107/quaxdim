"""Turn report results into a table, for pipelines and notebooks.

``to_dict``/``to_json`` are the canonical outputs and need no dependency.
``to_frame`` is the convenience on top of them: one row per rule, the counts
and rates as columns, ready for a CI gate (``frame.query("status == 'FAIL'")``)
or a quick comparison across runs.

pandas is imported lazily so the core install stays free of it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from ._lazy import LazyModule

if TYPE_CHECKING:
    import pandas as pd
else:
    pd = LazyModule("pandas", extra="pandas")

# Columns every result carries, in the order a reader wants them. Anything
# else a result type defines is appended after these, so the two engines can
# differ without the common prefix moving.
_LEADING_COLUMNS: tuple[str, ...] = (
    "rule_id",
    "asset_id",
    "metric_id",
    "dimension_ids",
    "status",
    "planner_status",
    "eligible_units",
    "passed_units",
    "failed_units",
    "indeterminate_units",
    "excluded_units",
    "measured_rate",
    "lower_rate",
    "upper_rate",
    "assessment_coverage",
)
_JOINED_SEQUENCES: frozenset[str] = frozenset({
    "dimension_ids",
    "reasons",
    "sample_failed_positions",
    "sample_indeterminate_positions",
})


def _cell(name: str, value: Any) -> Any:
    """Flatten one field into something a DataFrame column can hold."""
    if name in _JOINED_SEQUENCES and isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    if name == "threshold" and isinstance(value, Mapping):
        operator = value.get("operator", "")
        return f"{operator} {value.get('value')}".strip()
    return value


def results_to_frame(results: Sequence[Any]) -> pd.DataFrame:
    """Return one row per rule result, indexed by ``rule_id``.

    Args:
        results: Rule results of a report (``report.results``).

    Returns:
        A DataFrame whose first columns are the identifiers, the status and
        the counts and rates; the remaining fields of the result type follow.
        Sequence fields (``dimension_ids``, ``reasons``) are joined with
        commas and ``threshold`` is rendered as ``"gte 0.99"``, so the table
        survives a round trip through CSV.
    """
    rows = [r.to_dict() for r in results]
    if not rows:
        empty = pd.DataFrame(columns=list(_LEADING_COLUMNS))
        return empty.set_index("rule_id")
    ordered = [c for c in _LEADING_COLUMNS if c in rows[0]]
    ordered += [c for c in rows[0] if c not in ordered]
    cells = [
        {name: _cell(name, row.get(name)) for name in ordered} for row in rows
    ]
    frame = pd.DataFrame(cells, columns=ordered)
    return frame.set_index("rule_id")
