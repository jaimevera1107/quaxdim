"""Registry of atomic quality measures and the standard profiles built on it.

A *measure* is one way of quantifying one dimension: what it needs, which
QuaxDim evaluators implement it and which standards it can be cited against.
A *profile* is a standard's list of dimensions, mapped onto measures.

Every measure carries a category taken from the G/K/N scheme of Jung et al.
(arXiv 2610.10919): ``G`` is computable from the data alone, ``K`` needs
knowledge someone must supply (a rule, a reference, a timestamp), ``N``
cannot be automated without external ground truth. The category decides,
mechanically, whether a dimension can be assessed or must abstain, and the
abstention always names what was missing.

The category assignments below are QuaxDim's own, made with that scheme;
they are not copied from any standard. Standard references are citations
for traceability, never a claim of conformance.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

__all__ = [
    "ABSTENTION_REASONS",
    "MEASURES",
    "PROFILES",
    "Measure",
    "Profile",
    "ProfileDimension",
    "StandardRef",
    "get_profile",
    "list_measures",
    "measure_for_evaluator",
]

Category = Literal["G", "K", "N"]

ABSTENTION_REASONS: Mapping[str, str] = {
    "missing_rule": "No approved rule measures this dimension.",
    "missing_reference": "The measure needs reference data that was not "
    "supplied.",
    "missing_timestamp": "The measure needs event and receipt timestamps.",
    "missing_label": "The measure needs ground truth (an authoritative "
    "value per record), which cannot be derived from the data.",
    "missing_target_population": "The measure needs the expected "
    "population, which was not declared.",
    "system_dependent": "Rules exist but the engine could not measure them "
    "(unsupported, blocked or invalid).",
}
"""Typed reasons for not assessing a dimension, with their explanation."""

# What each required input is called when it is missing.
_INPUT_REASON: Mapping[str, str] = {
    "rule": "missing_rule",
    "reference": "missing_reference",
    "timestamp": "missing_timestamp",
    "label": "missing_label",
    "target_population": "missing_target_population",
}

_DAMA_UK = "DAMA UK 2013, The Six Primary Dimensions for Data Quality " \
    "Assessment"


@dataclass(frozen=True)
class StandardRef:
    """Citation of a standard, as declared in a rule or a measure.

    ``verification`` says how far the citation was checked:
    ``public_text`` (against a freely available text), ``licensed_copy``,
    ``derived_from_literature`` or ``conflicting_public_sources``.
    """

    standard: str
    clause: str | None = None
    measure_id: str | None = None
    interpretation: str = "as interpreted by QuaxDim"
    verification: str = "public_text"
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the fields that are set, as a JSON-ready dictionary."""
        return {
            name: value
            for name in (
                "standard",
                "clause",
                "measure_id",
                "interpretation",
                "verification",
                "note",
            )
            if (value := getattr(self, name)) is not None
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> StandardRef:
        """Build a reference from a contract's ``standard_refs`` entry."""
        return cls(
            standard=str(raw["standard"]),
            clause=raw.get("clause"),
            measure_id=raw.get("measure_id"),
            interpretation=raw.get(
                "interpretation", "as interpreted by QuaxDim"
            ),
            verification=raw.get("verification", "public_text"),
            note=raw.get("note"),
        )


@dataclass(frozen=True)
class Measure:
    """One atomic way of measuring one dimension."""

    measure_id: str
    name: str
    dimension_id: str
    category: Category
    required_inputs: tuple[str, ...]
    evaluators: tuple[str, ...]
    description: str
    orientation: str = "higher_is_better"
    unit: str = "ratio"
    standard_refs: tuple[StandardRef, ...] = ()

    @property
    def automatable(self) -> bool:
        """Whether QuaxDim can compute it at all (an evaluator exists)."""
        return self.category != "N" and bool(self.evaluators)

    @property
    def abstention_reason(self) -> str:
        """Reason to report when no rule of the contract uses this measure.

        A measure QuaxDim can run is missing a rule. One it cannot run
        (category ``N``) is missing its external input, whatever rules
        exist.
        """
        if self.automatable:
            return "missing_rule"
        external = [i for i in self.required_inputs if i != "rule"]
        return _INPUT_REASON[external[0]] if external else "missing_rule"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready dictionary."""
        return {
            "measure_id": self.measure_id,
            "name": self.name,
            "dimension_id": self.dimension_id,
            "category": self.category,
            "required_inputs": list(self.required_inputs),
            "evaluators": list(self.evaluators),
            "orientation": self.orientation,
            "unit": self.unit,
            "description": self.description,
            "standard_refs": [r.to_dict() for r in self.standard_refs],
        }


def _dama(dimension: str) -> tuple[StandardRef, ...]:
    return (
        StandardRef(
            standard=_DAMA_UK,
            clause=dimension,
            interpretation="derived from; as interpreted by QuaxDim",
            verification="public_text",
        ),
    )


MEASURES: Mapping[str, Measure] = {
    m.measure_id: m
    for m in (
        Measure(
            "qd.completeness.presence",
            "Share of values present",
            "completeness",
            "G",
            (),
            ("builtin.presence",),
            "Values that are not missing under the field's missing policy, "
            "over all values in scope.",
            standard_refs=_dama("Completeness"),
        ),
        Measure(
            "qd.uniqueness.composite_key",
            "Share of records with a unique key",
            "uniqueness",
            "K",
            ("rule",),
            ("builtin.composite_uniqueness",),
            "Records whose declared key occurs once, over all records. "
            "Needs the key, which only a person can declare.",
            standard_refs=_dama("Uniqueness"),
        ),
        Measure(
            "qd.validity.allowed_values",
            "Share of values in the declared domain",
            "validity",
            "K",
            ("rule",),
            ("builtin.allowed_values",),
            "Values that belong to a declared closed set.",
            standard_refs=_dama("Validity"),
        ),
        Measure(
            "qd.validity.value_range",
            "Share of values within the declared range",
            "validity",
            "K",
            ("rule",),
            ("builtin.value_range",),
            "Numeric values between declared bounds.",
            standard_refs=_dama("Validity"),
        ),
        Measure(
            "qd.validity.pattern",
            "Share of values matching the declared pattern",
            "validity",
            "K",
            ("rule",),
            ("builtin.regex_fullmatch",),
            "Values whose full text matches a declared regular expression.",
            standard_refs=_dama("Validity"),
        ),
        Measure(
            "qd.validity.reference_lookup",
            "Share of codes found in a versioned reference",
            "validity",
            "K",
            ("rule", "reference"),
            ("reference.lookup_temporal",),
            "Codes that exist in a reference table valid for the period.",
            standard_refs=_dama("Validity"),
        ),
        Measure(
            "qd.timeliness.max_delay",
            "Share of records available within the allowed delay",
            "timeliness",
            "K",
            ("rule", "timestamp"),
            ("builtin.max_delay",),
            "Records whose delay between event and availability does not "
            "exceed a declared maximum.",
            standard_refs=_dama("Timeliness"),
        ),
        Measure(
            "qd.consistency.column_compare",
            "Share of records satisfying a declared relation",
            "consistency",
            "K",
            ("rule",),
            ("builtin.column_compare",),
            "Records where a declared comparison between two fields holds.",
            standard_refs=_dama("Consistency"),
        ),
        Measure(
            "qd.accuracy.authoritative_match",
            "Share of values matching an authoritative source",
            "accuracy",
            "N",
            ("label",),
            (),
            "Values equal to the real-world value, which requires an "
            "authoritative value per record. QuaxDim has no evaluator for "
            "it; checking codes against a reference is validity, not "
            "accuracy.",
            standard_refs=_dama("Accuracy"),
        ),
    )
}
"""Every registered measure, by ID."""


def list_measures() -> list[dict[str, Any]]:
    """Return every registered measure as a dictionary, sorted by ID."""
    return [MEASURES[key].to_dict() for key in sorted(MEASURES)]


def measure_for_evaluator(evaluator: str) -> Measure | None:
    """Return the measure an evaluator implements, if it is registered."""
    for measure in MEASURES.values():
        if evaluator in measure.evaluators:
            return measure
    return None


@dataclass(frozen=True)
class ProfileDimension:
    """One dimension of a standard, mapped to QuaxDim measures."""

    label: str
    dimension_id: str
    measure_ids: tuple[str, ...]

    @property
    def measures(self) -> tuple[Measure, ...]:
        """The registered measures of this dimension."""
        return tuple(MEASURES[m] for m in self.measure_ids)


@dataclass(frozen=True)
class Profile:
    """A standard's dimensions, as QuaxDim can assess them."""

    profile_id: str
    title: str
    source: str
    licence_note: str
    dimensions: tuple[ProfileDimension, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready dictionary."""
        return {
            "profile_id": self.profile_id,
            "title": self.title,
            "source": self.source,
            "licence_note": self.licence_note,
            "dimensions": [
                {
                    "label": d.label,
                    "dimension_id": d.dimension_id,
                    "measure_ids": list(d.measure_ids),
                }
                for d in self.dimensions
            ],
        }


def _measures_of(dimension_id: str) -> tuple[str, ...]:
    return tuple(
        key for key, m in MEASURES.items() if m.dimension_id == dimension_id
    )


PROFILES: Mapping[str, Profile] = {
    "dama_uk_2013": Profile(
        profile_id="dama_uk_2013",
        title="DAMA UK six primary dimensions (2013)",
        source="DAMA UK Working Group, The Six Primary Dimensions for Data "
        "Quality Assessment, October 2013.",
        licence_note="Dimension names are cited; definitions are not "
        "reproduced. QuaxDim's measures are derived from and interpreted "
        "by QuaxDim.",
        dimensions=tuple(
            ProfileDimension(label, key, _measures_of(key))
            for label, key in (
                ("Completeness", "completeness"),
                ("Uniqueness", "uniqueness"),
                ("Timeliness", "timeliness"),
                ("Validity", "validity"),
                ("Accuracy", "accuracy"),
                ("Consistency", "consistency"),
            )
        ),
    )
}
"""Available standard profiles, by ID."""


def get_profile(profile_id: str) -> Profile:
    """Return a profile by ID.

    Raises:
        KeyError: If the profile does not exist; the message lists the
            available ones.
    """
    try:
        return PROFILES[profile_id]
    except KeyError:
        raise KeyError(
            f"Unknown profile {profile_id!r}; available: {sorted(PROFILES)}"
        ) from None
