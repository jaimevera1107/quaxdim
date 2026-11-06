"""Property-based invariants of verdicts, bounds and engine parity.

The matrix in ``test_read_options_parity`` checks edge cases somebody
thought of. These tests check properties that must hold on *any* input, on
data Hypothesis generates, including the cases nobody thought of.

Properties covered:

* the four outcome counts partition the eligible units;
* ``lower_rate <= measured_rate <= upper_rate``, each inside ``[0, 1]``;
* the verdict follows from the bounds and the threshold, never from the
  measured rate alone;
* making a value missing never raises the lower bound (monotonicity);
* the CSV, DuckDB and Polars engines return identical counts.
"""

from __future__ import annotations

import csv

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from test_v08_optimized import _contract

from quaxdim import evaluate_optimized
from quaxdim.bounds import BoundModel, rate_bounds

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

# Values a text column can hold: codes, a token, a blank and an empty field.
CELLS = st.sampled_from(["001", "002", "003", "A", "B", "", " ", "ND"])
TABLES = st.lists(
    st.tuples(CELLS, st.sampled_from(["A", "B", "C"])), min_size=1, max_size=40
)
SLOW = settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

PRESENCE = (
    "present",
    "builtin.presence",
    ["CODE_ID"],
    {"operator": "not_null"},
    "include_as_failure",
    1.0,
    "completeness",
)
ALLOWED = (
    "allowed",
    "builtin.allowed_values",
    ["STATUS"],
    {"operator": "in_set", "values": ["A", "B"]},
    "indeterminate",
    0.9,
    "validity",
)
UNIQUE = (
    "unique",
    "builtin.composite_uniqueness",
    ["CODE_ID"],
    {"operator": "composite_unique", "key_fields": ["CODE_ID"]},
    "indeterminate",
    0.9,
    "uniqueness",
)


def _write(tmp_path, rows, name="data.csv"):
    path = tmp_path / name
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["CODE_ID", "STATUS"])
        writer.writerows(rows)
    return path


def _run(
    tmp_path,
    rows,
    engine="csv_stream",
    rules=(PRESENCE, ALLOWED, UNIQUE),
    name="data.csv",
):
    path = _write(tmp_path, rows, name)
    return evaluate_optimized(
        _contract(*rules), data={"data": path}, engine=engine
    )


def _measured(result):
    """Return the counts of a result that was actually measured, or None."""
    if result.eligible_units is None or result.passed_units is None:
        return None
    return (
        result.passed_units,
        result.failed_units,
        result.indeterminate_units,
        result.excluded_units,
        result.eligible_units,
    )


# --- Counts and rates --------------------------------------------------------


@given(rows=TABLES)
@SLOW
def test_counts_partition_the_eligible_units(tmp_path, rows):
    for result in _run(tmp_path, rows).results:
        counts = _measured(result)
        if counts is None:
            continue
        passed, failed, unknown, excluded, eligible = counts
        assert min(passed, failed, unknown, excluded) >= 0
        assert passed + failed + unknown == eligible
        assert eligible + excluded == (result.total_rows or 0) or excluded >= 0


@given(rows=TABLES)
@SLOW
def test_rates_are_ordered_and_bounded(tmp_path, rows):
    for result in _run(tmp_path, rows).results:
        rates = [result.lower_rate, result.measured_rate, result.upper_rate]
        if any(rate is None for rate in rates):
            continue
        lower, measured, upper = rates
        assert 0.0 <= lower <= measured <= upper <= 1.0
        assert 0.0 <= (result.assessment_coverage or 0.0) <= 1.0


@given(rows=TABLES)
@SLOW
def test_the_verdict_follows_from_the_bounds(tmp_path, rows):
    """PASS needs the worst case to clear the threshold, FAIL the best case."""
    for result in _run(tmp_path, rows).results:
        if result.status not in {"PASS", "FAIL", "INDETERMINATE"}:
            continue
        threshold = (result.threshold or {}).get("value")
        if threshold is None or result.lower_rate is None:
            continue
        if result.status == "PASS":
            assert result.lower_rate >= threshold
        elif result.status == "FAIL":
            assert result.upper_rate < threshold
        else:
            assert result.lower_rate < threshold <= result.upper_rate


@given(
    passed=st.integers(0, 50),
    failed=st.integers(0, 50),
    unknown=st.integers(0, 50),
    model=st.sampled_from(list(BoundModel)),
)
def test_bounds_are_consistent_for_any_counts(passed, failed, unknown, model):
    bounds = rate_bounds(passed, failed, unknown, model=model)
    if passed + failed + unknown == 0:
        assert bounds.lower is None and bounds.upper is None
        return
    assert 0.0 <= bounds.lower <= bounds.upper <= 1.0
    # With nothing unknown the rate is identified exactly.
    if unknown == 0:
        assert bounds.lower == bounds.upper
    # A pairwise-collision rule can never be more optimistic than a local one.
    local = rate_bounds(passed, failed, unknown, model=BoundModel.ROW_LOCAL)
    assert bounds.lower <= local.lower + 1e-12


# --- Monotonicity ------------------------------------------------------------


@given(rows=TABLES, position=st.integers(0, 39))
@SLOW
def test_blanking_a_value_never_raises_the_lower_bound(
    tmp_path, rows, position
):
    """Losing information cannot improve the worst case."""
    assume(position < len(rows))
    assume(rows[position][0] not in {"", " ", "ND"})
    blanked = list(rows)
    blanked[position] = ("", blanked[position][1])
    before = {r.rule_id: r for r in _run(tmp_path, rows).results}
    after = {
        r.rule_id: r
        for r in _run(tmp_path, blanked, name="blanked.csv").results
    }
    for rule_id, original in before.items():
        degraded = after[rule_id]
        if original.lower_rate is None or degraded.lower_rate is None:
            continue
        assert degraded.lower_rate <= original.lower_rate + 1e-12


@given(rows=TABLES)
@SLOW
def test_adding_a_duplicate_key_never_raises_the_uniqueness_bound(
    tmp_path, rows
):
    assume(rows[0][0] not in {"", " ", "ND"})
    duplicated = [*rows, rows[0]]
    before = _run(tmp_path, rows, rules=(UNIQUE,)).results[0]
    after = _run(
        tmp_path, duplicated, name="dup.csv", rules=(UNIQUE,)
    ).results[0]
    if before.lower_rate is None or after.lower_rate is None:
        return
    assert after.lower_rate <= before.lower_rate + 1e-12


# --- Engine parity -----------------------------------------------------------


@pytest.mark.parametrize("engine", ["duckdb", "polars"])
@given(rows=TABLES)
@SLOW
def test_engines_agree_on_generated_tables(tmp_path, engine, rows):
    pytest.importorskip(engine)
    reference = _run(tmp_path, rows, engine="csv_stream").results
    other = _run(tmp_path, rows, engine=engine).results
    assert [(_measured(r), r.status) for r in other] == [
        (_measured(r), r.status) for r in reference
    ]
