"""Safety/regression tests for advisory inference, explicit approval and new kernels."""

from __future__ import annotations
from copy import deepcopy
import json
import pandas as pd
import pytest

from quaxdim import discover, evaluate_contract, list_evaluators
from quaxdim.applicability import ApplicabilityPlanner


def _pick(report, evaluator=None, kind="rule", field=None):
    return next(
        r
        for r in report.recommendations
        if r.kind == kind
        and (evaluator is None or r.evaluator == evaluator)
        and (field is None or field in r.fields)
    )


def _run(data, *recommendations):
    r = discover(data)
    ids = [_pick(r, name, field=field).id for name, field in recommendations]
    contract = r.to_contract(
        approve=ids, purpose="test after explicit human approval"
    )
    result = evaluate_contract(contract, data={"dataset": data})
    return result, contract, r


def test_inference_does_not_mutate_and_no_rules_implicitly_execute():
    df = pd.DataFrame(
        {"MPO_ID": ["05001", "05002", "05003"], "VACANTES": [0, 5, -1]}
    )
    original = df.copy(deep=True)
    report = discover(df)
    pd.testing.assert_frame_equal(df, original)
    assert report.backend == "pandas"
    assert all(
        x.kind in {"rule", "context", "semantic", "missing_policy"}
        for x in report.recommendations
    )
    assert all(
        x["approval_required"] for x in report.to_dict()["recommendations"]
    )
    assert "rules" not in report.to_dict()
    with pytest.raises(ValueError, match="Approve"):
        report.to_contract(approve=[], purpose="x")


def test_leading_zeros_are_preserved_in_suggested_code_pattern_and_execution():
    df = pd.DataFrame({"MPO_ID": ["05001", "05002", "05003"]})
    report = discover(df)
    pattern = _pick(report, "builtin.regex_fullmatch")
    assert pattern.expectation == {"operator": "regex", "pattern": "[0-9]{5}"}
    assert "05001" not in report.to_json()
    c = report.to_contract(approve=[pattern.id], purpose="identificadores")
    assert c["assets"][0]["fields"][0]["logical_type"] == "string"
    assert (
        evaluate_contract(c, data={"dataset": df}).results[0].status == "PASS"
    )
    altered = df.copy()
    altered.loc[0, "MPO_ID"] = "0500"
    assert (
        evaluate_contract(c, data={"dataset": altered}).results[0].failed_units
        == 1
    )


def test_rule_requirements_are_hypotheses_and_missing_sentinels_require_approval():
    df = pd.DataFrame({"COD": ["001", "ND", "002", "003"]})
    r = discover(df)
    token = _pick(r, kind="missing_policy")
    assert token.expectation == {"tokens": ["ND"]}
    assert "ND" not in r.to_json()  # no raw category values by default
    pres = _pick(r, "builtin.presence")
    c = r.to_contract(
        approve=[token.id, pres.id], purpose="obligatoriedad aprobada"
    )
    assert c["assets"][0]["fields"][0]["missing_policy"]["tokens"] == ["ND"]
    result = evaluate_contract(c, data={"dataset": df})
    assert result.results[0].failed_units == 1


def test_categorical_suggestions_have_no_automatic_closed_world_assumption():
    df = pd.DataFrame({"ESTADO": ["A"] * 20 + ["B"] * 20})
    r = discover(df)
    allowed = _pick(r, "builtin.allowed_values")
    assert allowed.confidence == "low"
    assert '"values"' not in r.to_json()
    assert '"values"' in r.to_json(include_configuration=True)
    c = r.to_contract(approve=[allowed.id], purpose="dominio cerrado revisado")
    assert evaluate_contract(c, data={"dataset": df}).passed
    df2 = df.copy()
    df2.loc[0, "ESTADO"] = "C"
    assert (
        evaluate_contract(c, data={"dataset": df2}).results[0].failed_units
        == 1
    )


def test_column_compare_declares_order_and_null_policy_indeterminate():
    df = pd.DataFrame(
        {
            "FECHA_INICIO": pd.to_datetime(["2025-01-01", "2025-01-05", None]),
            "FECHA_FIN": pd.to_datetime(
                ["2025-01-04", "2025-01-03", "2025-01-07"]
            ),
        }
    )
    result, contract, report = _run(
        df, ("builtin.column_compare", "FECHA_INICIO")
    )
    rr = result.results[0]
    assert (rr.passed_units, rr.failed_units, rr.indeterminate_units) == (
        1,
        1,
        1,
    )
    assert rr.status == "FAIL"
    assert contract["metrics"][0]["null_policy"] == "indeterminate"


def test_numeric_range_does_not_coerce_strings_implicitly():
    df = pd.DataFrame({"TOTAL": [1, 0, -2]})
    result, c, r = _run(df, ("builtin.value_range", "TOTAL"))
    assert (
        result.results[0].passed_units,
        result.results[0].failed_units,
    ) == (2, 1)
    weird = pd.DataFrame({"TOTAL": [1, "2", -2]})
    # mixed values need a new contract, but existing numeric contract must not cast strings
    result2 = evaluate_contract(c, data={"dataset": weird})
    assert result2.results[0].failed_units == 2


