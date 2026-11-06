"""First backend: pandas.

Checks are declarative; no expressions are eval'ed.
"""

from __future__ import annotations

import json
import numbers
import re
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ._io import write_text_atomic
from ._lazy import LazyModule
from .catalog import CATALOG
from .temporal import as_utc_instant, parse_declared_datetimes

if TYPE_CHECKING:
    import numpy as np
    import pandas as pd
else:
    np = LazyModule("numpy", extra="pandas")
    pd = LazyModule("pandas", extra="pandas")
import yaml

DIMENSIONS = frozenset(CATALOG)
RULES = {
    "required",
    "unique",
    "between",
    "allowed",
    "regex",
    "dtype",
    "compare",
    "sum_equals",
    "foreign_key",
    "delay_days",
    "freshness_days",
    "row_count",
    "schema_columns",
    "custom",
    "assessment",
    "metadata_fields",
    "coverage",
    "cardinality",
}
OPS = {
    "eq",
    "ne",
    "lt",
    "le",
    "gt",
    "ge",
    "in",
    "not_in",
    "is_null",
    "not_null",
}


@dataclass(frozen=True)
class Check:
    """A single auditable contract. `columns` is a tuple of source column
    names.

    `null_policy` determines how missing inputs behave for non-required rules:
    - exclude: omit from denominator;
    - fail: count as failures;
    - pass: count as successes.
    """

    id: str
    dimension: str
    rule: str
    columns: tuple[str, ...] = ()
    params: dict[str, Any] = field(default_factory=dict)
    min_pass_rate: float = 1.0
    null_policy: str = "exclude"
    when: dict[str, Any] | None = None
    severity: str = "error"
    description: str = ""
    applies: bool = True
    not_applicable_reason: str = ""

    def __post_init__(self) -> None:
        """Validate the check definition, raising ``ValueError`` if invalid."""
        if not self.id or not isinstance(self.id, str):
            raise ValueError("Check id must be a nonempty string")
        if self.dimension not in DIMENSIONS and not re.fullmatch(
            r"custom\.[a-z][a-z0-9_]*", self.dimension
        ):
            raise ValueError(
                f"Unknown dimension: {self.dimension}. Custom dimensions must "
                f"start custom."
            )
        if self.rule not in RULES:
            raise ValueError(f"Unknown rule: {self.rule}")
        if (
            not isinstance(self.min_pass_rate, (int, float))
            or not 0 <= self.min_pass_rate <= 1
        ):
            raise ValueError("min_pass_rate must be between 0 and 1")
        if self.null_policy not in {"exclude", "fail", "pass"}:
            raise ValueError("null_policy must be exclude, fail, or pass")
        if self.severity not in {"info", "warning", "error"}:
            raise ValueError("severity must be info, warning or error")
        if not isinstance(self.params, dict):
            raise ValueError("params must be an object")
        if self.when is not None and not isinstance(self.when, dict):
            raise ValueError("when must be an object")
        if (
            self.rule
            not in {
                "row_count",
                "schema_columns",
                "assessment",
                "metadata_fields",
                "coverage",
                "cardinality",
            }
            and not self.columns
        ):
            raise ValueError(f"{self.rule}: columns required")
        if self.rule == "custom" and "fn" not in self.params:
            raise ValueError(
                "Custom rule must provide params.fn registered callback name"
            )
        if not isinstance(self.applies, bool):
            raise ValueError("applies must be a boolean")
        if not self.applies and not self.not_applicable_reason.strip():
            raise ValueError("A reason is mandatory when applies=False")

    @classmethod
    def from_dict(cls, obj: Mapping[str, Any]) -> Check:
        """Build a check from a mapping."""
        allowed = {
            "id",
            "dimension",
            "rule",
            "columns",
            "params",
            "min_pass_rate",
            "null_policy",
            "when",
            "severity",
            "description",
            "applies",
            "not_applicable_reason",
        }
        unknown = set(obj) - allowed
        if unknown:
            raise ValueError(f"Unknown Check fields: {sorted(unknown)}")
        data = dict(obj)
        if isinstance(data.get("columns"), str):
            raise ValueError(
                "columns must be a list of column names, not a string"
            )
        data["columns"] = tuple(data.get("columns", ()))
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "id": self.id,
            "dimension": self.dimension,
            "rule": self.rule,
            "columns": list(self.columns),
            "params": self.params,
            "min_pass_rate": self.min_pass_rate,
            "null_policy": self.null_policy,
            "when": self.when,
            "severity": self.severity,
            "description": self.description,
            "applies": self.applies,
            "not_applicable_reason": self.not_applicable_reason,
        }


