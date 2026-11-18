"""Methodology report: how a contract's verdicts were produced.

ISO/IEC 25024 asks that an assessment state what was evaluated, on which
entity, with which measures and adaptations, and what was left out. This
module writes that document from an execution report. It is traceability,
never certification: the wording is fixed in :data:`STATEMENT` and the tests
forbid "compliant" and "certified".

No score is computed. A dimension that could not be assessed is listed with
a typed reason (:data:`quaxdim.measures.ABSTENTION_REASONS`) and never enters
any aggregate, because none exists.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .catalog import CATALOG
from .measures import (
    ABSTENTION_REASONS,
    StandardRef,
    get_profile,
    measure_for_evaluator,
)

__all__ = [
    "STATEMENT",
    "ContractContext",
    "MethodologyReport",
    "RuleContext",
    "build_methodology",
]

STATEMENT = (
    "This document records how the data were assessed, for traceability. "
    "Measures are derived from the cited references and aligned with them "
    "as interpreted by QuaxDim. It is not a certification and makes no "
    "claim of conformance with any standard."
)

MEASURED: frozenset[str] = frozenset({"PASS", "FAIL", "INDETERMINATE"})

_NULL_POLICY_TEXT: Mapping[str, str] = {
    "include_as_failure": "a missing input counts as a failure",
    "indeterminate": "a missing input is unknown and widens the bounds",
    "exclude": "a missing input is left out of the denominator",
}


@dataclass(frozen=True)
class RuleContext:
    """What the contract declared about one rule, beyond its result."""

    evaluator: str | None = None
    null_policy: str | None = None
    description: str | None = None
    standard_refs: tuple[StandardRef, ...] = ()


@dataclass(frozen=True)
class ContractContext:
    """The parts of a contract the methodology report needs.

    Reports keep it so that ``to_methodology()`` works without the
    contract; it is not part of ``to_dict()``.
    """

    rules: Mapping[str, RuleContext] = field(default_factory=dict)
    dimensions: tuple[str, ...] = ()

    @classmethod
    def from_contract(cls, contract: Mapping[str, Any]) -> ContractContext:
        """Extract the context from a validated contract."""
        metrics = {
            m.get("id"): m
            for m in contract.get("metrics", [])
            if isinstance(m, Mapping)
        }
        rules: dict[str, RuleContext] = {}
        for rule in contract.get("rules", []):
            if not isinstance(rule, Mapping):
                continue
            metric = metrics.get(rule.get("metric_id"), {})
            rules[str(rule.get("id"))] = RuleContext(
                evaluator=metric.get("evaluator"),
                null_policy=metric.get("null_policy"),
                description=rule.get("description"),
                standard_refs=tuple(
                    StandardRef.from_mapping(ref)
                    for ref in rule.get("standard_refs", [])
                ),
            )
        dimensions = tuple(
            str(d.get("id"))
            for d in contract.get("dimensions", [])
            if isinstance(d, Mapping)
        )
        return cls(rules=rules, dimensions=dimensions)


@dataclass(frozen=True)
class MethodologyReport:
    """The methodology document; render it with ``to_markdown``."""

    header: Mapping[str, Any]
    statement: str
    target_entities: tuple[Mapping[str, Any], ...]
    evaluated: tuple[Mapping[str, Any], ...]
    not_evaluated: tuple[Mapping[str, Any], ...]
    added_measures: tuple[Mapping[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready dictionary."""
        return {
            "header": dict(self.header),
            "statement": self.statement,
            "target_entities": [dict(x) for x in self.target_entities],
            "evaluated": [dict(x) for x in self.evaluated],
            "not_evaluated": [dict(x) for x in self.not_evaluated],
            "added_measures": [dict(x) for x in self.added_measures],
        }

    def to_markdown(self) -> str:
        """Render the document as Markdown, ready to attach to a release."""
        return _render(self)


def _label(dimension_id: str) -> str:
    spec = CATALOG.get(dimension_id)
    return spec.name if spec else dimension_id


def _rule_entry(result: Any, context: ContractContext) -> dict[str, Any]:
    rule = context.rules.get(result.rule_id, RuleContext())
    measure = measure_for_evaluator(rule.evaluator or "")
    adaptations = []
    policy_text = _NULL_POLICY_TEXT.get(rule.null_policy or "")
    if policy_text:
        adaptations.append(policy_text)
    adaptations += [
        f"{ref.standard}: {ref.note}" for ref in rule.standard_refs if ref.note
    ]
    threshold = result.threshold or {}
    return {
        "rule_id": result.rule_id,
        "asset_id": result.asset_id,
        "metric_id": result.metric_id,
        "evaluator": rule.evaluator,
        "measure_id": measure.measure_id if measure else None,
        "category": measure.category if measure else None,
        "threshold": (
            f"{threshold.get('operator')} {threshold.get('value')}"
            if threshold
            else None
        ),
        "status": result.status,
        "lower_rate": result.lower_rate,
        "upper_rate": result.upper_rate,
        "adaptations": adaptations,
        "standard_refs": [
            r.to_dict()
            for r in (
                rule.standard_refs
                or (measure.standard_refs if measure else ())
            )
        ],
    }


def _abstention(rules: Sequence[Mapping[str, Any]]) -> str:
    """Why dimensions with rules could still not be assessed."""
    if any(r["status"] == "NEEDS_EVIDENCE" for r in rules):
        return "missing_reference"
    return "system_dependent"


