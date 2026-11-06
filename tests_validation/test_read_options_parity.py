"""Phase 2: declared CSV read options, malformed rows and engine parity.

Pins audit findings H6 (discovery and evaluation read CSV differently) and
H7 (engines disagree on the same data), plus the CSV edge cases listed in
the first review (BOM, CRLF, quoted line breaks, duplicate headers...).
"""

import csv
import gzip
import math

import pytest
from test_v08_optimized import _contract

from quaxdim import (
    DiscoveryBudget,
    build_reviewed_contract,
    discover_advanced,
    evaluate_optimized,
    plan_optimized,
)
from quaxdim.contract_validation import ContractError
from quaxdim.csv_dialect import (
    CsvDialectError,
    CsvReadOptions,
    MalformedCsvError,
    iter_records,
)

TEXT_ENGINES = ("csv_stream", "duckdb", "polars")


def _require(engine):
    if engine in {"duckdb", "polars"}:
        pytest.importorskip(engine)


def _counts(result):
    return (
        result.status,
        result.passed_units,
        result.failed_units,
        result.indeterminate_units,
        result.excluded_units,
    )


def _write(path, text, encoding="utf-8"):
    path.write_bytes(text.encode(encoding))
    return path


# --- Rules shared by the matrix --------------------------------------------

PRESENCE = (
    "present",
    "builtin.presence",
    ["CODE_ID"],
    {"operator": "not_null"},
    "include_as_failure",
    0.5,
    "completeness",
)
ALLOWED = (
    "allowed",
    "builtin.allowed_values",
    ["STATUS"],
    {"operator": "in_set", "values": ["A", "B"]},
    "indeterminate",
    0.5,
    "validity",
)
PATTERN = (
    "pattern",
    "builtin.regex_fullmatch",
    ["CODE_ID"],
    {"operator": "regex", "pattern": "[0-9]{3}"},
    "exclude",
    0.5,
    "validity",
)
UNIQUE = (
    "unique",
    "builtin.composite_uniqueness",
    ["CODE_ID"],
    {"operator": "composite_unique", "key_fields": ["CODE_ID"]},
    "indeterminate",
    0.5,
    "uniqueness",
)


def _matrix_contract(read_options=None):
    contract = _contract(PRESENCE, ALLOWED, PATTERN, UNIQUE)
    field = contract["assets"][0]["fields"][0]
    field["missing_policy"] = {"tokens": ["ND"]}
    if read_options is not None:
        contract["assets"][0]["read_options"] = read_options
    return contract


# Semicolon-separated, BOM, CRLF, a quoted line break, a quoted delimiter,
# a doubled quote, a quoted "", an unquoted empty field, a token, a
# duplicate key and a code that fails the pattern.
_MATRIX_CSV = (
    "\ufeffCODE_ID;STATUS\r\n"
    '001;"A"\r\n'
    '002;"B;x"\r\n'
    '002;"multi\r\nline"\r\n'
    '"";A\r\n'
    ";B\r\n"
    "ND;A\r\n"
    '01X;"say ""hi"""\r\n'
    "003;A\r\n"
)


@pytest.fixture
def matrix_csv(tmp_path):
    return _write(tmp_path / "matrix.csv", _MATRIX_CSV)


def test_matrix_expected_counts_on_reference_engine(matrix_csv):
    contract = _matrix_contract({"delimiter": ";"})
    report = evaluate_optimized(
        contract, data={"data": matrix_csv}, engine="csv_stream"
    )
    by_id = {r.rule_id: r for r in report.results}
    # 8 rows; "", empty and ND are missing -> 5 present keys.
    assert (by_id["present"].passed_units, by_id["present"].failed_units) == (
        5,
        3,
    )
    # STATUS: A, B;x (invalid), multi-line (invalid), A, B, A, say "hi"
    # (invalid), A.
    assert (by_id["allowed"].passed_units, by_id["allowed"].failed_units) == (
        5,
        3,
    )
    # Present codes 001, 002, 002, 01X, 003 -> 01X fails; 3 excluded.
    assert _counts(by_id["pattern"])[1:] == (4, 1, 0, 3)
    # 002 repeats (2 failures), 001/01X/003 unique, 3 unknown keys.
    assert _counts(by_id["unique"])[1:] == (3, 2, 3, 0)