@dataclass(frozen=True)
class RuleResult:
    """Outcome of one check: counts and a status such as PASS or FAIL."""

    id: str
    dimension: str
    rule: str
    status: str
    checked: int
    failed: int
    passed: int
    excluded: int
    pass_rate: float | None
    min_pass_rate: float
    sample_failed_rows: tuple[
        int, ...
    ] = ()  # 0-based row positions, not index labels
    message: str = ""
    severity: str = "error"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            **self.__dict__,
            "sample_failed_rows": list(self.sample_failed_rows),
        }


@dataclass(frozen=True)
class QualityReport:
    """Results of running a :class:`QualitySuite` on one table."""

    results: tuple[RuleResult, ...]
    row_count: int
    generated_at: str

    def to_frame(self) -> pd.DataFrame:
        """Return one row per check as a pandas DataFrame."""
        return pd.DataFrame([r.to_dict() for r in self.results])

    def dimension_coverage(
        self, *, include_custom: bool = True
    ) -> pd.DataFrame:
        """Expose the whole catalog, including dimensions NOT_EVALUATED by this
        suite.

        Dimension coverage is an inventory, not a claim of measured data
        quality.
        """
        ids = list(CATALOG)
        if include_custom:
            ids += sorted({x.dimension for x in self.results} - set(ids))
        rows = []
        for dim in ids:
            checks = [x for x in self.results if x.dimension == dim]
            statuses = {x.status for x in checks}
            if not checks:
                status = "NOT_EVALUATED"
            elif "ERROR" in statuses:
                status = "ERROR"
            elif "FAIL" in statuses:
                status = "FAIL"
            elif "SKIP" in statuses:
                status = "PARTIAL" if "PASS" in statuses else "SKIP"
            elif statuses == {"NOT_APPLICABLE"}:
                status = "NOT_APPLICABLE"
            else:
                status = "PASS"
            rows.append(
                {
                    "dimension": dim,
                    "dimension_name": CATALOG[dim].name
                    if dim in CATALOG
                    else dim,
                    "status": status,
                    "rules": len(checks),
                }
            )
        return pd.DataFrame.from_records(rows)

    def summary(self) -> pd.DataFrame:
        """MACRO average of applicable, successfully executed check rates.

        Not a universal measure of quality: rule weighting and overlapping
        records
        are intentionally NOT hidden behind a single global number.
        """
        records = []
        for dim in sorted({r.dimension for r in self.results}):
            group = [r for r in self.results if r.dimension == dim]
            eligible = [
                r.pass_rate
                for r in group
                if r.status in {"PASS", "FAIL"} and r.pass_rate is not None
            ]
            records.append(
                {
                    "dimension": dim,
                    "rules": len(group),
                    "passed_rules": sum(r.status == "PASS" for r in group),
                    "failed_rules": sum(r.status == "FAIL" for r in group),
                    "errors": sum(r.status == "ERROR" for r in group),
                    "skipped": sum(r.status == "SKIP" for r in group),
                    "not_applicable": sum(
                        r.status == "NOT_APPLICABLE" for r in group
                    ),
                    "macro_pass_rate": sum(eligible) / len(eligible)
                    if eligible
                    else None,
                }
            )
        return pd.DataFrame.from_records(records)

    def weighted_score(self, weights: Mapping[str, float]) -> float:
        """Compute an EXPLICIT user-defined composite, with no silently omitted
        checks.

        Supply a positive weight for every executed check ID (excluding only
        checks
        explicitly NOT_APPLICABLE). Any SKIP/ERROR aborts the calculation.
        Scores mix units/constructs and must be interpreted for the intended
        use.
        """
        active = {
            r.id: r for r in self.results if r.status != "NOT_APPLICABLE"
        }
        if not active or set(weights) != set(active):
            raise ValueError(
                "Weights must match all applicable check IDs exactly"
            )
        if any(
            r.status not in {"PASS", "FAIL"} or r.pass_rate is None
            for r in active.values()
        ):
            raise ValueError(
                "Cannot score results with SKIP/ERROR/unevaluated checks"
            )
        if any(
            isinstance(w, bool)
            or not isinstance(w, (int, float))
            or not 0 < w < float("inf")
            for w in weights.values()
        ):
            raise ValueError("Weights must be finite positive numbers")
        denominator = sum(weights.values())
        return (
            sum((active[k].pass_rate or 0.0) * weights[k] for k in active)
            / denominator
        )

    @property
    def passed(self) -> bool:
        """Errors fail closed; skips need explicit handling by consuming
        pipelines.
        """
        return any(r.status == "PASS" for r in self.results) and all(
            r.status in {"PASS", "NOT_APPLICABLE"} for r in self.results
        )

    def to_json(self, path: str | Path | None = None) -> str:
        """Serialise to JSON, optionally writing it to ``path``.

        Returns:
            The JSON text.
        """
        data = {
            "row_count": self.row_count,
            "generated_at": self.generated_at,
            "passed": self.passed,
            "results": [r.to_dict() for r in self.results],
        }
        output = json.dumps(
            data, ensure_ascii=False, indent=2, allow_nan=False
        )
        if path is not None:
            write_text_atomic(path, output + "\n")
        return output


