"""Measure registry, the DAMA UK 2013 profile and the methodology report."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from quaxdim import (
    ContractError,
    evaluate_optimized,
    get_profile,
    list_measures,
    plan_optimized,
)
from quaxdim.evaluator_registry import REGISTRY
from quaxdim.measures import ABSTENTION_REASONS, MEASURES, measure_for_evaluator
from quaxdim.methodology import STATEMENT

ROOT = Path(__file__).resolve().parents[1]
CSV = str(ROOT / "examples" / "employees.csv")
CONTRACT = ROOT / "examples" / "contract_employees.yaml"


def contract() -> dict[str, Any]:
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


def report(document: dict[str, Any] | None = None) -> Any:
    return evaluate_optimized(
        document or contract(), data={"employees": CSV}
    )


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


def test_every_registered_evaluator_has_a_measure() -> None:
    # A new evaluator without a measure would silently fall outside every
    # profile; fail here instead.
    assert [e for e in REGISTRY if measure_for_evaluator(e) is None] == []


def test_measures_are_well_formed() -> None:
    for measure in MEASURES.values():
        assert measure.category in {"G", "K", "N"}
        assert measure.abstention_reason in ABSTENTION_REASONS
        # Only non-automatable measures may lack an evaluator.
        assert bool(measure.evaluators) == (measure.category != "N")
        # G means computable from the data alone: no knowledge input.
        if measure.category == "G":
            assert measure.required_inputs == ()


def test_list_measures_is_json_ready() -> None:
    assert json.loads(json.dumps(list_measures()))[0]["measure_id"]


def test_dama_uk_profile_has_its_six_dimensions() -> None:
    profile = get_profile("dama_uk_2013")
    assert [d.label for d in profile.dimensions] == [
        "Completeness",
        "Uniqueness",
        "Timeliness",
        "Validity",
        "Accuracy",
        "Consistency",
    ]
    assert all(d.measure_ids for d in profile.dimensions)


def test_unknown_profile_lists_the_available_ones() -> None:
    with pytest.raises(KeyError, match="dama_uk_2013"):
        get_profile("iso_25012_2008")


# --------------------------------------------------------------------------
# Methodology report
# --------------------------------------------------------------------------


def test_dama_uk_methodology_on_the_example() -> None:
    doc = report().to_methodology("dama_uk_2013")
    assert [d["label"] for d in doc.evaluated] == [
        "Completeness",
        "Uniqueness",
        "Validity",
    ]
    reasons = {d["label"]: d["reason"] for d in doc.not_evaluated}
    assert reasons == {
        "Timeliness": "missing_rule",
        "Accuracy": "missing_label",
        "Consistency": "missing_rule",
    }
    assert doc.added_measures == ()


def test_rules_carry_measure_category_and_adaptations() -> None:
    doc = report().to_methodology("dama_uk_2013")
    unique = next(
        r
        for d in doc.evaluated
        for r in d["rules"]
        if r["rule_id"] == "employee_id_unique"
    )
    assert unique["measure_id"] == "qd.uniqueness.composite_key"
    assert unique["category"] == "K"
    assert unique["threshold"] == "gte 0.99"
    assert unique["adaptations"] == [
        "a missing input is unknown and widens the bounds"
    ]
    # Without refs in the contract, the measure's own citation is used.
    assert "DAMA UK 2013" in unique["standard_refs"][0]["standard"]


def test_without_profile_the_contract_dimensions_frame_the_report() -> None:
    doc = report().to_methodology()
    assert {d["dimension_id"] for d in doc.evaluated} == {
        "completeness",
        "validity",
        "uniqueness",
    }
    assert doc.not_evaluated == ()


def test_declared_dimension_without_a_rule_is_reported() -> None:
    document = contract()
    document["dimensions"].append({
        "id": "consistency",
        "name": "Consistency",
        "framework": "mahanti_or_extension",
        "version": "1.0.0",
        "levels": ["record"],
        "measurement_modes": ["internal"],
    })
    doc = report(document).to_methodology()
    assert [(d["dimension_id"], d["reason"]) for d in doc.not_evaluated] == [
        ("consistency", "missing_rule")
    ]


def test_contract_standard_refs_are_cited_with_their_note() -> None:
    document = contract()
    document["rules"][0]["standard_refs"] = [{
        "standard": "Internal data policy 2026",
        "clause": "4.2",
        "interpretation": "as interpreted by QuaxDim",
        "verification": "public_text",
        "note": "employee IDs must never be blank",
    }]
    doc = report(document).to_methodology("dama_uk_2013")
    present = doc.evaluated[0]["rules"][0]
    assert present["standard_refs"][0]["clause"] == "4.2"
    assert (
        "Internal data policy 2026: employee IDs must never be blank"
        in present["adaptations"]
    )


def test_invalid_standard_ref_is_rejected_by_the_schema() -> None:
    document = contract()
    document["rules"][0]["standard_refs"] = [
        {"standard": "ISO/IEC 25024:2015", "verification": "trust_me"}
    ]
    with pytest.raises(ContractError):
        plan_optimized(document, data={"employees": CSV})


def test_reference_without_evidence_abstains_as_missing_reference() -> None:
    from tests.test_execution import contract as ref_contract
    from tests.test_execution import execute

    # The pandas engine: its only validity rule needs a reference table.
    doc = execute(c=ref_contract(), ev={}).to_methodology("dama_uk_2013")
    validity = next(
        d for d in doc.not_evaluated if d["dimension_id"] == "validity"
    )
    assert validity["reason"] == "missing_reference"
    assert validity["rules"] == ["OCU_ID_2025_catalog"]
    # A FAIL or INDETERMINATE dimension still counts as evaluated.
    assert [d["label"] for d in doc.evaluated] == [
        "Completeness",
        "Uniqueness",
        "Timeliness",
    ]


def test_wording_never_claims_conformance() -> None:
    forbidden = re.compile(r"\b(compliant|compliance|certified)\b", re.I)
    doc = report().to_methodology("dama_uk_2013")
    for text in (STATEMENT, doc.to_markdown(), json.dumps(doc.to_dict())):
        assert not forbidden.search(text)
    assert "aligned with" in STATEMENT and "derived from" in STATEMENT


def test_methodology_is_json_ready_and_markdown_renders() -> None:
    doc = report().to_methodology("dama_uk_2013")
    payload = json.loads(json.dumps(doc.to_dict()))
    assert payload["header"]["profile"]["profile_id"] == "dama_uk_2013"
    markdown = doc.to_markdown()
    assert markdown.startswith("# Assessment methodology")
    assert "## Not evaluated" in markdown
    assert "`missing_label`" in markdown


def test_report_json_is_unchanged_by_the_context() -> None:
    plain = report().to_dict()
    assert "context" not in plain
    governed = copy.deepcopy(contract())
    assert report(governed).to_dict().keys() == plain.keys()


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["native-execute", "execute"])
def test_cli_writes_the_methodology(command: str, tmp_path: Path) -> None:
    if command == "execute":
        pytest.importorskip("pandas")
    from quaxdim.cli import main

    target = tmp_path / "methodology.md"
    code = main([
        command, CSV, "--asset", "employees", "--contract", str(CONTRACT),
        "--output", str(tmp_path / "report.json"),
        "--methodology", str(target), "--profile", "dama_uk_2013",
    ])
    assert code == 1  # the example data fail two rules
    text = target.read_text(encoding="utf-8")
    assert "DAMA UK" in text and "`missing_label`" in text


def test_cli_unknown_profile_fails_before_running(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from quaxdim.cli import main

    code = main([
        "native-execute", CSV, "--asset", "employees",
        "--contract", str(CONTRACT),
        "--output", str(tmp_path / "report.json"),
        "--methodology", str(tmp_path / "m.md"), "--profile", "nope",
    ])
    assert code == 2
    assert "dama_uk_2013" in capsys.readouterr().err
    assert not (tmp_path / "report.json").exists()


def test_cli_lists_measures(capsys: pytest.CaptureFixture[str]) -> None:
    from quaxdim.cli import main

    assert main(["measures"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["measures"]) == len(MEASURES)
    assert payload["profiles"][0]["profile_id"] == "dama_uk_2013"
