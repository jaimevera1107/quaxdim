"""Planner regression tests: no quality status is inferred from readiness."""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import json
import pytest
import pandas as pd

from quality_dimensions.applicability import ApplicabilityPlanner, ContractError, load_contract, Capability

ROOT = Path(__file__).resolve().parents[1]


def sample_contract():
    return json.loads((ROOT / "contracts" / "contrato_calidad.json").read_text(encoding="utf-8"))


def sample_frame():
    return pd.DataFrame({
        "YEAR": [2025, 2025, 2025],
        "QUARTER": [1, 1, 1],
        "MPO_ID": ["05001", "05001", "05002"],
        "OCU_ID": ["001", None, "003"],
        "SUELDO_MES": ["Entre 1 y 2", "Entre 2 y 3", "Entre 3 y 4"],
        "FECHA_EVENTO": pd.to_datetime(["2025-02-01", "2025-03-01", "2025-04-01"]),
        "FECHA_CAPTURA": pd.to_datetime(["2025-02-04", "2025-03-04", "2025-04-04"]),
    })


def results(c, df=None, evidence=None):
    p = ApplicabilityPlanner().plan(c, data={"mercado_laboral": df if df is not None else sample_frame()}, evidence=evidence)
    return {x.rule_id:x for x in p.decisions}


def test_reference_example_ready_vs_missing_evidence():
    r = results(sample_contract())
    assert r["OCU_ID_required"].status == "READY"
    assert r["grain_unique"].status == "READY"
    assert r["capture_within_7_days"].status == "READY"
    assert r["OCU_ID_2025_catalog"].status == "NEEDS_EVIDENCE"
    assert "REFERENCE_UNRESOLVED" in [x.code for x in r["OCU_ID_2025_catalog"].reasons]


def test_reference_not_verified_even_when_present():
    r = results(sample_contract(), evidence={"ocupaciones_2025": {"values": ["001", "003"]}})
    assert r["OCU_ID_2025_catalog"].status == "NEEDS_EVIDENCE"
    assert "REFERENCE_UNVERIFIED" in [x.code for x in r["OCU_ID_2025_catalog"].reasons]


def test_reference_verified_can_be_ready():
    r = results(sample_contract(), evidence={"ocupaciones_2025": {"values": ["001", "003"], "verified":True, "version":"2025"}})
    assert r["OCU_ID_2025_catalog"].status == "READY"


def test_reference_version_mismatch_not_ready():
    r = results(sample_contract(), evidence={"ocupaciones_2025": {"values": ["001"], "verified":True, "version":"2024"}})
    assert r["OCU_ID_2025_catalog"].status == "NEEDS_EVIDENCE"


def test_missing_frame_not_a_pass():
    p = ApplicabilityPlanner().plan(sample_contract())
    assert set(p.summary()) == {"NEEDS_DATA", "NEEDS_EVIDENCE"} or all(x.status != "READY" for x in p.decisions)
    assert all(x.status != "READY" for x in p.decisions)


def test_missing_schema_column_needs_context():
    c=sample_contract(); c["assets"][0]["fields"]=[x for x in c["assets"][0]["fields"] if x["name"] != "OCU_ID"]
    assert results(c)["OCU_ID_required"].status == "NEEDS_CONTEXT"


def test_missing_runtime_column_needs_context():
    assert results(sample_contract(), df=sample_frame().drop(columns="MPO_ID"))["grain_unique"].status == "NEEDS_CONTEXT"


def test_duplicate_runtime_column_labels_raise():
    df=sample_frame();df.columns=[*df.columns[:-1],df.columns[-2]]
    with pytest.raises(ContractError, match="Repeated column labels"):
        results(sample_contract(), df=df)


def test_duplicate_ids_rejected_even_if_schema_accepts():
    c=sample_contract();c["rules"].append(deepcopy(c["rules"][0]))
    with pytest.raises(ContractError, match="Duplicate IDs"):
        results(c)


def test_unsupported_evaluator_is_not_ready():
    c=sample_contract();c["metrics"][0]["evaluator"]="builtin.unregistered"
    assert results(c)["OCU_ID_required"].status == "UNSUPPORTED"


def test_unsupported_operator_is_not_ready():
    c=sample_contract();c["rules"][0]["expectation"]["operator"]="regex"
    assert results(c)["OCU_ID_required"].status == "UNSUPPORTED"


def test_wrong_target_level_is_invalid():
    c=sample_contract();c["rules"][0]["target"]["level"]="table"
    assert results(c)["OCU_ID_required"].status == "INVALID_CONTRACT"


def test_no_rows_is_not_100_percent_quality():
    r = results(sample_contract(),df=sample_frame().iloc[0:0])
    assert r["OCU_ID_required"].status == "NO_ELIGIBLE_DATA"


