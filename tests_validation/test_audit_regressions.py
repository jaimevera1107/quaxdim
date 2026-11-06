"""Regression tests for findings of the external audits (v0.8.0rc1).

Each test names the finding it pins down (C1, H1, H5, H7, H2...). They must
fail on commit 8ad8313 and pass afterwards.
"""

import csv
import math

import pytest
from test_v08_optimized import _contract, _csv

from quaxdim import evaluate_optimized, plan_optimized
from quaxdim.bounds import BoundModel, rate_bounds
from quaxdim.contract_validation import ContractError

_UNIQUE_KEY_RULE = (
    "unique_code",
    "builtin.composite_uniqueness",
    ["CODE_ID"],
    {"operator": "composite_unique", "key_fields": ["CODE_ID"]},
    "indeterminate",
    0.6,
    "uniqueness",
)
_TEXT_ENGINES = ("csv_stream", "duckdb", "polars")


def _engine_available(engine):
    if engine == "duckdb":
        pytest.importorskip("duckdb")
    if engine == "polars":
        pytest.importorskip("polars")


# --- C1: non-local identification bounds ---------------------------------


def test_bounds_row_local_versus_pairwise_collision():
    local = rate_bounds(3, 0, 1, model=BoundModel.ROW_LOCAL)
    pairwise = rate_bounds(3, 0, 1, model=BoundModel.PAIRWISE_COLLISION)
    assert (local.lower, local.upper) == (0.75, 1.0)
    assert (pairwise.lower, pairwise.upper) == (0.5, 1.0)
    assert rate_bounds(0, 0, 0, model=BoundModel.ROW_LOCAL).lower is None
    # More unknowns than passes cannot push the bound below zero.
    assert rate_bounds(1, 0, 5, model=BoundModel.PAIRWISE_COLLISION).lower == 0
    with pytest.raises(ValueError):
        rate_bounds(-1, 0, 0, model=BoundModel.ROW_LOCAL)


@pytest.mark.parametrize("engine", _TEXT_ENGINES)
def test_c1_unknown_key_makes_uniqueness_indeterminate(tmp_path, engine):
    """Keys 001, 002, 003, "" with threshold 0.6 must be INDETERMINATE.

    The blank key may equal "001", giving 2/4 = 0.5 < 0.6 in the worst case.
    Before the fix every engine reported lower=0.75 and PASS.
    """
    _engine_available(engine)
    path = _csv(
        tmp_path,
        [("001", "A"), ("002", "A"), ("003", "A"), ("", "A")],
    )
    contract = _contract(_UNIQUE_KEY_RULE)
    report = evaluate_optimized(contract, data={"data": path}, engine=engine)
    result = report.results[0]
    assert (result.passed_units, result.indeterminate_units) == (3, 1)
    assert result.lower_rate == pytest.approx(0.5)
    assert result.upper_rate == pytest.approx(1.0)
    assert result.status == "INDETERMINATE"


def test_c1_pandas_engine_agrees():
    pd = pytest.importorskip("pandas")
    from quaxdim import evaluate_contract

    frame = pd.DataFrame(
        {"CODE_ID": ["001", "002", "003", ""], "STATUS": ["A"] * 4}
    )
    contract = _contract(_UNIQUE_KEY_RULE)
    result = evaluate_contract(contract, data={"data": frame}).results[0]
    assert result.lower_rate == pytest.approx(0.5)
    assert result.status == "INDETERMINATE"


# --- H1: shared structural preflight ---------------------------------------


def test_h1_duplicate_rule_ids_are_rejected_by_optimized_engine(tmp_path):
    path = _csv(tmp_path, [("001", "A")])
    contract = _contract(_UNIQUE_KEY_RULE)
    contract["rules"].append(dict(contract["rules"][0]))
    with pytest.raises(ContractError, match="Duplicate IDs in rules"):
        plan_optimized(contract, data={"data": path})
    with pytest.raises(ContractError, match="Duplicate IDs in rules"):
        evaluate_optimized(contract, data={"data": path})


def test_h1_schema_violations_are_rejected_by_optimized_engine(tmp_path):
    path = _csv(tmp_path, [("001", "A")])
    contract = _contract(_UNIQUE_KEY_RULE)
    del contract["evaluation"]
    with pytest.raises(ContractError, match="schema violation"):
        plan_optimized(contract, data={"data": path})


def test_h1_undeclared_dimension_is_invalid(tmp_path):
    path = _csv(tmp_path, [("001", "A")])
    contract = _contract(_UNIQUE_KEY_RULE)
    contract["metrics"][0]["dimension_ids"] = ["not_declared"]
    plan = plan_optimized(contract, data={"data": path})
    assert plan.rules[0].status == "INVALID_CONTRACT"


# --- H5: evaluated key must equal declared key ----------------------------


