"""Missingness defaults shared by every engine.

Each engine translates a field's ``missing_policy`` into its own expressions.
The *defaults* and the type tests live here so that engines cannot drift:
an engine that forgets a default would report a different denominator for
the same data and the same contract.

Defaults (applied when the policy does not declare the key):

* ``empty_string``: ``True``. An empty string in a string field is treated
  as missing. Readers differ in how they surface empty CSV fields (DuckDB
  returns NULL, the standard library returns ``""``), so treating both as
  missing is the only default that every engine can honour identically.
  Declare ``empty_string: false`` to keep ``""`` as a legitimate value.
* ``nan_is_missing``: ``True``. NaN in a floating-point column is missing,
  whatever ``physical_type`` the contract declares. Detection uses the
  *actual* engine type, never the declaration, because a wrong declaration
  must not turn NaN into a measured value.

The module imports neither pandas nor any optional engine.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

EMPTY_STRING_IS_MISSING_DEFAULT = True
NAN_IS_MISSING_DEFAULT = True

# A value that never occurs in real CSV text. Readers that would otherwise
# map empty fields to NULL receive it as their only "null string", so that
# empty fields reach the missingness policy as ``""``.
NEVER_NULL_SENTINEL = "\x00quaxdim:never-null\x00"

_FLOAT_MARKERS: tuple[str, ...] = ("float", "double", "real")


def empty_string_is_missing(policy: Mapping[str, Any]) -> bool:
    """Return whether ``""`` counts as missing under ``policy``."""
    return bool(policy.get("empty_string", EMPTY_STRING_IS_MISSING_DEFAULT))


def nan_is_missing(policy: Mapping[str, Any]) -> bool:
    """Return whether NaN counts as missing under ``policy``."""
    return bool(policy.get("nan_is_missing", NAN_IS_MISSING_DEFAULT))


def is_float_type(type_name: str) -> bool:
    """Return whether an engine type name denotes a floating-point type.

    Args:
        type_name: Engine-reported type, e.g. ``DOUBLE`` (DuckDB),
            ``Float64`` (Polars) or ``double`` (Spark).
    """
    lowered = type_name.lower()
    return any(marker in lowered for marker in _FLOAT_MARKERS)