def test_temporal_out_of_scope_never_passes():
    df=sample_frame();df["FECHA_EVENTO"] = pd.to_datetime(["2024-01-01"]*len(df))
    assert results(sample_contract(),df=df)["OCU_ID_2025_catalog"].status in {"NO_ELIGIBLE_DATA","NEEDS_EVIDENCE"}


def test_ambiguous_date_strings_not_guessed():
    df=sample_frame();df["FECHA_EVENTO"]=["01/02/2025", "03/04/2025", "05/06/2025"]
    r=results(sample_contract(),df=df)
    assert r["OCU_ID_2025_catalog"].status == "AMBIGUOUS"
    assert r["capture_within_7_days"].status == "AMBIGUOUS"


def test_explicit_date_format_is_permitted():
    c=sample_contract();df=sample_frame()
    df["FECHA_EVENTO"]=pd.Series(["01/02/2025", "03/04/2025", "05/06/2025"])
    for f in c["assets"][0]["fields"]:
        if f["name"]=="FECHA_EVENTO":
            f["logical_type"]="local_datetime"
            f["representation"]={"date_patterns":["%d/%m/%Y"],"timezone":"America/Bogota"}
    r=results(c,df=df)
    assert r["OCU_ID_2025_catalog"].status == "NEEDS_EVIDENCE"
    assert r["capture_within_7_days"].status == "READY"


def test_periodic_variant_overlap_same_priority_is_ambiguous():
    c=sample_contract(); f=next(x for x in c["assets"][0]["fields"] if x["name"]=="SUELDO_MES")
    f["variants"].append(deepcopy(f["variants"][-1]));f["variants"][-1]["id"]="duplicate_recent"
    # direct field rule for salary to activate variant matching
    c["rules"][0]["target"]["fields"]=["SUELDO_MES"]
    r=results(c)
    assert r["OCU_ID_required"].status == "AMBIGUOUS"


def test_periodic_variant_outside_all_ranges_is_ambiguous():
    c=sample_contract();c["rules"][0]["target"]["fields"]=["SUELDO_MES"]
    df=sample_frame(); df["FECHA_EVENTO"]=pd.to_datetime(["2027-02-01"]*3)
    assert results(c,df=df)["OCU_ID_required"].status == "AMBIGUOUS"


def test_null_time_does_not_silently_exclude():
    df=sample_frame();df.loc[0,"FECHA_EVENTO"]=pd.NaT
    assert results(sample_contract(),df=df)["OCU_ID_2025_catalog"].status == "AMBIGUOUS"


def test_custom_invalid_dependency():
    c=sample_contract();c["rules"][0]["dependencies"]=["nonexistent"]
    assert results(c)["OCU_ID_required"].status == "INVALID_CONTRACT"


def test_cycle_invalid_both_rules():
    c=sample_contract();c["rules"][0]["dependencies"]=["grain_unique"]
    c["rules"][2]["dependencies"]=["OCU_ID_required"]
    r=results(c)
    assert r["OCU_ID_required"].status == "INVALID_CONTRACT"
    assert r["grain_unique"].status == "INVALID_CONTRACT"


def test_dependent_rule_blocked_by_missing_reference():
    c=sample_contract(); c["rules"][0]["dependencies"]=["OCU_ID_2025_catalog"]
    assert results(c)["OCU_ID_required"].status == "BLOCKED_DEPENDENCY"


def test_suggestions_never_become_automatic_controls():
    p=ApplicabilityPlanner().plan(sample_contract(),data={"mercado_laboral":sample_frame()}, suggest=True)
    suggested=[x for x in p.decisions if x.source=="suggestion"]
    assert suggested and all(x.status=="RECOMMENDED" for x in suggested)


def test_schema_violation_not_silenced():
    c=sample_contract();c["contract_version"]=33
    with pytest.raises(ContractError,match="schema violation"):
        results(c)


def test_inputs_untouched():
    c=sample_contract();df=sample_frame();before=df.copy(deep=True)
    results(c,df=df)
    pd.testing.assert_frame_equal(df,before)


def test_json_report_roundtrip(tmp_path):
    p=ApplicabilityPlanner().plan(sample_contract(),data={"mercado_laboral":sample_frame()})
    target=tmp_path/'plan.json';p.to_json(target)
    assert json.loads(target.read_text(encoding="utf-8"))["summary"]==p.summary()
    assert len(p.to_frame())==4


def test_load_contract_formats():
    assert load_contract(ROOT/'contracts'/'contrato_calidad.yaml')==load_contract(ROOT/'contracts'/'contrato_calidad.json')


def test_unregistered_shape_honored():
    c=sample_contract();c["assets"][0]["shape"]="graph"
    assert results(c)["OCU_ID_required"].status=="UNSUPPORTED"


def test_composite_key_mismatch_is_invalid():
    c=sample_contract();c["rules"][2]["expectation"]["key_fields"]=["YEAR"]
    assert results(c)["grain_unique"].status == "INVALID_CONTRACT"


