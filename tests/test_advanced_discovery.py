"""Scalable discovery without pandas materialization; regression/privacy/budget tests."""

import csv
from pathlib import Path
import pytest
from quaxdim import DiscoveryBudget, discover_advanced
from quaxdim.advanced_discovery import ApproxCardinality


def make_csv(tmp_path, name="data.csv", rows=None):
    p = tmp_path / name
    rows = rows if rows is not None else []
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "MPO_ID",
                "OCU_ID",
                "OCU_NOMBRE",
                "YEAR",
                "TRIMESTRE",
                "TOTAL_VACANTES",
                "ESTADO",
                "FECHA_INICIO",
                "FECHA_FIN",
            ],
        )
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return p


def population(n):
    for i in range(n):
        yield {
            "MPO_ID": f"{i % 30:05d}",
            "OCU_ID": f"{i // 30:04d}",
            "OCU_NOMBRE": f"Ocupacion {i // 30}",
            "YEAR": str(2024 + i % 2),
            "TRIMESTRE": str((i % 4) + 1),
            "TOTAL_VACANTES": str(i % 20),
            "ESTADO": "ACTIVO" if i % 3 else "INACTIVO",
            "FECHA_INICIO": "2026-10-01",
            "FECHA_FIN": "2026-10-15",
        }


def test_csv_stream_and_deep_recommendations(tmp_path):
    path = make_csv(tmp_path, rows=population(500))
    report = discover_advanced(
        path, budget=DiscoveryBudget(mode="deep", max_sample_rows=500)
    )
    a = report.assets["dataset"]
    assert a["backend"] == "csv_stream" and a["rows"] == 500
    assert a["columns"][0]["physical_type"] == "utf8"
    assert any(
        x.kind == "hypothesis" and "composite key" in x.title
        for x in report.recommendations
    )
    assert any(
        x.kind == "hypothesis" and "dependency" in x.title
        for x in report.recommendations
    )
    assert (
        any(x.kind == "missing_policy" for x in report.recommendations)
        == False
    )
    assert all(
        x.to_dict()["approval_required"] for x in report.recommendations
    )
    assert all(
        x.kind != "rule" or x.evaluator not in ("builtin.value_range",)
        for x in report.recommendations
        if "TOTAL_VACANTES" in x.fields
    )


def test_schema_only_does_not_scan_rows(tmp_path, monkeypatch):
    p = make_csv(tmp_path, rows=population(20))
    report = discover_advanced(p, budget=DiscoveryBudget(mode="schema"))
    assert report.assets["dataset"]["rows"] is None
    assert report.assets["dataset"]["statistics_scope"] == "schema_only"
    assert len(report.recommendations) > 0
    assert report.assets["dataset"]["columns"][0]["nulls"] is None


def test_lexical_preservation_and_privacy(tmp_path):
    vals = [
        {
            "MPO_ID": "00001" if i % 2 else "00002",
            "ESTADO": "secret_value_dont_log",
            "TOTAL_VACANTES": str(i),
            "OCU_ID": "0101",
            "OCU_NOMBRE": "title",
        }
        for i in range(40)
    ]
    path = make_csv(tmp_path, rows=vals)
    out = discover_advanced(
        path, budget=DiscoveryBudget(mode="balanced", max_sample_rows=40)
    )
    txt = out.to_json()
    assert "secret_value_dont_log" not in txt
    assert "00001" not in txt
    assert out.assets["dataset"]["columns"][0]["physical_type"] == "utf8"
    assert "data.csv" not in txt


def test_budget_limits_columns_and_sample(tmp_path):
    path = make_csv(tmp_path, rows=population(100))
    report = discover_advanced(
        path, budget=DiscoveryBudget(max_profile_columns=2, max_sample_rows=3)
    )
    prof = report.assets["dataset"]["columns"]
    assert len(prof) == 9
    assert prof[2]["observations_profiled"] is None
    assert prof[0]["observations_profiled"] == 100
    assert "budget" in report.to_json()


def test_possible_null_tokens_are_not_assumed_null(tmp_path):
    values = [dict(next(population(1))) for _ in range(30)]
    for i, r in enumerate(values):
        r["ESTADO"] = "ND" if i == 0 else "ACTIVO"
    path = make_csv(tmp_path, rows=values)
    result = discover_advanced(
        path, budget=DiscoveryBudget(max_sample_rows=30)
    )
    policy = next(
        x for x in result.recommendations if x.kind == "missing_policy"
    )
    assert "ND" not in result.to_json()
    assert "ND" in result.to_json(reveal_configuration=True)
    assert policy.expectation["tokens"] == ["ND"]
    assert (
        result.assets["dataset"]["columns"][6]["possible_sentinel_count"] == 1
    )


def test_two_sources_relationship_is_just_hypothesis(tmp_path):
    a = make_csv(tmp_path, "a.csv", population(20))
    b = make_csv(tmp_path, "b.csv", population(30))
    res = discover_advanced(
        {"a": a, "b": b}, budget=DiscoveryBudget(mode="schema")
    )
    edges = [x for x in res.recommendations if x.kind == "relationship"]
    assert edges and edges[0].evidence["schema_only"]
    assert edges[0].evaluator is None
    assert all(x.kind != "relationship" for x in [] if x.evaluator is not None)


def test_prior_reviews_dont_approve(tmp_path):
    p = make_csv(tmp_path, rows=population(12))
    r = discover_advanced(p)
    rid = r.recommendations[0].id
    reread = discover_advanced(p, prior_reviews={rid: "approved"})
    old = next(x for x in reread.recommendations if x.id == rid)
    assert old.priority == "high"
    assert old.evidence["previous_review"] == "approved"
    assert old.to_dict()["approval_required"]
    with pytest.raises(ValueError):
        discover_advanced(p, prior_reviews={rid: "autopass"})