@pytest.mark.parametrize("engine", ["duckdb", "polars"])
def test_matrix_parity_with_reference_engine(matrix_csv, engine):
    _require(engine)
    contract = _matrix_contract({"delimiter": ";"})
    reference = evaluate_optimized(
        contract, data={"data": matrix_csv}, engine="csv_stream"
    )
    other = evaluate_optimized(
        contract, data={"data": matrix_csv}, engine=engine
    )
    assert [_counts(r) for r in other.results] == [
        _counts(r) for r in reference.results
    ]


@pytest.mark.parametrize("engine", TEXT_ENGINES)
def test_gzip_parity(tmp_path, engine):
    _require(engine)
    path = tmp_path / "matrix.csv.gz"
    path.write_bytes(gzip.compress(_MATRIX_CSV.encode("utf-8")))
    contract = _matrix_contract({"delimiter": ";"})
    plain = _write(tmp_path / "plain.csv", _MATRIX_CSV)
    reference = evaluate_optimized(
        contract, data={"data": plain}, engine="csv_stream"
    )
    report = evaluate_optimized(contract, data={"data": path}, engine=engine)
    assert [_counts(r) for r in report.results] == [
        _counts(r) for r in reference.results
    ]


@pytest.mark.parametrize("engine", ["duckdb", "polars"])
def test_whitespace_policy_is_blocked_not_approximated(matrix_csv, engine):
    _require(engine)
    contract = _matrix_contract({"delimiter": ";"})
    contract["assets"][0]["fields"][0]["missing_policy"] = {
        "whitespace_only": True
    }
    plan = plan_optimized(contract, data={"data": matrix_csv}, engine=engine)
    statuses = {r.rule_id: r.status for r in plan.rules}
    assert statuses["present"] == "UNSUPPORTED"


# --- H6: discovery and evaluation share the dialect ------------------------


def test_h6_discovered_delimiter_travels_into_the_contract(tmp_path):
    rows = "".join(f"{i:04d}|A\n" for i in range(40))
    path = _write(tmp_path / "pipes.csv", "CODE_ID|STATUS\n" + rows)
    report = discover_advanced(
        path, csv_delimiter="|", budget=DiscoveryBudget(mode="deep")
    )
    presence = next(
        r
        for r in report.recommendations
        if r.kind == "rule"
        and r.evaluator == "builtin.presence"
        and r.fields == ("CODE_ID",)
    )
    contract, _ = build_reviewed_contract(
        report,
        approve=[presence.id],
        thresholds={presence.id: 1.0},
        purpose="pipe-delimited feed",
    )
    assert contract["assets"][0]["read_options"]["delimiter"] == "|"
    for engine in TEXT_ENGINES:
        if engine != "csv_stream":
            pytest.importorskip(engine)
        result = evaluate_optimized(
            contract, data={"dataset": path}, engine=engine
        ).results[0]
        # Before the fix evaluation read "CODE_ID|STATUS" as one column
        # and failed with a missing-field error.
        assert (result.status, result.passed_units) == ("PASS", 40)


# --- 2.2: malformed rows are structural errors on every engine -------------

_MALFORMED = {
    "short_row": "CODE_ID,STATUS\n001,A\n002\n003,B\n",
    "extra_field": "CODE_ID,STATUS\n001,A\n002,B,extra\n003,B\n",
    "duplicate_header": "CODE_ID,CODE_ID\n001,A\n",
    "empty_header_name": "CODE_ID,\n001,A\n",
}


@pytest.mark.parametrize("engine", TEXT_ENGINES)
@pytest.mark.parametrize("case", sorted(_MALFORMED))
def test_malformed_csv_is_an_error_not_missing_data(tmp_path, engine, case):
    _require(engine)
    path = _write(tmp_path / f"{case}.csv", _MALFORMED[case])
    contract = _contract(PRESENCE)
    result = evaluate_optimized(
        contract, data={"data": path}, engine=engine
    ).results[0]
    # Before the fix csv_stream read a short row's missing field as None and
    # counted it as an ordinary missing value.
    assert result.status == "ERROR"
    assert result.passed_units is None


def test_header_only_file_has_no_eligible_data(tmp_path):
    path = _write(tmp_path / "empty.csv", "CODE_ID,STATUS\n")
    result = evaluate_optimized(
        _contract(PRESENCE), data={"data": path}
    ).results[0]
    assert result.status == "NO_ELIGIBLE_DATA"