def test_boolean_and_integer_domain_are_not_equal():
    df = pd.DataFrame({"STATUS": pd.Series([1, True, 1, True], dtype=object)})
    r = discover(df)
    presence = _pick(r, "builtin.presence")
    c = r.to_contract(approve=[presence.id], purpose="test")
    c["metrics"][0]["evaluator"] = "builtin.allowed_values"
    c["rules"][0]["expectation"] = {"operator": "in_set", "values": [1]}
    result = evaluate_contract(c, data={"dataset": df})
    assert (
        result.results[0].status == "UNSUPPORTED"
    )  # mixed semantic type must not be coerced
    from quaxdim.pandas_evaluators import evaluate_pandas_extra
    import numpy as np

    out, _ = evaluate_pandas_extra(
        "builtin.allowed_values",
        df,
        {"STATUS": {}},
        {
            "target": {"fields": ["STATUS"]},
            "expectation": {"operator": "in_set", "values": [1]},
        },
        np.ones(len(df), dtype=bool),
        "indeterminate",
        lambda series, *args: series.isna().to_numpy(dtype=bool),
    )
    assert (out == 0).sum() == 2


def test_regexp_timeout_safe_and_finite_length():
    df = pd.DataFrame({"MPO_ID": ["1" * 5] * 3})
    r = discover(df)
    regex = _pick(r, "builtin.regex_fullmatch")
    c = r.to_contract(approve=[regex.id], purpose="regex")
    c["rules"][0]["expectation"]["pattern"] = "(" + "x" * 600 + ")"
    p = ApplicabilityPlanner().plan(c, data={"dataset": df})
    assert p.decisions[0].status == "INVALID_CONTRACT"
    assert not evaluate_contract(c, data={"dataset": df}).passed


def test_locales_and_ambiguous_dates_are_not_guessed():
    df = pd.DataFrame({"FECHA": ["01/02/2025", "12/03/2025", "04/05/2025"]})
    r = discover(df)
    assert _pick(r, kind="semantic", field="FECHA").title.startswith(
        "Confirm the regional date format"
    )
    assert r.profile[0]["logical_type"] == "string"
    assert not any(
        x.evaluator == "builtin.max_delay" for x in r.recommendations
    )


def test_variant_salary_not_forcibly_converted_or_assumed_invalid():
    df = pd.DataFrame({"SUELDO_MES": [1200000, "1-2 SMLV", None]})
    r = discover(df)
    assert r.profile[0]["logical_type"] == "variant"
    assert _pick(r, kind="context", field="SUELDO_MES")
    assert df.iloc[0, 0] == 1200000


def test_all_null_and_empty_dataframe_no_fake_quality_guarantee():
    for frame in [
        pd.DataFrame({"X": [None, None]}),
        pd.DataFrame({"X": pd.Series(dtype="object")}),
    ]:
        r = discover(frame)
        assert r.profile[0]["logical_type"] == "unknown"
        prop = _pick(r, "builtin.presence")
        contract = r.to_contract(approve=[prop.id], purpose="test")
        rr = evaluate_contract(contract, data={"dataset": frame}).results[0]
        assert rr.status in {"FAIL", "NO_ELIGIBLE_DATA"}
        assert rr.status != "PASS"


def test_unknown_or_duplicate_approval_refused():
    r = discover(pd.DataFrame({"X": [1, 2]}))
    suggestion = r.recommendations[0]
    with pytest.raises(ValueError):
        r.to_contract(approve=["nonexistent"], purpose="x")
    with pytest.raises(ValueError):
        r.to_contract(approve=[suggestion.id, suggestion.id], purpose="x")
    with pytest.raises(ValueError):
        r.to_contract(approve=[suggestion.id], purpose="")


def test_registry_explicit_backend_coverage():
    index = {x["id"]: x for x in list_evaluators()}
    assert "spark" in index["builtin.value_range"]["backends"]
    assert "spark" in index["builtin.allowed_values"]["backends"]
    assert "spark" not in index["builtin.regex_fullmatch"]["backends"]
    assert "spark" not in index["builtin.column_compare"]["backends"]


def test_default_report_has_no_sensitive_category_values():
    df = pd.DataFrame(
        {"CATEGORY": ["secret_address_abc"] * 15 + ["secret_address_xyz"] * 15}
    )
    r = discover(df)
    result = r.to_json()
    assert (
        "secret_address_abc" not in result
        and "secret_address_xyz" not in result
    )
    assert '"values"' not in result


def test_new_preflight_rejects_invalid_expressions():
    df = pd.DataFrame({"TOTAL": [1, 2, 3]})
    r = discover(df)
    p = _pick(r, "builtin.value_range")
    c = r.to_contract(approve=[p.id], purpose="test")
    c["rules"][0]["expectation"]["value"] = "bad"
    plan = ApplicabilityPlanner().plan(c, data={"dataset": df})
    assert plan.decisions[0].status == "INVALID_CONTRACT"


def test_whitespace_missing_requires_and_obeys_approval():
    df = pd.DataFrame({"COD": ["001", "   ", "002", "ND"]})
    r = discover(df)
    policies = [x.id for x in r.recommendations if x.kind == "missing_policy"]
    assert len(policies) == 2
    presence = _pick(r, "builtin.presence")
    c = r.to_contract(
        approve=policies + [presence.id], purpose="control de ausencias"
    )
    assert c["assets"][0]["fields"][0]["missing_policy"]["whitespace_only"]
    assert c["assets"][0]["fields"][0]["missing_policy"]["tokens"] == ["ND"]
    report = evaluate_contract(c, data={"dataset": df})
    assert report.results[0].failed_units == 2
