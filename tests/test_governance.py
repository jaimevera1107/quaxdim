"""Tests for the governance module and its integration with both engines.

Coverage
--------
- Governance.from_contract: happy path, empty block, validation errors.
- Governance helpers: criticality_rank, is_overdue, days_since_approval.
- apply_governance: enrichment fields, sort order.
- warn_if_overdue: warning emitted / suppressed.
- Integration: evaluate_optimized and evaluate_contract propagate governance.
"""

from __future__ import annotations

import datetime
import warnings
from dataclasses import dataclass

import pytest

from quaxdim._governance_apply import StaleContractWarning, apply_governance, warn_if_overdue
from quaxdim.governance import (
    CRITICALITY_ORDER,
    Governance,
    GovernanceError,
    RuleGovernance,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_contract_dict(**governance_kwargs):
    """Minimal contract dict with an optional governance block."""
    d = {"contract_version": "0.1.0"}
    if governance_kwargs:
        d["governance"] = governance_kwargs
    return d


# ---------------------------------------------------------------------------
# Governance.from_contract
# ---------------------------------------------------------------------------


class TestFromContract:
    def test_empty_contract_gives_undeclared_governance(self):
        g = Governance.from_contract({})
        assert not g.declared

    def test_full_governance_block(self):
        g = Governance.from_contract(
            _make_contract_dict(
                data_owner="Finance",
                approved_by="jvera",
                approved_at="2026-01-15",
                review_cycle="quarterly",
                criticality="regulatory_reporting",
                rules={
                    "rule_a": {"steward": "team-A", "on_failure": "block_publication"}
                },
            )
        )
        assert g.declared
        assert g.data_owner == "Finance"
        assert g.approved_by == "jvera"
        assert g.approved_at == "2026-01-15"
        assert g.review_cycle == "quarterly"
        assert g.criticality == "regulatory_reporting"
        assert g.for_rule("rule_a").steward == "team-A"
        assert g.for_rule("rule_a").on_failure == "block_publication"

    def test_missing_rule_returns_empty_rule_governance(self):
        g = Governance.from_contract(_make_contract_dict(criticality="analytical"))
        rg = g.for_rule("nonexistent_rule")
        assert rg.steward is None
        assert rg.on_failure is None

    def test_invalid_criticality_raises(self):
        with pytest.raises(GovernanceError, match="criticality"):
            Governance.from_contract(_make_contract_dict(criticality="cosmic"))

    def test_invalid_review_cycle_raises(self):
        with pytest.raises(GovernanceError, match="review_cycle"):
            Governance.from_contract(_make_contract_dict(review_cycle="weekly"))

    def test_invalid_on_failure_raises(self):
        with pytest.raises(GovernanceError, match="on_failure"):
            Governance.from_contract(
                _make_contract_dict(
                    rules={"r": {"on_failure": "ignore_it"}}
                )
            )

    def test_invalid_approved_at_raises(self):
        with pytest.raises(GovernanceError, match="approved_at"):
            Governance.from_contract(_make_contract_dict(approved_at="not-a-date"))

    def test_governance_key_absent_gives_undeclared(self):
        g = Governance.from_contract({"contract_version": "1.0", "rules": []})
        assert not g.declared


# ---------------------------------------------------------------------------
# Governance helpers
# ---------------------------------------------------------------------------


class TestGovernanceHelpers:
    def test_criticality_rank_order(self):
        ranks = [
            Governance(criticality=c).criticality_rank
            for c in CRITICALITY_ORDER
        ]
        assert ranks == sorted(ranks)

    def test_criticality_rank_unknown_sorts_last(self):
        g = Governance(criticality=None)
        assert g.criticality_rank > Governance(criticality="exploratory").criticality_rank

    def test_days_since_approval(self):
        g = Governance(approved_at="2026-01-01")
        today = datetime.date(2026, 2, 1)
        assert g.days_since_approval(today) == 31

    def test_days_since_approval_none_when_absent(self):
        g = Governance()
        assert g.days_since_approval() is None

    def test_is_overdue_true(self):
        g = Governance(approved_at="2025-01-01", review_cycle="quarterly")
        assert g.is_overdue(today=datetime.date(2026, 1, 1))

    def test_is_overdue_false_recent(self):
        today = datetime.date.today()
        g = Governance(
            approved_at=(today - datetime.timedelta(days=10)).isoformat(),
            review_cycle="quarterly",
        )
        assert not g.is_overdue(today=today)

    def test_is_overdue_false_when_no_cycle(self):
        g = Governance(approved_at="2020-01-01")
        assert not g.is_overdue()

    def test_to_dict_omits_none_fields(self):
        g = Governance(data_owner="Alice")
        d = g.to_dict()
        assert "data_owner" in d
        assert "approved_by" not in d
        assert "rules" not in d

    def test_to_dict_round_trip(self):
        g = Governance(
            data_owner="Alice",
            criticality="operational",
            rules={"r1": RuleGovernance(steward="team", on_failure="notify")},
        )
        d = g.to_dict()
        assert d["criticality"] == "operational"
        assert d["rules"]["r1"]["steward"] == "team"


# ---------------------------------------------------------------------------
# apply_governance
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeResult:
    rule_id: str
    status: str
    steward: str | None = None
    on_failure: str | None = None
    criticality: str | None = None


class TestApplyGovernance:
    def _governance(self, **kwargs) -> Governance:
        return Governance.from_contract(_make_contract_dict(**kwargs))

    def test_enriches_steward_and_on_failure(self):
        g = self._governance(
            rules={"r1": {"steward": "team-A", "on_failure": "notify"}}
        )
        result = _FakeResult(rule_id="r1", status="PASS")
        (enriched,) = apply_governance([result], g)
        assert enriched.steward == "team-A"
        assert enriched.on_failure == "notify"

    def test_enriches_criticality(self):
        g = self._governance(criticality="regulatory_reporting")
        result = _FakeResult(rule_id="r1", status="PASS")
        (enriched,) = apply_governance([result], g)
        assert enriched.criticality == "regulatory_reporting"

    def test_no_governance_no_enrichment(self):
        g = Governance()
        result = _FakeResult(rule_id="r1", status="PASS")
        (enriched,) = apply_governance([result], g)
        # Fields remain None
        assert enriched.steward is None
        assert enriched.on_failure is None
        assert enriched.criticality is None

    def test_sort_indeterminate_before_fail_before_pass(self):
        g = Governance()
        results = [
            _FakeResult("pass_rule", "PASS"),
            _FakeResult("fail_rule", "FAIL"),
            _FakeResult("indet_rule", "INDETERMINATE"),
        ]
        ordered = apply_governance(results, g)
        statuses = [r.status for r in ordered]
        assert statuses == ["INDETERMINATE", "FAIL", "PASS"]

    def test_original_order_preserved_within_same_status(self):
        g = Governance()
        results = [
            _FakeResult("fail_1", "FAIL"),
            _FakeResult("fail_2", "FAIL"),
        ]
        ordered = apply_governance(results, g)
        assert [r.rule_id for r in ordered] == ["fail_1", "fail_2"]

    def test_returns_tuple(self):
        g = Governance()
        result = apply_governance([], g)
        assert isinstance(result, tuple)


# ---------------------------------------------------------------------------
# warn_if_overdue
# ---------------------------------------------------------------------------


class TestWarnIfOverdue:
    def test_emits_warning_when_overdue(self):
        g = Governance(approved_at="2020-01-01", review_cycle="quarterly")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            warn_if_overdue(g)
        assert any(issubclass(w.category, StaleContractWarning) for w in caught)

    def test_no_warning_when_recent(self):
        today = datetime.date.today()
        g = Governance(
            approved_at=(today - datetime.timedelta(days=5)).isoformat(),
            review_cycle="quarterly",
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            warn_if_overdue(g)
        assert not any(issubclass(w.category, StaleContractWarning) for w in caught)

    def test_no_warning_when_undeclared(self):
        g = Governance()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            warn_if_overdue(g)
        assert not any(issubclass(w.category, StaleContractWarning) for w in caught)


# ---------------------------------------------------------------------------
# Integration: evaluate_optimized
# ---------------------------------------------------------------------------


class TestEvaluateOptimizedIntegration:
    """Light integration tests: only check that governance flows into the report."""

    def _run(self, governance_block=None):
        """Run evaluate_optimized on the sample employees contract."""
        import yaml
        from quaxdim import evaluate_optimized

        with open("examples/contract_employees.yaml", encoding="utf-8") as fh:
            contract = yaml.safe_load(fh)

        if governance_block:
            contract["governance"] = governance_block
        else:
            contract.pop("governance", None)

        return evaluate_optimized(
            contract, data={"employees": "examples/employees.csv"}
        )

    def test_no_governance_report_has_undeclared_governance(self):
        report = self._run()
        assert not report.governance.declared

    def test_governance_propagates_to_report(self):
        report = self._run(
            governance_block={
                "criticality": "operational",
                "data_owner": "HR",
                "rules": {
                    "employee_id_present": {
                        "steward": "hr-team",
                        "on_failure": "open_ticket",
                    }
                },
            }
        )
        assert report.governance.criticality == "operational"
        assert report.governance.data_owner == "HR"

    def test_governance_enriches_results(self):
        report = self._run(
            governance_block={
                "criticality": "analytical",
                "rules": {
                    "employee_id_present": {"steward": "data-team"}
                },
            }
        )
        result = next(r for r in report.results if r.rule_id == "employee_id_present")
        assert result.steward == "data-team"
        assert result.criticality == "analytical"

    def test_to_dict_includes_governance(self):
        report = self._run(governance_block={"criticality": "exploratory"})
        d = report.to_dict()
        assert "governance" in d
        assert d["governance"]["criticality"] == "exploratory"
