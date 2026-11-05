"""Regression tests for contract-v0.2 evaluation: correctness before breadth."""

from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import json

import pandas as pd
import pytest

from quaxdim.applicability import ApplicabilityPlanner
from quaxdim.execution import (
    ContractExecutor,
    EvaluationError,
    _failure_status,
    _predicates,
)

ROOT = Path(__file__).resolve().parents[1]


def contract():
    return json.loads(
        (ROOT / "contracts" / "quality_contract.json").read_text(
            encoding="utf-8"
        )
    )


def sample():
    return pd.DataFrame(
        {
            "YEAR": [2025, 2025, 2025],
            "QUARTER": [1, 1, 1],
            "MPO_ID": ["05001", "05001", "05002"],
            "OCU_ID": ["001", None, "003"],
            "SUELDO_MES": ["1-2", "2-3", "3-4"],
            "FECHA_EVENTO": pd.to_datetime(
                ["2025-02-01", "2025-03-01", "2025-04-01"]
            ),
            "FECHA_CAPTURA": pd.to_datetime(
                ["2025-02-04", "2025-03-04", "2025-04-04"]
            ),
        }
    )


def evidence(values=None):
    return {
        "ocupaciones_2025": {
            "values": values or ["001", "003"],
            "verified": True,
            "version": "2025",
        }
    }


def execute(c=None, df=None, ev=None):
    c = c if c is not None else contract()
    df = df if df is not None else sample()
    return ContractExecutor().run(
        c,
        data={"mercado_laboral": df},
        evidence=ev if ev is not None else evidence(),
    )


def results(c=None, df=None, ev=None):
    return {x.rule_id: x for x in execute(c, df, ev).results}


def check(c, name):
    return next(x for x in c["rules"] if x["id"] == name)


def metric(c, name):
    return next(x for x in c["metrics"] if x["id"] == name)


def field(c, name):
    return next(x for x in c["assets"][0]["fields"] if x["name"] == name)


def test_from_v03_contract_4_checks_are_truly_executed():
    r = results()
    assert r["OCU_ID_required"].status == "FAIL"
    assert (
        r["OCU_ID_required"].passed_units,
        r["OCU_ID_required"].failed_units,
    ) == (2, 1)
    assert r["OCU_ID_2025_catalog"].status == "PASS"
    assert r["OCU_ID_2025_catalog"].excluded_units == 1
    assert r["grain_unique"].status == "INDETERMINATE"
    assert r["grain_unique"].indeterminate_units == 1
    assert r["grain_unique"].measured_rate == 1
    assert r["grain_unique"].lower_rate == pytest.approx(2 / 3)
    assert r["capture_within_7_days"].status == "PASS"


def test_missing_reference_does_not_run_or_pass():
    r = results(ev={})["OCU_ID_2025_catalog"]
    assert (
        r.status == "NEEDS_EVIDENCE" and r.planner_status == "NEEDS_EVIDENCE"
    )
    assert r.measured_rate is None and r.passed_units is None


def test_reference_version_mismatch_no_pass():
    ev = evidence()
    ev["ocupaciones_2025"]["version"] = "2024"
    assert results(ev=ev)["OCU_ID_2025_catalog"].status == "NEEDS_EVIDENCE"


def test_empty_input_is_not_perfect():
    r = execute(df=sample().iloc[0:0])
    assert not r.passed
    assert all(x.status != "PASS" for x in r.results)
    assert r.results[0].status == "NO_ELIGIBLE_DATA"


def test_no_mutation_and_duplicate_index_independent_of_positions():
    df = sample()
    df.index = [2, 2, 1]
    original = df.copy(deep=True)
    r = execute(df=df)
    pd.testing.assert_frame_equal(df, original)
    assert list(r.results[0].sample_failed_positions) == [1]


def test_multiple_missing_tokens_and_lexical_codes_no_coercion():
    c = contract()
    df = sample()
    df["OCU_ID"] = ["  nd  ", "001", "003"]
    assert results(c, df)["OCU_ID_required"].failed_units == 1
    # The numeric 1 is NOT the lexical occupation code '001'.
    df["OCU_ID"] = [1, "001", "003"]
    r = results(c, df)["OCU_ID_2025_catalog"]
    assert r.failed_units == 1 and r.status == "FAIL"