def test_zero_byte_file_is_an_error(tmp_path):
    path = _write(tmp_path / "zero.csv", "")
    result = evaluate_optimized(
        _contract(PRESENCE), data={"data": path}
    ).results[0]
    assert result.status == "ERROR"


def test_oversized_field_is_rejected_and_global_limit_restored(tmp_path):
    before = csv.field_size_limit()
    path = _write(tmp_path / "big.csv", "CODE_ID,STATUS\n" + "9" * 50 + ",A\n")
    contract = _contract(PRESENCE)
    contract["assets"][0]["read_options"] = {"max_field_chars": 10}
    result = evaluate_optimized(contract, data={"data": path}).results[0]
    assert result.status == "ERROR"
    assert csv.field_size_limit() == before


# --- Encodings and option validation ---------------------------------------


def test_latin1_is_read_by_stdlib_and_duckdb_and_blocked_on_polars(tmp_path):
    path = _write(
        tmp_path / "latin.csv",
        "CODE_ID,STATUS\n001,Bogotá\n",
        encoding="latin-1",
    )
    contract = _contract(PRESENCE)
    contract["assets"][0]["read_options"] = {"encoding": "latin-1"}
    assert evaluate_optimized(
        contract, data={"data": path}, engine="csv_stream"
    ).results[0].status == ("PASS")
    if pytest.importorskip("duckdb"):
        assert (
            evaluate_optimized(contract, data={"data": path}, engine="duckdb")
            .results[0]
            .status
            == "PASS"
        )
    pytest.importorskip("polars")
    plan = plan_optimized(contract, data={"data": path}, engine="polars")
    assert plan.rules[0].status == "UNSUPPORTED"
    assert "latin-1" in plan.rules[0].reason


def test_invalid_read_options_are_rejected(tmp_path):
    path = _write(tmp_path / "x.csv", "CODE_ID,STATUS\n001,A\n")
    contract = _contract(PRESENCE)
    contract["assets"][0]["read_options"] = {"delimiter": "||"}
    with pytest.raises(ContractError, match="schema violation"):
        plan_optimized(contract, data={"data": path})
    contract["assets"][0]["read_options"] = {
        "delimiter": '"',
        "quote_char": '"',
    }
    plan = plan_optimized(contract, data={"data": path})
    assert plan.rules[0].status == "INVALID_CONTRACT"
    contract["assets"][0]["read_options"] = {"encoding": "no-such-codec"}
    plan = plan_optimized(contract, data={"data": path})
    assert plan.rules[0].status == "INVALID_CONTRACT"


def test_read_options_on_non_csv_source_are_invalid(tmp_path):
    pl = pytest.importorskip("polars")
    frame = pl.DataFrame({"CODE_ID": ["001"], "STATUS": ["A"]}).lazy()
    contract = _contract(PRESENCE)
    contract["assets"][0]["read_options"] = {"delimiter": ";"}
    plan = plan_optimized(contract, data={"data": frame})
    assert plan.rules[0].status == "INVALID_CONTRACT"


def test_read_options_object_validation():
    with pytest.raises(CsvDialectError):
        CsvReadOptions(delimiter="\n")
    with pytest.raises(CsvDialectError):
        CsvReadOptions(header=False)
    with pytest.raises(CsvDialectError):
        CsvReadOptions(max_field_chars=0)
    with pytest.raises(CsvDialectError, match="Unknown read options"):
        CsvReadOptions.from_mapping({"sep": ";"}, path="x.csv")
    assert CsvReadOptions.from_mapping({}, path="x.tsv").delimiter == "\t"
    assert CsvReadOptions.from_mapping({}, path="x.tsv.gz").delimiter == "\t"


def test_iter_records_reports_row_numbers(tmp_path):
    path = _write(tmp_path / "bad.csv", "A,B\n1,2\n3\n")
    with pytest.raises(MalformedCsvError, match="row 2"):
        list(iter_records(path, CsvReadOptions()))


# --- Numeric parity on typed sources ----------------------------------------

