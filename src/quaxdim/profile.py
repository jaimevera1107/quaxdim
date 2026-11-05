"""Non-prescriptive profiling. Profiles are observations, not quality judgments."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ._lazy import LazyModule

if TYPE_CHECKING:
    import pandas as pd
else:
    pd = LazyModule("pandas", extra="pandas")


def profile(data: pd.DataFrame, *, count_distinct: bool = True) -> pd.DataFrame:
    """Summarize physical characteristics without making business assumptions.

    Counts all-null columns and avoids silently treating identifiers as numbers.
    For very large datasets, count_distinct=False avoids expensive cardinalities.
    """
    if not isinstance(data, pd.DataFrame):
        raise TypeError("profile() requires pandas.DataFrame")
    n = len(data)
    rows = []
    for pos, column in enumerate(data.columns):
        s = data.iloc[:, pos]
        missing = int(s.isna().sum())
        rows.append({
            "column": str(column),
            "physical_type": str(s.dtype),
            "row_count": n,
            "missing": missing,
            "non_missing": n - missing,
            "completeness_observed": (n - missing) / n if n else None,
            "distinct_observed": int(s.nunique(dropna=True)) if count_distinct else None,
            "all_null": bool(missing == n),
            "semantic_type": None,  # must be declared by data owner/contract
        })
    return pd.DataFrame.from_records(rows)
