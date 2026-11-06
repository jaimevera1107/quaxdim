"""Read-only v0.2 contract executor for a deliberately narrow, proven pandas
backend.

Planning and measurement are independent: READY never means PASS. Each unit of
measurement receives exactly one of PASS, FAIL, UNKNOWN or EXCLUDED. The
reported rate never hides UNKNOWN, and a threshold cannot pass on insufficient
certainty. No input frame or external reference is modified.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ._lazy import LazyModule
from .pandas_evaluators import EXTRA_PANDAS_EVALUATORS, evaluate_pandas_extra

if TYPE_CHECKING:
    import numpy as np
    import pandas as pd
else:
    np = LazyModule("numpy", extra="pandas")
    pd = LazyModule("pandas", extra="pandas")

from .applicability import (
    ApplicabilityPlanner,
    Decision,
    EvaluationPlan,
    _datetime_series,
    _time_mask,
)
from .bounds import rate_bounds
from .evaluator_registry import bound_model
from .missingness import empty_string_is_missing


class EvaluationError(ValueError):
    """Evaluation cannot be completed with the supplied inputs, safely."""


@dataclass(frozen=True)
class ContractRuleResult:
    """Measured outcome of one rule: counts, rates and status."""

    rule_id: str
    asset_id: str
    metric_id: str
    dimension_ids: tuple[str, ...]
    status: str
    planner_status: str
    total_rows: int | None
    scoped_rows: int | None
    excluded_rows: int | None
    unit: str
    eligible_units: int | None
    passed_units: int | None
    failed_units: int | None
    indeterminate_units: int | None
    excluded_units: int | None
    measured_rate: float | None
    lower_rate: float | None
    upper_rate: float | None
    assessment_coverage: float | None
    threshold: dict[str, Any] | None
    severity: str
    failure_action: str
    sample_failed_positions: tuple[int, ...] = ()
    sample_indeterminate_positions: tuple[int, ...] = ()
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            **asdict(self),
            "sample_failed_positions": list(self.sample_failed_positions),
            "sample_indeterminate_positions": list(
                self.sample_indeterminate_positions
            ),
            "dimension_ids": list(self.dimension_ids),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class ContractExecutionReport:
    """Results of executing a contract, together with its plan."""

    contract_version: str
    contract_sha256: str
    generated_at: str
    plan: EvaluationPlan
    results: tuple[ContractRuleResult, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "contract_version": self.contract_version,
            "contract_sha256": self.contract_sha256,
            "generated_at": self.generated_at,
            "all_passed": self.passed,
            "plan": self.plan.to_dict(),
            "results": [r.to_dict() for r in self.results],
        }

    def to_json(self, path: str | Path | None = None) -> str:
        """Serialise to JSON, optionally writing it to ``path``.

        Returns:
            The JSON text.
        """
        data = json.dumps(
            self.to_dict(), indent=2, ensure_ascii=False, allow_nan=False
        )
        if path is not None:
            Path(path).write_text(data + "\n", encoding="utf-8")
        return data

    def to_frame(self) -> pd.DataFrame:
        """Return one row per rule as a pandas DataFrame."""
        return pd.DataFrame([r.to_dict() for r in self.results])

    @property
    def passed(self) -> bool:
        """An unmeasurable/unevaluated obligation is never a successful
        execution.
        """
        applicable = [r for r in self.results if r.status != "NOT_APPLICABLE"]
        return bool(applicable) and all(r.status == "PASS" for r in applicable)

    def dimension_summary(self) -> pd.DataFrame:
        """Summarise by dimension, without a universal index.

        Overlapping rules are never averaged implicitly.
        """
        rows = []
        for dim in sorted({d for x in self.results for d in x.dimension_ids}):
            subset = [x for x in self.results if dim in x.dimension_ids]
            rows.append(
                {
                    "dimension_id": dim,
                    "rules": len(subset),
                    "pass": sum(x.status == "PASS" for x in subset),
                    "fail": sum(x.status == "FAIL" for x in subset),
                    "indeterminate": sum(
                        x.status == "INDETERMINATE" for x in subset
                    ),
                    "blocked_or_other": sum(
                        x.status not in {"PASS", "FAIL", "INDETERMINATE"}
                        for x in subset
                    ),
                }
            )
        return pd.DataFrame(rows)


def _mask(df: pd.DataFrame, value: bool = True) -> np.ndarray:
    return np.full(len(df), value, dtype=np.bool_)


def _is_missing(
    series: pd.Series,
    declaration: Mapping[str, Any],
    df: pd.DataFrame | None = None,
) -> np.ndarray:
    policy = declaration.get("missing_policy", {})
    missing = series.isna().to_numpy(dtype=bool, copy=True)
    if policy.get("intrinsic_nulls") is False:
        # The missing cell cannot be silently converted into a present scalar.
        # Treat intrinsic null as unknown/missing for conservative quality
        # checks.
        missing = series.isna().to_numpy(dtype=bool, copy=True)
    # The empty-string default only applies to text columns: a numeric
    # column cannot hold "" and must not be stringified to look for it.
    textual = pd.api.types.is_object_dtype(
        series.dtype
    ) or pd.api.types.is_string_dtype(series.dtype)
    empty_is_missing = textual and empty_string_is_missing(policy)
    if empty_is_missing or policy.get("whitespace_only"):
        s = series.astype("string")
        if empty_is_missing:
            missing |= s.eq("").fillna(False).to_numpy(dtype=bool)
        if policy.get("whitespace_only"):
            missing |= s.str.strip().eq("").fillna(False).to_numpy(dtype=bool)
    tokens = policy.get("tokens", [])
    if tokens:
        normalize = policy.get("normalization", "none")

        def string_normalize(x: str) -> str:
            if normalize in {"trim", "trim_casefold"}:
                x = x.strip()
            if normalize in {"casefold", "trim_casefold"}:
                x = x.casefold()
            return x

        # Preserve type identity: numeric sentinel -1 differs from string '-1'.
        numeric_tokens = [
            x
            for x in tokens
            if isinstance(x, (int, float)) and not isinstance(x, bool)
        ]
        string_tokens = [
            string_normalize(x) for x in tokens if isinstance(x, str)
        ]
        if numeric_tokens and pd.api.types.is_numeric_dtype(series.dtype):
            missing |= series.isin(numeric_tokens).to_numpy(dtype=bool)
        elif numeric_tokens and series.dtype == object:
            missing |= series.map(
                lambda v: (
                    isinstance(v, (int, float))
                    and not isinstance(v, bool)
                    and v in numeric_tokens
                )
            ).to_numpy(dtype=bool)
        if string_tokens:
            # Comparing only string values, never textualizing legitimate
            # numeric codes.
            strings = series.map(
                lambda v: string_normalize(v) if isinstance(v, str) else None
            )
            missing |= strings.isin(string_tokens).to_numpy(dtype=bool)
    # If a custom missingness policy is conditioned on rows, keep other rows
    # untouched.
    conditions = policy.get("when")
    if conditions:
        if df is None:
            raise EvaluationError(
                "Conditional missing policy requires the whole dataset"
            )
        relevant, unknown = _predicates(df, conditions)
        if unknown.any():
            raise EvaluationError(
                "Conditional missing policy is ambiguous for some rows"
            )
        missing &= relevant
        # Physical missing must remain missing regardless of conditioned
        # sentinel tokens.
        missing |= series.isna().to_numpy(dtype=bool)
    return missing


def _predicates(
    df: pd.DataFrame,
    conditions: list[Mapping[str, Any]] | Mapping[str, Any],
    *,
    mode: str = "all",
) -> tuple[np.ndarray, np.ndarray]:
    """Three-valued predicates (AND or OR); no eval/exec."""
    if mode not in {"all", "any"}:
        raise EvaluationError("Predicate mode must be 'all' or 'any'")
    if isinstance(conditions, Mapping):
        conditions = [conditions]
    if not isinstance(conditions, list):
        raise EvaluationError("Invalid predicates (expected a list)")
    truth = _mask(df, mode == "all")
    unknown = _mask(df, False)
    for predicate in conditions:
        col = predicate.get("field")
        if col not in df:
            raise EvaluationError(f"Predicate field {col} does not exist")
        s = df[col]
        op = predicate.get("op")
        isnull = s.isna().to_numpy(dtype=bool)
        try:
            if op in ("exists", "not_null"):
                val = ~isnull
                undef = _mask(df, False)
            elif op in ("not_exists", "is_null"):
                val = isnull
                undef = _mask(df, False)
            else:
                undef = isnull.copy()
                if op == "eq":
                    val = s.eq(predicate.get("value"))
                elif op == "ne":
                    val = s.ne(predicate.get("value"))
                elif op == "in":
                    val = s.isin(predicate.get("values", []))
                elif op == "not_in":
                    val = ~s.isin(predicate.get("values", []))
                elif op == "lt":
                    val = s.lt(predicate.get("value"))
                elif op == "lte":
                    val = s.le(predicate.get("value"))
                elif op == "gt":
                    val = s.gt(predicate.get("value"))
                elif op == "gte":
                    val = s.ge(predicate.get("value"))
                elif op == "between":
                    val = s.ge(predicate.get("from")) & s.le(
                        predicate.get("to")
                    )
                else:
                    raise EvaluationError(
                        f"Unsupported predicate operator: {op}"
                    )
                if hasattr(val, "isna"):
                    undef |= val.isna().to_numpy(dtype=bool)
                val = val.fillna(False) if hasattr(val, "fillna") else val
                val = np.asarray(val, dtype=bool)
            val = np.asarray(val, dtype=bool)
            if mode == "all":
                # False dominates Unknown for conjunction.
                was_false = ~truth & ~unknown
                is_false = was_false | ~(val | undef)
                new_unknown = (~is_false) & (unknown | undef)
                truth = ~(is_false | new_unknown)
                unknown = new_unknown
            else:
                # True dominates Unknown for disjunction.
                is_true = truth | (val & ~undef)
                unknown = (~is_true) & (unknown | undef)
                truth = is_true
        except (TypeError, ValueError) as exc:
            raise EvaluationError(
                f"Predicate {col} could not be evaluated: {type(exc).__name__}"
            ) from exc
    return truth, unknown


def _period_scope(
    df: pd.DataFrame, rule: Mapping[str, Any], fields: Mapping[str, Any]
) -> np.ndarray:
    if "when_period" not in rule:
        return _mask(df)
    result, problem = _time_mask(df, rule["when_period"], fields)
    if problem or result is None:
        raise EvaluationError(
            f"Temporal scope cannot be safely evaluated: {problem}"
        )
    return result.to_numpy(dtype=bool)


def _failure_status(
    lower: float, upper: float, threshold: Mapping[str, Any]
) -> str:
    op = threshold["operator"]
    boundary = float(threshold["value"])
    if not math.isfinite(boundary) or not 0 <= boundary <= 1:
        raise EvaluationError(
            "Ratio threshold must be finite and within [0,1]"
        )
    if op == "gte":
        if lower >= boundary:
            return "PASS"
        if upper < boundary:
            return "FAIL"
    elif op == "gt":
        if lower > boundary:
            return "PASS"
        if upper <= boundary:
            return "FAIL"
    elif op == "lte":
        if upper <= boundary:
            return "PASS"
        if lower > boundary:
            return "FAIL"
    elif op == "lt":
        if upper < boundary:
            return "PASS"
        if lower >= boundary:
            return "FAIL"
    elif op == "eq":
        if lower == upper == boundary:
            return "PASS"
        if boundary < lower or boundary > upper:
            return "FAIL"
    elif op == "between":
        hi = float(threshold.get("upper", float("nan")))
        if not math.isfinite(hi) or not boundary <= hi <= 1:
            raise EvaluationError("Invalid between threshold")
        if lower >= boundary and upper <= hi:
            return "PASS"
        if upper < boundary or lower > hi:
            return "FAIL"
    else:
        raise EvaluationError(f"Unsupported threshold operator: {op}")
    return "INDETERMINATE"


def _normalise_dates(
    s: pd.Series, f: Mapping[str, Any], *, unit: str
) -> pd.Series:
    # v0.3 parser validates explicit patterns or declared datetime dtypes.
    temp = pd.DataFrame({"v": s})
    dates, problem = _datetime_series(temp, "v", {"v": f})
    if dates is None or problem:
        raise EvaluationError(f"Date parsing unsafe: {problem}")
    if not pd.api.types.is_datetime64_any_dtype(dates.dtype):
        raise EvaluationError("Temporal data are not datetime64")
    try:
        tz = f.get("representation", {}).get("timezone")
        logical = f.get("logical_type")
        if dates.dt.tz is None:
            if tz:
                dates = dates.dt.tz_localize(
                    tz, ambiguous="NaT", nonexistent="NaT"
                )
            elif logical == "instant":
                raise EvaluationError("Naive instant needs declared timezone")
        if dates.dt.tz is not None:
            return dates.dt.tz_convert("UTC")
        return dates
    except (ValueError, TypeError) as exc:
        raise EvaluationError(
            f"Timezone conversion failed: {type(exc).__name__}"
        ) from exc


class ContractExecutor:
    """Run the four formally planned evaluators against pandas tables.

    Unknown data are not silently discarded. Reports contain row positions
    only;
    samples never expose actual values. Run preflight first; non-READY controls
    yield blocked decisions, not manufactured measurement results.
    """

    def __init__(
        self,
        *,
        planner: ApplicabilityPlanner | None = None,
        max_failure_examples: int = 10,
    ):
        """Create an executor.

        Args:
            planner: Planner used for the preflight; defaults to a new one.
            max_failure_examples: Maximum failing row positions kept per rule.
        """
        self.planner = planner or ApplicabilityPlanner()
        if max_failure_examples < 0:
            raise ValueError("max_failure_examples must be >= 0")
        self.max_failure_examples = max_failure_examples

    def run(
        self,
        contract: Mapping[str, Any],
        *,
        data: Mapping[str, pd.DataFrame],
        evidence: Mapping[str, Any] | None = None,
        plan: EvaluationPlan | None = None,
    ) -> ContractExecutionReport:
        """Plan and execute ``contract`` on pandas tables.

        Rules that are not READY are reported as blocked, never as passed.
        """
        # Replan against the supplied objects; don't trust an obsolete user
        # plan.
        # If a plan is explicitly supplied, reject any incompatible decisions.
        actual_plan = self.planner.plan(contract, data=data, evidence=evidence)
        if plan is not None and plan.to_dict() != actual_plan.to_dict():
            raise EvaluationError(
                "Supplied plan differs from a fresh preflight"
            )
        metrics = {x["id"]: x for x in contract.get("metrics", [])}
        rules = {x["id"]: x for x in contract.get("rules", [])}
        assets = {x["id"]: x for x in contract.get("assets", [])}
        decisions = {x.rule_id: x for x in actual_plan.decisions}
        # Visit dependencies in topological order; planner already marks
        # cycles.
        results: dict[str, ContractRuleResult] = {}

        def run_rule(rule_id: str) -> ContractRuleResult:
            if rule_id in results:
                return results[rule_id]
            d = decisions[rule_id]
            rule = rules[rule_id]
            if d.status != "READY":
                result = self._blocked(d, rule)
            else:
                deps = [run_rule(dep) for dep in rule.get("dependencies", [])]
                if any(x.status != "PASS" for x in deps):
                    result = self._blocked(
                        d,
                        rule,
                        status="BLOCKED_DEPENDENCY",
                        reason="At least one prerequisite did not pass",
                    )
                else:
                    try:
                        df = data[d.asset_id].reset_index(drop=True)
                        fields = {
                            x["name"]: x
                            for x in assets[d.asset_id].get("fields", [])
                        }
                        result = self._execute(
                            rule,
                            metrics[d.metric_id],
                            df,
                            fields,
                            evidence or {},
                            assets[d.asset_id],
                        )
                    except (
                        EvaluationError,
                        ValueError,
                        TypeError,
                        OverflowError,
                        KeyError,
                    ) as exc:
                        result = self._blocked(
                            d,
                            rule,
                            status="ERROR",
                            reason=f"{type(exc).__name__}: {exc}",
                        )
            results[rule_id] = result
            return result

        # The planner catches dependency cycles, but do not descend into them.
        for rule_id in rules:
            run_rule(rule_id)
        digest = sha256(
            json.dumps(
                contract,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        return ContractExecutionReport(
            str(contract.get("contract_version", "unknown")),
            digest,
            datetime.now(timezone.utc).isoformat(),
            actual_plan,
            tuple(results[rid] for rid in rules),
        )

    @staticmethod
    def _blocked(
        d: Decision,
        rule: Mapping[str, Any],
        *,
        status: str | None = None,
        reason: str | None = None,
    ) -> ContractRuleResult:
        return ContractRuleResult(
            rule_id=d.rule_id,
            asset_id=d.asset_id,
            metric_id=d.metric_id,
            dimension_ids=d.dimension_ids,
            status=status or d.status,
            planner_status=d.status,
            total_rows=d.total_rows,
            scoped_rows=d.eligible_rows,
            excluded_rows=None,
            unit="not_measured",
            eligible_units=None,
            passed_units=None,
            failed_units=None,
            indeterminate_units=None,
            excluded_units=None,
            measured_rate=None,
            lower_rate=None,
            upper_rate=None,
            assessment_coverage=None,
            threshold=rule.get("threshold"),
            severity=rule.get("severity", "medium"),
            failure_action=rule.get("failure_action", "report"),
            reasons=tuple(
                [r.code + ": " + r.message for r in d.reasons]
                + ([reason] if reason else [])
            ),
        )

    def _execute(
        self,
        rule: Mapping[str, Any],
        metric: Mapping[str, Any],
        df: pd.DataFrame,
        fields: Mapping[str, Any],
        evidence: Mapping[str, Any],
        asset: Mapping[str, Any],
    ) -> ContractRuleResult:
        rid = rule["id"]
        names = list(rule["target"]["fields"])
        evaluator = metric["evaluator"]
        threshold = rule.get("threshold")
        # In v0.4, all implemented metrics are rates requiring explicit
        # thresholds.
        if (
            metric.get("kind") != "conformance"
            or metric.get("unit") != "ratio"
        ):
            raise EvaluationError(
                "Only conformance ratio metrics are executable in v0.4"
            )
        if not threshold:
            raise EvaluationError(
                "Conformance evaluator requires an explicit threshold"
            )
        missing_policy = metric.get("null_policy", "indeterminate")
        if missing_policy not in {
            "exclude",
            "include_as_failure",
            "include_as_pass",
            "indeterminate",
        }:
            raise EvaluationError(f"Unsupported null policy: {missing_policy}")
        selected = _mask(df)
        filter_unknown = _mask(df, False)
        if rule.get("when"):
            selected, filter_unknown = _predicates(df, rule["when"])
        selected &= _period_scope(df, rule, fields)
        filter_unknown &= _period_scope(df, rule, fields)
        if rule.get("exceptions"):
            exc_true, exc_unknown = _predicates(
                df, rule["exceptions"], mode="any"
            )
            # An exception whose truth cannot be established yields UNKNOWN,
            # not exclusion.
            filter_unknown |= selected & exc_unknown
            selected &= ~exc_true & ~exc_unknown
        selected &= ~filter_unknown
        scoped = int(selected.sum())
        # No samples include full original values, even when the field holds
        # sensitive data.
        if evaluator == "builtin.presence":
            unit = "record" if rule["target"]["level"] == "record" else "value"
            misses = np.column_stack(
                [_is_missing(df[c], fields[c], df) for c in names]
            )
            if unit == "record":
                good = ~misses.any(axis=1)
                state = np.where(good, 1, 0).astype(np.int8)
                state[~selected] = -2
                unknown_scope_units = int(filter_unknown.sum())
            else:
                state = np.where(~misses, 1, 0).astype(np.int8)
                state[~selected, :] = -2
                unknown_scope_units = int(filter_unknown.sum()) * len(names)
            # Presence is not an eligibility-null rule; physical/sentinel
            # missing
            # values are failures regardless of the metric null policy.
        elif evaluator == "builtin.composite_uniqueness":
            unit = "record"
            key_misses = np.column_stack(
                [_is_missing(df[c], fields[c], df) for c in names]
            ).any(axis=1)
            state = np.full(len(df), -2, dtype=np.int8)
            valid = selected & ~key_misses
            if valid.any():
                frame_keys = df.loc[valid, names].copy()
                for c in names:
                    s = frame_keys[c]
                    # For object keys, distinguish e.g. boolean True from
                    # integer 1.
                    if s.dtype == object:
                        if s.map(type).nunique() > 1:
                            frame_keys[c] = s.map(
                                lambda x: (type(x).__name__, x)
                            )
                try:
                    dup = frame_keys.duplicated(keep=False).to_numpy(
                        dtype=bool
                    )
                except TypeError as exc:
                    raise EvaluationError(
                        "Non-hashable key; use a scalar comparator"
                    ) from exc
                state[valid] = np.where(dup, 0, 1)
            nullstate = {
                "exclude": -2,
                "include_as_failure": 0,
                "include_as_pass": 1,
                "indeterminate": -1,
            }[missing_policy]
            state[selected & key_misses] = nullstate
            unknown_scope_units = int(filter_unknown.sum())
        elif evaluator == "builtin.max_delay":
            unit = "record"
            ext = rule["expectation"].get("extensions", {})
            start, end = ext["start"], ext["end"]
            scale = ext["unit"]
            first = _normalise_dates(df[start], fields[start], unit=scale)
            last = _normalise_dates(df[end], fields[end], unit=scale)
            if (first.dt.tz is None) != (last.dt.tz is None):
                raise EvaluationError(
                    "Cannot mix zoned and unzoned datetime series"
                )
            if scale == "calendar_days":
                # Calendar days are civil days in a *specified* timezone, not
                # UTC
                # 24-hour blocks. If absent, use the start clock's declared
                # zone.
                civil_zone = ext.get("calendar_timezone") or fields[start].get(
                    "representation", {}
                ).get("timezone")
                if first.dt.tz is not None:
                    if not civil_zone:
                        raise EvaluationError(
                            "Calendar timezone must be explicit for zoned "
                            "clocks"
                        )
                    a = first.dt.tz_convert(civil_zone).dt.date
                    b = last.dt.tz_convert(civil_zone).dt.date
                    delta = pd.Series(
                        (pd.to_datetime(b) - pd.to_datetime(a)).dt.days,
                        index=df.index,
                    )
                else:
                    delta = (
                        last.dt.normalize() - first.dt.normalize()
                    ).dt.days
            elif scale == "hours":
                delta = (last - first).dt.total_seconds() / 3600.0
            else:
                delta = (last - first).dt.total_seconds()
            nan = delta.isna().to_numpy(dtype=bool)
            valid_value = delta.ge(0) & delta.le(
                float(rule["expectation"]["value"])
            )
            state = np.full(len(df), -2, dtype=np.int8)
            state[selected & ~nan] = np.where(
                valid_value.to_numpy(dtype=bool)[selected & ~nan], 1, 0
            )
            state[selected & nan] = {
                "exclude": -2,
                "include_as_failure": 0,
                "include_as_pass": 1,
                "indeterminate": -1,
            }[missing_policy]
            unknown_scope_units = int(filter_unknown.sum())
        elif evaluator == "reference.lookup_temporal":
            unit = "value"
            ref_id = rule["expectation"].get("reference_id")
            if not ref_id or ref_id not in evidence:
                raise EvaluationError("Reference evidence not bound")
            bundle = evidence[ref_id]
            if (
                not isinstance(bundle, Mapping)
                or bundle.get("verified") is not True
            ):
                raise EvaluationError(
                    "Reference evidence must be explicitly verified"
                )
            values = bundle.get("values", [])
            if not isinstance(values, (list, tuple, set)) or not values:
                raise EvaluationError(
                    "Reference values must be a nonempty collection"
                )
            target = names[0]
            missing = _is_missing(df[target], fields[target], df)
            state = np.full(len(df), -2, dtype=np.int8)
            valid = selected & ~missing
            if all(not isinstance(x, (dict, list, set)) for x in values):
                # Static versioned catalog; the planner ensures a rule's
                # when_period does not exceed the reference's valid_period.
                valid_codes = (
                    df[target].isin(list(values)).to_numpy(dtype=bool)
                )
                state[valid] = np.where(valid_codes[valid], 1, 0)
            elif all(isinstance(x, dict) and "value" in x for x in values):
                # Per-code validity windows are inclusive civil dates.
                # The time_field must be explicit, never inferred by name.
                field_time = rule.get("expectation", {}).get(
                    "extensions", {}
                ).get("time_field") or rule.get("when_period", {}).get(
                    "time_field"
                )
                if not field_time or field_time not in fields:
                    raise EvaluationError(
                        "Temporal catalog rows require explicit time_field"
                    )
                dates = _normalise_dates(
                    df[field_time], fields[field_time], unit="calendar_days"
                )
                tz = (
                    fields[field_time]
                    .get("representation", {})
                    .get("timezone")
                )
                if dates.dt.tz is not None and tz:
                    dates = dates.dt.tz_convert(tz)
                day_values = dates.dt.date
                from datetime import date

                bounds: dict[Any, list[tuple[Any, Any]]] = {}
                for entry in values:
                    try:
                        value = entry["value"]
                        lo = date.fromisoformat(
                            entry.get("valid_from", "0001-01-01")
                        )
                        hi = date.fromisoformat(
                            entry.get("valid_to", "9999-12-31")
                        )
                        if lo > hi:
                            raise EvaluationError(
                                "Inverted validity window in reference"
                            )
                        bounds.setdefault(value, []).append((lo, hi))
                    except (TypeError, ValueError, KeyError) as exc:
                        raise EvaluationError(
                            "Invalid catalog entry or ISO validity date"
                        ) from exc
                # Group only eligible, nonmissing codes. Cost scales with data
                # rows and entries, not (row_count x reference_count).
                candidate = df.loc[valid, target]
                state[valid] = 0
                state[valid & day_values.isna().to_numpy(dtype=bool)] = -1
                groups = candidate.groupby(
                    candidate, dropna=True, sort=False
                ).groups
                for code, index_labels in groups.items():
                    positions = np.asarray(index_labels, dtype=int)
                    periods = bounds.get(code, ())
                    if periods:
                        days = day_values.iloc[positions]
                        ok = np.zeros(len(positions), dtype=bool)
                        for lo, hi in periods:
                            ok |= (
                                ((days >= lo) & (days <= hi))
                                .fillna(False)
                                .to_numpy(dtype=bool)
                            )
                        state[positions[ok]] = 1
            else:
                raise EvaluationError(
                    "Reference values must be all scalars or all temporal "
                    "records"
                )
            state[selected & missing] = {
                "exclude": -2,
                "include_as_failure": 0,
                "include_as_pass": 1,
                "indeterminate": -1,
            }[missing_policy]
            unknown_scope_units = int(filter_unknown.sum())
        elif evaluator in EXTRA_PANDAS_EVALUATORS:
            state, unit = evaluate_pandas_extra(
                evaluator,
                df,
                fields,
                rule,
                selected,
                missing_policy,
                _is_missing,
            )
            unknown_scope_units = int(filter_unknown.sum())
        else:
            raise EvaluationError(f"Evaluator not implemented: {evaluator}")

        # Scope indeterminacy is a genuine third state; policy may explicitly
        # convert it to failure or exclusion. Never apply that coercion
        # silently.
        unknown_policy = metric.get("unknown_policy", "indeterminate")
        if unknown_policy not in {"indeterminate", "fail", "exclude"}:
            raise EvaluationError(
                f"Unsupported unknown_policy: {unknown_policy}"
            )
        if unknown_policy != "indeterminate":
            override = 0 if unknown_policy == "fail" else -2
            state[state == -1] = override
            if state.ndim == 2:
                state[filter_unknown, :] = override
            else:
                state[filter_unknown] = override
            unknown_scope_units = 0
        array = np.asarray(state)
        passed = int((array == 1).sum())
        failed = int((array == 0).sum())
        unknown_in_scope = int((array == -1).sum()) + unknown_scope_units
        excluded = int((array == -2).sum()) - unknown_scope_units
        # EXCLUDED includes every nonselected row/cell and optional null-policy
        # exclusions.
        eligible = passed + failed + unknown_in_scope
        observed = passed + failed
        measured_rate = (passed / observed) if observed else None
        coverage = (observed / eligible) if eligible else None
        lower: float | None = None
        upper: float | None = None
        if not eligible:
            status = "NO_ELIGIBLE_DATA"
        else:
            bounds = rate_bounds(
                passed,
                failed,
                unknown_in_scope,
                model=bound_model(evaluator),
            )
            lower, upper = bounds.lower, bounds.upper
            assert lower is not None and upper is not None
            status = _failure_status(lower, upper, threshold)
        failed_rows = np.flatnonzero(
            (array == 0).any(axis=1) if array.ndim == 2 else (array == 0)
        )
        unknown_rows = np.flatnonzero(
            (array == -1).any(axis=1) if array.ndim == 2 else (array == -1)
        )
        if filter_unknown.any():
            unknown_rows = np.union1d(
                unknown_rows, np.flatnonzero(filter_unknown)
            )
        limit = self.max_failure_examples
        return ContractRuleResult(
            rule_id=rid,
            asset_id=rule["asset_id"],
            metric_id=rule["metric_id"],
            dimension_ids=tuple(metric.get("dimension_ids", ())),
            status=status,
            planner_status="READY",
            total_rows=len(df),
            scoped_rows=scoped,
            excluded_rows=int(len(df) - scoped - filter_unknown.sum()),
            unit=unit,
            eligible_units=eligible,
            passed_units=passed,
            failed_units=failed,
            indeterminate_units=unknown_in_scope,
            excluded_units=excluded,
            measured_rate=measured_rate,
            lower_rate=lower,
            upper_rate=upper,
            assessment_coverage=coverage,
            threshold=dict(threshold),
            severity=rule.get("severity", "medium"),
            failure_action=rule.get("failure_action", "report"),
            sample_failed_positions=tuple(int(x) for x in failed_rows[:limit]),
            sample_indeterminate_positions=tuple(
                int(x) for x in unknown_rows[:limit]
            ),
        )
