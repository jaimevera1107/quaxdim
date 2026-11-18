"""Governance block: parsing, validation, enrichment, ordering and the CLI."""

from __future__ import annotations

import copy
import datetime
import json
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from quaxdim import (
    ContractError,
    Governance,
    GovernanceError,
    StaleContractWarning,
    evaluate_contract,
    evaluate_optimized,
    plan_optimized,
)
from quaxdim._governance_apply import apply_governance, warn_if_overdue
from quaxdim.cli import main

ROOT = Path(__file__).resolve().parents[1]
CSV = str(ROOT / "examples" / "employees.csv")
CONTRACT = ROOT / "examples" / "contract_employees.yaml"
RULES = ("employee_id_present", "status_in_catalogue", "employee_id_unique")


def contract(governance: dict[str, Any] | None = None) -> dict[str, Any]:
    """The example contract, optionally with a governance block."""
    loaded = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    if governance is not None:
        loaded["governance"] = copy.deepcopy(governance)
    return loaded


def recent() -> str:
    return (datetime.date.today() - datetime.timedelta(days=3)).isoformat()


FULL = {
    "data_owner": "Dirección de Empleo y Trabajo",
    "approved_by": "jvera",
    "approved_at": "2026-11-06",
    "review_cycle": "quarterly",
    "criticality": "analytical",
    "rules": {
        "employee_id_unique": {
            "steward": "equipo-SIGA",
            "on_failure": "block_publication",
            "criticality": "regulatory_reporting",
        }
    },
}


# --------------------------------------------------------------------------
# Parsing and validation
# --------------------------------------------------------------------------


def test_absent_block_is_undeclared() -> None:
    assert not Governance.from_contract(contract()).declared


def test_full_block_is_parsed() -> None:
    g = Governance.from_contract(contract(FULL))
    assert g.declared and g.data_owner == "Dirección de Empleo y Trabajo"
    assert g.for_rule("employee_id_unique").steward == "equipo-SIGA"
    assert g.criticality_for("employee_id_unique") == "regulatory_reporting"
    # A rule without its own criticality inherits the contract's.
    assert g.criticality_for("employee_id_present") == "analytical"


def test_round_trip_through_to_dict() -> None:
    g = Governance.from_contract(contract(FULL))
    assert g.to_dict() == FULL


@pytest.mark.parametrize(
    ("block", "match"),
    [
        ({"criticality": "cosmic"}, "criticality"),
        ({"review_cycle": "weekly"}, "review_cycle"),
        ({"approved_at": "2026-02-30"}, "approved_at"),
        ({"rules": {"employee_id_present": {"on_failure": "x"}}}, "on_failure"),
        (
            {"rules": {"employee_id_present": {"criticality": "x"}}},
            "criticality",
        ),
    ],
)
def test_invalid_values_raise(block: dict[str, Any], match: str) -> None:
    with pytest.raises(GovernanceError, match=match):
        Governance.from_contract(contract(block))


def test_orphan_rule_id_is_a_contract_error() -> None:
    block = {"rules": {"no_such_rule": {"steward": "team"}}}
    with pytest.raises(ContractError, match="no_such_rule"):
        Governance.from_contract(contract(block))


def test_every_planner_rejects_orphans_before_running() -> None:
    bad = contract({"rules": {"no_such_rule": {"steward": "team"}}})
    with pytest.raises(GovernanceError, match="no_such_rule"):
        plan_optimized(bad, data={"employees": CSV})
    pd = pytest.importorskip("pandas")
    frame = pd.read_csv(CSV, dtype="string", keep_default_na=False)
    with pytest.raises(GovernanceError, match="no_such_rule"):
        evaluate_contract(bad, data={"employees": frame})


def test_schema_rejects_unknown_governance_keys() -> None:
    with pytest.raises(ContractError):
        plan_optimized(
            contract({"owner": "typo"}), data={"employees": CSV}
        )


def test_governance_error_is_a_contract_error() -> None:
    assert issubclass(GovernanceError, ContractError)


# --------------------------------------------------------------------------
# Review cycle
# --------------------------------------------------------------------------


def test_overdue_after_the_cycle() -> None:
    g = Governance(approved_at="2026-01-01", review_cycle="quarterly")
    assert g.days_since_approval(datetime.date(2026, 2, 1)) == 31
    assert not g.is_overdue(datetime.date(2026, 4, 1))
    assert g.is_overdue(datetime.date(2026, 4, 30))


def test_never_overdue_without_both_fields() -> None:
    assert not Governance(approved_at="2020-01-01").is_overdue()
    assert not Governance(review_cycle="monthly").is_overdue()


def test_warning_only_when_overdue() -> None:
    with pytest.warns(StaleContractWarning, match="quarterly"):
        warn_if_overdue(
            Governance(approved_at="2020-01-01", review_cycle="quarterly")
        )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        warn_if_overdue(Governance(approved_at=recent(), review_cycle="annual"))
        warn_if_overdue(Governance())


def test_stale_contract_warns_but_still_evaluates() -> None:
    stale = contract({"approved_at": "2020-01-01", "review_cycle": "annual"})
    with pytest.warns(StaleContractWarning):
        report = evaluate_optimized(stale, data={"employees": CSV})
    assert len(report.results) == len(RULES)