def build_methodology(
    report: Any, profile: str | None = None
) -> MethodologyReport:
    """Write the methodology document of an execution report.

    Args:
        report: An ``OptimizedReport`` or ``ContractExecutionReport``.
        profile: Standard profile to assess against, e.g.
            ``"dama_uk_2013"``. Without one, the dimensions declared in the
            contract are the frame of reference.

    Returns:
        The methodology document.

    Raises:
        KeyError: If the profile does not exist.
    """
    context: ContractContext = report.context
    entries = [_rule_entry(r, context) for r in report.results]
    by_dimension: dict[str, list[dict[str, Any]]] = {}
    for result, entry in zip(report.results, entries, strict=True):
        for dimension_id in result.dimension_ids:
            by_dimension.setdefault(dimension_id, []).append(entry)

    chosen = get_profile(profile) if profile else None
    if chosen:
        frame = [(d.dimension_id, d.label) for d in chosen.dimensions]
        reasons = {
            d.dimension_id: (
                "missing_rule"
                if any(m.automatable for m in d.measures)
                else d.measures[0].abstention_reason
            )
            for d in chosen.dimensions
        }
    else:
        declared = list(context.dimensions)
        declared += [d for d in by_dimension if d not in declared]
        frame = [(d, _label(d)) for d in declared]
        reasons = {}

    evaluated: list[dict[str, Any]] = []
    not_evaluated: list[dict[str, Any]] = []
    for dimension_id, label in frame:
        rules = by_dimension.get(dimension_id, [])
        if any(r["status"] in MEASURED for r in rules):
            evaluated.append(
                {"dimension_id": dimension_id, "label": label, "rules": rules}
            )
            continue
        if rules:
            reason = _abstention(rules)
        else:
            reason = reasons.get(dimension_id, "missing_rule")
        not_evaluated.append({
            "dimension_id": dimension_id,
            "label": label,
            "reason": reason,
            "explanation": ABSTENTION_REASONS[reason],
            "rules": [r["rule_id"] for r in rules],
        })

    in_frame = {d for d, _ in frame}
    added = [
        entry
        for result, entry in zip(report.results, entries, strict=True)
        if entry["measure_id"] is None
        or not in_frame.intersection(result.dimension_ids)
    ]
    header = {
        "contract_version": report.contract_version,
        "contract_sha256": report.contract_sha256,
        "library_version": report.library_version,
        "generated_at": report.generated_at,
        "profile": chosen.to_dict() if chosen else None,
        "governance": report.governance.to_dict(),
    }
    return MethodologyReport(
        header=header,
        statement=STATEMENT,
        target_entities=tuple(
            {"asset_id": asset_id, "source": dict(source)}
            for asset_id, source in report.sources.items()
        ),
        evaluated=tuple(evaluated),
        not_evaluated=tuple(not_evaluated),
        added_measures=tuple(added),
    )


def _cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value).replace("|", "\\|")


def _render(doc: MethodologyReport) -> str:
    header = doc.header
    lines = [
        "# Assessment methodology",
        "",
        f"> {doc.statement}",
        "",
        f"- Contract version: {header['contract_version']}",
        f"- Contract SHA-256: `{header['contract_sha256']}`",
        f"- QuaxDim version: {header['library_version']}",
        f"- Generated at: {header['generated_at']}",
    ]
    if header["profile"]:
        lines.append(
            f"- Profile: {header['profile']['title']} "
            f"({header['profile']['source']})"
        )
    governance = header["governance"]
    for key in ("data_owner", "approved_by", "approved_at", "criticality"):
        if key in governance:
            lines.append(f"- {key.replace('_', ' ').capitalize()}: "
                         f"{governance[key]}")

    lines += ["", "## Target entities", ""]
    for entity in doc.target_entities:
        source = entity["source"]
        where = source.get("path") or source.get("kind") or "in memory"
        lines.append(f"- `{entity['asset_id']}`: {where}")

    lines += ["", "## Characteristics evaluated", ""]
    if not doc.evaluated:
        lines.append("None.")
    for dimension in doc.evaluated:
        lines += [
            f"### {dimension['label']}",
            "",
            "| Rule | Measure | Category | Threshold | Status | Bounds |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for r in dimension["rules"]:
            bounds = f"{_cell(r['lower_rate'])} – {_cell(r['upper_rate'])}"
            lines.append(
                f"| `{r['rule_id']}` | {_cell(r['measure_id'])} | "
                f"{_cell(r['category'])} | {_cell(r['threshold'])} | "
                f"{r['status']} | {bounds} |"
            )
        adaptations = [
            f"- `{r['rule_id']}`: {a}"
            for r in dimension["rules"]
            for a in r["adaptations"]
        ]
        if adaptations:
            lines += ["", "Adaptations:", "", *adaptations]
        lines.append("")

    lines += ["## Not evaluated", ""]
    if not doc.not_evaluated:
        lines.append("Every dimension in scope was evaluated.")
    for d in doc.not_evaluated:
        lines.append(
            f"- **{d['label']}** (`{d['reason']}`): {d['explanation']}"
        )

    if doc.added_measures:
        lines += ["", "## Measures outside the frame of reference", ""]
        lines += [
            f"- `{r['rule_id']}` ({_cell(r['evaluator'])})"
            for r in doc.added_measures
        ]
    return "\n".join(lines).rstrip() + "\n"
