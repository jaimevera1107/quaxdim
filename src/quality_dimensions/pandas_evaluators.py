"""Additional pandas evaluator kernels. No mutation, no implicit numeric/string coercion.

1=pass, 0=fail, -1=unknown, -2=excluded. Caller applies scope and aggregation.
"""
from __future__ import annotations
from decimal import Decimal
from numbers import Real
from typing import Any, Mapping
import math
import numpy as np
import pandas as pd

EXTRA_PANDAS_EVALUATORS = frozenset({
    "builtin.allowed_values", "builtin.value_range", "builtin.regex_fullmatch",
    "builtin.column_compare",
})


def _typed_key(x: Any):
    if isinstance(x, np.generic):
        x = x.item()
    if isinstance(x, (list, dict, set)):
        raise ValueError("Nested values require a dedicated equality comparator")
    if isinstance(x, float) and math.isnan(x):
        return ('float', 'NaN')
    return (type(x).__name__, x)


def _numeric(x: Any) -> bool:
    return (not isinstance(x, (bool, np.bool_)) and isinstance(x, (Real, Decimal))
            and math.isfinite(float(x)))


def evaluate_pandas_extra(name: str, df: pd.DataFrame, fields: Mapping[str, Any],
                          rule: Mapping[str, Any], selected: np.ndarray, missing_policy: str,
                          is_missing) -> tuple[np.ndarray, str]:
    names = rule['target']['fields']
    expectation = rule['expectation']
    output = np.full(len(df), -2, dtype=np.int8)
    policy_state = {"exclude": -2, "include_as_failure": 0,
                    "include_as_pass": 1, "indeterminate": -1}[missing_policy]
    missing = np.column_stack([is_missing(df[c], fields[c], df) for c in names]).any(axis=1)
    useful = selected & ~missing
    output[selected & missing] = policy_state
    if not useful.any():
        return output, 'record' if name == 'builtin.column_compare' else 'value'
    if name == 'builtin.allowed_values':
        vals = expectation.get('values')
        if not isinstance(vals, list) or not vals or len(vals) > 100_000:
            raise ValueError('Explicit, nonempty allowed-values list required (maximum 100000)')
        permitted = {_typed_key(v) for v in vals}
        ok = np.array([_typed_key(v) in permitted for v in df[names[0]].iloc[np.flatnonzero(useful)]], dtype=bool)
    elif name == 'builtin.value_range':
        op = expectation['operator']
        lo = expectation.get('lower') if op == 'between' else expectation.get('value')
        hi = expectation.get('upper') if op == 'between' else expectation.get('value')
        if op in ('min', 'between') and (not _numeric(lo) or not math.isfinite(float(lo))):
            raise ValueError('Finite numeric lower bound required')
        if op in ('max', 'between') and (not _numeric(hi) or not math.isfinite(float(hi))):
            raise ValueError('Finite numeric upper bound required')
        if op == 'between' and lo > hi:
            raise ValueError('Range lower bound greater than upper bound')
        values = df[names[0]].iloc[np.flatnonzero(useful)]
        # no coercion of numeric-looking identifiers or strings
        if op == 'min':
            ok = np.array([_numeric(v) and v >= lo for v in values], dtype=bool)
        elif op == 'max':
            ok = np.array([_numeric(v) and v <= hi for v in values], dtype=bool)
        else:
            ok = np.array([_numeric(v) and lo <= v <= hi for v in values], dtype=bool)
    elif name == 'builtin.regex_fullmatch':
        pattern = expectation.get('pattern')
        if not isinstance(pattern, str) or not pattern or len(pattern) > 512:
            raise ValueError('Regex pattern must be a nonempty string, at most 512 characters')
        try:
            import regex  # timeout on each distinct lexical value
        except ImportError as e:
            raise ValueError('Install quality-dimensions-starter[regex] to evaluate regex rules') from e
        try:
            compiled = regex.compile(pattern)
        except regex.error as e:
            raise ValueError(f'Invalid regex pattern: {e}') from e
        values = df[names[0]].iloc[np.flatnonzero(useful)]
        if len(values) > 500_000:
            raise ValueError('Regex evaluation exceeds limit (500000 values); partition the asset')
        cache: dict[str, bool] = {}
        answer = []
        for v in values:
            if not isinstance(v, str) or len(v) > 4096:
                answer.append(False)
                continue
            if v not in cache:
                try:
                    cache[v] = compiled.fullmatch(v, timeout=0.02) is not None
                except TimeoutError as e:
                    raise ValueError('Regex timed out; use a non-pathological pattern') from e
            answer.append(cache[v])
        ok = np.asarray(answer, dtype=bool)
    elif name == 'builtin.column_compare':
        op = expectation.get('extensions', {}).get('comparison')
        if op not in {'lt', 'lte', 'eq', 'ne', 'gte', 'gt'}:
            raise ValueError('comparison must be one of lt,lte,eq,ne,gte,gt')
        left = df[names[0]].iloc[np.flatnonzero(useful)]
        right = df[names[1]].iloc[np.flatnonzero(useful)]
        try:
            func = {'lt': lambda a,b: a < b, 'lte': lambda a,b: a <= b,
                    'eq': lambda a,b: a == b, 'ne': lambda a,b: a != b,
                    'gt': lambda a,b: a > b, 'gte': lambda a,b: a >= b}[op]
            # do not compare mixed incompatible types or rely on pandas coercion
            pair_results = []
            for a,b in zip(left, right):
                if type(a) is not type(b):
                    # numeric dtypes legitimately differ in scalar representation
                    if not (_numeric(a) and _numeric(b)):
                        pair_results.append(False)
                        continue
                try:
                    pair_results.append(bool(func(a,b)))
                except (TypeError, ValueError):
                    pair_results.append(False)
            ok = np.asarray(pair_results, dtype=bool)
        except (TypeError, ValueError) as e:
            raise ValueError('Column comparison could not be evaluated') from e
    else:
        raise ValueError(f'Unknown evaluator kernel: {name}')
    output[useful] = np.where(ok, 1, 0).astype(np.int8)
    return output, 'record' if name == 'builtin.column_compare' else 'value'