# --------------------------------------------------------------------------
# Enrichment and ordering
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Fake:
    rule_id: str
    status: str
    steward: str | None = None
    on_failure: str | None = None
    criticality: str | None = None


def test_without_governance_results_are_untouched() -> None:
    results = (Fake("a", "PASS"), Fake("b", "FAIL"), Fake("c", "ERROR"))
    assert apply_governance(results, Governance()) == results


def test_attention_order() -> None:
    g = Governance(
        criticality="analytical",
        rules={
            "reg_unknown": _rule("regulatory_reporting"),
            "reg_fail": _rule("regulatory_reporting"),
            "reg_pass": _rule("regulatory_reporting"),
        },
    )
    results = [
        Fake("ana_pass", "PASS"),
        Fake("ana_fail", "FAIL"),
        Fake("reg_pass", "PASS"),
        Fake("ana_error", "ERROR"),
        Fake("reg_fail", "FAIL"),
        Fake("reg_unknown", "INDETERMINATE"),
    ]
    ordered = [r.rule_id for r in apply_governance(results, g)]
    assert ordered == [
        # Regulatory problems first; "could not measure" before "failed".
        "reg_unknown",
        "reg_fail",
        # Then analytical problems, errors before failures.
        "ana_error",
        "ana_fail",
        # Passing rules last, in contract order.
        "ana_pass",
        "reg_pass",
    ]


def _rule(criticality: str) -> Any:
    from quaxdim.governance import RuleGovernance

    return RuleGovernance(criticality=criticality)


def test_results_gain_their_metadata() -> None:
    g = Governance.from_contract(contract(FULL))
    by_id = {r.rule_id: r for r in apply_governance(
        [Fake(rule_id, "PASS") for rule_id in RULES], g
    )}
    unique = by_id["employee_id_unique"]
    assert (unique.steward, unique.on_failure, unique.criticality) == (
        "equipo-SIGA",
        "block_publication",
        "regulatory_reporting",
    )
    present = by_id["employee_id_present"]
    assert (present.steward, present.criticality) == (None, "analytical")


# --------------------------------------------------------------------------
# Both engines
# --------------------------------------------------------------------------


def test_no_block_gives_the_same_report_as_before() -> None:
    report = evaluate_optimized(contract(), data={"employees": CSV})
    assert [r.rule_id for r in report.results] == list(RULES)
    assert all(r.criticality is None for r in report.results)
    assert report.to_dict()["governance"] == {}


def test_native_engine_orders_and_enriches() -> None:
    governed = contract({**FULL, "approved_at": recent()})
    report = evaluate_optimized(governed, data={"employees": CSV})
    assert report.results[0].rule_id == "employee_id_unique"
    assert report.results[0].steward == "equipo-SIGA"
    assert report.results[-1].status == "PASS"
    payload = json.loads(report.to_json())
    assert payload["governance"]["data_owner"] == FULL["data_owner"]


def test_pandas_engine_orders_and_enriches() -> None:
    pd = pytest.importorskip("pandas")
    frame = pd.read_csv(CSV, dtype="string", keep_default_na=False)
    governed = contract({**FULL, "approved_at": recent()})
    report = evaluate_contract(governed, data={"employees": frame})
    assert report.results[0].rule_id == "employee_id_unique"
    assert report.governance.criticality == "analytical"
    table = report.to_frame()
    assert list(table.columns[:4]) == [
        "asset_id",
        "metric_id",
        "dimension_ids",
        "status",
    ]
    assert list(table.columns[4:7]) == ["criticality", "steward", "on_failure"]
    assert table.loc["employee_id_unique", "steward"] == "equipo-SIGA"


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def _write(tmp_path: Path, document: dict[str, Any]) -> str:
    path = tmp_path / "contract.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return str(path)


def test_cli_native_execute_shows_governance_first(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, contract({**FULL, "approved_at": recent()}))
    code = main([
        "native-execute", CSV, "--asset", "employees", "--contract", path,
        "--output", str(tmp_path / "report.json"),
    ])
    assert code == 1
    printed = capsys.readouterr().out
    rows = json.loads(printed[: printed.rindex("]") + 1])
    assert rows[0]["rule_id"] == "employee_id_unique"
    assert rows[0]["criticality"] == "regulatory_reporting"


def test_cli_pandas_execute_prints_a_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest.importorskip("pandas")
    path = _write(tmp_path, contract({**FULL, "approved_at": recent()}))
    code = main([
        "execute", CSV, "--asset", "employees", "--contract", path,
        "--output", str(tmp_path / "report.json"),
    ])
    out = capsys.readouterr().out
    assert code == 1
    assert "equipo-SIGA" in out and "Overall: NOT PASS" in out


def test_cli_pandas_execute_without_governance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest.importorskip("pandas")
    code = main([
        "execute", CSV, "--asset", "employees", "--contract", str(CONTRACT),
        "--output", str(tmp_path / "report.json"),
    ])
    out = capsys.readouterr().out
    assert code == 1
    assert "steward" not in out and "employee_id_present" in out