def test_physical_missing_in_presence_even_if_metric_null_policy_exclude():
    c = contract()
    metric(c, "presence_rate")["null_policy"] = "exclude"
    assert results(c)["OCU_ID_required"].failed_units == 1


def test_duplicate_every_occurrence_fails_not_only_later_duplicates():
    df = sample()
    df["OCU_ID"] = ["001", "001", "003"]
    r = results(df=df)["grain_unique"]
    assert r.failed_units == 2 and r.passed_units == 1
    assert r.sample_failed_positions == (0, 1)
    assert r.status == "FAIL"


def test_composite_key_missing_indeterminate_vs_fail():
    c = contract()
    r = results(c)["grain_unique"]
    assert r.status == "INDETERMINATE"
    metric(c, "grain_uniqueness_rate")["null_policy"] = "include_as_failure"
    assert results(c)["grain_unique"].status == "FAIL"


def test_wrong_temporal_order_fails():
    df = sample()
    df.loc[0, "FECHA_CAPTURA"] = pd.Timestamp("2025-01-31")
    r = results(df=df)["capture_within_7_days"]
    assert r.failed_units == 1 and r.sample_failed_positions == (0,)


def test_late_arrival_fails():
    df = sample()
    df.loc[1, "FECHA_CAPTURA"] = pd.Timestamp("2025-04-15")
    r = results(df=df)["capture_within_7_days"]
    assert r.failed_units == 1 and r.passed_units == 2


def test_naive_instant_w_timezones_uses_elapsed_hours():
    c = contract()
    df = sample()
    ext = check(c, "capture_within_7_days")["expectation"]["extensions"]
    ext.update({"unit": "hours"})
    check(c, "capture_within_7_days")["expectation"]["value"] = 48
    # Local event 2025-02-01 00:00 Bogotá == UTC 05:00; arrival UTC 2025-02-02 04:00 (23h)
    df["FECHA_CAPTURA"] = pd.to_datetime(
        ["2025-02-02 04:00", "2025-03-02 04:00", "2025-04-02 04:00"]
    )
    r = results(c, df)["capture_within_7_days"]
    assert r.status == "PASS" and r.passed_units == 3


def test_calendar_days_use_event_local_zone_not_utc_days():
    c = contract()
    df = sample()
    df.loc[0, "FECHA_EVENTO"] = pd.Timestamp("2025-02-01 23:00")
    # 2025-02-02 08 UTC is Feb 2 03 Bogotá, one local calendar day later.
    df.loc[0, "FECHA_CAPTURA"] = pd.Timestamp("2025-02-02 08:00")
    check(c, "capture_within_7_days")["expectation"]["value"] = 1
    r = results(c, df)["capture_within_7_days"]
    assert r.passed_units == 1 and r.failed_units == 2


def test_temporal_catalog_entries_per_code_and_date():
    df = sample()
    df["OCU_ID"] = ["001", "001", "003"]
    ev = evidence(
        [
            {
                "value": "001",
                "valid_from": "2025-01-01",
                "valid_to": "2025-02-28",
            },
            {
                "value": "003",
                "valid_from": "2025-01-01",
                "valid_to": "2025-12-31",
            },
        ]
    )
    r = results(df=df, ev=ev)["OCU_ID_2025_catalog"]
    assert r.passed_units == 2 and r.failed_units == 1
    assert r.sample_failed_positions == (1,)


def test_temporal_catalog_without_clock_is_refused():
    c = contract()
    check(c, "OCU_ID_2025_catalog").pop("when_period")
    assert results(c)["OCU_ID_2025_catalog"].status == "NEEDS_CONTEXT"


def test_temporal_reference_without_clock_for_entries_errors_if_ref_unbounded():
    c = contract()
    check(c, "OCU_ID_2025_catalog").pop("when_period")
    c["references"][0].pop("valid_period")
    ev = evidence([{"value": "001", "valid_from": "2025-01-01"}])
    assert results(c, ev=ev)["OCU_ID_2025_catalog"].status == "ERROR"


def test_when_predicates_array_with_conjunction_filters_scope():
    c = contract()
    check(c, "OCU_ID_required")["when"] = [
        {"field": "MPO_ID", "op": "eq", "value": "05001"},
        {"field": "YEAR", "op": "gte", "value": 2025},
    ]
    r = results(c)["OCU_ID_required"]
    assert r.scoped_rows == 2 and r.excluded_units == 1 and r.failed_units == 1