def test_explicit_exclusion_requires_justification():
    c=sample_contract();c["rules"][0]["extensions"]={"enabled":False, "reason":"This field is intentionally optional in a given use"}
    assert results(c)["OCU_ID_required"].status=="NOT_APPLICABLE"
    c["rules"][0]["extensions"]={"enabled":False}
    assert results(c)["OCU_ID_required"].status=="INVALID_CONTRACT"


def test_resource_budget_detected_not_silently_skipped():
    c=sample_contract()
    large=pd.concat([sample_frame()]*4000, ignore_index=True)
    # Different pandas/string backends may store the same records at different
    # densities. Ensure the frame actually exceeds the contract's integer limit.
    c["evaluation"]["max_memory_mb"] = 1
    while large.memory_usage(index=True, deep=True).sum() <= 1024 ** 2:
        large = pd.concat([large, large], ignore_index=True)
    r=results(c,df=large)
    assert r["OCU_ID_required"].status=="BLOCKED_RESOURCE"


def test_same_field_history_valid_when_ranges_cover_current_rows():
    c=sample_contract();c["rules"][0]["target"]["fields"]=["SUELDO_MES"]
    assert results(c)["OCU_ID_required"].status=="READY"


def test_priority_resolves_variant_overlap():
    c=sample_contract();f=next(x for x in c["assets"][0]["fields"] if x["name"]=="SUELDO_MES")
    f["variants"].append(deepcopy(f["variants"][-1])); f["variants"][-1]["id"]="recent_priority_two"
    f["variants"][-1]["priority"]=2
    c["rules"][0]["target"]["fields"]=["SUELDO_MES"]
    assert results(c)["OCU_ID_required"].status=="READY"


def test_outside_period_zero_eligible_even_when_reference_missing():
    df=sample_frame();df["FECHA_EVENTO"]=pd.to_datetime(["2024-02-01"]*len(df))
    assert results(sample_contract(),df=df)["OCU_ID_2025_catalog"].status=="NO_ELIGIBLE_DATA"


def test_source_code_preserves_leading_zeroes():
    df=sample_frame()
    assert df.loc[0,"MPO_ID"]=="05001"
    assert results(sample_contract(),df=df)["grain_unique"].status=="READY"


def test_present_null_marker_never_guessed_as_null():
    df=sample_frame();df["OCU_ID"]=["N/A", "NO REPORTA", "001"]
    assert results(sample_contract(),df=df)["OCU_ID_required"].status=="READY"
    # Only an execution engine using the declared missing policy may mark it missing.


def test_cli_plan_json(tmp_path):
    from quality_dimensions.cli import main
    frame=sample_frame().drop(columns=["FECHA_EVENTO", "FECHA_CAPTURA"])
    # No timestamps -> NEEDS_CONTEXT, not a successful evaluation.
    path=tmp_path/'data.csv';out=tmp_path/'plan.json'
    frame.to_csv(path,index=False)
    rc=main(["plan", str(path), "--contract", str(ROOT/'contracts'/'contrato_calidad.yaml'),
             "--asset", "mercado_laboral", "--output", str(out)])
    assert rc == 1
    assert json.loads(out.read_text(encoding="utf-8"))["contract_version"]=="0.2.0"


def test_planner_cannot_claim_all_37_dimensions_measured():
    p=ApplicabilityPlanner().plan(sample_contract(),data={"mercado_laboral":sample_frame()})
    assert p.summary()=={"NEEDS_EVIDENCE":1,"READY":3}
    assert all(x.status not in {"PASS", "FAIL"} for x in p.decisions)


def test_undeclared_time_field_in_filter_is_ambiguous():
    c=sample_contract();c["assets"][0]["fields"]=[x for x in c["assets"][0]["fields"] if x["name"]!="FECHA_EVENTO"]
    assert results(c)["OCU_ID_2025_catalog"].status=="AMBIGUOUS"


def test_disabled_rule_with_broken_metric_not_accepted():
    c=sample_contract();c["rules"][0]["metric_id"]="does_not_exist"
    c["rules"][0]["extensions"]={"enabled":False,"reason":"Optional"}
    assert results(c)["OCU_ID_required"].status=="INVALID_CONTRACT"


def test_naive_instant_requires_declared_zone():
    c=sample_contract()
    for f in c["assets"][0]["fields"]:
        if f["name"]=="FECHA_CAPTURA":f.pop("representation",None)
    r=results(c)
    assert r["capture_within_7_days"].status=="NEEDS_CONTEXT"


def test_malformed_evidence_is_needs_evidence_not_crash():
    r=results(sample_contract(),evidence={"ocupaciones_2025":{"verified":True,"values":42,"version":"2025"}})
    assert r["OCU_ID_2025_catalog"].status=="NEEDS_EVIDENCE"