_RANGE = (
    "range",
    "builtin.value_range",
    ["LOW"],
    {"operator": "min", "value": 0},
    "indeterminate",
    0.5,
    "validity",
)
_COMPARE = (
    "compare",
    "builtin.column_compare",
    ["LOW", "HIGH"],
    {"operator": "expression", "extensions": {"comparison": "lte"}},
    "indeterminate",
    0.5,
    "consistency",
)
_NUMERIC_VALUES = {
    "LOW": [1.0, -2.0, None, math.nan, math.inf, 0.0, 5.0],
    "HIGH": [2.0, -3.0, 1.0, 1.0, math.inf, math.nan, 4.0],
}


def _numeric_contract():
    contract = _contract(_RANGE, _COMPARE, fields=("LOW", "HIGH"))
    for field in contract["assets"][0]["fields"]:
        field.update(physical_type="DOUBLE", logical_type="float")
    for rule in contract["rules"]:
        if rule["id"] == "compare":
            rule["target"]["level"] = "record"
    for metric in contract["metrics"]:
        if metric["id"] == "m_compare":
            metric["target_levels"] = ["record"]
            metric["denominator_unit"] = "record"
    return contract


def test_numeric_parity_duckdb_parquet_and_polars(tmp_path):
    pytest.importorskip("duckdb")
    pl = pytest.importorskip("polars")
    path = tmp_path / "numbers.parquet"
    pl.DataFrame(_NUMERIC_VALUES).write_parquet(path)
    contract = _numeric_contract()
    duck = evaluate_optimized(contract, data={"data": path}, engine="duckdb")
    polars = evaluate_optimized(contract, data={"data": pl.scan_parquet(path)})
    assert [_counts(r) for r in duck.results] == [
        _counts(r) for r in polars.results
    ]
    by_id = {r.rule_id: r for r in duck.results}
    # LOW: 1, -2, null, NaN, inf, 0, 5 -> null and NaN unknown; -2 fails.
    assert _counts(by_id["range"])[1:] == (4, 1, 2, 0)
    # Pairs: (1,2) pass, (-2,-3) fail, (null,1) unknown, (NaN,1) unknown,
    # (inf,inf) pass, (0,NaN) unknown, (5,4) fail.
    assert _counts(by_id["compare"])[1:] == (2, 2, 3, 0)


# --- infinities_are_invalid ---------------------------------------------------


def _infinity_contract(invalid):
    contract = _numeric_contract()
    if invalid:
        for field in contract["assets"][0]["fields"]:
            field["missing_policy"] = {"infinities_are_invalid": True}
    return contract


@pytest.mark.parametrize("engine", ["duckdb", "polars"])
def test_infinities_are_compared_as_numbers_by_default(tmp_path, engine):
    _require(engine)
    pl = pytest.importorskip("polars")
    path = tmp_path / "inf.parquet"
    pl.DataFrame(
        {"LOW": [1.0, math.inf], "HIGH": [2.0, math.inf]}
    ).write_parquet(path)
    source = path if engine == "duckdb" else pl.scan_parquet(path)
    by_id = {
        r.rule_id: r
        for r in evaluate_optimized(
            _infinity_contract(False), data={"data": source}
        ).results
    }
    assert _counts(by_id["range"])[1:] == (2, 0, 0, 0)


@pytest.mark.parametrize("engine", ["duckdb", "polars"])
def test_infinities_fail_every_rule_when_declared_invalid(tmp_path, engine):
    _require(engine)
    pl = pytest.importorskip("polars")
    path = tmp_path / "inf.parquet"
    pl.DataFrame(
        {"LOW": [1.0, math.inf], "HIGH": [2.0, math.inf]}
    ).write_parquet(path)
    source = path if engine == "duckdb" else pl.scan_parquet(path)
    by_id = {
        r.rule_id: r
        for r in evaluate_optimized(
            _infinity_contract(True), data={"data": source}
        ).results
    }
    assert _counts(by_id["range"])[1:] == (1, 1, 0, 0)
    assert _counts(by_id["compare"])[1:] == (1, 1, 0, 0)


def test_infinity_policy_is_blocked_on_pandas_not_ignored():
    pytest.importorskip("pandas")
    from quaxdim import ApplicabilityPlanner

    plan = ApplicabilityPlanner().plan(_infinity_contract(True))
    assert {d.status for d in plan.decisions} == {"UNSUPPORTED"}