def test_exception_row_out_of_scope_not_failure():
    c = contract()
    check(c, "OCU_ID_required")["exceptions"] = [
        {"field": "MPO_ID", "op": "eq", "value": "05001"}
    ]
    r = results(c)["OCU_ID_required"]
    assert r.scoped_rows == 1 and r.excluded_units == 2 and r.status == "PASS"


def test_exception_null_operand_indeterminate_not_excluded():
    c = contract()
    df = sample()
    df["MPO_ID"] = ["05001", None, "05002"]
    check(c, "OCU_ID_required")["exceptions"] = [
        {"field": "MPO_ID", "op": "eq", "value": "05001"}
    ]
    r = results(c, df)["OCU_ID_required"]
    assert r.indeterminate_units == 1 and r.excluded_units == 1
    assert (
        r.passed_units == 1 and r.status == "INDETERMINATE"
    )  # upper bound can meet .98
    assert r.sample_indeterminate_positions == (1,)


def test_unknown_threshold_bounds_can_still_pass_or_fail():
    assert (
        _failure_status(0.99, 1, {"operator": "gte", "value": 0.98}) == "PASS"
    )
    assert (
        _failure_status(0.3, 0.5, {"operator": "gte", "value": 0.9}) == "FAIL"
    )
    assert (
        _failure_status(0.7, 1, {"operator": "gte", "value": 0.9})
        == "INDETERMINATE"
    )
    assert _failure_status(1, 1, {"operator": "eq", "value": 1}) == "PASS"
    assert (
        _failure_status(0.7, 1, {"operator": "eq", "value": 1})
        == "INDETERMINATE"
    )
    assert (
        _failure_status(
            0.1, 0.2, {"operator": "between", "value": 0.3, "upper": 0.8}
        )
        == "FAIL"
    )
    assert (
        _failure_status(
            0.5, 0.6, {"operator": "between", "value": 0.3, "upper": 0.8}
        )
        == "PASS"
    )
    with pytest.raises(EvaluationError):
        _failure_status(
            0.1, 0.2, {"operator": "between", "value": 0.3, "upper": 0.1}
        )


def test_custom_null_policy_rejected_safely():
    c = contract()
    metric(c, "grain_uniqueness_rate")["null_policy"] = "custom"
    assert results(c)["grain_unique"].status == "ERROR"


def test_rule_dependency_failed_blocks_child():
    c = contract()
    check(c, "capture_within_7_days")["dependencies"] = ["OCU_ID_required"]
    assert results(c)["capture_within_7_days"].status == "BLOCKED_DEPENDENCY"


def test_disabled_rule_not_a_pass_and_does_not_execute():
    c = contract()
    check(c, "OCU_ID_required")["extensions"] = {
        "enabled": False,
        "reason": "Temporary exemption",
    }
    r = results(c)["OCU_ID_required"]
    assert r.status == "NOT_APPLICABLE" and r.measured_rate is None


def test_plan_supplied_must_match():
    c = contract()
    df = sample()
    ev = evidence()
    p = ApplicabilityPlanner().plan(
        c, data={"mercado_laboral": df}, evidence=ev
    )
    r = ContractExecutor().run(
        c, data={"mercado_laboral": df}, evidence=ev, plan=p
    )
    assert len(r.results) == 4
    with pytest.raises(EvaluationError, match="differs"):
        ContractExecutor().run(
            c, data={"mercado_laboral": df}, evidence={}, plan=p
        )


def test_json_report_roundtrip_no_values(tmp_path):
    r = execute()
    p = tmp_path / "report.json"
    r.to_json(p)
    result = json.loads(p.read_text(encoding="utf8"))
    assert len(result["results"]) == 4
    assert result["all_passed"] is False
    assert "Entre 1 y 2" not in p.read_text(encoding="utf8")
    assert len(r.contract_sha256) == 64
    assert not r.dimension_summary().empty


def test_no_implicit_score_or_doubled_dimensions():
    r = execute()
    assert not hasattr(r, "weighted_score")
    assert set(r.dimension_summary().dimension_id) == {
        "completeness",
        "uniqueness",
        "validity",
        "timeliness",
    }