def test_h5_key_fields_must_match_target_fields(tmp_path):
    path = _csv(
        tmp_path,
        [("001", "A"), ("001", "B")],
    )
    rule = list(_UNIQUE_KEY_RULE)
    rule[2] = ["CODE_ID", "STATUS"]  # evaluated key
    rule[3] = {"operator": "composite_unique", "key_fields": ["CODE_ID"]}
    contract = _contract(tuple(rule))
    report = evaluate_optimized(contract, data={"data": path})
    # Before the fix, (CODE_ID, STATUS) was evaluated and the rule passed
    # although CODE_ID, the declared key, repeats.
    assert report.results[0].status == "INVALID_CONTRACT"
    assert "key fields" in report.results[0].reasons[0]


# --- H7 / 1.3: empty strings are read identically by every engine ---------


def _write_blank_variants(tmp_path):
    path = tmp_path / "blanks.csv"
    # Row 2 holds a quoted "" and row 3 an unquoted empty field: readers used
    # to disagree on both (DuckDB: NULL/NULL, Polars: ""/NULL, stdlib: "").
    path.write_text(
        'CODE_ID,STATUS\n001,A\n"",A\n,A\n', encoding="utf-8", newline=""
    )
    return path


_PRESENCE_RULE = (
    "present",
    "builtin.presence",
    ["CODE_ID"],
    {"operator": "not_null"},
    "include_as_failure",
    0.5,
    "completeness",
)


@pytest.mark.parametrize("engine", _TEXT_ENGINES)
def test_empty_strings_are_missing_by_default(tmp_path, engine):
    _engine_available(engine)
    path = _write_blank_variants(tmp_path)
    contract = _contract(_PRESENCE_RULE)
    result = evaluate_optimized(
        contract, data={"data": path}, engine=engine
    ).results[0]
    assert (result.passed_units, result.failed_units) == (1, 2)


@pytest.mark.parametrize("engine", _TEXT_ENGINES)
def test_empty_strings_can_be_declared_legitimate(tmp_path, engine):
    _engine_available(engine)
    path = _write_blank_variants(tmp_path)
    contract = _contract(_PRESENCE_RULE)
    contract["assets"][0]["fields"][0]["missing_policy"] = {
        "empty_string": False
    }
    result = evaluate_optimized(
        contract, data={"data": path}, engine=engine
    ).results[0]
    assert (result.passed_units, result.failed_units) == (3, 0)


# --- H2 / 1.3: NaN detection follows the real engine type -----------------

_MIN_ZERO_RULE = (
    "non_negative",
    "builtin.value_range",
    ["VALUE"],
    {"operator": "min", "value": 0},
    "indeterminate",
    1.0,
    "validity",
)


def _numeric_contract():
    contract = _contract(_MIN_ZERO_RULE, fields=("VALUE",))
    field = contract["assets"][0]["fields"][0]
    # Deliberately wrong physical declaration (integer instead of float):
    # the old code looked for "float"/"double" in it before checking NaN.
    field["logical_type"] = "float"
    field["physical_type"] = "INTEGER"
    return contract


def _assert_nan_is_unknown(result):
    assert (result.passed_units, result.indeterminate_units) == (2, 1)
    assert result.status == "INDETERMINATE"


def test_h2_polars_nan_is_missing_despite_wrong_physical_type():
    pl = pytest.importorskip("polars")
    frame = pl.DataFrame({"VALUE": [1.0, 2.0, math.nan]}).lazy()
    report = evaluate_optimized(_numeric_contract(), data={"data": frame})
    _assert_nan_is_unknown(report.results[0])


def test_h2_duckdb_nan_is_missing_despite_wrong_physical_type(tmp_path):
    pytest.importorskip("duckdb")
    pl = pytest.importorskip("polars")
    path = tmp_path / "values.parquet"
    pl.DataFrame({"VALUE": [1.0, 2.0, math.nan]}).write_parquet(path)
    report = evaluate_optimized(_numeric_contract(), data={"data": path})
    # Before the fix DuckDB ordered NaN above every number, so NaN >= 0
    # passed and the rule reported PASS.
    _assert_nan_is_unknown(report.results[0])


def test_h2_nan_can_be_declared_a_value():
    pl = pytest.importorskip("polars")
    frame = pl.DataFrame({"VALUE": [1.0, 2.0, math.nan]}).lazy()
    contract = _numeric_contract()
    contract["assets"][0]["fields"][0]["missing_policy"] = {
        "nan_is_missing": False
    }
    result = evaluate_optimized(contract, data={"data": frame}).results[0]
    assert result.indeterminate_units == 0


def test_fixture_writer_round_trip(tmp_path):
    """Guard the CSV helper used above: blanks survive the writer."""
    path = _csv(tmp_path, [("", "A")])
    with path.open(encoding="utf-8", newline="") as handle:
        assert list(csv.reader(handle))[1] == ["", "A"]
