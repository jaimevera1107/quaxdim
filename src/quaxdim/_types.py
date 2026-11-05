"""Shared type aliases.

Optional engines (PySpark, Polars, DuckDB) are never imported at
type-checking time, so their objects are typed with documented aliases.
"""

from __future__ import annotations

from typing import Any, TypeAlias

#: A PySpark ``DataFrame`` (PySpark is an optional dependency).
SparkFrame: TypeAlias = Any
#: A PySpark ``Column`` expression.
SparkColumn: TypeAlias = Any
#: An optional engine module passed around lazily (``polars``, ``functions``).
EngineModule: TypeAlias = Any