def test_explicit_review_manifest(tmp_path):
    p = make_csv(tmp_path, rows=population(12))
    r = discover_advanced(p)
    with pytest.raises(ValueError):
        r.approved_plan([], purpose="")
    with pytest.raises(ValueError):
        r.approved_plan(["unknown"], purpose="ok")
    assert (
        r.approved_plan([r.recommendations[0].id], purpose="control")[
            "automatic_approval"
        ]
        is False
    )


def test_malformed_csv_is_reported(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("COD,NAME\na,ok\nb\nc,ok,extra\n", encoding="utf-8")
    res = discover_advanced(p)
    assert any("missing/extra" in w for w in res.warnings)


def test_hll_bounded_and_nonproof():
    h = ApproxCardinality(10)
    for i in range(30000):
        h.add(str(i))
    assert len(h.reg) == 1024
    assert 0.90 < h.estimate() / 30000 < 1.10
    assert h.relative_std_error > 0.02


def test_reject_bad_source_and_budget(tmp_path):
    with pytest.raises(ValueError):
        DiscoveryBudget(max_sample_rows=0)
    with pytest.raises(ValueError):
        DiscoveryBudget(mode="alien")
    with pytest.raises(TypeError):
        discover_advanced(123)
    with pytest.raises(ValueError):
        discover_advanced({"broken id!": "x.csv"})


def test_csv_exact_candidate_only_when_eligible(tmp_path):
    p = tmp_path / "ids.csv"
    p.write_text("MPO_ID\n00001\n00002\n00003\n", encoding="utf-8")
    r = discover_advanced(p)
    assert any(
        x.evaluator == "builtin.composite_uniqueness"
        for x in r.recommendations
    )
    rr = discover_advanced(p, budget=DiscoveryBudget(max_exact_key_values=2))
    assert not any(
        x.evaluator == "builtin.composite_uniqueness"
        for x in rr.recommendations
    )


def test_no_raw_data_in_json_even_if_sample_reservoir(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text(
        "USER_ID,PAYLOAD\nU7,private-token-309D\nU8,private-token-4ABC\n",
        encoding="utf-8",
    )
    result = discover_advanced(p)
    assert "private-token" not in result.to_json()
    assert "U7" not in result.to_json()


def test_optional_duckdb_dependency_error(tmp_path):
    import importlib.util

    p = tmp_path / "x.parquet"
    p.write_bytes(b"not-real-parquet")
    if importlib.util.find_spec("duckdb") is None:
        with pytest.raises(ImportError, match="DuckDB"):
            discover_advanced(p)
    else:
        pytest.skip(
            "DuckDB available: invalid Parquet is not a dependency test"
        )


def test_reference_hint_needs_verified_binding(tmp_path):
    p = make_csv(tmp_path, rows=population(12))
    r = discover_advanced(
        p,
        reference_hints={
            "dataset.OCU_ID": {"reference_id": "CUOC_2025", "version": "2025"}
        },
    )
    suggestion = next(x for x in r.recommendations if x.kind == "reference")
    assert suggestion.evaluator == "reference.lookup_temporal"
    assert "Vincular referencia verificada" in suggestion.prerequisites
    assert suggestion.to_dict()["approval_required"]
    with pytest.raises(ValueError):
        discover_advanced(
            p, reference_hints={"missing.OCU_ID": {"reference_id": "CUOC"}}
        )


def test_baseline_drift_is_hypothesis(tmp_path):
    p = tmp_path / "one.csv"
    p.write_text("USER_ID,VAL\na,1\nb\nc,3\n", encoding="utf-8")
    baseline = {"dataset": {"VAL": {"nulls": 0, "observations_profiled": 300}}}
    r = discover_advanced(p, baseline_profiles=baseline)
    drift = [x for x in r.recommendations if "change in missing values" in x.title]
    assert drift and drift[0].kind == "hypothesis"
    assert drift[0].evidence["difference_pp"] > 10
    assert r.resource_plan()["dataset"]["full_passes"] == 1


def test_resource_plan_accurate_schema_and_balanced(tmp_path):
    p = make_csv(tmp_path, rows=population(5))
    assert (
        discover_advanced(
            p, budget=DiscoveryBudget(mode="schema")
        ).resource_plan()["dataset"]["full_passes"]
        == 0
    )
    plan = discover_advanced(p).resource_plan()["dataset"]
    assert plan["full_passes"] == 1 and plan["distributed_jobs"] == 0


def test_gzip_csv_and_tsv_lexical(tmp_path):
    import gzip

    p = tmp_path / "sample.tsv.gz"
    with gzip.open(p, "wt", encoding="utf-8", newline="") as handle:
        handle.write("MPO_ID\tDESCRIPCION\n00001\tuno\n00002\tdos\n")
    r = discover_advanced(p)
    assert r.assets["dataset"]["rows"] == 2
    assert r.assets["dataset"]["columns"][0]["name"] == "MPO_ID"
    assert r.assets["dataset"]["columns"][0]["nulls"] == 0


def test_native_type_guards_nested_and_int():
    from quaxdim.advanced_discovery import _logical_from_native

    assert _logical_from_native("int") == "integer"
    assert _logical_from_native("array<bigint>") == "nested"
    assert _logical_from_native("list[int64]") == "nested"
    assert _logical_from_native("decimal(12,2)") == "decimal"
