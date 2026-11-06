"""v0.8: conservative, fused, native execution of a *portable subset* of
contracts.

Input rows never go through pandas. DuckDB/Polars/Spark execute their own
expressions; CSV is read sequentially with bounded memory. Only aggregate
counts
are returned. Unsupported semantics are BLOCKED, never coerced into PASS.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from ._io import describe_source, write_text_atomic
from ._types import EngineModule, SparkColumn
from ._version import __version__
from .bounds import rate_bounds
from .contract_validation import (
    identity_key_problem,
    unknown_dimension_ids,
    validate_contract_structure,
)
from .csv_dialect import (
    CsvDialectError,
    CsvReadOptions,
    duckdb_csv_relation,
    is_csv_path,
    iter_records,
    read_header,
    validate_structure,
)
from .evaluator_registry import bound_model
from .missingness import (
    NEVER_NULL_SENTINEL,
    empty_string_is_missing,
    is_float_type,
    nan_is_missing,
)

_PORTABLE = {
    "builtin.presence",
    "builtin.allowed_values",
    "builtin.value_range",
    "builtin.column_compare",
    "builtin.regex_fullmatch",
    "builtin.composite_uniqueness",
}
_NULL_STATES = {
    "exclude": -2,
    "include_as_failure": 0,
    "include_as_pass": 1,
    "indeterminate": -1,
}
_MAX_FUSED_RULES = 32  # Avoid unbounded expression plans on large schemas.


def _batches(
    items: Sequence[Any], limit: int = _MAX_FUSED_RULES
) -> Iterator[Sequence[Any]]:
    for start in range(0, len(items), limit):
        yield items[start : start + limit]


_REGEX_SAFE = re.compile(
    r"^(?:\[0-9\]|\[A-Z\]|\[a-z\]|\[A-Za-z0-9_-\])\{[1-9][0-9]{0,2}\}$"
)


def _esc(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _lit(value: Any) -> str:
    if value is True:
        return "TRUE"
    if value is False:
        return "FALSE"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError("Non-finite literal")
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    raise ValueError("Non-scalar literal")


def _class_type(value: Any) -> str:
    if type(value) is str:
        return "string"
    if type(value) is bool:
        return "boolean"
    if type(value) in (int, float) and math.isfinite(value):
        return "numeric"
    return "unknown"


def _logical_type(s: str) -> str:
    t = s.lower()
    if any(
        x in t
        for x in ("int", "float", "double", "decimal", "real", "hugeint")
    ) or t in (
        "long",
        "short",
        "byte",
        "i8",
        "i16",
        "i32",
        "i64",
        "u8",
        "u16",
        "u32",
        "u64",
    ):
        return "numeric"
    if any(x in t for x in ("varchar", "string", "utf8", "text", "char")):
        return "string"
    if t in ("bool", "boolean"):
        return "boolean"
    if "date" in t or "timestamp" in t:
        return "temporal"
    return "unsupported"


def _source_engine(source: Any, engine: str) -> str:
    if engine != "auto":
        return engine
    if isinstance(source, (str, Path)):
        p = str(source).lower()
        if p.endswith((".csv", ".tsv", ".csv.gz", ".tsv.gz")):
            return "csv_stream"
        if p.endswith(".parquet"):
            return "duckdb"
    module = type(source).__module__
    if module.startswith("polars."):
        return "polars"
    if module.startswith("pyspark."):
        return "spark"
    raise TypeError(
        "Unsupported input; use a CSV, Parquet, Polars LazyFrame or classic "
        "Spark DataFrame"
    )


@dataclass(frozen=True)
class PlannedRule:
    """Preflight verdict for one rule on a native engine."""

    rule_id: str
    asset_id: str
    metric_id: str
    evaluator: str
    fields: tuple[str, ...]
    status: str
    reason: str
    cost: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {**asdict(self), "fields": list(self.fields)}


@dataclass(frozen=True)
class OptimizedPlan:
    """Engine chosen per asset and preflight verdict of every rule."""

    engine_by_asset: dict[str, str]
    rules: tuple[PlannedRule, ...]
    scan_groups: dict[str, dict[str, int]]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "engine_by_asset": self.engine_by_asset,
            "rules": [r.to_dict() for r in self.rules],
            "scan_groups": self.scan_groups,
            "warnings": list(self.warnings),
        }

    def to_json(self, path: str | Path | None = None) -> str:
        """Serialise to JSON, optionally writing it to ``path``.

        Returns:
            The JSON text.
        """
        s = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        if path:
            write_text_atomic(path, s + "\n")
        return s


@dataclass(frozen=True)
class OptimizedRuleResult:
    """Measured outcome of one rule on a native engine."""

    rule_id: str
    asset_id: str
    metric_id: str
    dimension_ids: tuple[str, ...]
    status: str
    planner_status: str
    total_rows: int | None
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
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            **asdict(self),
            "dimension_ids": list(self.dimension_ids),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class OptimizedReport:
    """Results of a native execution, together with its plan."""

    contract_version: str
    contract_sha256: str
    generated_at: str
    plan: OptimizedPlan
    results: tuple[OptimizedRuleResult, ...]
    # Audit trail: which library produced the verdicts and on which inputs.
    library_version: str = __version__
    sources: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "contract_version": self.contract_version,
            "contract_sha256": self.contract_sha256,
            "generated_at": self.generated_at,
            "library_version": self.library_version,
            "sources": {k: dict(v) for k, v in self.sources.items()},
            "plan": self.plan.to_dict(),
            "results": [r.to_dict() for r in self.results],
        }

    def to_json(self, path: str | Path | None = None) -> str:
        """Serialise to JSON, optionally writing it to ``path``.

        Returns:
            The JSON text.
        """
        s = json.dumps(
            self.to_dict(), ensure_ascii=False, indent=2, allow_nan=False
        )
        if path:
            write_text_atomic(path, s + "\n")
        return s

    @property
    def passed(self) -> bool:
        """Whether every rule passed; false when nothing was measured."""
        return bool(self.results) and all(
            r.status == "PASS" for r in self.results
        )


def _plan_rule(
    rule: Mapping[str, Any],
    metric: Mapping[str, Any] | None,
    asset: Mapping[str, Any] | None,
    engine: str,
    dimensions: Mapping[str, Any],
) -> tuple[str, str]:
    if not metric:
        return "INVALID_CONTRACT", "Metric not declared"
    if not asset:
        return "INVALID_CONTRACT", "Asset not declared"
    missing_dimensions = unknown_dimension_ids(metric, dimensions)
    if missing_dimensions:
        return (
            "INVALID_CONTRACT",
            f"Metric refers to undeclared dimensions: {missing_dimensions}",
        )
    evaluator = metric.get("evaluator")
    if evaluator not in _PORTABLE:
        return "UNSUPPORTED", "Evaluator not in portable v0.8 subset"
    if asset.get("shape") != "table":
        return "UNSUPPORTED", "Only table assets supported"
    if metric.get("unit") != "ratio" or metric.get("kind") != "conformance":
        return "UNSUPPORTED", "Only conformance ratios"
    if any(
        rule.get(k)
        for k in ("when", "when_period", "exceptions", "dependencies")
    ):
        return (
            "UNSUPPORTED",
            "Scoped, conditional, temporal or dependent rules need "
            "backend-specific evaluator",
        )
    if metric.get("unknown_policy", "indeterminate") != "indeterminate":
        return (
            "UNSUPPORTED",
            "Unknown-policy conversion unsupported in fused planner",
        )
    null_policy = metric.get("null_policy", "indeterminate")
    if null_policy not in _NULL_STATES:
        return "UNSUPPORTED", "Nonstandard null policy"
    threshold = rule.get("threshold")
    if not isinstance(threshold, dict) or threshold.get("operator") not in {
        "gte",
        "gt",
        "lte",
        "lt",
        "eq",
        "between",
    }:
        return "INVALID_CONTRACT", "Explicit numeric threshold required"
    if (
        type(threshold.get("value")) not in (int, float)
        or not 0 <= threshold["value"] <= 1
    ):
        return "INVALID_CONTRACT", "Threshold must be between zero and one"
    if threshold["operator"] == "between":
        upper: Any = threshold.get("upper")
        if (
            type(upper) not in (int, float)
            or not threshold["value"] <= upper <= 1
        ):
            return "INVALID_CONTRACT", "Invalid upper threshold"
    fields = rule.get("target", {}).get("fields", [])
    defs = {x["name"]: x for x in asset.get("fields", [])}
    if not fields or any(x not in defs for x in fields):
        return (
            "INVALID_CONTRACT",
            "Target fields missing from asset declaration",
        )
    if any(defs[x].get("variants") for x in fields):
        return (
            "UNSUPPORTED",
            "Historical variants require a temporal evaluator",
        )
    if engine == "csv_stream" and any(
        _logical_type(defs[name]["logical_type"]) != "string"
        for name in fields
    ):
        return (
            "UNSUPPORTED",
            "CSV streaming is lexical; do not silently coerce to typed values",
        )
    for name in fields:
        p = defs[name].get("missing_policy", {})
        if p.get("when") or p.get("normalization", "none") not in {
            "none",
            "trim",
        }:
            return (
                "UNSUPPORTED",
                "Conditional or Unicode casefold missingness cannot be "
                "emulated",
            )
        if engine != "csv_stream" and (
            p.get("whitespace_only") or p.get("normalization") == "trim"
        ):
            return (
                "UNSUPPORTED",
                "Cross-engine Unicode whitespace trimming is not equivalent",
            )
        if any(
            _class_type(v) != _logical_type(defs[name]["logical_type"])
            for v in p.get("tokens", [])
        ):
            return "UNSUPPORTED", "Typed null tokens must match the field kind"
        if len(p.get("tokens", [])) > 128:
            return "BLOCKED_RESOURCE", "Too many literal null tokens"
    if evaluator == "builtin.presence":
        if rule.get("expectation", {}).get("operator") != "not_null":
            return "INVALID_CONTRACT", "Presence requires not_null"
        if (
            len(fields) != 1
            and rule.get("target", {}).get("level") != "record"
        ):
            return (
                "UNSUPPORTED",
                "Multiple-field presence requires record level",
            )
    elif evaluator == "builtin.composite_uniqueness":
        if rule.get("expectation", {}).get("operator") != "composite_unique":
            return "INVALID_CONTRACT", "Uniqueness requires composite_unique"
        key_problem = identity_key_problem(rule)
        if key_problem:
            return "INVALID_CONTRACT", key_problem
        if any(
            _logical_type(defs[x]["logical_type"]) == "unsupported"
            for x in fields
        ):
            return "UNSUPPORTED", "Nested/unsupported key type"
    elif evaluator == "builtin.allowed_values":
        values = rule.get("expectation", {}).get("values", [])
        if rule.get("expectation", {}).get("operator") != "in_set":
            return "INVALID_CONTRACT", "Allowed values require in_set"
        if (
            len(fields) != 1
            or not isinstance(values, list)
            or not values
            or len(values) > 256
        ):
            return (
                "UNSUPPORTED",
                "Allowed values require 1..256 literals and one field",
            )
        dtype = _logical_type(defs[fields[0]]["logical_type"])
        if any(_class_type(v) != dtype for v in values):
            return (
                "UNSUPPORTED",
                "Allowed-value types do not match declared type",
            )
    elif evaluator == "builtin.value_range":
        ex = rule.get("expectation", {})
        if (
            len(fields) != 1
            or _logical_type(defs[fields[0]]["logical_type"]) != "numeric"
        ):
            return "UNSUPPORTED", "Range requires one declared numeric field"
        op = ex.get("operator")
        if op not in {"min", "max", "between"}:
            return "INVALID_CONTRACT", "Invalid range operator"
        vals = (
            [ex.get("lower"), ex.get("upper")]
            if op == "between"
            else [ex.get("value")]
        )
        if any(_class_type(v) != "numeric" for v in vals):
            return (
                "INVALID_CONTRACT",
                "Range bounds must be finite numeric values",
            )
        if op == "between" and vals[0] > vals[1]:
            return "INVALID_CONTRACT", "Inverted range"
    elif evaluator == "builtin.column_compare":
        ex = rule.get("expectation", {})
        op = ex.get("extensions", {}).get("comparison")
        if (
            len(fields) != 2
            or ex.get("operator") != "expression"
            or op not in {"eq", "ne", "lt", "lte", "gt", "gte"}
        ):
            return (
                "INVALID_CONTRACT",
                "Comparison requires two fields and supported operator",
            )
        kinds = [_logical_type(defs[x]["logical_type"]) for x in fields]
        if kinds[0] != kinds[1] or kinds[0] != "numeric":
            return (
                "UNSUPPORTED",
                "Portable comparison requires typed numeric fields (timezones "
                "require a separate policy)",
            )
    elif evaluator == "builtin.regex_fullmatch":
        ex = rule.get("expectation", {})
        if (
            len(fields) != 1
            or _logical_type(defs[fields[0]]["logical_type"]) != "string"
        ):
            return "UNSUPPORTED", "Regex requires one string field"
        if ex.get("operator") != "regex" or not _REGEX_SAFE.fullmatch(
            ex.get("pattern", "")
        ):
            return (
                "UNSUPPORTED",
                "Only portable ASCII fixed-width regex patterns supported",
            )
    if engine == "csv_stream" and evaluator not in {
        "builtin.presence",
        "builtin.allowed_values",
        "builtin.regex_fullmatch",
        "builtin.composite_uniqueness",
    }:
        return (
            "UNSUPPORTED",
            "CSV stream is lexical; numeric/temporal coercion requires "
            "explicit adapter",
        )
    return "READY", "Safe portable subset"


def _read_options_problem(
    asset: Mapping[str, Any] | None, source: Any, engine: str
) -> tuple[str, str] | None:
    """Return a blocking verdict when a CSV cannot be read as declared."""
    if asset is None:
        return None
    if not is_csv_path(source):
        if asset.get("read_options"):
            return (
                "INVALID_CONTRACT",
                "read_options are only valid for CSV sources",
            )
        return None
    try:
        options = CsvReadOptions.from_asset(asset, path=source)
    except CsvDialectError as exc:
        return "INVALID_CONTRACT", f"Invalid read_options: {exc}"
    if options.engine_encoding(engine) is None:
        return (
            "UNSUPPORTED",
            f"Engine {engine} cannot decode {options.encoding}; "
            "use engine='csv_stream'",
        )
    return None


def plan_optimized(
    contract: Mapping[str, Any],
    *,
    data: Mapping[str, Any],
    engine: str = "auto",
) -> OptimizedPlan:
    """Preflight without running expensive data actions.

    Nothing is approved implicitly. The contract first passes the same
    structural validation used by every other engine.

    Raises:
        ContractError: If the contract is structurally invalid (schema
            violation or repeated identifiers).
    """
    validate_contract_structure(contract)
    assets = {a["id"]: a for a in contract.get("assets", [])}
    dimensions = {d["id"]: d for d in contract.get("dimensions", [])}
    metrics = {m["id"]: m for m in contract.get("metrics", [])}
    if not data:
        raise ValueError("At least one source required")
    engines = {aid: _source_engine(src, engine) for aid, src in data.items()}
    unsafe_spark = {
        aid
        for aid, src in data.items()
        if engines[aid] == "spark"
        and (
            ".connect." in type(src).__module__
            or getattr(src, "isStreaming", False)
        )
    }
    read_problems = {
        aid: _read_options_problem(assets.get(aid), src, engines[aid])
        for aid, src in data.items()
    }
    rules = []
    for rule in contract.get("rules", []):
        aid = rule.get("asset_id", "")
        metric = metrics.get(rule.get("metric_id"))
        chosen = engines.get(aid)
        if chosen is None:
            status, reason = "NEEDS_DATA", "Asset source not provided"
        else:
            status, reason = _plan_rule(
                rule, metric, assets.get(aid), chosen, dimensions
            )
            problem = read_problems.get(aid)
            if status == "READY" and problem is not None:
                status, reason = problem
            if aid in unsafe_spark:
                status, reason = (
                    "UNSUPPORTED",
                    "Spark Connect/Structured Streaming not verified",
                )
        evaluator = metric.get("evaluator", "") if metric else ""
        rules.append(
            PlannedRule(
                rule.get("id", ""),
                aid,
                rule.get("metric_id", ""),
                evaluator,
                tuple(rule.get("target", {}).get("fields", ())),
                status,
                reason,
                "shuffle"
                if evaluator == "builtin.composite_uniqueness"
                else "aggregate",
            )
        )
    groups = {}
    for aid, eng in engines.items():
        selected = [
            r for r in rules if r.asset_id == aid and r.status == "READY"
        ]
        if selected:
            simple_count = sum(r.cost == "aggregate" for r in selected)
            key_count = sum(r.cost == "shuffle" for r in selected)
            n_batches = (
                simple_count + _MAX_FUSED_RULES - 1
            ) // _MAX_FUSED_RULES
            groups[aid] = {
                "fused_aggregates": simple_count,
                "fused_batches": 1
                if eng == "csv_stream" and simple_count
                else n_batches,
                "max_rules_per_batch": _MAX_FUSED_RULES,
                "exact_key_checks": key_count,
                "estimated_full_passes": 1
                if eng == "csv_stream"
                else n_batches + 2 * key_count,
                "raw_rows_collected": 0,
            }
    return OptimizedPlan(
        engines,
        tuple(rules),
        groups,
        (
            "Counts are exact; engine-level floating-point and collation "
            "semantics require parity testing.",
            "No unapproved recommendations are executed.",
        ),
    )


def _null_sql(
    name: str, definition: Mapping[str, Any], *, is_float: bool
) -> str:
    """Build the SQL missingness predicate of one field.

    Args:
        name: Column name.
        definition: Contract field declaration.
        is_float: Whether the *engine* type is floating point; NaN detection
            follows the real type, not the declared ``physical_type``.
    """
    c = _esc(name)
    p = definition.get("missing_policy", {})
    pieces = [f"{c} IS NULL"]
    if is_float and nan_is_missing(p):
        # ``isnan`` alone is not enough: DuckDB 2.0 returned false for NaN
        # read from Parquet while ``isfinite``/``isinf`` were both false.
        # "Neither finite nor infinite" identifies NaN on every version.
        pieces.append(
            f"(isnan({c}) OR (NOT isfinite({c}) AND NOT isinf({c})))"
        )
    if _logical_type(definition["logical_type"]) == "string":
        if empty_string_is_missing(p):
            pieces.append(f"{c} = ''")
        if p.get("whitespace_only"):
            pieces.append(f"TRIM({c}) = ''")
    tokens = p.get("tokens", [])
    if tokens:
        if p.get("normalization") == "trim":
            pieces.append(
                f"TRIM({c}) IN (" + ",".join(_lit(t) for t in tokens) + ")"
            )
        else:
            pieces.append(
                f"{c} IN (" + ",".join(_lit(t) for t in tokens) + ")"
            )
    return "(" + " OR ".join(pieces) + ")"


def _sql_ok(rule: Mapping[str, Any], evaluator: str) -> str:
    names = rule["target"]["fields"]
    c = _esc(names[0])
    ex = rule["expectation"]
    if evaluator == "builtin.allowed_values":
        return f"{c} IN (" + ",".join(_lit(x) for x in ex["values"]) + ")"
    if evaluator == "builtin.value_range":
        op = ex["operator"]
        if op == "min":
            return f"{c} >= {_lit(ex['value'])}"
        if op == "max":
            return f"{c} <= {_lit(ex['value'])}"
        return f"{c} BETWEEN {_lit(ex['lower'])} AND {_lit(ex['upper'])}"
    if evaluator == "builtin.column_compare":
        op = {
            "eq": "=",
            "ne": "!=",
            "lt": "<",
            "lte": "<=",
            "gt": ">",
            "gte": ">=",
        }[ex["extensions"]["comparison"]]
        return f"{c} {op} {_esc(names[1])}"
    if evaluator == "builtin.regex_fullmatch":
        return f"regexp_full_match({c}, {_lit(ex['pattern'])})"
    raise ValueError("unsupported evaluator")


def _sql_state(
    rule: Mapping[str, Any],
    metric: Mapping[str, Any],
    fields: Mapping[str, Any],
    float_fields: frozenset[str],
) -> str:
    names = rule["target"]["fields"]
    evaluator = metric["evaluator"]
    missing = (
        "("
        + " OR ".join(
            _null_sql(n, fields[n], is_float=n in float_fields) for n in names
        )
        + ")"
    )
    if evaluator == "builtin.presence":
        return f"CASE WHEN {missing} THEN 0 ELSE 1 END"
    policy = _NULL_STATES[metric.get("null_policy", "indeterminate")]
    ok = _sql_ok(rule, evaluator)
    return f"CASE WHEN {missing} THEN {policy} WHEN ({ok}) THEN 1 ELSE 0 END"


def _as_aggregate_sql(
    rules: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    asset: Mapping[str, Any],
    float_fields: frozenset[str],
) -> str:
    fields = {f["name"]: f for f in asset["fields"]}
    exprs = ["COUNT(*) AS _total"]
    for index, r in enumerate(rules):
        state = _sql_state(r, metrics[r["metric_id"]], fields, float_fields)
        for code, label in (
            (1, "pass"),
            (0, "fail"),
            (-1, "unknown"),
            (-2, "excluded"),
        ):
            exprs.append(
                f"COUNT(*) FILTER (WHERE ({state}) = {code}) AS "
                f"_r{index}_{label}"
            )
    return ", ".join(exprs)


def _summary_counts(report_row: Mapping[str, Any], i: int) -> dict[str, int]:
    return {
        "passed": int(report_row[f"_r{i}_pass"] or 0),
        "failed": int(report_row[f"_r{i}_fail"] or 0),
        "unknown": int(report_row[f"_r{i}_unknown"] or 0),
        "excluded": int(report_row[f"_r{i}_excluded"] or 0),
    }


def _fetch_one(cursor: Any) -> tuple[Any, ...]:
    """Return the single row of an aggregate query, failing loudly if absent.

    An aggregate without ``GROUP BY`` always yields one row; ``None`` means
    the engine misbehaved and must not be read as zero counts.
    """
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError("Aggregate query returned no row")
    return tuple(row)


def _duckdb_run(
    source: Any,
    asset: Mapping[str, Any],
    ready: Sequence[PlannedRule],
    rules: Mapping[str, Mapping[str, Any]],
    metrics: Mapping[str, Any],
) -> dict[str, tuple[int, dict[str, int]]]:
    import duckdb

    conn = duckdb.connect()
    try:
        if is_csv_path(source):
            # Strict, declared dialect: no sniffing, malformed rows raise,
            # and empty fields reach the missingness policy as "" (DuckDB
            # would otherwise map them to NULL).
            options = CsvReadOptions.from_asset(asset, path=source)
            relation = duckdb_csv_relation(
                source,
                options,
                read_header(source, options),
                null_sentinel=NEVER_NULL_SENTINEL,
            )
        else:
            relation = f"read_parquet({_lit(str(source))})"
        described = conn.execute(
            f"DESCRIBE SELECT * FROM {relation}"
        ).fetchall()
        actual = {r[0]: _logical_type(str(r[1])) for r in described}
        float_fields = frozenset(
            str(r[0]) for r in described if is_float_type(str(r[1]))
        )
        for r in ready:
            for name in r.fields:
                if name not in actual:
                    raise ValueError(f"Missing source field: {name}")
                declared = _logical_type(
                    next(f for f in asset["fields"] if f["name"] == name)[
                        "logical_type"
                    ]
                )
                if declared != actual[name]:
                    raise ValueError(
                        f"Physical/logical mismatch for {name}: "
                        f"{actual[name]} vs {declared}"
                    )
        simple = [rules[r.rule_id] for r in ready if r.cost == "aggregate"]
        out = {}
        for batch in _batches(simple):
            aggregates = _as_aggregate_sql(batch, metrics, asset, float_fields)
            cur = conn.execute(f"SELECT {aggregates} FROM {relation}")
            record = dict(
                zip(
                    [x[0] for x in cur.description],
                    _fetch_one(cur),
                    strict=True,
                )
            )
            for i, r in enumerate(batch):
                out[r["id"]] = (
                    int(record["_total"]),
                    _summary_counts(record, i),
                )
        for p in [r for r in ready if r.cost == "shuffle"]:
            fields = {f["name"]: f for f in asset["fields"]}
            missing = (
                "("
                + " OR ".join(
                    _null_sql(n, fields[n], is_float=n in float_fields)
                    for n in p.fields
                )
                + ")"
            )
            keys = ", ".join(_esc(n) for n in p.fields)
            q = (
                f"SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE {missing}) "
                f"AS missing "
                f"FROM {relation}"
            )
            total, n_missing = _fetch_one(conn.execute(q))
            dupq = (
                f"SELECT COALESCE(SUM(CASE WHEN n>1 THEN n ELSE 0 END),0) "
                f"FROM "
                f"(SELECT {keys}, COUNT(*) n FROM {relation} WHERE NOT "
                f"{missing} GROUP BY {keys})"
            )
            duplicates = int(_fetch_one(conn.execute(dupq))[0])
            nullstate = _NULL_STATES[
                metrics[p.metric_id].get("null_policy", "indeterminate")
            ]
            counts = {
                "passed": int(total - n_missing - duplicates),
                "failed": int(duplicates),
                "unknown": 0,
                "excluded": 0,
            }
            counts[
                {1: "passed", 0: "failed", -1: "unknown", -2: "excluded"}[
                    nullstate
                ]
            ] += int(n_missing)
            out[p.rule_id] = (int(total), counts)
        return out
    finally:
        conn.close()


def _polars_expr(
    rule: Mapping[str, Any],
    metric: Mapping[str, Any],
    fields: Mapping[str, Any],
    pl: EngineModule,
    float_fields: frozenset[str],
) -> Any:
    names = rule["target"]["fields"]
    missing = pl.any_horizontal(
        [
            _polars_missing(n, fields[n], pl, is_float=n in float_fields)
            for n in names
        ]
    )
    evaluator = metric["evaluator"]
    if evaluator == "builtin.presence":
        return pl.when(missing).then(0).otherwise(1)
    a = pl.col(names[0])
    ex = rule["expectation"]
    if evaluator == "builtin.allowed_values":
        ok = a.is_in(ex["values"])
    elif evaluator == "builtin.value_range":
        op = ex["operator"]
        ok = (
            a >= ex["value"]
            if op == "min"
            else a <= ex["value"]
            if op == "max"
            else a.is_between(ex["lower"], ex["upper"])
        )
    elif evaluator == "builtin.column_compare":
        b = pl.col(names[1])
        op = ex["extensions"]["comparison"]
        ok = {
            "eq": lambda: a == b,
            "ne": lambda: a != b,
            "lt": lambda: a < b,
            "lte": lambda: a <= b,
            "gt": lambda: a > b,
            "gte": lambda: a >= b,
        }[op]()
    elif evaluator == "builtin.regex_fullmatch":
        ok = a.str.contains("^(?:" + ex["pattern"] + ")$")
    else:
        raise ValueError("Unknown portable evaluator")
    return (
        pl.when(missing)
        .then(_NULL_STATES[metric.get("null_policy", "indeterminate")])
        .when(ok)
        .then(1)
        .otherwise(0)
    )


def _polars_missing(
    name: str, f: Mapping[str, Any], pl: EngineModule, *, is_float: bool
) -> Any:
    x = pl.col(name)
    p = f.get("missing_policy", {})
    checks = [x.is_null()]
    if is_float and nan_is_missing(p):
        checks.append(x.is_nan())
    if _logical_type(f["logical_type"]) == "string":
        if empty_string_is_missing(p):
            checks.append(x == "")
        if p.get("whitespace_only"):
            checks.append(x.str.strip_chars() == "")
    tokens = p.get("tokens", [])
    if tokens:
        q = x.str.strip_chars() if p.get("normalization") == "trim" else x
        checks.append(q.is_in(tokens))
    return pl.any_horizontal(checks)


def _polars_scan_text_csv(
    pl: EngineModule, path: str, options: CsvReadOptions
) -> Any:
    """Scan a CSV as text, keeping empty fields as ``""`` on Polars 1 and 2.

    Polars 1 exposes ``missing_utf8_is_empty_string``; Polars 2 replaced it
    with ``empty_string_is_null``. Either way empty fields must reach the
    missingness policy as ``""``, as in the other readers.
    """
    import inspect

    encoding = options.engine_encoding("polars")
    if encoding is None:
        raise CsvDialectError(
            f"Polars cannot decode {options.encoding}; use csv_stream"
        )
    parameters = inspect.signature(pl.scan_csv).parameters
    kwargs: dict[str, Any] = {
        "infer_schema_length": 0,
        "separator": options.delimiter,
        "quote_char": options.quote_char,
        "encoding": encoding,
        "has_header": True,
    }
    if "empty_string_is_null" in parameters:
        kwargs["empty_string_is_null"] = False
    elif "missing_utf8_is_empty_string" in parameters:
        kwargs["missing_utf8_is_empty_string"] = True
    else:
        raise RuntimeError(
            "Unsupported Polars version: cannot preserve empty CSV fields"
        )
    return pl.scan_csv(path, **kwargs)


def _polars_run(
    source: Any,
    asset: Mapping[str, Any],
    ready: Sequence[PlannedRule],
    rules: Mapping[str, Mapping[str, Any]],
    metrics: Mapping[str, Any],
) -> dict[str, tuple[int, dict[str, int]]]:
    import polars as pl

    if is_csv_path(source):
        source_path = str(source)
        options = CsvReadOptions.from_asset(asset, path=source_path)
        # Polars pads short rows with empty values and checks headers
        # loosely, so the structure is verified first (one extra sequential
        # pass, constant memory).
        validate_structure(source_path, options)
        lf = _polars_scan_text_csv(pl, source_path, options)
    elif isinstance(source, (str, Path)):
        lf = pl.scan_parquet(str(source))
    else:
        lf = source.lazy() if isinstance(source, pl.DataFrame) else source
    schema = lf.collect_schema()
    float_fields = frozenset(
        str(n) for n, dtype in schema.items() if dtype.is_float()
    )
    decl = {f["name"]: f for f in asset["fields"]}
    for r in ready:
        for name in r.fields:
            if name not in schema:
                raise ValueError(f"Missing field: {name}")
            if _logical_type(str(schema[name])) != _logical_type(
                decl[name]["logical_type"]
            ):
                raise ValueError(f"Type mismatch: {name}")
    simple = [rules[r.rule_id] for r in ready if r.cost == "aggregate"]
    out = {}
    for batch in _batches(simple):
        agg = [pl.len().alias("_total")]
        for i, r in enumerate(batch):
            state = _polars_expr(
                r, metrics[r["metric_id"]], decl, pl, float_fields
            )
            for code, label in (
                (1, "pass"),
                (0, "fail"),
                (-1, "unknown"),
                (-2, "excluded"),
            ):
                agg.append((state == code).sum().alias(f"_r{i}_{label}"))
        row = lf.select(agg).collect(engine="streaming").row(0, named=True)
        for i, r in enumerate(batch):
            out[r["id"]] = (int(row["_total"]), _summary_counts(row, i))
    for p in [r for r in ready if r.cost == "shuffle"]:
        missing = pl.any_horizontal(
            [
                _polars_missing(n, decl[n], pl, is_float=n in float_fields)
                for n in p.fields
            ]
        )
        first = (
            lf.select(pl.len().alias("n"), missing.sum().alias("missing"))
            .collect(engine="streaming")
            .row(0, named=True)
        )
        valid = (
            lf.filter(~missing)
            .group_by(list(p.fields))
            .agg(pl.len().alias("_n"))
        )
        count = valid.select(
            pl.when(pl.col("_n") > 1)
            .then(pl.col("_n"))
            .otherwise(0)
            .sum()
            .alias("duplicates")
        )
        duplicates = int(
            count.collect(engine="streaming")["duplicates"][0] or 0
        )
        n = int(first["n"])
        miss = int(first["missing"])
        nullstate = _NULL_STATES[
            metrics[p.metric_id].get("null_policy", "indeterminate")
        ]
        counts = {
            "passed": n - miss - duplicates,
            "failed": duplicates,
            "unknown": 0,
            "excluded": 0,
        }
        counts[
            {1: "passed", 0: "failed", -1: "unknown", -2: "excluded"}[
                nullstate
            ]
        ] += miss
        out[p.rule_id] = (n, counts)
    return out


def _spark_col(name: str, F: EngineModule) -> SparkColumn:
    return F.col("`" + name.replace("`", "``") + "`")


def _spark_missing(
    name: str, f: Mapping[str, Any], F: EngineModule, *, is_float: bool
) -> SparkColumn:
    x = _spark_col(name, F)
    p = f.get("missing_policy", {})
    checks = [x.isNull()]
    if is_float and nan_is_missing(p):
        checks.append(F.isnan(x))
    if _logical_type(f["logical_type"]) == "string":
        if empty_string_is_missing(p):
            checks.append(x == F.lit(""))
        if p.get("whitespace_only"):
            checks.append(F.trim(x) == F.lit(""))
    if p.get("tokens"):
        y = F.trim(x) if p.get("normalization") == "trim" else x
        checks.append(y.isin(p["tokens"]))
    out = checks[0]
    for q in checks[1:]:
        out = out | q
    return out


def _spark_expr(
    rule: Mapping[str, Any],
    metric: Mapping[str, Any],
    fields: Mapping[str, Any],
    F: EngineModule,
    float_fields: frozenset[str],
) -> SparkColumn:
    names = rule["target"]["fields"]
    missing = _spark_missing(
        names[0], fields[names[0]], F, is_float=names[0] in float_fields
    )
    for name in names[1:]:
        missing = missing | _spark_missing(
            name, fields[name], F, is_float=name in float_fields
        )
    evaluator = metric["evaluator"]
    if evaluator == "builtin.presence":
        return F.when(missing, F.lit(0)).otherwise(F.lit(1))
    a = _spark_col(names[0], F)
    ex = rule["expectation"]
    if evaluator == "builtin.allowed_values":
        ok = a.isin(ex["values"])
    elif evaluator == "builtin.value_range":
        op = ex["operator"]
        ok = (
            a >= F.lit(ex["value"])
            if op == "min"
            else a <= F.lit(ex["value"])
            if op == "max"
            else a.between(ex["lower"], ex["upper"])
        )
    elif evaluator == "builtin.column_compare":
        b = _spark_col(names[1], F)
        op = ex["extensions"]["comparison"]
        ok = {
            "eq": lambda: a == b,
            "ne": lambda: a != b,
            "lt": lambda: a < b,
            "lte": lambda: a <= b,
            "gt": lambda: a > b,
            "gte": lambda: a >= b,
        }[op]()
    elif evaluator == "builtin.regex_fullmatch":
        ok = a.rlike("^(?:" + ex["pattern"] + ")$")
    else:
        raise ValueError("Unknown portable evaluator")
    return (
        F.when(
            missing,
            F.lit(_NULL_STATES[metric.get("null_policy", "indeterminate")]),
        )
        .when(ok, F.lit(1))
        .otherwise(F.lit(0))
    )


def _spark_run(
    source: Any,
    asset: Mapping[str, Any],
    ready: Sequence[PlannedRule],
    rules: Mapping[str, Mapping[str, Any]],
    metrics: Mapping[str, Any],
) -> dict[str, tuple[int, dict[str, int]]]:
    from pyspark.sql import functions as F

    if ".connect." in type(source).__module__ or source.isStreaming:
        raise ValueError("Spark Connect and Streaming are not verified")
    decl = {f["name"]: f for f in asset["fields"]}
    actual = {x.name: x.dataType.simpleString() for x in source.schema.fields}
    float_fields = frozenset(n for n, t in actual.items() if is_float_type(t))
    for r in ready:
        for name in r.fields:
            if name not in actual:
                raise ValueError(f"Missing field: {name}")
            if _logical_type(actual[name]) != _logical_type(
                decl[name]["logical_type"]
            ):
                raise ValueError(f"Type mismatch: {name}")
    simple = [rules[r.rule_id] for r in ready if r.cost == "aggregate"]
    out = {}
    for batch in _batches(simple):
        exprs = [F.count(F.lit(1)).alias("_total")]
        for i, r in enumerate(batch):
            state = _spark_expr(
                r, metrics[r["metric_id"]], decl, F, float_fields
            )
            for code, label in (
                (1, "pass"),
                (0, "fail"),
                (-1, "unknown"),
                (-2, "excluded"),
            ):
                exprs.append(
                    F.sum(
                        F.when(state == F.lit(code), F.lit(1)).otherwise(
                            F.lit(0)
                        )
                    ).alias(f"_r{i}_{label}")
                )
        row = source.agg(*exprs).first().asDict()
        for i, r in enumerate(batch):
            out[r["id"]] = (int(row["_total"]), _summary_counts(row, i))
    for p in [r for r in ready if r.cost == "shuffle"]:
        missing = _spark_missing(
            p.fields[0],
            decl[p.fields[0]],
            F,
            is_float=p.fields[0] in float_fields,
        )
        for name in p.fields[1:]:
            missing = missing | _spark_missing(
                name, decl[name], F, is_float=name in float_fields
            )
        first = source.agg(
            F.count(F.lit(1)).alias("n"),
            F.sum(F.when(missing, 1).otherwise(0)).alias("missing"),
        ).first()
        grouped = (
            source.where(~missing)
            .groupBy(*[_spark_col(n, F) for n in p.fields])
            .count()
        )
        dup = grouped.agg(
            F.sum(
                F.when(F.col("count") > 1, F.col("count")).otherwise(F.lit(0))
            ).alias("duplicates")
        ).first()
        n = int(first["n"])
        miss = int(first["missing"] or 0)
        duplicates = int(dup["duplicates"] or 0)
        nullstate = _NULL_STATES[
            metrics[p.metric_id].get("null_policy", "indeterminate")
        ]
        counts = {
            "passed": n - miss - duplicates,
            "failed": duplicates,
            "unknown": 0,
            "excluded": 0,
        }
        counts[
            {1: "passed", 0: "failed", -1: "unknown", -2: "excluded"}[
                nullstate
            ]
        ] += miss
        out[p.rule_id] = (n, counts)
    return out


def _csv_run(
    source: Any,
    asset: Mapping[str, Any],
    ready: Sequence[PlannedRule],
    rules: Mapping[str, Mapping[str, Any]],
    metrics: Mapping[str, Any],
    *,
    max_exact_keys: int = 1_000_000,
) -> dict[str, tuple[int, dict[str, int]]]:
    """One CSV read for all simple checks. Exact unique-key checking has a hard
    memory cap.
    """
    path = Path(source)
    options = CsvReadOptions.from_asset(asset, path=path)
    fields = {f["name"]: f for f in asset["fields"]}
    simple = [r for r in ready if r.cost == "aggregate"]
    key_rules = [r for r in ready if r.cost == "shuffle"]
    buckets = {
        r.rule_id: {"passed": 0, "failed": 0, "unknown": 0, "excluded": 0}
        for r in ready
    }
    key_counts: dict[str, dict[Any, int]] = {r.rule_id: {} for r in key_rules}
    n = 0
    header = read_header(path, options)
    for r in ready:
        if any(k not in header for k in r.fields):
            raise ValueError("Missing CSV field")
    for row in iter_records(path, options):
        n += 1
        for r in simple:
            rule = rules[r.rule_id]
            metric = metrics[r.metric_id]
            misses = [_csv_missing(row[c], fields[c]) for c in r.fields]
            if r.evaluator == "builtin.presence":
                state = 0 if any(misses) else 1
            elif any(misses):
                state = _NULL_STATES[
                    metric.get("null_policy", "indeterminate")
                ]
            elif r.evaluator == "builtin.allowed_values":
                state = int(row[r.fields[0]] in rule["expectation"]["values"])
            elif r.evaluator == "builtin.regex_fullmatch":
                state = int(
                    re.fullmatch(
                        rule["expectation"]["pattern"],
                        row[r.fields[0]],
                        flags=re.ASCII,
                    )
                    is not None
                )
            else:
                raise ValueError("Unsupported CSV simple evaluator")
            buckets[r.rule_id][
                {1: "passed", 0: "failed", -1: "unknown", -2: "excluded"}[
                    state
                ]
            ] += 1
        for r in key_rules:
            vals = tuple(row[k] for k in r.fields)
            if any(_csv_missing(row[k], fields[k]) for k in r.fields):
                state = _NULL_STATES[
                    metrics[r.metric_id].get("null_policy", "indeterminate")
                ]
                buckets[r.rule_id][
                    {
                        1: "passed",
                        0: "failed",
                        -1: "unknown",
                        -2: "excluded",
                    }[state]
                ] += 1
            else:
                tally = key_counts[r.rule_id]
                tally[vals] = tally.get(vals, 0) + 1
                if len(tally) > max_exact_keys:
                    raise MemoryError(
                        "Exact CSV uniqueness exceeds hard cap; switch to "
                        "DuckDB"
                    )
    for r in key_rules:
        dup = sum(c for c in key_counts[r.rule_id].values() if c > 1)
        buckets[r.rule_id]["failed"] += dup
        buckets[r.rule_id]["passed"] += sum(
            c for c in key_counts[r.rule_id].values() if c == 1
        )
    return {r.rule_id: (n, buckets[r.rule_id]) for r in ready}


def _csv_missing(x: str, field: Mapping[str, Any]) -> bool:
    if x is None:
        return True
    p = field.get("missing_policy", {})
    if empty_string_is_missing(p) and x == "":
        return True
    if p.get("whitespace_only") and x.strip() == "":
        return True
    y = x.strip() if p.get("normalization") == "trim" else x
    return y in p.get("tokens", [])


def _status(
    lower: float | None, upper: float | None, threshold: Mapping[str, Any]
) -> str:
    if lower is None:
        return "NO_ELIGIBLE_DATA"
    op = threshold["operator"]
    val = threshold["value"]
    if op == "gte":
        return (
            "PASS"
            if lower >= val
            else "FAIL"
            if upper < val
            else "INDETERMINATE"
        )
    if op == "gt":
        return (
            "PASS"
            if lower > val
            else "FAIL"
            if upper <= val
            else "INDETERMINATE"
        )
    if op == "lte":
        return (
            "PASS"
            if upper <= val
            else "FAIL"
            if lower > val
            else "INDETERMINATE"
        )
    if op == "lt":
        return (
            "PASS"
            if upper < val
            else "FAIL"
            if lower >= val
            else "INDETERMINATE"
        )
    if op == "eq":
        return (
            "PASS"
            if lower == upper == val
            else "FAIL"
            if val < lower or val > upper
            else "INDETERMINATE"
        )
    if op == "between":
        hi = threshold["upper"]
        return (
            "PASS"
            if lower >= val and upper <= hi
            else "FAIL"
            if upper < val or lower > hi
            else "INDETERMINATE"
        )
    raise ValueError("Invalid threshold")


def evaluate_optimized(
    contract: Mapping[str, Any],
    *,
    data: Mapping[str, Any],
    engine: str = "auto",
    max_exact_keys: int = 1_000_000,
) -> OptimizedReport:
    """Run explicitly declared rules only; no implicit conversion or
    auto-approval.
    """
    plan = plan_optimized(contract, data=data, engine=engine)
    rules = {r["id"]: r for r in contract.get("rules", [])}
    assets = {a["id"]: a for a in contract.get("assets", [])}
    metrics = {m["id"]: m for m in contract.get("metrics", [])}
    measured = {}
    errors = {}
    for aid, backend in plan.engine_by_asset.items():
        ready = [
            p for p in plan.rules if p.asset_id == aid and p.status == "READY"
        ]
        if not ready:
            continue
        try:
            if backend == "csv_stream":
                result = _csv_run(
                    data[aid],
                    assets[aid],
                    ready,
                    rules,
                    metrics,
                    max_exact_keys=max_exact_keys,
                )
            elif backend == "duckdb":
                result = _duckdb_run(
                    data[aid], assets[aid], ready, rules, metrics
                )
            elif backend == "polars":
                result = _polars_run(
                    data[aid], assets[aid], ready, rules, metrics
                )
            elif backend == "spark":
                result = _spark_run(
                    data[aid], assets[aid], ready, rules, metrics
                )
            else:
                raise TypeError(f"Unsupported backend: {backend}")
            measured.update(result)
        except Exception as exc:
            # The execution is not allowed to claim success after an engine
            # error.
            for r in ready:
                errors[r.rule_id] = f"{type(exc).__name__}: {exc}"
    outputs = []
    for p in plan.rules:
        metric = metrics.get(p.metric_id, {})
        rule = rules[p.rule_id]
        if p.rule_id not in measured:
            reason = errors.get(p.rule_id, p.reason)
            outputs.append(
                OptimizedRuleResult(
                    p.rule_id,
                    p.asset_id,
                    p.metric_id,
                    tuple(metric.get("dimension_ids", ())),
                    "ERROR" if p.rule_id in errors else p.status,
                    p.status,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    rule.get("threshold"),
                    (reason,),
                )
            )
            continue
        total, counts = measured[p.rule_id]
        passed, failed, unknown, excluded = (
            counts[k] for k in ("passed", "failed", "unknown", "excluded")
        )
        if passed + failed + unknown + excluded != total:
            raise AssertionError("Count invariant violated")
        eligible = passed + failed + unknown
        observed = passed + failed
        bounds = rate_bounds(
            passed, failed, unknown, model=bound_model(p.evaluator)
        )
        low, upper = bounds.lower, bounds.upper
        outputs.append(
            OptimizedRuleResult(
                p.rule_id,
                p.asset_id,
                p.metric_id,
                tuple(metric.get("dimension_ids", ())),
                _status(low, upper, rule["threshold"]),
                "READY",
                total,
                eligible,
                passed,
                failed,
                unknown,
                excluded,
                passed / observed if observed else None,
                low,
                upper,
                observed / eligible if eligible else None,
                rule["threshold"],
            )
        )
    digest = sha256(
        json.dumps(
            contract, sort_keys=True, default=str, ensure_ascii=False
        ).encode()
    ).hexdigest()
    return OptimizedReport(
        str(contract.get("contract_version", "unknown")),
        digest,
        datetime.now(timezone.utc).isoformat(),
        plan,
        tuple(outputs),
        sources={aid: describe_source(src) for aid, src in data.items()},
    )
