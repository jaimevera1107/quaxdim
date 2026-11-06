"""Extensible conceptual taxonomy; a catalog entry is NOT a measured metric.

The thirty Mahanti (2019) concepts follow Table 3.5 (pp. 126-127),
including cardinality, which is not pictured in Figure 3.3.
ISO/IEC 25012 adds seven distinct identifiers not already represented here.
Family assignments and suggested measurement modes are library-design choices.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DimensionSpec:
    """Catalog entry for one data-quality dimension."""

    id: str
    name: str
    family: str
    approach: str  # objective, subjective, mixed, context-dependent
    standard: str
    evidence_hint: str


# (id, label, family, approach, initial evidence requirement)
_MAHANTI = [
    (
        "accessibility",
        "Accessibility",
        "access",
        "subjective",
        "Access tests / surveys",
    ),
    (
        "accuracy",
        "Accuracy",
        "intrinsic",
        "objective",
        "Verifiable source of truth",
    ),
    (
        "believability",
        "Believability",
        "trust",
        "subjective",
        "Judgement of data consumers",
    ),
    (
        "credibility",
        "Credibility",
        "trust",
        "subjective",
        "Source assessment",
    ),
    (
        "trustworthiness",
        "Source trustworthiness",
        "trust",
        "subjective",
        "Provenance and source assessment",
    ),
    (
        "timeliness",
        "Timeliness",
        "temporal",
        "objective",
        "Event/receipt dates and deadline",
    ),
    (
        "currency",
        "Currency",
        "temporal",
        "objective",
        "Update date and expected horizon",
    ),
    (
        "volatility",
        "Volatility",
        "temporal",
        "objective",
        "Historical series / changes per period",
    ),
    (
        "correctness",
        "Correctness",
        "intrinsic",
        "objective",
        "Rules that let errors be determined",
    ),
    (
        "precision",
        "Precision",
        "intrinsic",
        "objective",
        "Unit, resolution and expected tolerance",
    ),
    (
        "reliability",
        "Reliability",
        "trust",
        "mixed",
        "Usage evidence plus stability tests",
    ),
    (
        "consistency",
        "Consistency",
        "integrity",
        "objective",
        "Rules across fields/sources",
    ),
    (
        "integrity",
        "Relational integrity",
        "integrity",
        "objective",
        "Keys and reference tables",
    ),
    (
        "completeness",
        "Completeness",
        "intrinsic",
        "objective",
        "Expected fields/entities",
    ),
    (
        "validity",
        "Validity or conformity",
        "intrinsic",
        "objective",
        "Domain, pattern and type contract",
    ),
    (
        "interpretability",
        "Interpretability",
        "usability",
        "subjective",
        "User assessment and glossary",
    ),
    (
        "security",
        "Security",
        "access",
        "objective",
        "Permission and control tests",
    ),
    (
        "conciseness",
        "Conciseness",
        "usability",
        "subjective",
        "Representation assessment",
    ),
    (
        "reputation",
        "Reputation",
        "trust",
        "subjective",
        "Source survey/rating",
    ),
    (
        "uniqueness",
        "Uniqueness",
        "integrity",
        "objective",
        "Identity key / granularity",
    ),
    (
        "duplication",
        "Duplication",
        "integrity",
        "objective",
        "Identity key and duplicate policy",
    ),
    (
        "cardinality",
        "Cardinality",
        "structure",
        "objective",
        "Distinct values / cardinality contract",
    ),
    (
        "relevance",
        "Relevance",
        "usability",
        "mixed",
        "Purpose of use and relevant universe",
    ),
    (
        "manipulability",
        "Manipulability",
        "usability",
        "subjective",
        "Data usage test",
    ),
    (
        "objectivity",
        "Objectivity",
        "trust",
        "subjective",
        "Bias audit and human assessment",
    ),
    (
        "traceability",
        "Traceability and lineage",
        "trust",
        "mixed",
        "Metadata and transformation log",
    ),
    (
        "data_specification",
        "Data specification",
        "structure",
        "objective",
        "Dictionary/contract/metadata",
    ),
    (
        "coverage",
        "Coverage",
        "structure",
        "objective",
        "Complete expected universe",
    ),
    (
        "granularity",
        "Granularity",
        "structure",
        "objective",
        "Declared unit of observation",
    ),
    (
        "redundancy",
        "Redundancy",
        "structure",
        "objective",
        "Inventory of copies/systems",
    ),
]

_ISO_EXTRA = [
    (
        "compliance",
        "Compliance",
        "governance",
        "context-dependent",
        "Applicable regulation or specification",
    ),
    (
        "confidentiality",
        "Confidentiality",
        "access",
        "objective",
        "Effective access control",
    ),
    (
        "efficiency",
        "Efficiency",
        "system",
        "objective",
        "Resource/latency measurements",
    ),
    (
        "understandability",
        "Understandability",
        "usability",
        "mixed",
        "Glossaries and user assessment",
    ),
    (
        "availability",
        "Availability",
        "system",
        "objective",
        "Availability metrics (SLA)",
    ),
    (
        "portability",
        "Portability",
        "system",
        "objective",
        "Transfer tests between systems",
    ),
    (
        "recoverability",
        "Recoverability",
        "system",
        "objective",
        "Recovery/restore tests",
    ),
]

assert len(_MAHANTI) == 30
assert len(_ISO_EXTRA) == 7

CATALOG = {
    key: DimensionSpec(key, name, family, approach, "Mahanti (2019)", hint)
    for key, name, family, approach, hint in _MAHANTI
}
CATALOG.update(
    {
        key: DimensionSpec(
            key, name, family, approach, "ISO/IEC 25012:2008", hint
        )
        for key, name, family, approach, hint in _ISO_EXTRA
    }
)


def list_dimensions(source: str | None = None) -> list[dict]:
    """List 37 conceptual concepts. This is not a claim of 37 evaluators."""
    result = [asdict(x) for x in CATALOG.values()]
    if source is not None:
        result = [
            x for x in result if source.casefold() in x["standard"].casefold()
        ]
    return result
