import pandas as pd
import pytest
from quaxdim import Check, QualitySuite, list_dimensions, profile


def outcome(frame, **kwargs):
    return {x.id: x for x in QualitySuite(kwargs.pop("checks")).run(frame, **kwargs).results}


def test_catalog_30_book_plus_7_iso():
    rows = list_dimensions()
    assert len(rows) == 37
    assert len([x for x in rows if x["standard"].startswith("Mahanti")]) == 30
    assert {"cardinality", "coverage", "accessibility", "traceability"} <= {x["id"] for x in rows}


def test_nullable_required_and_nonmutating_sentinel():
    df = pd.DataFrame({"x": [1, None, -1, 3]})
    c = Check("r", "completeness", "required", ("x",), {"null_values": [-1]})
    r = outcome(df, checks=[c])["r"]
    assert r.failed == 2 and r.checked == 4 and r.pass_rate == 0.5
    assert df.loc[2, "x"] == -1


def test_duplicate_entity_scope():
    df = pd.DataFrame({"code": ["01", "01", "02"]})
    c = Check("u", "uniqueness", "unique", ("code",))
    r = outcome(df, checks=[c])["u"]
    assert r.failed == 2 and r.sample_failed_rows == (0, 1)


def test_zero_eligible_is_skip_not_pass():
    df = pd.DataFrame({"x": [None, None]})
    c = Check("a", "validity", "allowed", ("x",), {"values": [1]})
    r = outcome(df, checks=[c])["a"]
    assert r.status == "SKIP" and r.pass_rate is None


def test_missing_reference_is_error_not_pass():
    df = pd.DataFrame({"id": [1]})
    c = Check("fk", "integrity", "foreign_key", ("id",), {"reference": "not_there"})
    r = outcome(df, checks=[c])["fk"]
    assert r.status == "ERROR"


def test_coverage_uses_population_denominator():
    df = pd.DataFrame({"id": [1, 1, 3]})
    ref = pd.DataFrame({"key": [1, 2, 3, 3]})
    c = Check("cv", "coverage", "coverage", ("id",), {"reference": "expected", "reference_columns": ["key"]})
    r = outcome(df, checks=[c], references={"expected": ref})["cv"]
    assert r.status == "FAIL" and r.pass_rate == pytest.approx(2 / 3)
    assert r.checked == 3


def test_all_null_columns_profiled():
    df = pd.DataFrame({"a": [None, None], "b": ["x", "y"]})
    p = profile(df)
    assert bool(p.loc[0, "all_null"]) and p.loc[0, "completeness_observed"] == 0


def test_subjective_evidence_required():
    df = pd.DataFrame({"x": [0]})
    invalid = Check("i", "reputation", "assessment", params={"score": 0.99})
    valid = Check("j", "reputation", "assessment", params={"score": 0.7, "assessor": "owner", "source": "survey"}, min_pass_rate=0.6)
    r = outcome(df, checks=[invalid, valid])
    assert r["i"].status == "ERROR" and r["j"].status == "PASS"


def test_column_schema_and_metadata():
    df = pd.DataFrame({"a": [1]})
    checks = [Check("s", "data_specification", "schema_columns", params={"expected": ["a", "b"]}),
              Check("m", "data_specification", "metadata_fields", params={"required": ["owner", "version"]}, min_pass_rate=0.5)]
    r = outcome(df, checks=checks, metadata={"owner": "team"})
    assert r["s"].status == "FAIL" and r["m"].status == "PASS"


def test_no_eval_of_external_yaml_and_string_columns_rejected():
    with pytest.raises(ValueError):
        Check.from_dict({"id": "bad", "dimension": "validity", "rule": "regex", "columns": "name", "params": {"pattern": ".*"}})


def test_semantic_dtype_handles_mixed_and_null():
    df = pd.DataFrame({"code": ["001", 1, None]})
    c = Check("dtype", "validity", "dtype", ("code",), {"type": "string"})
    r = outcome(df, checks=[c])["dtype"]
    assert r.checked == 2 and r.failed == 1


def test_empty_population_skips():
    df = pd.DataFrame({"code": [1]})
    c = Check("coverage", "coverage", "coverage", ("code",), {"reference": "ref"})
    assert outcome(df, checks=[c], references={"ref": pd.DataFrame({"code": []})})["coverage"].status == "SKIP"


def test_report_pass_fail_closed():
    df = pd.DataFrame({"code": [1, 1]})
    c = Check("dup", "uniqueness", "unique", ("code",))
    report = QualitySuite([c]).run(df)
    assert not report.passed and report.summary().loc[0, "macro_pass_rate"] == 0


def test_dimension_inventory_not_evaluated_is_not_pass():
    df = pd.DataFrame({"v": [1]})
    report = QualitySuite([Check("r", "completeness", "required", ("v",))]).run(df)
    d = report.dimension_coverage().set_index("dimension")
    assert len(d) == 37
    assert d.loc["completeness", "status"] == "PASS"
    assert d.loc["objectivity", "status"] == "NOT_EVALUATED"


def test_explicit_non_applicability_requires_reason():
    with pytest.raises(ValueError):
        Check("na", "volatility", "assessment", applies=False)
    df = pd.DataFrame({"v": [3]})
    c = Check("na", "volatility", "assessment", applies=False,
              not_applicable_reason="Only one observation date")
    report = QualitySuite([c]).run(df)
    assert report.results[0].status == "NOT_APPLICABLE" and not report.passed


def test_coverage_accounting_and_compare_null_exclusion():
    df = pd.DataFrame({"id": [1, 3], "a": [5, 4], "b": [None, 5]})
    ref = pd.DataFrame({"id": [1, 2, 3]})
    checks = [Check("cov", "coverage", "coverage", ("id",), {"reference": "pop"}),
              Check("cmp", "consistency", "compare", ("a",), {"op": "le", "right_column": "b"})]
    r = outcome(df, checks=checks, references={"pop": ref})
    assert r["cov"].checked == 3 and r["cov"].passed == 2 and r["cov"].failed == 1
    assert r["cmp"].checked == 1 and r["cmp"].excluded == 1


def test_weighted_composite_is_explicit_and_fails_closed():
    df = pd.DataFrame({"a": [1, None, 3]})
    r = QualitySuite([Check("required", "completeness", "required", ("a",)),
                      Check("n", "data_specification", "row_count", params={"min": 1})]).run(df)
    assert r.weighted_score({"required": 0.75, "n": 0.25}) == pytest.approx(0.75)
    with pytest.raises(ValueError):
        r.weighted_score({"required": 1.0})


def test_uniqueness_within_condition_and_duplicate_dataframe_index():
    df = pd.DataFrame({"id": [1, 1, 1], "phase": ["active", "archive", "active"]},
                      index=[10, 10, 20])
    c = Check("u", "uniqueness", "unique", ("id",),
              when={"column": "phase", "op": "eq", "value": "active"})
    r = outcome(df, checks=[c])["u"]
    assert r.status == "FAIL" and r.checked == 2 and r.failed == 2 and r.excluded == 1


def test_object_string_with_numeric_sentinels_not_confused_with_bool():
    df = pd.DataFrame({"value": [False, 0, 1, "0", "N/A"]}, dtype="object")
    c = Check("missing", "completeness", "required", ("value",), {"null_values": [0, "N/A"]})
    r = outcome(df, checks=[c])["missing"]
    assert r.failed == 2  # Numeric 0 and N/A, but False and string "0" are not nulls.