class QualitySuite:
    """A set of declarative checks evaluated on a pandas DataFrame.

    .. deprecated:: 0.8.0
        Use reviewed contracts with :func:`quaxdim.evaluate_contract` or
        :func:`quaxdim.evaluate_optimized`. ``QualitySuite`` will be removed
        in a future major release.
    """

    def __init__(
        self,
        checks: list[Check] | tuple[Check, ...] | None = None,
        *,
        max_failure_examples: int = 10,
    ):
        """Create a suite.

        Args:
            checks: Initial checks; identifiers must be unique.
            max_failure_examples: Maximum failure examples kept per check.
        """
        warnings.warn(
            "QualitySuite is deprecated; use reviewed contracts with "
            "evaluate_contract() or evaluate_optimized()",
            DeprecationWarning,
            stacklevel=2,
        )
        self.checks = list(checks or [])
        self.max_failure_examples = max_failure_examples
        self._callbacks: dict[
            str, Callable[[pd.DataFrame, Check], pd.Series]
        ] = {}
        self._ensure_unique_ids()

    def _ensure_unique_ids(self) -> None:
        ids = [c.id for c in self.checks]
        if len(ids) != len(set(ids)):
            raise ValueError("Check ids must be unique")

    def add(self, check: Check) -> QualitySuite:
        """Add a check and return the suite, to allow chaining.

        The suite is left unchanged when the check is rejected (audit
        finding H3): validation happens before mutation.

        Raises:
            ValueError: If ``check.id`` already exists in the suite.
        """
        if any(existing.id == check.id for existing in self.checks):
            raise ValueError(f"Check id already exists: {check.id}")
        self.checks.append(check)
        return self

    def register(
        self, name: str, fn: Callable[[pd.DataFrame, Check], pd.Series]
    ) -> QualitySuite:
        """Register a custom check function under ``name``."""
        if not name or not callable(fn):
            raise ValueError("register(name, callable) required")
        self._callbacks[name] = fn
        return self

    @classmethod
    def from_yaml(cls, path: str | Path) -> QualitySuite:
        """Load a suite from a YAML file."""
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if (
            not isinstance(data, dict)
            or set(data) != {"checks"}
            or not isinstance(data["checks"], list)
        ):
            raise ValueError(
                "Expected YAML object with exactly one key: checks (list)"
            )
        return cls([Check.from_dict(x) for x in data["checks"]])

    def to_yaml(self, path: str | Path) -> None:
        """Write the suite to a YAML file."""
        write_text_atomic(
            path,
            yaml.safe_dump(
                {"checks": [c.to_dict() for c in self.checks]},
                allow_unicode=True,
                sort_keys=False,
            ),
        )

    def run(
        self,
        data: pd.DataFrame,
        *,
        references: Mapping[str, pd.DataFrame] | None = None,
        as_of: str | datetime | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> QualityReport:
        """Evaluate every check on ``data`` and return the report."""
        if not isinstance(data, pd.DataFrame):
            raise TypeError("First backend supports pandas.DataFrame only")
        references = references or {}
        # Fix execution time once, to avoid different times across checks.
        now = (
            pd.Timestamp(as_of)
            if as_of is not None
            else pd.Timestamp.now(tz="UTC")
        )
        results = tuple(
            self._execute(data, c, references, now, metadata or {})
            for c in self.checks
        )
        return QualityReport(
            results, len(data), datetime.now(timezone.utc).isoformat()
        )

    def _execute(
        self,
        data: pd.DataFrame,
        check: Check,
        references: Mapping[str, pd.DataFrame],
        now: pd.Timestamp,
        metadata: Mapping[str, Any],
    ) -> RuleResult:
        try:
            if not check.applies:
                return RuleResult(
                    check.id,
                    check.dimension,
                    check.rule,
                    "NOT_APPLICABLE",
                    0,
                    0,
                    0,
                    len(data),
                    None,
                    check.min_pass_rate,
                    message=check.not_applicable_reason,
                    severity=check.severity,
                )
            if check.rule in {
                "row_count",
                "schema_columns",
                "assessment",
                "metadata_fields",
                "coverage",
                "cardinality",
            }:
                if check.when is not None:
                    raise ValueError(
                        "Dataset-level checks do not accept 'when'"
                    )
                rate, count, msg = self._dataset_rule(
                    data, check, references, metadata
                )
                status = (
                    "SKIP"
                    if rate is None
                    else "PASS"
                    if rate + 1e-12 >= check.min_pass_rate
                    else "FAIL"
                )
                if rate is None:
                    passed, failed = 0, 0
                elif check.rule in {"coverage", "metadata_fields"}:
                    passed = round(rate * count)
                    failed = count - passed
                else:
                    passed = int(rate >= check.min_pass_rate)
                    failed = 1 - passed
                return RuleResult(
                    check.id,
                    check.dimension,
                    check.rule,
                    status,
                    count,
                    failed,
                    passed,
                    0,
                    rate,
                    check.min_pass_rate,
                    message=msg,
                    severity=check.severity,
                )

            cols = list(check.columns)
            for col in cols:
                if col not in data.columns:
                    raise ValueError(f"Missing column: {col}")
            scope = self._scope(data, check.when)
            # For required checks, every scoped row is part of the denominator.
            if check.rule == "required":
                null_any = self._null_any(data, cols, check.params)
                eligible = scope
                good = ~null_any
            else:
                needed_cols = cols.copy()
                if check.rule == "sum_equals":
                    needed_cols += list(check.params["parts"])
                if check.rule == "compare":
                    needed_cols.append(check.params["right_column"])
                if check.rule == "delay_days":
                    needed_cols.append(check.params["arrival_column"])
                null_any = self._null_any(data, needed_cols, check.params)
                eligible = scope & (
                    ~null_any
                    if check.null_policy == "exclude"
                    else pd.Series(True, index=data.index)
                )
                good = self._row_rule(data, check, references, now, scope)
                if check.null_policy == "fail":
                    good = good & ~null_any
                elif check.null_policy == "pass":
                    good = good | null_any

            good = _as_bool_series(good, data)
            evaluated = eligible.to_numpy(dtype=bool)
            passed_mask = evaluated & good.to_numpy(dtype=bool)
            failed_mask = evaluated & ~good.to_numpy(dtype=bool)
            checked = int(evaluated.sum())
            passed = int(passed_mask.sum())
            failed = int(failed_mask.sum())
            excluded = len(data) - checked
            rate = passed / checked if checked else None
            status = (
                "SKIP"
                if checked == 0
                else "PASS"
                if rate is not None and rate + 1e-12 >= check.min_pass_rate
                else "FAIL"
            )
            sample = tuple(
                int(x)
                for x in np.flatnonzero(failed_mask)[
                    : self.max_failure_examples
                ]
            )
            return RuleResult(
                check.id,
                check.dimension,
                check.rule,
                status,
                checked,
                failed,
                passed,
                excluded,
                rate,
                check.min_pass_rate,
                sample,
                "No eligible rows" if checked == 0 else "",
                check.severity,
            )
        except Exception as exc:
            return RuleResult(
                check.id,
                check.dimension,
                check.rule,
                "ERROR",
                0,
                0,
                0,
                len(data),
                None,
                check.min_pass_rate,
                (),
                f"{type(exc).__name__}: {exc}",
                check.severity,
            )

    @staticmethod
    def _dataset_rule(
        data: pd.DataFrame,
        check: Check,
        references: Mapping[str, pd.DataFrame],
        metadata: Mapping[str, Any],
    ) -> tuple[float | None, int, str]:
        """Return (ratio, metric denominator, detail); never invent quality
        evidence.
        """
        p = check.params
        if check.rule == "row_count":
            ok = ("min" not in p or len(data) >= p["min"]) and (
                "max" not in p or len(data) <= p["max"]
            )
            return float(ok), 1, f"row_count={len(data)}"
        if check.rule == "schema_columns":
            expected_list = list(p["expected"])
            if len(set(expected_list)) != len(expected_list):
                raise ValueError("schema_columns: 'expected' repeats a name")
            if len(set(data.columns)) != len(data.columns):
                # Set comparison would hide a duplicated header (audit H4).
                raise ValueError("Data has duplicate column names")
            expected = set(expected_list)
            actual = set(data.columns)
            ok = expected <= actual and (
                not p.get("exact", False) or expected == actual
            )
            return (
                float(ok),
                1,
                f"missing={sorted(expected - actual)}; "
                f"unexpected={sorted(actual - expected)}",
            )
        if check.rule == "metadata_fields":
            required = p.get("required", [])
            if not isinstance(required, list) or not required:
                raise ValueError(
                    "metadata_fields requires a nonempty 'required' list"
                )
            found = [
                x
                for x in required
                if x in metadata
                and metadata[x] is not None
                and (
                    not isinstance(metadata[x], str)
                    or bool(metadata[x].strip())
                )
            ]
            return (
                len(found) / len(required),
                len(required),
                f"present={found}; "
                f"absent={sorted(set(required) - set(found))}",
            )
        if check.rule == "assessment":
            # Manually supplied, attributable, contextualized assessment. Not a
            # dataframe calculation.
            source, assessor = p.get("source"), p.get("assessor")
            score = p.get("score")
            if (
                not source
                or not assessor
                or isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not 0 <= score <= 1
            ):
                raise ValueError(
                    "assessment requires source, assessor and score in [0, 1]"
                )
            return (
                float(score),
                1,
                f"External assessment: source={source}; assessor={assessor}",
            )
        if check.rule == "cardinality":
            if len(check.columns) != 1 or check.columns[0] not in data.columns:
                raise ValueError("cardinality requires one existing column")
            val = int(
                data[check.columns[0]].nunique(dropna=p.get("dropna", True))
            )
            ok = ("min" not in p or val >= p["min"]) and (
                "max" not in p or val <= p["max"]
            )
            return float(ok), 1, f"distinct_count={val}"
        if check.rule == "coverage":
            # Keys in an external population frame constitute the denominator.
            refname = p.get("reference")
            cols = list(check.columns)
            ref_cols = list(p.get("reference_columns", cols))
            if not isinstance(refname, str) or refname not in references:
                raise ValueError(
                    f"Missing expected population reference: {refname}"
                )
            ref = references[refname]
            if (
                not cols
                or len(ref_cols) != len(cols)
                or any(x not in data.columns for x in cols)
                or any(x not in ref.columns for x in ref_cols)
            ):
                raise ValueError(
                    "Coverage requires valid and aligned key columns"
                )
            population = pd.MultiIndex.from_frame(
                ref[ref_cols].drop_duplicates()
            )
            observed = pd.MultiIndex.from_frame(data[cols].drop_duplicates())
            if not len(population):
                return None, 0, "Population reference has no eligible entities"
            covered = int(population.isin(observed).sum())
            return (
                covered / len(population),
                len(population),
                f"covered={covered}; expected={len(population)}",
            )
        raise ValueError(f"Unsupported dataset-level rule {check.rule}")

    @staticmethod
    def _null_any(
        data: pd.DataFrame, cols: list[str], p: dict[str, Any]
    ) -> pd.Series:
        missing = pd.Series(False, index=data.index, dtype=bool)
        sentinels = p.get("null_values", [])
        blank = p.get("blank_as_null", True)
        casefold = p.get("null_casefold", False)
        for col in cols:
            if col not in data.columns:
                raise ValueError(f"Missing column: {col}")
            s = data[col]
            mask = s.isna()
            scalar_numeric = pd.api.types.is_numeric_dtype(
                s.dtype
            ) and not pd.api.types.is_bool_dtype(s.dtype)
            if (
                blank
                and not scalar_numeric
                and not pd.api.types.is_bool_dtype(s.dtype)
            ):
                if pd.api.types.is_object_dtype(s.dtype):
                    mask = mask | s.map(
                        lambda x: isinstance(x, str) and x.strip() == ""
                    )
                else:
                    mask = mask | s.str.strip().eq("").fillna(False)
            for item in sentinels:
                if isinstance(item, str):
                    if not scalar_numeric and not pd.api.types.is_bool_dtype(
                        s.dtype
                    ):
                        if pd.api.types.is_object_dtype(s.dtype):
                            mask = mask | s.map(
                                lambda x, item=item: (
                                    isinstance(x, str)
                                    and (
                                        x.strip().casefold()
                                        == item.strip().casefold()
                                        if casefold
                                        else x == item
                                    )
                                )
                            )
                        else:
                            target = (
                                s.str.strip().str.casefold() if casefold else s
                            )
                            mask = mask | target.eq(
                                item.strip().casefold() if casefold else item
                            ).fillna(False)
                elif isinstance(item, numbers.Number) and not isinstance(
                    item, bool
                ):
                    if scalar_numeric:
                        mask = mask | s.eq(item).fillna(False)
                    elif not pd.api.types.is_bool_dtype(s.dtype):
                        # Float promotion (int + null -> float) is normal. Yet
                        # False != 0.
                        mask = mask | s.map(
                            lambda x, item=item: (
                                isinstance(x, numbers.Number)
                                and not isinstance(x, bool)
                                and x == item
                            )
                        )
                else:
                    mask = mask | s.map(
                        lambda x, item=item: (
                            type(x) is type(item) and x == item
                        )
                    )
            missing |= mask.astype(bool)
        return missing

    @staticmethod
    def _scope(data: pd.DataFrame, when: dict[str, Any] | None) -> pd.Series:
        if when is None:
            return pd.Series(True, index=data.index, dtype=bool)
        col = when.get("column")
        op = when.get("op", "eq")
        if col not in data.columns or op not in OPS:
            raise ValueError(f"Invalid when condition: {when}")
        s = data[col]
        val = when.get("value")
        match op:
            case "eq":
                out = s.eq(val)
            case "ne":
                out = s.ne(val)
            case "lt":
                out = s.lt(val)
            case "le":
                out = s.le(val)
            case "gt":
                out = s.gt(val)
            case "ge":
                out = s.ge(val)
            case "in":
                out = s.isin(val)
            case "not_in":
                out = s.notna() & ~s.isin(val)
            case "is_null":
                out = s.isna()
            case "not_null":
                out = s.notna()
        return out.fillna(False).astype(bool)

    def _row_rule(
        self,
        data: pd.DataFrame,
        c: Check,
        refs: Mapping[str, pd.DataFrame],
        now: pd.Timestamp,
        scope: pd.Series,
    ) -> pd.Series:
        p, rule = c.params, c.rule
        s = data[c.columns[0]]
        if rule == "unique":
            # When a subset is specified, uniqueness is defined within that
            # subset.
            positions = np.flatnonzero(scope.to_numpy(dtype=bool))
            result = np.ones(len(data), dtype=bool)
            result[positions] = (
                ~data.iloc[positions][list(c.columns)]
                .duplicated(keep=False)
                .to_numpy()
            )
            return pd.Series(result, index=data.index)
        if rule == "between":
            num, valid = _numeric(s, coerce=p.get("coerce", False))
            ok = valid.copy()
            if "min" in p:
                ok &= (
                    num.ge(p["min"])
                    if p.get("inclusive_min", True)
                    else num.gt(p["min"])
                )
            if "max" in p:
                ok &= (
                    num.le(p["max"])
                    if p.get("inclusive_max", True)
                    else num.lt(p["max"])
                )
            return ok.fillna(False)
        if rule == "allowed":
            return s.isin(p["values"])
        if rule == "regex":
            pattern = re.compile(p["pattern"], p.get("flags", 0))
            strings = s.map(lambda x: isinstance(x, str))
            return (
                s.where(strings).str.fullmatch(pattern).fillna(False)
                if p.get("fullmatch", True)
                else s.where(strings)
                .str.contains(pattern, regex=True)
                .fillna(False)
            )
        if rule == "dtype":
            typ = p["type"]
            if typ not in {
                "integer",
                "float",
                "number",
                "string",
                "boolean",
                "datetime",
            }:
                raise ValueError(f"Unsupported semantic type {typ}")
            return s.map(lambda x: _is_type(x, typ))
        if rule == "compare":
            right = p.get("right_column")
            if right not in data.columns:
                raise ValueError(f"Missing comparison column: {right}")
            # Null checking for RHS is explicit; do not compare missing to real
            # values.
            rhs = data[right]
            if p.get("op") not in {"eq", "ne", "lt", "le", "gt", "ge"}:
                raise ValueError("compare requires op eq/ne/lt/le/gt/ge")
            comparison = getattr(s, p["op"])(rhs)
            return comparison.fillna(False) & rhs.notna()
        if rule == "sum_equals":
            parts = p["parts"]
            if not parts:
                raise ValueError("sum_equals parts cannot be empty")
            target, valid = _numeric(s, coerce=p.get("coerce", False))
            total = pd.Series(0.0, index=data.index)
            for part in parts:
                num, v = _numeric(data[part], coerce=p.get("coerce", False))
                valid &= v
                total = total + num
            atol = float(p.get("abs_tolerance", 0))
            rtol = float(p.get("rel_tolerance", 0))
            if atol < 0 or rtol < 0:
                raise ValueError("Tolerances must be >= 0")
            return valid & (target - total).abs().le(atol + rtol * total.abs())
        if rule == "foreign_key":
            refname = p.get("reference")
            if not isinstance(refname, str) or refname not in refs:
                raise ValueError(f"Unknown reference dataset: {refname}")
            ref = refs[refname]
            ref_cols = p.get("reference_columns", list(c.columns))
            if len(ref_cols) != len(c.columns):
                raise ValueError("Foreign key arity mismatch")
            if any(column not in ref.columns for column in ref_cols):
                raise ValueError(f"Missing reference column among {ref_cols}")
            # MultiIndex hash lookup, not a potentially explosive many-to-many
            # merge.
            right = pd.MultiIndex.from_frame(
                ref[list(ref_cols)].drop_duplicates()
            )
            left = pd.MultiIndex.from_frame(data[list(c.columns)])
            return pd.Series(left.isin(right), index=data.index)
        if rule in {"delay_days", "freshness_days"}:
            # Same guarantees as the native executor: declared format and
            # zone, no inference; unparseable values abort the check
            # (ERROR) instead of being counted as late (audit finding C2).
            timezone = p.get("timezone")
            dt = parse_declared_datetimes(
                s, date_format=p.get("date_format"), timezone=timezone
            )
            if rule == "delay_days":
                col = p["arrival_column"]
                if col not in data.columns:
                    raise ValueError(f"Missing arrival column: {col}")
                arrival = parse_declared_datetimes(
                    data[col],
                    date_format=p.get("arrival_date_format")
                    or p.get("date_format"),
                    timezone=p.get("arrival_timezone") or timezone,
                )
                days = (arrival - dt).dt.total_seconds() / 86400
                return (
                    dt.notna()
                    & arrival.notna()
                    & days.ge(0)
                    & days.le(p["max_days"])
                )
            current = as_utc_instant(now, timezone)
            days = (current - dt).dt.total_seconds() / 86400
            return dt.notna() & days.ge(0) & days.le(p["max_days"])
        if rule == "custom":
            fn = p["fn"]
            if fn not in self._callbacks:
                raise ValueError(f"Unregistered callback: {fn}")
            return _as_bool_series(self._callbacks[fn](data, c), data)
        raise ValueError(f"Unsupported rule: {rule}")


def _numeric(
    series: pd.Series, *, coerce: bool = False
) -> tuple[pd.Series, pd.Series]:
    """Strict by default: don't silently turn code strings such as '001' into
    numbers.
    """
    if coerce:
        n = pd.to_numeric(series, errors="coerce")
        return n, n.notna()
    good = series.map(
        lambda x: (
            isinstance(x, (numbers.Real, Decimal))
            and not isinstance(x, (bool,))
            and not pd.isna(x)
        )
    )
    n = pd.to_numeric(series.where(good), errors="coerce")
    return n, good & n.notna()


def _is_type(value: Any, typ: str) -> bool:
    if pd.isna(value):
        return False
    if typ == "string":
        return isinstance(value, str)
    if typ == "boolean":
        return isinstance(value, (bool, __import__("numpy").bool_))
    if typ == "integer":
        return isinstance(value, numbers.Integral) and not isinstance(
            value, (bool,)
        )
    if typ == "float":
        return isinstance(value, numbers.Real) and not isinstance(
            value, numbers.Integral
        )
    if typ == "number":
        return isinstance(value, (numbers.Real, Decimal)) and not isinstance(
            value, (bool,)
        )
    return isinstance(value, (pd.Timestamp, datetime))


def _as_bool_series(value: Any, frame: pd.DataFrame) -> pd.Series:
    if not isinstance(value, pd.Series) or len(value) != len(frame):
        raise ValueError(
            "Rule must return a boolean Series with one value per input row"
        )
    # Ignore source indices; pandas dataframes can have repeated index labels.
    if not pd.api.types.is_bool_dtype(value.dtype):
        if not (value.dropna().map(lambda x: isinstance(x, bool)).all()):
            raise ValueError(
                "Rule must return booleans; numbers/strings are not accepted"
            )
    return (
        pd.Series(value.to_numpy(), index=frame.index)
        .fillna(False)
        .astype(bool)
    )
