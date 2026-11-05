"""Strict, non-destructive preflight planner for quality-contract v0.2.

A READY decision means that the supported evaluator has its declared inputs;
it is NOT a claim that the quality rule has passed or even been executed.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ._lazy import LazyModule
from .evaluator_registry import Capability, capabilities

if TYPE_CHECKING:
    import pandas as pd
else:
    pd = LazyModule("pandas", extra="pandas")


class ContractError(ValueError):
    """A malformed top-level contract that cannot be planned safely."""


@dataclass(frozen=True)
class Reason:
    code: str
    message: str


@dataclass(frozen=True)
class Decision:
    rule_id: str
    asset_id: str
    metric_id: str
    evaluator: str
    status: str
    reasons: tuple[Reason, ...] = ()
    eligible_rows: int | None = None
    total_rows: int | None = None
    dimension_ids: tuple[str, ...] = ()
    source: str = "declared"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["reasons"] = [asdict(r) for r in self.reasons]
        return d


@dataclass(frozen=True)
class EvaluationPlan:
    decisions: tuple[Decision, ...]
    contract_version: str
    warnings: tuple[str, ...] = ()

    def summary(self) -> dict[str, int]:
        return dict(sorted(Counter(x.status for x in self.decisions).items()))

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    **d.to_dict(),
                    "reasons": "; ".join(r.code for r in d.reasons),
                }
                for d in self.decisions
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "summary": self.summary(),
            "warnings": list(self.warnings),
            "decisions": [d.to_dict() for d in self.decisions],
        }

    def to_json(self, path: str | Path | None = None) -> str:
        output = json.dumps(self.to_dict(), indent=2, ensure_ascii=False)
        if path is not None:
            Path(path).write_text(output, encoding="utf-8")
        return output


# Capabilities come from one canonical, backend-aware evaluator registry.
BUILTIN_CAPABILITIES = capabilities()

_STATUS_PRIORITY = (
    "INVALID_CONTRACT",
    "UNSUPPORTED",
    "AMBIGUOUS",
    "NEEDS_CONTEXT",
    "NO_ELIGIBLE_DATA",
    "NOT_APPLICABLE",
    "NEEDS_EVIDENCE",
    "NEEDS_DATA",
    "BLOCKED_RESOURCE",
    "BLOCKED_DEPENDENCY",
)


def _status(codes: set[str]) -> str:
    for status in _STATUS_PRIORITY:
        if status in codes:
            return status
    return "READY"


def _ids_unique(items: list[dict[str, Any]]) -> bool:
    ids = [x.get("id") for x in items]
    return len(ids) == len(set(ids))


def _date_repr(value: Any) -> str:
    return str(value) if value is not None else ""


def _date_boundary(value: str, *, end: bool) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    # Dates in contracts are inclusive civil dates, not midnight at their start.
    if end and len(value) == 10 and value[4] == "-" and value[7] == "-":
        ts = ts + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    return ts


def _datetime_series(
    frame: pd.DataFrame, field: str, field_defs: Mapping[str, Any]
) -> tuple[pd.Series | None, str | None]:
    """Parse only declared datetime types and explicit formats. Never guess locale."""
    if field not in frame.columns:
        return None, "MISSING_FIELD"
    declaration = field_defs.get(field, {})
    logical = str(declaration.get("logical_type", "")).lower()
    s = frame[field]
    if pd.api.types.is_datetime64_any_dtype(s.dtype):
        return s, None
    if logical not in {
        "date",
        "datetime",
        "local_datetime",
        "instant",
        "timestamp",
        "date_time",
    }:
        return None, "UNDECLARED_TEMPORAL_TYPE"
    patterns = declaration.get("representation", {}).get("date_patterns", [])
    if not patterns:
        return None, "DATE_FORMAT_UNDECLARED"
    if len(patterns) > 1:
        return None, "MULTIPLE_DATE_FORMATS_REQUIRE_EXPLICIT_POLICY"
    fmt = patterns[0]
    try:
        result = pd.to_datetime(s, format=fmt, errors="coerce")
    except (ValueError, TypeError, OverflowError):
        return None, "DATE_PARSE_ERROR"
    if result.notna().sum() == 0 and s.notna().sum():
        return None, "DATE_PARSE_ERROR"
    if result.notna().sum() < s.notna().sum():
        return None, "DATE_PARTIALLY_UNPARSEABLE"
    return result, None


def _time_mask(
    frame: pd.DataFrame, window: Mapping[str, Any], fields: Mapping[str, Any]
) -> tuple[pd.Series | None, str | None]:
    time_field = window.get("time_field")
    if not time_field or time_field not in fields:
        return None, "TIME_FIELD_UNDECLARED"
    series, issue = _datetime_series(frame, str(time_field), fields)
    if issue:
        return None, issue
    if series is None:
        return None, "UNKNOWN_TEMPORAL_TYPE"
    if series.isna().any():
        return None, "TIME_VALUES_MISSING"
    try:
        start = _date_boundary(str(window["from"]), end=False)
        end = _date_boundary(str(window["to"]), end=True)
        if start > end:
            return None, "INVERTED_TIME_WINDOW"
        if getattr(series.dt, "tz", None) is not None:
            zone = window.get("timezone") or fields.get(time_field, {}).get(
                "representation", {}
            ).get("timezone")
            if not zone:
                return None, "TIMEZONE_REQUIRED"
            if start.tzinfo is None:
                start = start.tz_localize(zone)
            if end.tzinfo is None:
                end = end.tz_localize(zone)
            start = start.tz_convert(series.dt.tz)
            end = end.tz_convert(series.dt.tz)
        elif start.tzinfo is not None or end.tzinfo is not None:
            return None, "TIMEZONE_AMBIGUOUS"
        return series.between(start, end, inclusive="both").fillna(False), None
    except (TypeError, ValueError, OverflowError) as ex:
        return None, f"TIME_WINDOW_ERROR:{type(ex).__name__}"


def _predicate_mask(
    frame: pd.DataFrame, pred: Mapping[str, Any] | list[dict[str, Any]]
) -> tuple[pd.Series | None, str | None]:
    """Conjunction of JSON-schema predicates, refusing ambiguous null comparisons.

    Also accepts a single predicate for legacy consumers. A comparison with
    missing operands is not silently treated as False, even if only one row
    is affected. The executor can use row-wise three-valued logic separately.
    """
    if isinstance(pred, list):
        result = pd.Series(True, index=frame.index, dtype=bool)
        for item in pred:
            mask, issue = _predicate_mask(frame, item)
            if issue:
                return None, issue
            result &= mask
        return result, None
    if not isinstance(pred, Mapping):
        return None, "PREDICATE_INVALID"
    field = pred.get("field")
    if field not in frame.columns:
        return None, "PREDICATE_FIELD_MISSING"
    s = frame[field]
    op = pred.get("op")
    allowed = {
        "eq",
        "ne",
        "in",
        "not_in",
        "lt",
        "lte",
        "gt",
        "gte",
        "between",
        "exists",
        "not_exists",
        "is_null",
        "not_null",
    }
    if op not in allowed:
        return None, "PREDICATE_UNSUPPORTED"
    try:
        if op in {"exists", "not_null"}:
            return s.notna(), None
        if op in {"not_exists", "is_null"}:
            return s.isna(), None
        # Comparisons involving NULL cannot prove either True or False.
        if s.isna().any():
            return None, "PREDICATE_NULL_INPUT"
        if op == "eq":
            mask = s.eq(pred.get("value"))
        elif op == "ne":
            mask = s.ne(pred.get("value"))
        elif op == "in":
            mask = s.isin(pred.get("values", []))
        elif op == "not_in":
            mask = ~s.isin(pred.get("values", []))
        elif op == "lt":
            mask = s.lt(pred.get("value"))
        elif op == "lte":
            mask = s.le(pred.get("value"))
        elif op == "gt":
            mask = s.gt(pred.get("value"))
        elif op == "gte":
            mask = s.ge(pred.get("value"))
        else:
            mask = s.ge(pred.get("from")) & s.le(pred.get("to"))
        if mask.isna().any():
            return None, "PREDICATE_NULL_INPUT"
        return mask.astype(bool), None
    except (ValueError, TypeError, KeyError):
        return None, "PREDICATE_TYPE_ERROR"


def _variant_problem(
    field: dict[str, Any],
    frame: pd.DataFrame,
    field_defs: dict[str, Any],
    active: pd.Series,
) -> str | None:
    variants = field.get("variants", [])
    if not variants or int(active.sum()) == 0:
        return None
    memberships: dict[int, list[pd.Series]] = {}
    for variant in variants:
        when = variant.get("when", {})
        if "period" in when:
            mask, error = _time_mask(frame, when["period"], field_defs)
        elif "predicate" in when:
            mask, error = _predicate_mask(frame, when["predicate"])
        else:
            return "VARIANT_CONDITION_UNSUPPORTED"
        if error:
            return error
        if mask is None:
            return "VARIANT_CONDITION_UNDETERMINED"
        memberships.setdefault(int(variant.get("priority", 0)), []).append(
            mask & active
        )
    higher = pd.Series(False, index=frame.index)
    chosen = pd.Series(False, index=frame.index)
    for priority in sorted(memberships, reverse=True):
        matching = memberships[priority]
        # Equal-priority overlaps are ambiguous, regardless of file ordering.
        counter = pd.concat([m.astype("int8") for m in matching], axis=1).sum(
            axis=1
        )
        if ((counter > 1) & ~higher).any():
            return "VARIANTS_OVERLAP_AT_SAME_PRIORITY"
        now = (counter > 0) & ~higher
        chosen |= now
        higher |= counter > 0
    if (active & ~chosen).any():
        return "VARIANTS_DO_NOT_COVER_ACTIVE_ROWS"
    return None


class ApplicabilityPlanner:
    """Capability-based planner for explicit contract v0.2 declarations.

    Does not mutate input frames; does not execute the measurement rules.
    """

    def __init__(
        self,
        *,
        capabilities: Mapping[str, Capability] | None = None,
        validate_schema: bool = True,
    ):
        self.capabilities = dict(
            BUILTIN_CAPABILITIES if capabilities is None else capabilities
        )
        self.validate_schema = validate_schema

    def plan(
        self,
        contract: Mapping[str, Any],
        *,
        data: Mapping[str, pd.DataFrame] | None = None,
        evidence: Mapping[str, Any] | None = None,
        suggest: bool = False,
    ) -> EvaluationPlan:
        contract = dict(contract)
        if self.validate_schema:
            try:
                import jsonschema

                schema = json.loads(
                    files("quaxdim")
                    .joinpath("schemas", "quality-contract.schema.json")
                    .read_text(encoding="utf-8")
                )
                jsonschema.Draft202012Validator(
                    schema, format_checker=jsonschema.FormatChecker()
                ).validate(contract)
            except ImportError as exc:
                raise ContractError(
                    "JSON Schema validation requires jsonschema>=4.18"
                ) from exc
            except (OSError, ValueError) as exc:
                raise ContractError(
                    f"Cannot load contract schema: {exc}"
                ) from exc
            except jsonschema.ValidationError as exc:
                raise ContractError(
                    f"Contract v0.2 schema violation at {list(exc.path)}: {exc.message}"
                ) from exc
        for section in (
            "assets",
            "dimensions",
            "metrics",
            "rules",
            "references",
        ):
            if not _ids_unique(contract.get(section, [])):
                raise ContractError(f"Duplicate IDs in {section}")
        assets = {x["id"]: x for x in contract.get("assets", [])}
        metrics = {x["id"]: x for x in contract.get("metrics", [])}
        dimensions = {x["id"]: x for x in contract.get("dimensions", [])}
        refs = {x["id"]: x for x in contract.get("references", [])}
        rules = {x["id"]: x for x in contract.get("rules", [])}
        frames = {
            k: v.reset_index(drop=True) if isinstance(v, pd.DataFrame) else v
            for k, v in (data or {}).items()
        }
        ev = evidence or {}
        for key, value in frames.items():
            if not isinstance(value, pd.DataFrame):
                raise TypeError(
                    f"Data for asset {key} must be a pandas DataFrame in this backend"
                )
            if value.columns.duplicated().any():
                raise ContractError(
                    f"Repeated column labels in runtime data asset {key}"
                )
        decisions: list[Decision] = []
        extra_warnings: list[str] = []
        for rule in contract.get("rules", []):
            reasons: list[Reason] = []
            statuses: set[str] = set()

            def note(
                state: str,
                code: str,
                explanation: str,
                _statuses: set[str] = statuses,
                _reasons: list[Reason] = reasons,
            ) -> None:
                _statuses.add(state)
                _reasons.append(Reason(code, explanation))

            rid, aid, mid = rule["id"], rule["asset_id"], rule["metric_id"]
            metric = metrics.get(mid)
            asset = assets.get(aid)
            evaluator = str(metric.get("evaluator", "")) if metric else ""
            capability = self.capabilities.get(evaluator)
            if rule.get("extensions", {}).get("enabled") is False:
                if metric is None or asset is None:
                    decisions.append(
                        Decision(
                            rid,
                            aid,
                            mid,
                            evaluator,
                            "INVALID_CONTRACT",
                            (
                                Reason(
                                    "UNKNOWN_REFERENCE",
                                    "Disabled rule still references undeclared asset or metric",
                                ),
                            ),
                            None,
                            None,
                            tuple(metric.get("dimension_ids", ()))
                            if metric
                            else (),
                        )
                    )
                    continue
                rationale = rule.get("extensions", {}).get("reason", "")
                if not isinstance(rationale, str) or not rationale.strip():
                    note(
                        "INVALID_CONTRACT",
                        "EXCLUSION_REQUIRES_REASON",
                        "An explicitly disabled rule must provide a reason",
                    )
                else:
                    decisions.append(
                        Decision(
                            rid,
                            aid,
                            mid,
                            evaluator,
                            "NOT_APPLICABLE",
                            (Reason("EXPLICIT_EXCLUSION", rationale),),
                            None,
                            len(frames[aid]) if aid in frames else None,
                            tuple(metric.get("dimension_ids", ()))
                            if metric
                            else (),
                        )
                    )
                    continue
            if metric is None or asset is None:
                note(
                    "INVALID_CONTRACT",
                    "UNKNOWN_REFERENCE",
                    "Rule references undeclared asset or metric",
                )
            if metric is not None and any(
                d not in dimensions for d in metric.get("dimension_ids", [])
            ):
                note(
                    "INVALID_CONTRACT",
                    "UNKNOWN_DIMENSION",
                    "Metric refers to a missing dimension",
                )
            if not capability:
                note(
                    "UNSUPPORTED",
                    "EVALUATOR_NOT_REGISTERED",
                    f"No capability registered for '{evaluator}'",
                )
            level = rule.get("target", {}).get("level")
            cols = list(rule.get("target", {}).get("fields", []))
            operator = rule.get("expectation", {}).get("operator")
            if capability:
                if asset and asset.get("shape") not in capability.shapes:
                    note(
                        "UNSUPPORTED",
                        "SHAPE_UNSUPPORTED",
                        "Evaluator does not support this data shape",
                    )
                if level not in capability.levels or (
                    metric and level not in metric.get("target_levels", [])
                ):
                    note(
                        "INVALID_CONTRACT",
                        "LEVEL_MISMATCH",
                        "Metric, rule, and evaluator have incompatible levels",
                    )
                if operator not in capability.operators:
                    note(
                        "UNSUPPORTED",
                        "OPERATOR_UNSUPPORTED",
                        "Evaluator does not implement the operator",
                    )
                if len(cols) < capability.min_fields or (
                    capability.max_fields is not None
                    and len(cols) > capability.max_fields
                ):
                    note(
                        "INVALID_CONTRACT",
                        "TARGET_FIELD_COUNT",
                        "Incorrect number of target fields",
                    )
            frame = frames.get(aid)
            if frame is None:
                note(
                    "NEEDS_DATA",
                    "DATA_NOT_BOUND",
                    "Bind a dataframe to this asset before preflight",
                )
            fields = (
                {x["name"]: x for x in asset.get("fields", [])}
                if asset
                else {}
            )
            missing_schema = [c for c in cols if c not in fields]
            if missing_schema:
                note(
                    "NEEDS_CONTEXT",
                    "UNDECLARED_FIELDS",
                    f"Fields missing from data contract: {missing_schema}",
                )
            if frame is not None:
                memory_limit = contract.get("evaluation", {}).get(
                    "max_memory_mb"
                )
                if (
                    memory_limit is not None
                    and frame.memory_usage(index=True, deep=True).sum()
                    > float(memory_limit) * 1024**2
                ):
                    note(
                        "BLOCKED_RESOURCE",
                        "MEMORY_BUDGET_EXCEEDED",
                        "Bound in-memory data exceed declared memory budget",
                    )
                missing_actual = [c for c in cols if c not in frame.columns]
                if missing_actual:
                    note(
                        "NEEDS_CONTEXT",
                        "RUNTIME_FIELDS_MISSING",
                        f"Fields missing from runtime: {missing_actual}",
                    )
            if capability and capability.scalar_only:
                prohibited = {
                    "struct",
                    "list",
                    "array",
                    "map",
                    "tensor",
                    "geometry",
                    "raster",
                    "document",
                    "mixed",
                    "variant",
                }
                for c in cols:
                    if (
                        fields.get(c, {}).get("logical_type", "").lower()
                        in prohibited
                    ):
                        # variant may still be supported by a specialized evaluator; this one is not.
                        note(
                            "UNSUPPORTED",
                            "NONSCALAR_FIELD",
                            f"{evaluator} requires scalar fields; found {c}",
                        )
            # Rules with specified prerequisites must reference declared rule IDs.
            for dep in rule.get("dependencies", []):
                if dep not in rules:
                    note(
                        "INVALID_CONTRACT",
                        "MISSING_DEPENDENCY",
                        f"Dependency {dep} not declared",
                    )
                elif dep == rid:
                    note(
                        "INVALID_CONTRACT",
                        "SELF_DEPENDENCY",
                        "Rule depends on itself",
                    )
            # Metadata-driven preflight for the additional registered evaluator kernels.
            expectation = rule.get("expectation", {})
            if evaluator == "builtin.allowed_values":
                values = expectation.get("values")
                if (
                    not isinstance(values, list)
                    or not values
                    or len(values) > 100_000
                    or any(
                        isinstance(v, (list, dict, set)) or v is None
                        for v in values
                    )
                ):
                    note(
                        "INVALID_CONTRACT",
                        "INVALID_ALLOWED_SET",
                        "Declare a nonempty scalar value list",
                    )
            if evaluator == "builtin.value_range":
                operator = expectation.get("operator")
                lo = (
                    expectation.get("lower")
                    if operator == "between"
                    else expectation.get("value")
                )
                hi = (
                    expectation.get("upper")
                    if operator == "between"
                    else expectation.get("value")
                )

                def bounded(x):
                    try:
                        return (
                            not isinstance(x, bool)
                            and isinstance(x, (float, int))
                            and math.isfinite(x)
                        )
                    except (TypeError, ValueError):
                        return False

                if (operator in ("min", "between") and not bounded(lo)) or (
                    operator in ("max", "between") and not bounded(hi)
                ):
                    note(
                        "INVALID_CONTRACT",
                        "INVALID_RANGE_BOUNDS",
                        "Declare finite numeric bounds",
                    )
                elif operator == "between" and lo > hi:
                    note(
                        "INVALID_CONTRACT",
                        "INVALID_RANGE_ORDER",
                        "Lower bound exceeds upper bound",
                    )
            if evaluator == "builtin.regex_fullmatch":
                pattern = expectation.get("pattern")
                if (
                    not isinstance(pattern, str)
                    or not pattern
                    or len(pattern) > 512
                ):
                    note(
                        "INVALID_CONTRACT",
                        "INVALID_REGEX",
                        "Pattern must be 1..512 characters",
                    )
                else:
                    try:
                        import regex

                        regex.compile(pattern)
                    except ImportError:
                        note(
                            "BLOCKED_RESOURCE",
                            "REGEX_EXTRA_MISSING",
                            "Install optional [regex] extra",
                        )
                    except Exception:
                        note(
                            "INVALID_CONTRACT",
                            "REGEX_COMPILE_FAILED",
                            "Invalid regular expression",
                        )
            if evaluator == "builtin.column_compare":
                if expectation.get("extensions", {}).get("comparison") not in {
                    "lt",
                    "lte",
                    "eq",
                    "ne",
                    "gte",
                    "gt",
                }:
                    note(
                        "INVALID_CONTRACT",
                        "COLUMN_COMPARE_OPERATOR",
                        "Declare supported comparison in extensions",
                    )
            if evaluator == "builtin.composite_uniqueness":
                keys = rule.get("expectation", {}).get("key_fields", [])
                if keys != cols or len(set(cols)) != len(cols):
                    note(
                        "INVALID_CONTRACT",
                        "IDENTITY_KEY_MISMATCH",
                        "Target fields and declared key fields must match without repeats",
                    )
            if evaluator == "builtin.max_delay":
                extra = rule.get("expectation", {}).get("extensions", {})
                unit = extra.get("unit")
                if unit not in {"calendar_days", "hours", "seconds"}:
                    note(
                        "NEEDS_CONTEXT",
                        "DELAY_UNIT_MISSING",
                        "Declare supported delay unit",
                    )
                if (
                    extra.get("start") not in cols
                    or extra.get("end") not in cols
                    or extra.get("start") == extra.get("end")
                ):
                    note(
                        "INVALID_CONTRACT",
                        "DELAY_CLOCKS_INVALID",
                        "Two distinct clocks with matching field names are required",
                    )
                delay_limit = rule.get("expectation", {}).get("value")
                if (
                    isinstance(delay_limit, bool)
                    or not isinstance(delay_limit, (int, float))
                    or not math.isfinite(delay_limit)
                    or delay_limit < 0
                ):
                    note(
                        "INVALID_CONTRACT",
                        "DELAY_LIMIT_INVALID",
                        "Non-negative numeric delay is required",
                    )
                for c in cols:
                    logical = fields.get(c, {}).get("logical_type", "")
                    if logical not in {
                        "date",
                        "datetime",
                        "local_datetime",
                        "instant",
                        "timestamp",
                        "date_time",
                    }:
                        note(
                            "NEEDS_CONTEXT",
                            "TEMPORAL_ROLE_UNDECLARED",
                            f"Declare temporal logical type for {c}",
                        )
                    if (
                        logical == "instant"
                        and frame is not None
                        and c in frame.columns
                    ):
                        dtype = frame[c].dtype
                        if pd.api.types.is_datetime64_any_dtype(dtype):
                            tz = getattr(frame[c].dt, "tz", None)
                            if tz is None and not fields[c].get(
                                "representation", {}
                            ).get("timezone"):
                                note(
                                    "NEEDS_CONTEXT",
                                    "INSTANT_TIMEZONE_UNDECLARED",
                                    f"Naive instant {c} requires an explicit timezone",
                                )
                    if frame is not None and c in frame.columns:
                        _, issue = _datetime_series(frame, c, fields)
                        if issue:
                            note(
                                "AMBIGUOUS",
                                issue,
                                f"Cannot interpret clock field {c} safely",
                            )
                # Comparing instants with naive local timestamps is risky: UTC != local.
                if len(cols) == 2:
                    a, b = (fields.get(c, {}) for c in cols)
                    if {a.get("logical_type"), b.get("logical_type")} == {
                        "local_datetime",
                        "instant",
                    }:
                        zone = a.get("representation", {}).get("timezone")
                        if not zone:
                            note(
                                "NEEDS_CONTEXT",
                                "TIMEZONE_REQUIRED",
                                "Mixed local time and instant require a declared IANA zone",
                            )
            # evidence_required lists evidence kinds; reference_ids lists IDs.
            kinds_needed = (
                set(metric.get("evidence_required", [])) if metric else set()
            )
            ref_ids = set(rule.get("reference_ids", []))
            expectation_ref = rule.get("expectation", {}).get("reference_id")
            if expectation_ref:
                ref_ids.add(expectation_ref)
            for ref_id in ref_ids:
                if ref_id not in refs:
                    note(
                        "INVALID_CONTRACT",
                        "REFERENCE_UNDECLARED",
                        f"Reference {ref_id} is not defined",
                    )
                elif ref_id not in ev:
                    note(
                        "NEEDS_EVIDENCE",
                        "REFERENCE_UNRESOLVED",
                        f"Reference {ref_id} is described but content is not bound/verified",
                    )
                else:
                    payload = ev[ref_id]
                    verified = False
                    supplied_version = None
                    if isinstance(payload, Mapping):
                        values = payload.get("values")
                        supplied_version = payload.get("version")
                        verified = (
                            payload.get("verified") is True
                            and values is not None
                            and not isinstance(values, (str, bytes))
                            and hasattr(values, "__len__")
                            and len(values) > 0
                        )
                    if not verified:
                        note(
                            "NEEDS_EVIDENCE",
                            "REFERENCE_UNVERIFIED",
                            f"Reference {ref_id} requires verified values and provenance",
                        )
                    if refs[ref_id].get(
                        "version"
                    ) and supplied_version != refs[ref_id].get("version"):
                        note(
                            "NEEDS_EVIDENCE",
                            "REFERENCE_VERSION_MISMATCH",
                            f"Reference {ref_id} version must match contract, including when omitted",
                        )
                    # Official catalog validity may be narrower than a temporal rule.
                    when_period = rule.get("when_period", {})
                    ref_period = refs[ref_id].get("valid_period", {})
                    if when_period and ref_period:
                        if str(when_period.get("from", "")) < str(
                            ref_period.get("from", "")
                        ) or str(when_period.get("to", "")) > str(
                            ref_period.get("to", "")
                        ):
                            note(
                                "NEEDS_EVIDENCE",
                                "REFERENCE_TEMPORAL_SCOPE",
                                "Reference validity does not cover the rule period",
                            )
            if evaluator == "reference.lookup_temporal":
                for ref_id in ref_ids:
                    ref = refs.get(ref_id, {})
                    if ref.get("valid_period") and not rule.get("when_period"):
                        note(
                            "NEEDS_CONTEXT",
                            "REFERENCE_PERIOD_UNSCOPED",
                            "A time-limited catalog requires explicit when_period; otherwise historical data may be misclassified",
                        )
            if capability and capability.evidence_required and not ref_ids:
                note(
                    "NEEDS_EVIDENCE",
                    "REFERENCE_NOT_SELECTED",
                    "External lookup requires a reference ID",
                )
            for kind in kinds_needed:
                if not any(
                    refs.get(r, {}).get("kind") == kind for r in ref_ids
                ):
                    note(
                        "NEEDS_EVIDENCE",
                        "REFERENCE_KIND_UNSATISFIED",
                        f"No reference of kind {kind}",
                    )
            # Applicability to the actual observations.
            eligible: int | None = None
            total: int | None = len(frame) if frame is not None else None
            if frame is not None and not (
                {"INVALID_CONTRACT", "UNSUPPORTED", "BLOCKED_RESOURCE"}
                & statuses
            ):
                active = pd.Series(True, index=frame.index, dtype=bool)
                where = rule.get("when")
                if where is not None:
                    w, err = _predicate_mask(frame, where)
                    if err:
                        note(
                            "AMBIGUOUS",
                            err,
                            "Cannot safely evaluate rule predicate",
                        )
                    elif w is not None:
                        active &= w
                if rule.get("when_period") is not None:
                    w, err = _time_mask(frame, rule["when_period"], fields)
                    if err:
                        note(
                            "AMBIGUOUS",
                            err,
                            "Cannot safely establish temporal eligibility",
                        )
                    elif w is not None:
                        active &= w
                if "AMBIGUOUS" not in statuses:
                    eligible = int(active.sum())
                    if eligible == 0:
                        note(
                            "NO_ELIGIBLE_DATA",
                            "EMPTY_ELIGIBLE_POPULATION",
                            "No observations meet the declared selection",
                        )
                    else:
                        for c in cols:
                            if c in fields:
                                issue = _variant_problem(
                                    fields[c], frame, fields, active
                                )
                                if issue:
                                    note(
                                        "AMBIGUOUS",
                                        issue,
                                        f"Representation variant for {c} cannot be selected unambiguously",
                                    )
            decisions.append(
                Decision(
                    rid,
                    aid,
                    mid,
                    evaluator,
                    _status(statuses),
                    tuple(reasons),
                    eligible,
                    total,
                    tuple(metric.get("dimension_ids", ())) if metric else (),
                )
            )
        # Propagate dependency issues, including cycles, without silently choosing an ordering.
        index = {r.rule_id: r for r in decisions}
        graph = {
            r["id"]: set(r.get("dependencies", []))
            for r in contract.get("rules", [])
        }
        visited: set[str] = set()
        visiting: set[str] = set()
        cycles: set[str] = set()

        def find_cycles(node: str, path: list[str]) -> None:
            if node in visiting:
                cycles.update(path[path.index(node) :])
                return
            if node in visited or node not in graph:
                return
            visiting.add(node)
            path.append(node)
            for dep in graph[node]:
                find_cycles(dep, path)
            path.pop()
            visiting.remove(node)
            visited.add(node)

        for node in graph:
            find_cycles(node, [])
        memo: dict[str, Decision] = {}

        def decide(rid: str) -> Decision:
            if rid in memo:
                return memo[rid]
            base = index[rid]
            reasons = list(base.reasons)
            if rid in cycles:
                reasons.append(
                    Reason(
                        "DEPENDENCY_CYCLE", "Circular dependency between rules"
                    )
                )
                status = "INVALID_CONTRACT"
            else:
                bad = [
                    dep
                    for dep in graph.get(rid, ())
                    if dep in index and decide(dep).status != "READY"
                ]
                status = base.status
                if bad and status == "READY":
                    status = "BLOCKED_DEPENDENCY"
                    reasons.append(
                        Reason(
                            "PREREQUISITE_NOT_READY",
                            f"Blocked by rules: {sorted(bad)}",
                        )
                    )
            memo[rid] = Decision(
                base.rule_id,
                base.asset_id,
                base.metric_id,
                base.evaluator,
                status,
                tuple(reasons),
                base.eligible_rows,
                base.total_rows,
                base.dimension_ids,
                base.source,
            )
            return memo[rid]

        output = [decide(d.rule_id) for d in decisions]
        if suggest:
            # The richer discovery advisor is advisory only and never appends rules
            # to the actual contract. No raw category values enter preflight reports.
            from .discovery import discover

            declared = {
                (
                    r["asset_id"],
                    metrics[r["metric_id"]]["evaluator"],
                    tuple(r.get("target", {}).get("fields", ())),
                )
                for r in rules.values()
                if r.get("metric_id") in metrics
            }
            for aid, frame in frames.items():
                if aid not in assets:
                    continue
                hints = discover(frame, asset_id=aid)
                for proposed in hints.recommendations:
                    if proposed.kind != "rule" or not proposed.evaluator:
                        continue
                    if (aid, proposed.evaluator, proposed.fields) in declared:
                        continue
                    output.append(
                        Decision(
                            proposed.id,
                            aid,
                            "unapproved",
                            proposed.evaluator,
                            "RECOMMENDED",
                            (
                                Reason("USER_REVIEW_REQUIRED", proposed.title),
                                Reason("HEURISTIC_ONLY", proposed.rationale),
                            ),
                            None,
                            len(frame),
                            (proposed.dimension,)
                            if proposed.dimension
                            else (),
                            "suggestion",
                        )
                    )
        return EvaluationPlan(
            tuple(output),
            str(contract.get("contract_version", "unknown")),
            tuple(extra_warnings),
        )


def load_contract(path: str | Path) -> dict[str, Any]:
    """Load JSON or YAML safely. No code execution or unsafe YAML tags."""
    path = Path(path)
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
    elif path.suffix.lower() in {".yaml", ".yml"}:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    else:
        raise ValueError(
            "Contract file extension must be .json, .yaml or .yml"
        )
    if not isinstance(data, dict):
        raise ContractError("Contract root must be a mapping")
    return data