def test_scoped_out_population_is_not_a_pass():
    c = contract()
    check(c, "OCU_ID_required")["when"] = [
        {"field": "YEAR", "op": "eq", "value": 2030}
    ]
    r = results(c)["OCU_ID_required"]
    assert r.status == "NO_ELIGIBLE_DATA" and r.measured_rate is None


def test_unsupported_shape_is_never_executed():
    c = contract()
    c["assets"][0]["shape"] = "graph"
    assert results(c)["OCU_ID_required"].status == "UNSUPPORTED"


def test_exception_with_predicate_three_valued_logic():
    df = pd.DataFrame({"a": pd.Series([1, None, 3]), "b": ["x", "y", "x"]})
    t, u = _predicates(
        df,
        [
            {"field": "a", "op": "eq", "value": 1},
            {"field": "b", "op": "eq", "value": "x"},
        ],
    )
    assert list(t) == [True, False, False]
    assert list(u) == [
        False,
        False,
        False,
    ]  # b='y' forces false even though a is NULL


def test_unknown_policy_fail_is_explicit_and_accounted():
    c = contract()
    metric(c, "grain_uniqueness_rate")["unknown_policy"] = "fail"
    r = results(c)["grain_unique"]
    assert r.failed_units == 1 and r.indeterminate_units == 0
    assert r.eligible_units == 3 and r.excluded_units == 0


def test_unknown_policy_exclude_is_explicit_and_accounted():
    c = contract()
    metric(c, "grain_uniqueness_rate")["unknown_policy"] = "exclude"
    r = results(c)["grain_unique"]
    assert r.passed_units == 2 and r.excluded_units == 1
    assert r.indeterminate_units == 0 and r.eligible_units == 2


def test_threshold_outside_unit_interval_returns_error_not_fail():
    c = contract()
    check(c, "OCU_ID_required")["threshold"]["value"] = 1.1
    assert results(c)["OCU_ID_required"].status == "ERROR"


def test_missing_reference_version_is_missing_evidence():
    ev = evidence()
    ev["ocupaciones_2025"].pop("version")
    assert results(ev=ev)["OCU_ID_2025_catalog"].status == "NEEDS_EVIDENCE"


def test_type_aware_boolean_vs_integer_unique_key():
    c = contract()
    df = sample()
    df["OCU_ID"] = pd.Series([True, 1, "003"], dtype=object)
    r = results(c, df)["grain_unique"]
    assert r.passed_units == 3 and r.failed_units == 0


def test_100_percent_failed_due_to_missing_or_unknown_does_not_pass():
    df = sample()
    df["OCU_ID"] = None
    r = results(df=df)["grain_unique"]
    assert r.status == "INDETERMINATE" and r.measured_rate is None
    assert r.indeterminate_units == 3
    assert results(df=df)["OCU_ID_required"].status == "FAIL"


def test_multiple_exceptions_are_or_not_and():
    c = contract()
    check(c, "OCU_ID_required")["exceptions"] = [
        {"field": "MPO_ID", "op": "eq", "value": "05001"},
        {"field": "OCU_ID", "op": "eq", "value": "003"},
    ]
    r = results(c)["OCU_ID_required"]
    assert r.status == "NO_ELIGIBLE_DATA" and r.excluded_units == 3


def test_three_valued_or_true_overrides_unknown():
    df = pd.DataFrame({"x": [None, 2], "y": ["yes", "no"]})
    valid, unknown = _predicates(
        df,
        [
            {"field": "x", "op": "eq", "value": 1},
            {"field": "y", "op": "eq", "value": "yes"},
        ],
        mode="any",
    )
    assert valid.tolist() == [True, False] and unknown.tolist() == [
        False,
        False,
    ]


def test_verified_must_be_boolean_true_not_nonempty_string():
    ev = evidence()
    ev["ocupaciones_2025"]["verified"] = "false"
    assert results(ev=ev)["OCU_ID_2025_catalog"].status == "NEEDS_EVIDENCE"


def test_nonfinite_delay_rejected_in_preflight():
    c = contract()
    check(c, "capture_within_7_days")["expectation"]["value"] = float("inf")
    assert results(c)["capture_within_7_days"].status == "INVALID_CONTRACT"
