"""Scalable, conservative, and review-only quality-rule advisor.

No pandas materialization, and never convert Spark/Polars/DuckDB sources into
pandas. Source adapters return aggregate metadata; only CSV's optional bounded
reservoir holds raw rows, in process and never serialized.

Discovering a plausible rule is not certifying that rule or its observed
compliance.
"""

from __future__ import annotations

import csv
import gzip
import json
import math
import random
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import blake2b, sha256
from itertools import combinations
from pathlib import Path
from typing import Any

from ._io import write_text_atomic
from ._types import SparkFrame
from .csv_dialect import (
    CsvReadOptions,
    duckdb_csv_relation,
    field_size_limit,
    is_csv_path,
    read_header,
)
from .evaluator_registry import REGISTRY
from .heuristics import (
    HeuristicThresholds,
    dependency_evidence,
    good_turing_unseen_mass,
    has_role,
    hll_suggests_unique,
    min_detectable_duplicate_share,
    rule_of_three_upper,
    semantic_roles,
    start_end_pairs,
)
from .missingness import NEVER_NULL_SENTINEL

# Semantic roles and start/end vocabularies live in ``heuristics`` and are
# matched on name tokens, not substrings (audit finding A3).
_RE_CODE = re.compile(r"^[0-9]+$")
_RE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[ T].*)?$")
_RE_AMBIGUOUS = re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$")
_SENTINELS = frozenset(
    {
        "N/A",
        "NA",
        "NULL",
        "NAN",
        "ND",
        "SIN DATO",
        "S/D",
        "UNKNOWN",
        "#N/A",
        "?",
    }
)
# Output cap for cross-asset name hints (a volume limit, not a decision
# threshold): beyond it, hints stop being reviewable.
_MAX_RELATIONSHIP_HINTS = 12
_ID_VALID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")


@dataclass(frozen=True)
class DiscoveryBudget:
    """Limits are part of the public contract, not hints to be silently
    ignored.
    """

    mode: str = "balanced"  # schema | balanced | deep
    max_profile_columns: int = 64
    max_sample_rows: int = 1500
    max_sample_field_chars: int = 256
    max_candidate_keys: int = 12
    max_key_pairs: int = 24
    max_exact_key_values: int = 20_000
    max_domain_values: int = 20
    hll_precision: int = 10
    seed: int = 42
    allow_sample_values: bool = (
        False  # local analysis; never in default report
    )
    # Every threshold that decides whether a hypothesis is proposed; shared
    # with the pandas advisor so both propose the same things.
    thresholds: HeuristicThresholds = field(
        default_factory=HeuristicThresholds
    )

    def __post_init__(self) -> None:
        """Validate the mode and require every numeric limit to be positive."""
        if self.mode not in {"schema", "balanced", "deep"}:
            raise ValueError("mode must be schema, balanced or deep")
        for name in (
            "max_profile_columns",
            "max_sample_rows",
            "max_sample_field_chars",
            "max_candidate_keys",
            "max_key_pairs",
            "max_exact_key_values",
            "max_domain_values",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if not 5 <= self.hll_precision <= 16:
            raise ValueError("hll_precision must be in [5,16]")


class ApproxCardinality:
    """Deterministic HyperLogLog sketch, descriptive only, never uniqueness
    proof.
    """

    def __init__(self, precision: int = 10):
        """Create a sketch with ``2 ** precision`` registers."""
        self.p = precision
        self.m = 1 << precision
        self.reg = bytearray(self.m)

    def add(self, value: str) -> None:
        """Add one value to the sketch; the value itself is never stored."""
        digest = int.from_bytes(
            blake2b(
                value.encode("utf-8", "surrogatepass"), digest_size=8
            ).digest(),
            "big",
        )
        index = digest >> (64 - self.p)
        tail = (digest << self.p) & ((1 << 64) - 1)
        bits = 64 - self.p
        rho = bits + 1 if tail == 0 else 65 - tail.bit_length()
        if rho > self.reg[index]:
            self.reg[index] = rho

    def estimate(self) -> float:
        """Return the estimated number of distinct values added so far."""
        import math

        m = self.m
        alpha = (
            0.7213 / (1.0 + 1.079 / m)
            if m >= 128
            else (0.673 if m == 16 else 0.697 if m == 32 else 0.709)
        )
        total = sum(2.0**-v for v in self.reg)
        e = alpha * m * m / total
        zeroes = self.reg.count(0)
        if e <= 2.5 * m and zeroes:
            e = m * math.log(m / zeroes)
        return e

    @property
    def relative_std_error(self) -> float:
        """Theoretical relative standard error of the estimate."""
        return 1.04 / self.m**0.5


@dataclass(frozen=True)
class Advice:
    """One reviewable recommendation produced by discovery.

    Advice is advisory: nothing is enabled until a person approves it.
    """

    id: str
    kind: str
    title: str
    asset_id: str
    fields: tuple[str, ...]
    dimension: str | None
    evaluator: str | None
    expectation: dict[str, Any] | None
    confidence: str  # ordinal heuristic; not probability
    priority: str  # review order, not quality severity
    cost: str  # schema | aggregate | exact_scan | external
    rationale: str
    evidence: dict[str, Any]
    prerequisites: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()
    supported_backends: tuple[str, ...] = ()

    def to_dict(self, reveal_configuration: bool = False) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary.

        Args:
            reveal_configuration: Include raw expectation values, which may
                contain data values, only when explicitly requested.
        """
        out = {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "asset_id": self.asset_id,
            "fields": list(self.fields),
            "dimension": self.dimension,
            "evaluator": self.evaluator,
            "confidence": self.confidence,
            "priority": self.priority,
            "cost": self.cost,
            "rationale": self.rationale,
            "evidence": self.evidence,
            "prerequisites": list(self.prerequisites),
            "caveats": list(self.caveats),
            "supported_backends": list(self.supported_backends),
            "approval_required": True,
        }
        if reveal_configuration:
            out["expectation"] = self.expectation
        return out


@dataclass(frozen=True)
class AdvancedDiscoveryReport:
    """Result of :func:`discover_advanced`.

    Holds per-asset profiles, recommendations and warnings.
    """

    assets: dict[str, dict[str, Any]]
    recommendations: tuple[Advice, ...]
    warnings: tuple[str, ...] = ()
    budget: DiscoveryBudget = field(default_factory=DiscoveryBudget)

    def to_dict(self, *, reveal_configuration: bool = False) -> dict[str, Any]:
        """Return the whole report as a JSON-serialisable dictionary."""
        return {
            "version": "0.8.0.dev0",
            "budget": self.budget.__dict__,
            "assets": self.assets,
            "warnings": list(self.warnings),
            "recommendations": [
                r.to_dict(reveal_configuration) for r in self.recommendations
            ],
        }

    def to_json(
        self,
        path: str | Path | None = None,
        *,
        reveal_configuration: bool = False,
    ) -> str:
        """Serialise to JSON, optionally writing it to ``path``.

        Returns:
            The JSON text.

        Args:
            path: Optional output file.
            reveal_configuration: See :meth:`Advice.to_dict`.
        """
        # Budget reveals no row values. All raw lexical samples are kept
        # internally.
        doc = json.dumps(
            self.to_dict(reveal_configuration=reveal_configuration),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
            default=str,
        )
        if path is not None:
            write_text_atomic(path, doc + "\n")
        return doc

    def capability_coverage(self) -> dict[str, Any]:
        """Comparable recommendation-family counts; not a promise of equal data
        semantics.
        """
        out = {}
        for aid in self.assets:
            arr = [r for r in self.recommendations if r.asset_id == aid]
            families: dict[str, Any] = {}
            for r in arr:
                key = r.evaluator or r.kind
                families[key] = families.get(key, 0) + 1
            out[aid] = {
                "engine": self.assets[aid]["backend"],
                "families": families,
                "total_recommendations": len(arr),
            }
        return out

    def resource_plan(self) -> dict[str, Any]:
        """Auditable footprint; reports actual passes, not fabricated
        timing/cost.
        """
        output = {}
        for aid, a in self.assets.items():
            mode = a["statistics_scope"]
            scans = 0 if mode == "schema_only" else 1
            output[aid] = {
                "backend": a["backend"],
                "full_passes": scans,
                "distributed_jobs": 1
                if a["backend"] == "spark" and scans
                else 0,
                "raw_rows_returned_to_driver": 0
                if a["backend"] in {"spark", "duckdb", "polars_lazy"}
                else None,
                "local_sample_cap_rows": self.budget.max_sample_rows
                if a["backend"] == "csv_stream" and scans
                else 0,
                "profiled_column_count": sum(
                    c["observations_profiled"] is not None
                    for c in a["columns"]
                ),
                "descriptive_cardinality_approximate": any(
                    c["approx_distinct"] is not None for c in a["columns"]
                ),
            }
        return output

    def review_queue(self, priority: str | None = None) -> list[Advice]:
        """Return recommendations ordered by priority.

        Args:
            priority: When given, keep only recommendations of that priority.
        """
        rank = {"high": 0, "medium": 1, "low": 2}
        return sorted(
            (
                x
                for x in self.recommendations
                if priority is None or x.priority == priority
            ),
            key=lambda x: (rank.get(x.priority, 3), x.cost, x.asset_id, x.id),
        )

    def approved_plan(
        self, ids: Sequence[str], *, purpose: str
    ) -> dict[str, Any]:
        """Review manifest, NOT an executable quality contract; requires typed
        bindings.
        """
        if not purpose.strip():
            raise ValueError("purpose must not be empty")
        mapping = {r.id: r for r in self.recommendations}
        if len(ids) != len(set(ids)) or any(i not in mapping for i in ids):
            raise ValueError("unknown or repeated recommendation")
        return {
            "purpose": purpose,
            "reviewed": True,
            "automatic_approval": False,
            "rules": [
                mapping[i].to_dict(reveal_configuration=True) for i in ids
            ],
            "note": (
                "Review manifest only. Requires explicit bindings, types, and "
                "compatible execution backend."
            ),
        }


def _advice(
    kind: str,
    title: str,
    asset_id: str,
    fields: Iterable[str],
    *,
    dimension: str | None = None,
    evaluator: str | None = None,
    expectation: dict[str, Any] | None = None,
    confidence: str = "low",
    priority: str = "medium",
    cost: str = "aggregate",
    rationale: str = "",
    evidence: dict[str, Any] | None = None,
    prerequisites: Iterable[str] = (),
    caveats: Iterable[str] = (),
) -> Advice:
    key = "\x1f".join((kind, evaluator or "", asset_id, *fields, title))
    rid = "recommend_" + sha256(key.encode("utf-8")).hexdigest()[:16]
    support = tuple(
        sorted(
            (
                REGISTRY[evaluator].backends
                if evaluator in REGISTRY
                else frozenset()
            )
            | (
                {"csv_stream", "duckdb", "polars", "spark"}
                if evaluator
                in {
                    "builtin.presence",
                    "builtin.composite_uniqueness",
                    "builtin.allowed_values",
                    "builtin.regex_fullmatch",
                }
                else {"duckdb", "polars", "spark"}
                if evaluator
                in {"builtin.value_range", "builtin.column_compare"}
                else set()
            )
        )
    )
    return Advice(
        rid,
        kind,
        title,
        asset_id,
        tuple(fields),
        dimension,
        evaluator,
        expectation,
        confidence,
        priority,
        cost,
        rationale,
        evidence or {},
        tuple(prerequisites),
        tuple(caveats),
        support,
    )


def _roles(name: str, logical: str) -> list[str]:
    return semantic_roles(name, logical)


def _logical_from_native(type_name: str) -> str:
    t = type_name.lower()
    if any(
        t.startswith(prefix) for prefix in ("array", "list", "map", "struct")
    ):
        return "nested"
    if "timestamp" in t or "datetime" in t:
        return "datetime"
    if t == "date":
        return "date"
    if t in {
        "int",
        "int8",
        "int16",
        "int32",
        "int64",
        "int128",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "tinyint",
        "smallint",
        "bigint",
        "hugeint",
        "integer",
        "long",
        "short",
        "byte",
    }:
        return "integer"
    if any(x in t for x in ("decimal", "float", "double", "real")):
        return "decimal"
    if any(
        x in t for x in ("varchar", "text", "string", "utf8", "large_string")
    ):
        return "string"
    if "bool" in t:
        return "boolean"
    return "unknown"


def _init_profile(name: str, physical: str, logical: str) -> dict[str, Any]:
    return {
        "name": name,
        "physical_type": physical,
        "logical_type": logical,
        "semantic_roles": _roles(name, logical),
        "nulls": None,
        "blanks": None,
        "possible_sentinel_count": None,
        "approx_distinct": None,
        "approx_distinct_relative_error": None,
        "observations_profiled": None,
        "lexical_digits_only": None,
        "lexical_min_length": None,
        "lexical_max_length": None,
    }


def _is_csv_path(path: Path) -> bool:
    return str(path).lower().endswith((".csv", ".tsv", ".csv.gz", ".tsv.gz"))


def _read_csv_stream(
    path: Path,
    b: DiscoveryBudget,
    *,
    delimiter: str = ",",
    encoding: str = "utf-8-sig",
    quote_char: str = '"',
) -> dict[str, Any]:
    """A single sequential pass, O(columns + sample_rows*columns + bounded
    sketches).
    """
    opener = gzip.open if path.name.lower().endswith(".gz") else Path.open
    if opener is gzip.open:
        handle_context = gzip.open(
            path, mode="rt", encoding=encoding, newline=""
        )
    else:
        handle_context = path.open("r", encoding=encoding, newline="")
    with handle_context as handle:
        reader = csv.DictReader(
            handle,
            delimiter=delimiter,
            quotechar=quote_char,
            restkey="__qd_extra_fields__",
        )
        fields = list(reader.fieldnames or [])
        if (
            not fields
            or len(set(fields)) != len(fields)
            or any(not f or f == "__qd_extra_fields__" for f in fields)
        ):
            raise ValueError("CSV must have unique, nonempty headers")
        selected = fields[: b.max_profile_columns]
        profile = [_init_profile(f, "utf8", "string") for f in fields]
        byname = {c["name"]: c for c in profile}
        n = 0
        reservoir: list[Any] = []
        rng = random.Random(b.seed)
        sketches = (
            {
                f: ApproxCardinality(b.hll_precision)
                for f in selected
                if has_role(f, "identifier")
            }
            if b.mode != "schema"
            else {}
        )
        candidate_keys = list(sketches)[: b.max_candidate_keys]
        exact: dict[str, set[Any]] = {c: set() for c in candidate_keys}
        exact_overflow = set()
        num_missing = {f: 0 for f in selected}
        num_blank = {f: 0 for f in selected}
        num_sentinel = {f: 0 for f in selected}
        code_digits = {f: 0 for f in selected if has_role(f, "identifier")}
        code_min: dict[str, int | None] = {f: None for f in code_digits}
        code_max: dict[str, int | None] = {f: None for f in code_digits}
        malformed = 0
        if b.mode == "schema":
            return {
                "backend": "csv_stream",
                "rows": None,
                "columns": profile,
                "statistics_scope": "schema_only",
                "sample": [],
                "key_samples": {},
                "exact_key_uniqueness": {},
                "warnings": [
                    "CSV schema-only: all fields remain lexical strings"
                ],
            }
        for raw in reader:
            n += 1
            if raw.get("__qd_extra_fields__") is not None:
                malformed += 1
            row = {}
            for c in selected:
                value = raw.get(c)
                if value is None:
                    malformed += 1
                    num_missing[c] += 1
                    continue
                if value == "":
                    num_blank[c] += 1
                if value.strip().upper() in _SENTINELS:
                    num_sentinel[c] += 1
                if c in code_digits and value != "":
                    if _RE_CODE.fullmatch(value):
                        code_digits[c] += 1
                    current_min, current_max = code_min[c], code_max[c]
                    code_min[c] = (
                        len(value)
                        if current_min is None
                        else min(current_min, len(value))
                    )
                    code_max[c] = (
                        len(value)
                        if current_max is None
                        else max(current_max, len(value))
                    )
                if c in sketches and value != "":
                    sketches[c].add(value)
                    if c not in exact_overflow:
                        exact[c].add(value)
                        if len(exact[c]) > b.max_exact_key_values:
                            exact_overflow.add(c)
                            exact[c].clear()
                row[c] = value[: b.max_sample_field_chars]
            if len(reservoir) < b.max_sample_rows:
                reservoir.append(row)
            else:
                j = rng.randrange(n)
                if j < len(reservoir):
                    reservoir[j] = row
        for f in selected:
            x = byname[f]
            x.update(
                nulls=num_missing[f],
                blanks=num_blank[f],
                possible_sentinel_count=num_sentinel[f],
                observations_profiled=n,
            )
            if f in code_digits:
                x.update(
                    lexical_digits_only=code_digits[f],
                    lexical_min_length=code_min[f],
                    lexical_max_length=code_max[f],
                )
            if f in sketches:
                x.update(
                    approx_distinct=int(round(sketches[f].estimate())),
                    approx_distinct_relative_error=round(
                        sketches[f].relative_std_error, 5
                    ),
                )
        key_status = {
            f: {
                "exact_distinct": len(exact[f])
                if f not in exact_overflow
                else None,
                "overflow": f in exact_overflow,
            }
            for f in candidate_keys
        }
        warn = [
            "CSV lexical profile preserves leading zeros; blanks are not "
            "automatically null."
        ]
        if malformed:
            warn.append(
                f"CSV contains {malformed} missing/extra field occurrences; "
                f"structural review needed."
            )
        if len(fields) > len(selected):
            warn.append(
                f"Profile budget limited statistics to {len(selected)} of "
                f"{len(fields)} columns."
            )
        return {
            "backend": "csv_stream",
            "rows": n,
            "columns": profile,
            "statistics_scope": "full_scan_plus_bounded_reservoir",
            "sample": reservoir,
            "exact_key_uniqueness": key_status,
            "warnings": warn,
        }


def _q_sql(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _lit_sql(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _duckdb_source(
    path: Path,
    b: DiscoveryBudget,
    *,
    connection: Any = None,
    csv_options: CsvReadOptions | None = None,
) -> dict[str, Any]:
    try:
        import duckdb
    except ImportError as exc:
        raise ImportError(
            'DuckDB support requires `pip install "quaxdim[duckdb]"`'
        ) from exc
    conn = connection or duckdb.connect()
    try:
        return _duckdb_source_with_connection(
            conn, path, b, csv_options=csv_options
        )
    finally:
        if connection is None:
            conn.close()


def _duckdb_source_with_connection(
    conn: Any,
    path: Path,
    b: DiscoveryBudget,
    *,
    csv_options: CsvReadOptions | None = None,
) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix not in {".csv", ".parquet"}:
        raise ValueError("DuckDB file discovery supports .csv and .parquet")
    # Literal escaping prevents path-to-SQL injection, and only trusted table
    # functions run. CSV files are read with the same strict, declared
    # dialect as evaluation; DuckDB's sniffer is never used.
    if suffix == ".parquet":
        rel = f"read_parquet({_lit_sql(str(path))})"
    else:
        options = csv_options or CsvReadOptions.from_mapping({}, path=path)
        rel = duckdb_csv_relation(
            path,
            options,
            read_header(path, options),
            null_sentinel=NEVER_NULL_SENTINEL,
        )
    descriptor = conn.execute("DESCRIBE SELECT * FROM " + rel).fetchall()
    cols = [
        _init_profile(
            str(row[0]), str(row[1]), _logical_from_native(str(row[1]))
        )
        for row in descriptor
    ]
    if not cols or len(set(p["name"] for p in cols)) != len(cols):
        raise ValueError("Invalid table schema")
    selected = cols[: b.max_profile_columns]
    if b.mode == "schema":
        return {
            "backend": "duckdb",
            "rows": None,
            "columns": cols,
            "statistics_scope": "schema_only",
            "sample": [],
            "warnings": [],
        }
    exprs = ["COUNT(*) AS _dq_rows"]
    for i, c in enumerate(selected):
        ident = _q_sql(c["name"])
        exprs.append(
            f"COUNT(*) FILTER (WHERE {ident} IS NULL) AS _dq_null_{i}"
        )
        if c["logical_type"] == "string":
            exprs.append(
                f"COUNT(*) FILTER (WHERE {ident} = '') AS _dq_blank_{i}"
            )
        if "identifier" in c["semantic_roles"]:
            exprs.append(f"approx_count_distinct({ident}) AS _dq_card_{i}")
            if c["logical_type"] == "string":
                exprs.extend(
                    [
                        f"COUNT(*) FILTER (WHERE regexp_full_match({ident}, "
                        f"'[0-9]+')) AS _dq_digits_{i}",
                        f"MIN(LENGTH({ident})) FILTER (WHERE {ident} IS NOT "
                        f"NULL AND {ident} != '') AS _dq_minlen_{i}",
                        f"MAX(LENGTH({ident})) FILTER (WHERE {ident} IS NOT "
                        f"NULL AND {ident} != '') AS _dq_maxlen_{i}",
                    ]
                )
    cursor = conn.execute("SELECT " + ", ".join(exprs) + " FROM " + rel)
    rec = dict(
        zip([d[0] for d in cursor.description], cursor.fetchone(), strict=True)
    )
    for i, c in enumerate(selected):
        c["nulls"] = int(rec[f"_dq_null_{i}"] or 0)
        c["observations_profiled"] = int(rec["_dq_rows"])
        if f"_dq_blank_{i}" in rec:
            c["blanks"] = int(rec[f"_dq_blank_{i}"] or 0)
        if f"_dq_card_{i}" in rec:
            c["approx_distinct"] = int(rec[f"_dq_card_{i}"] or 0)
        if f"_dq_digits_{i}" in rec:
            c.update(
                lexical_digits_only=int(rec[f"_dq_digits_{i}"] or 0),
                lexical_min_length=rec[f"_dq_minlen_{i}"],
                lexical_max_length=rec[f"_dq_maxlen_{i}"],
            )
    warnings = [
        "DuckDB approx_count_distinct is approximate; not proof of uniqueness."
    ]
    if suffix == ".csv":
        warnings.append(
            "CSV imported lexically by DuckDB; no implicit type or date "
            "coercion."
        )
    if len(cols) > len(selected):
        warnings.append("Profiling columns capped by budget.")
    return {
        "backend": "duckdb",
        "rows": int(rec["_dq_rows"]),
        "columns": cols,
        "statistics_scope": "full_aggregate_no_raw_rows",
        "sample": [],
        "warnings": warnings,
    }


def _polars_source(source: Any, b: DiscoveryBudget) -> dict[str, Any]:
    try:
        import polars as pl
    except ImportError as exc:
        raise ImportError(
            "Polars support requires optional `polars` dependency"
        ) from exc
    lf = source
    if isinstance(source, (str, Path)):
        p = Path(source)
        if p.suffix.lower() == ".parquet":
            lf = pl.scan_parquet(str(p))
        elif p.suffix.lower() == ".csv":
            lf = pl.scan_csv(
                str(p), infer_schema_length=0, try_parse_dates=False
            )
        else:
            raise ValueError("Polars file discovery accepts CSV or Parquet")
    if isinstance(lf, pl.DataFrame):
        lf = lf.lazy()
    if not isinstance(lf, pl.LazyFrame):
        raise TypeError("Expected polars LazyFrame/DataFrame or source path")
    schema = lf.collect_schema()
    columns = [
        _init_profile(c, str(t), _logical_from_native(str(t)))
        for c, t in schema.items()
    ]
    if b.mode == "schema":
        return {
            "backend": "polars_lazy",
            "rows": None,
            "columns": columns,
            "statistics_scope": "schema_only",
            "sample": [],
            "warnings": [],
        }
    selected = columns[: b.max_profile_columns]
    exprs = [pl.len().alias("_dq_rows")]
    for i, c in enumerate(selected):
        exprs.append(pl.col(c["name"]).null_count().alias(f"_dq_null_{i}"))
        if c["logical_type"] == "string":
            exprs.append(
                pl.col(c["name"])
                .eq("")
                .fill_null(False)
                .sum()
                .alias(f"_dq_blank_{i}")
            )
        if "identifier" in c["semantic_roles"]:
            exprs.append(
                pl.col(c["name"]).approx_n_unique().alias(f"_dq_card_{i}")
            )
            if c["logical_type"] == "string":
                v = pl.col(c["name"])
                exprs.extend(
                    [
                        (v.str.contains("^[0-9]+$").fill_null(False))
                        .sum()
                        .alias(f"_dq_digits_{i}"),
                        pl.when(v.is_not_null() & (v != ""))
                        .then(v.str.len_chars())
                        .otherwise(None)
                        .min()
                        .alias(f"_dq_minlen_{i}"),
                        pl.when(v.is_not_null() & (v != ""))
                        .then(v.str.len_chars())
                        .otherwise(None)
                        .max()
                        .alias(f"_dq_maxlen_{i}"),
                    ]
                )
    # ``engine="streaming"`` replaced ``streaming=True`` during Polars 1.x;
    # the legacy keyword was removed in Polars 2.
    selection: Any = lf.select(exprs)
    try:
        frame = selection.collect(engine="streaming")
    except TypeError:
        frame = selection.collect(streaming=True)
    result = frame.row(0, named=True)
    for i, c in enumerate(selected):
        c["nulls"] = int(result[f"_dq_null_{i}"] or 0)
        c["observations_profiled"] = int(result["_dq_rows"])
        if f"_dq_blank_{i}" in result:
            c["blanks"] = int(result[f"_dq_blank_{i}"] or 0)
        if f"_dq_card_{i}" in result:
            c["approx_distinct"] = int(result[f"_dq_card_{i}"] or 0)
        if f"_dq_digits_{i}" in result:
            c.update(
                lexical_digits_only=int(result[f"_dq_digits_{i}"] or 0),
                lexical_min_length=result[f"_dq_minlen_{i}"],
                lexical_max_length=result[f"_dq_maxlen_{i}"],
            )
    return {
        "backend": "polars_lazy",
        "rows": int(result["_dq_rows"]),
        "columns": columns,
        "statistics_scope": "lazy_aggregate_no_raw_rows",
        "sample": [],
        "warnings": [
            "Polars approx_n_unique is approximate, not a uniqueness "
            "certificate."
        ],
    }


def _spark_source(df: SparkFrame, b: DiscoveryBudget) -> dict[str, Any]:
    # Spark import is strictly optional, and schema mode schedules NO Spark
    # job.
    if ".connect." in type(df).__module__:
        raise NotImplementedError(
            "Spark Connect is not verified for this adapter"
        )
    if getattr(df, "isStreaming", False):
        raise NotImplementedError(
            "Structured Streaming requires a separate incremental adapter"
        )
    if not (hasattr(df, "schema") and hasattr(df, "columns")):
        raise TypeError("Spark DataFrame with schema required")
    fields = list(df.schema.fields)
    cols = [
        _init_profile(
            f.name,
            f.dataType.simpleString(),
            _logical_from_native(f.dataType.simpleString()),
        )
        for f in fields
    ]
    if len(set(f.name for f in fields)) != len(fields):
        raise ValueError("Duplicate column names")
    if b.mode == "schema":
        return {
            "backend": "spark",
            "rows": None,
            "columns": cols,
            "statistics_scope": "schema_only",
            "sample": [],
            "warnings": ["No Spark action was triggered."],
        }
    from pyspark.sql import functions as F

    selected = cols[: b.max_profile_columns]
    expressions = [F.count(F.lit(1)).alias("_dq_rows")]
    for i, c in enumerate(selected):
        v = F.col("`" + c["name"].replace("`", "``") + "`")
        expressions.append(
            F.sum(F.when(v.isNull(), 1).otherwise(0)).alias(f"_dq_null_{i}")
        )
        if c["logical_type"] == "string":
            expressions.append(
                F.sum(F.when(v == F.lit(""), 1).otherwise(0)).alias(
                    f"_dq_blank_{i}"
                )
            )
        if "identifier" in c["semantic_roles"]:
            expressions.append(
                F.approx_count_distinct(v).alias(f"_dq_card_{i}")
            )
            if c["logical_type"] == "string":
                expressions.extend(
                    [
                        F.sum(
                            F.when(v.rlike("^[0-9]+$"), 1).otherwise(0)
                        ).alias(f"_dq_digits_{i}"),
                        F.min(
                            F.when(v.isNotNull() & (v != ""), F.length(v))
                        ).alias(f"_dq_minlen_{i}"),
                        F.max(
                            F.when(v.isNotNull() & (v != ""), F.length(v))
                        ).alias(f"_dq_maxlen_{i}"),
                    ]
                )
    data = df.agg(*expressions).first().asDict()  # ONE aggregate row to driver
    for i, c in enumerate(selected):
        c["nulls"] = int(data.get(f"_dq_null_{i}") or 0)
        c["observations_profiled"] = int(data["_dq_rows"])
        if f"_dq_blank_{i}" in data:
            c["blanks"] = int(data[f"_dq_blank_{i}"] or 0)
        if f"_dq_card_{i}" in data:
            c["approx_distinct"] = int(data[f"_dq_card_{i}"] or 0)
        if f"_dq_digits_{i}" in data:
            c.update(
                lexical_digits_only=int(data[f"_dq_digits_{i}"] or 0),
                lexical_min_length=data[f"_dq_minlen_{i}"],
                lexical_max_length=data[f"_dq_maxlen_{i}"],
            )
    return {
        "backend": "spark",
        "rows": int(data["_dq_rows"]),
        "columns": cols,
        "statistics_scope": "distributed_aggregate_no_raw_rows",
        "sample": [],
        "warnings": ["Spark approx_count_distinct cannot certify uniqueness."],
    }


def _sample_valid(sample: Iterable[Mapping[str, Any]], col: str) -> list[Any]:
    return [r[col] for r in sample if col in r and r[col] not in (None, "")]


def _single_asset_advice(
    aid: str, asset: Mapping[str, Any], b: DiscoveryBudget
) -> list[Advice]:
    recs = []
    t = b.thresholds
    cols = asset["columns"]
    sample = asset.get("sample", [])
    rows = asset["rows"]
    for meta in cols:
        name = meta["name"]
        roles = meta["semantic_roles"]
        logical = meta["logical_type"]
        counted = meta["observations_profiled"] is not None
        ev = {
            "source": asset["statistics_scope"],
            "observed_rows": meta["observations_profiled"],
            "nulls": meta["nulls"],
            "blanks": meta["blanks"],
        }
        recs.append(
            _advice(
                "rule",
                f"Review whether {name} is required",
                aid,
                [name],
                dimension="completeness",
                evaluator="builtin.presence",
                expectation={"operator": "not_null"},
                confidence="medium" if "identifier" in roles else "low",
                priority="high" if "identifier" in roles else "medium",
                cost="aggregate",
                rationale=(
                    "Completeness is measurable; whether the field is "
                    "required depends on its use."
                ),
                evidence=ev,
                prerequisites=("Confirm that the field is required",),
            )
        )
        if not counted:
            if asset["statistics_scope"] != "schema_only":
                recs.append(
                    _advice(
                        "context",
                        f"Perfilar {name} cuando haya presupuesto",
                        aid,
                        [name],
                        priority="low",
                        cost="aggregate",
                        rationale="Outside the column budget.",
                    )
                )
        if "count" in roles:
            if logical in ("integer", "decimal"):
                recs.append(
                    _advice(
                        "rule",
                        f"Confirm that {name} is non-negative",
                        aid,
                        [name],
                        dimension="validity",
                        evaluator="builtin.value_range",
                        expectation={"operator": "min", "value": 0},
                        confidence="medium",
                        priority="high",
                        cost="aggregate",
                        rationale=(
                            "Name and type suggest a count; adjustments "
                            "may be negative."
                        ),
                        evidence={"physical_type": meta["physical_type"]},
                        caveats=(
                            "Do not apply to balances, differences or signed "
                            "adjustments.",
                        ),
                    )
                )
            else:
                recs.append(
                    _advice(
                        "semantic",
                        f"Declare a numeric type for {name}",
                        aid,
                        [name],
                        priority="high",
                        cost="aggregate",
                        rationale=(
                            "Looks like a count, but the type is lexical or "
                            "unknown."
                        ),
                        prerequisites=(
                            "Define conversion, locale, nulls and validity "
                            "period",
                        ),
                    )
                )
        if "temporal" in roles and logical == "string":
            vals = _sample_valid(sample, name)
            iso = sum(bool(_RE_ISO.fullmatch(v)) for v in vals)
            ambiguous = sum(bool(_RE_AMBIGUOUS.fullmatch(v)) for v in vals)
            recs.append(
                _advice(
                    "semantic",
                    f"Confirm the temporal interpretation of {name}",
                    aid,
                    [name],
                    priority="medium",
                    cost="aggregate",
                    rationale="Nombre temporal, tipo lexical.",
                    evidence={
                        "sample_size": len(vals),
                        "iso_lexical_matches": iso,
                        "ambiguous_date_patterns": ambiguous,
                    },
                    prerequisites=("Declare format, time zone and calendar",),
                    caveats=(
                        "Do not interpret ambiguous dates automatically.",
                    ),
                )
            )
        vals = _sample_valid(sample, name)
        if vals:
            sentinels = sorted(
                {
                    v.strip().upper()
                    for v in vals
                    if v.strip().upper() in _SENTINELS
                }
            )
            if sentinels:
                recs.append(
                    _advice(
                        "missing_policy",
                        f"Review missing-value markers in {name}",
                        aid,
                        [name],
                        expectation={"tokens": sentinels},
                        priority="medium",
                        cost="schema",
                        rationale=(
                            "The sample contains possible missing-value "
                            "markers."
                        ),
                        evidence={
                            "observed_token_types": len(sentinels),
                            "sample_rows": len(vals),
                        },
                        caveats=("Some tokens have a valid meaning.",),
                    )
                )
            if "identifier" in roles and logical == "string":
                strict = [
                    v for v in vals if v.strip().upper() not in _SENTINELS
                ]
                if len(strict) >= t.min_pattern_sample and all(
                    _RE_CODE.fullmatch(v) for v in strict
                ):
                    lens = {len(v) for v in strict}
                    if (
                        len(lens) == 1
                        and 0 < next(iter(lens)) <= t.max_code_width
                    ):
                        w = next(iter(lens))
                        recs.append(
                            _advice(
                                "rule",
                                f"Confirm the code width of {name}",
                                aid,
                                [name],
                                dimension="validity",
                                evaluator="builtin.regex_fullmatch",
                                expectation={
                                    "operator": "regex",
                                    "pattern": f"[0-9]{{{w}}}",
                                },
                                confidence="low",
                                priority="medium",
                                cost="aggregate",
                                rationale=(
                                    "Uniform pattern observed in a bounded "
                                    "sample."
                                ),
                                evidence={
                                    "sample_non_missing": len(strict),
                                    "width": w,
                                    # ~95% upper bound on the share of
                                    # other widths in the population.
                                    "other_width_share_upper_95": (
                                        rule_of_three_upper(len(strict))
                                    ),
                                },
                                caveats=(
                                    "Historical codes may vary in length.",
                                ),
                            )
                        )
            if "category" in roles and len(vals) >= t.min_category_sample:
                unique = set(vals)
                unseen = good_turing_unseen_mass(vals)
                if (
                    1 < len(unique) <= b.max_domain_values
                    and len(unique) / len(vals) <= t.max_category_unique_share
                ):
                    recs.append(
                        _advice(
                            "rule",
                            f"Review the candidate domain of {name}",
                            aid,
                            [name],
                            dimension="validity",
                            evaluator="builtin.allowed_values",
                            expectation={
                                "operator": "in_set",
                                "values": sorted(unique),
                            },
                            confidence="low",
                            priority="medium",
                            cost="external",
                            rationale=(
                                "Domain inferred from a sample; it does not "
                                "prove a closed world."
                            ),
                            evidence={
                                "distinct_in_sample": len(unique),
                                "sample_rows": len(vals),
                                # Good-Turing estimate of the population
                                # share in categories not seen here.
                                "unseen_category_share": unseen,
                            },
                            prerequisites=(
                                "Verify the official catalogue and its "
                                "validity period",
                            ),
                        )
                    )
        if "identifier" in roles and logical == "string" and not sample:
            nonempty = (
                (rows - (meta["nulls"] or 0) - (meta["blanks"] or 0))
                if rows is not None and meta["nulls"] is not None
                else None
            )
            digits = meta.get("lexical_digits_only")
            lo = meta.get("lexical_min_length")
            hi = meta.get("lexical_max_length")
            if (
                nonempty is not None
                and nonempty >= 3
                and digits == nonempty
                and lo == hi
                and lo is not None
                and 0 < lo <= t.max_code_width
            ):
                recs.append(
                    _advice(
                        "rule",
                        f"Confirm the code width of {name}",
                        aid,
                        [name],
                        dimension="validity",
                        evaluator="builtin.regex_fullmatch",
                        expectation={
                            "operator": "regex",
                            "pattern": f"[0-9]{{{lo}}}",
                        },
                        confidence="medium",
                        priority="medium",
                        cost="aggregate",
                        rationale=(
                            "Every non-empty value has the same width and "
                            "only digits."
                        ),
                        evidence={
                            "scope": "full_aggregate",
                            "nonempty": nonempty,
                            "digits": digits,
                            "width": lo,
                        },
                        caveats=(
                            "An observed pattern is not a mandatory standard; "
                            "validate historical changes.",
                        ),
                    )
                )
        if "identifier" in roles:
            exact = asset.get("exact_key_uniqueness", {}).get(name)
            # Distinct counts and sketches exclude blanks, so the
            # denominator must too (audit finding A1); otherwise 5 blanks
            # hide a 1000-value unique key.
            observed_non_null = (
                None
                if rows is None or meta["nulls"] is None
                else rows - meta["nulls"] - (meta.get("blanks") or 0)
            )
            if (
                exact
                and exact["exact_distinct"] is not None
                and observed_non_null is not None
                and observed_non_null >= t.min_exact_key_non_null
            ):
                is_unique = exact["exact_distinct"] == observed_non_null
                if is_unique:
                    recs.append(
                        _advice(
                            "rule",
                            f"Confirm candidate key {name}",
                            aid,
                            [name],
                            dimension="uniqueness",
                            evaluator="builtin.composite_uniqueness",
                            expectation={
                                "operator": "composite_unique",
                                "key_fields": [name],
                            },
                            confidence="medium",
                            priority="high",
                            cost="exact_scan",
                            rationale=(
                                "No duplication among present values in a "
                                "bounded exact scan."
                            ),
                            evidence={
                                "exact_non_null_distinct": exact[
                                    "exact_distinct"
                                ],
                                "non_null": observed_non_null,
                                "exact": True,
                            },
                            caveats=(
                                "Not equivalent to a uniqueness obligation by "
                                "design.",
                            ),
                        )
                    )
            elif (
                meta["approx_distinct"] is not None
                and observed_non_null
                and observed_non_null >= t.min_hll_non_null
            ):
                ratio = meta["approx_distinct"] / observed_non_null
                rse = meta.get("approx_distinct_relative_error") or (
                    1.04 / math.sqrt(1 << b.hll_precision)
                )
                if hll_suggests_unique(
                    meta["approx_distinct"],
                    observed_non_null,
                    rse,
                    z=t.hll_z,
                ):
                    recs.append(
                        _advice(
                            "hypothesis",
                            f"Investigate possible uniqueness of {name}",
                            aid,
                            [name],
                            dimension="uniqueness",
                            cost="exact_scan",
                            priority="high",
                            rationale=(
                                "Estimador aproximado sugiere alta "
                                "cardinalidad; requiere prueba exacta."
                            ),
                            evidence={
                                "approx_distinct": meta["approx_distinct"],
                                "non_null": observed_non_null,
                                "approx_ratio": round(ratio, 3),
                                "approx_relative_error": rse,
                            },
                            prerequisites=(
                                "Exact validation of the proposed key",
                            ),
                        )
                    )
    # Sample-only candidate keys; small, interpretable search to avoid N²
    # scans.
    keyed = [
        c["name"]
        for c in cols
        if "identifier" in c["semantic_roles"]
        or "temporal" in c["semantic_roles"]
    ]
    keyed = keyed[: b.max_candidate_keys]
    if b.mode == "deep" and len(sample) >= 4 and len(keyed) >= 2:
        examined = 0
        for pair in combinations(keyed, 2):
            if examined >= b.max_key_pairs:
                break
            examined += 1
            tuples = [(r.get(pair[0]), r.get(pair[1])) for r in sample]
            valid = [x for x in tuples if all(v not in (None, "") for v in x)]
            if (
                len(valid) < t.min_pair_sample
                or len(valid) / len(tuples) < t.min_pair_coverage
            ):
                continue
            if len(set(valid)) == len(valid) and all(
                len({x[i] for x in valid}) < len(valid) for i in (0, 1)
            ):
                recs.append(
                    _advice(
                        "hypothesis",
                        f"Investigate composite key {pair[0]} + {pair[1]}",
                        aid,
                        list(pair),
                        dimension="uniqueness",
                        cost="exact_scan",
                        priority="high",
                        rationale=(
                            "Candidate not duplicated in the reservoir; no "
                            "global proof."
                        ),
                        evidence={
                            "sample_pairs": len(valid),
                            "sample_unique": len(set(valid)),
                            "sample_based": True,
                            # Duplicate share the reservoir would reveal
                            # with ~95% probability; near 1.0 means the
                            # sample is almost no evidence (audit M1).
                            "min_detectable_duplicate_share": (
                                min_detectable_duplicate_share(
                                    rows, len(sample)
                                )
                            ),
                        },
                        prerequisites=(
                            "Exact distributed validation of the key",
                        ),
                    )
                )
    # Start/end pairs must share the event root (audit finding A3); the
    # previous loop paired every start field with every end field.
    temporal_typed = {
        c["name"]
        for c in cols
        if "temporal" in c["semantic_roles"]
        and c["logical_type"] in {"date", "datetime"}
    }
    for n1, n2 in start_end_pairs(sorted(temporal_typed)):
        recs.append(
            _advice(
                "rule",
                f"Review temporal order {n1} <= {n2}",
                aid,
                [n1, n2],
                dimension="consistency",
                evaluator="builtin.column_compare",
                expectation={
                    "operator": "expression",
                    "extensions": {"comparison": "lte"},
                },
                confidence="low",
                priority="high",
                cost="aggregate",
                rationale=(
                    "Two temporally typed fields with start and end names."
                ),
                prerequisites=("Confirm that they belong to the same event",),
            )
        )
    # Candidate functional dependencies are useful only with repeated
    # determinant values.
    # A unique identifier mapping to arbitrary attributes is trivial and not
    # suggested.
    if b.mode == "deep" and len(sample) >= 8:
        identifiers = [
            c["name"] for c in cols if "identifier" in c["semantic_roles"]
        ]
        names = [
            c["name"]
            for c in cols
            if re.search(
                r"(_nombre|_name|description|descripcion)$", c["name"], re.I
            )
        ]
        checked = 0
        for key in identifiers:
            for target in names:
                if checked >= b.max_key_pairs:
                    break
                if _name_stem(key) != _name_stem(target):
                    continue
                checked += 1
                pairs = [(r.get(key), r.get(target)) for r in sample]
                valid = [
                    (x, y)
                    for x, y in pairs
                    if x not in (None, "") and y not in (None, "")
                ]
                if len(valid) < t.min_fd_sample_pairs:
                    continue
                support = dependency_evidence(valid)
                # One repeated determinant is not evidence (audit M2).
                if (
                    support.consistent
                    and support.repeated_determinants
                    >= t.min_fd_repeated_determinants
                ):
                    recs.append(
                        _advice(
                            "hypothesis",
                            f"Investigate dependency {key} → {target}",
                            aid,
                            [key, target],
                            dimension="consistency",
                            cost="exact_scan",
                            priority="medium",
                            rationale=(
                                "A repeated code keeps the same name in the "
                                "reservoir."
                            ),
                            evidence={
                                "sample_pairs": support.pairs,
                                "distinct_determinants": support.determinants,
                                "repeated_determinants": (
                                    support.repeated_determinants
                                ),
                                "g3": support.g3,
                                "sample_based": True,
                            },
                            prerequisites=(
                                "Verify the catalogue version and the "
                                "dependency at full scale",
                            ),
                        )
                    )
    return recs


def _name_stem(name: str) -> str:
    return re.sub(
        r"(_(?:id|codigo|code|ref|key|nombre|name|descripcion|description))$",
        "",
        name.lower().replace(" ", "_"),
    ).strip("_")


def _relationship_advice(
    assets: Mapping[str, Mapping[str, Any]],
) -> list[Advice]:
    """Name/role affinity only; do not compare raw PII or claim referential
    validity.
    """
    out = []
    entries = list(assets.items())
    for idx, (aid, asset) in enumerate(entries):
        cols = asset["columns"]
        for bid, basset in entries[idx + 1 :]:
            matches = 0
            for col in cols:
                if "identifier" not in col["semantic_roles"]:
                    continue
                for other in basset["columns"]:
                    if "identifier" not in other["semantic_roles"]:
                        continue
                    if col["name"].casefold() == other[
                        "name"
                    ].casefold() or _name_stem(col["name"]) == _name_stem(
                        other["name"]
                    ):
                        out.append(
                            _advice(
                                "relationship",
                                f"Investigate reference {aid}.{col['name']} ↔ "
                                f"{bid}.{other['name']}",
                                aid,
                                [col["name"], other["name"]],
                                dimension="integrity",
                                cost="external",
                                confidence="low",
                                priority="high",
                                rationale=(
                                    "Name compatibility; referential "
                                    "integrity was not verified."
                                ),
                                evidence={
                                    "other_asset": bid,
                                    "other_field": other["name"],
                                    "schema_only": True,
                                },
                                prerequisites=(
                                    "Define parent/child direction, keys, "
                                    "nulls and validity period",
                                    "Join the sources with the corresponding "
                                    "engine",
                                ),
                            )
                        )
                        matches += 1
                        if matches >= _MAX_RELATIONSHIP_HINTS:
                            break
                if matches >= _MAX_RELATIONSHIP_HINTS:
                    break
    return out


def discover_advanced(
    sources: Any,
    *,
    asset_id: str = "dataset",
    engine: str = "auto",
    budget: DiscoveryBudget | None = None,
    csv_delimiter: str = ",",
    csv_encoding: str = "utf-8-sig",
    prior_reviews: Mapping[str, str] | None = None,
    reference_hints: Mapping[str, Mapping[str, str]] | None = None,
    baseline_profiles: Mapping[str, Mapping[str, Mapping[str, Any]]]
    | None = None,
) -> AdvancedDiscoveryReport:
    """Return conservative hypotheses using bounded memory and engine-native
    aggregates.

    Sources: CSV path (stdlib streaming), DuckDB Parquet/CSV path (optional),
    Polars LazyFrame/DataFrame (optional), or classic PySpark DataFrame
    (optional).
    Mapping[asset_id, source] enables cross-asset *structural* hints.

    Important: no inferred rule is automatically applied or certified.
    """
    b = budget or DiscoveryBudget()
    if len(csv_delimiter) != 1:
        raise ValueError("CSV delimiter must be a single character")
    # An explicit delimiter wins; otherwise .tsv files default to tab.
    declared_csv: dict[str, Any] = {"encoding": csv_encoding}
    if csv_delimiter != ",":
        declared_csv["delimiter"] = csv_delimiter
    sources_map = (
        dict(sources) if isinstance(sources, Mapping) else {asset_id: sources}
    )
    if not sources_map:
        raise ValueError("At least one source required")
    if any(
        not isinstance(a, str) or not _ID_VALID.fullmatch(a)
        for a in sources_map
    ):
        raise ValueError("Asset IDs must match [A-Za-z][A-Za-z0-9_.-]*")
    public_assets = {}
    private_assets = {}
    warnings: list[str] = []
    recommendations = []
    for aid, source in sources_map.items():
        selected_engine = engine
        if selected_engine == "auto":
            if isinstance(source, (str, Path)):
                selected_engine = (
                    "csv_stream" if _is_csv_path(Path(source)) else "duckdb"
                )
            elif type(source).__module__.startswith("pyspark."):
                selected_engine = "spark"
            elif type(source).__module__.startswith("polars."):
                selected_engine = "polars"
            else:
                raise TypeError(
                    "Unsupported source: use CSV path, Parquet path, Spark "
                    "DataFrame or Polars LazyFrame"
                )
        if selected_engine == "csv_stream":
            if not isinstance(source, (str, Path)) or not _is_csv_path(
                Path(source)
            ):
                raise ValueError("csv_stream requires a CSV path")
            csv_options = CsvReadOptions.from_mapping(
                declared_csv, path=source
            )
            with field_size_limit(csv_options.max_field_chars):
                raw = _read_csv_stream(
                    Path(source),
                    b,
                    delimiter=csv_options.delimiter,
                    encoding=csv_options.encoding,
                    quote_char=csv_options.quote_char,
                )
            raw["read_options"] = csv_options.to_contract()
        elif selected_engine == "duckdb":
            if not isinstance(source, (str, Path)):
                raise TypeError("DuckDB adapter requires a file path")
            duck_options = (
                CsvReadOptions.from_mapping(declared_csv, path=source)
                if is_csv_path(source)
                else None
            )
            raw = _duckdb_source(Path(source), b, csv_options=duck_options)
            if duck_options is not None:
                raw["read_options"] = duck_options.to_contract()
        elif selected_engine == "polars":
            raw = _polars_source(source, b)
        elif selected_engine == "spark":
            raw = _spark_source(source, b)
        else:
            raise ValueError(
                "engine must be auto, csv_stream, duckdb, polars or spark"
            )
        private_assets[aid] = raw
        public_assets[aid] = {
            k: v
            for k, v in raw.items()
            if k not in ("sample", "exact_key_uniqueness")
        }
        recommendations.extend(_single_asset_advice(aid, raw, b))
        warnings.extend(f"{aid}: {w}" for w in raw.get("warnings", []))
    if len(private_assets) > 1:
        recommendations.extend(_relationship_advice(private_assets))
    # External reference suggestions are based on user-provided *metadata*
    # only,
    # never inferred lookup content or blindly trusted catalog coverage.
    for signature, hint in (reference_hints or {}).items():
        a, dot, column = signature.partition(".")
        if (
            not dot
            or a not in public_assets
            or column not in {f["name"] for f in public_assets[a]["columns"]}
        ):
            raise ValueError(
                f"Reference hint targets unknown asset.field: {signature}"
            )
        ref_id = hint.get("reference_id")
        if not isinstance(ref_id, str) or not ref_id.strip():
            raise ValueError("reference_hints require nonblank reference_id")
        recommendations.append(
            _advice(
                "reference",
                f"Confirm the mapping of {signature} in {ref_id}",
                a,
                [column],
                dimension="validity",
                evaluator="reference.lookup_temporal",
                confidence="medium",
                priority="high",
                cost="external",
                rationale=(
                    "A user-declared catalogue exists but has not been "
                    "verified yet."
                ),
                evidence={
                    "reference_id": ref_id,
                    "version": hint.get("version"),
                    "valid_from": hint.get("valid_from"),
                    "valid_to": hint.get("valid_to"),
                },
                prerequisites=(
                    "Vincular referencia verificada",
                    "Define direction, period and policy for codes not found",
                ),
            )
        )
    # Drift is only a proposed investigation, never a validity finding.
    for aid, historical in (baseline_profiles or {}).items():
        if aid not in public_assets:
            raise ValueError(f"Unknown baseline asset: {aid}")
        for c in public_assets[aid]["columns"]:
            previous = historical.get(c["name"], {})
            old_n: Any = previous.get("observations_profiled")
            old_null: Any = previous.get("nulls")
            n: Any = c["observations_profiled"]
            null: Any = c["nulls"]
            if (
                all(
                    isinstance(x, int) and not isinstance(x, bool)
                    for x in (n, null, old_n, old_null)
                )
                and n > 0
                and old_n > 0
            ):
                old_rate = old_null / old_n
                rate = null / n
                if abs(rate - old_rate) >= 0.10:
                    recommendations.append(
                        _advice(
                            "hypothesis",
                            f"Investigate change in missing values in "
                            f"{aid}.{c['name']}",
                            aid,
                            [c["name"]],
                            dimension="reliability",
                            priority="medium",
                            cost="aggregate",
                            rationale=(
                                "Absolute deviation of 10 points or more from "
                                "the baseline."
                            ),
                            evidence={
                                "null_rate_now": round(rate, 6),
                                "null_rate_baseline": round(old_rate, 6),
                                "difference_pp": round(
                                    (rate - old_rate) * 100, 3
                                ),
                            },
                            prerequisites=(
                                "Confirm equivalence of populations, period "
                                "and null policy",
                            ),
                            caveats=(
                                "The change may be legitimate and does not "
                                "prove an error.",
                            ),
                        )
                    )
    # IDs are stable for a given schema/candidate; no approval is implicit.
    dedup = {r.id: r for r in recommendations}
    if prior_reviews:
        from dataclasses import replace

        for rid, status in prior_reviews.items():
            if status not in {"approved", "rejected"}:
                raise ValueError(
                    "prior_reviews states must be approved or rejected"
                )
            if rid in dedup:
                old = dedup[rid]
                dedup[rid] = replace(
                    old,
                    evidence={**old.evidence, "previous_review": status},
                    priority="high" if status == "approved" else "low",
                    caveats=old.caveats
                    + ("Prior review is informative, never auto-approval.",),
                )
    warnings.extend(
        [
            "Heuristic confidence levels are NOT calibrated probabilities.",
            "Sample patterns, approximate cardinality, and name similarity "
            "cannot certify data quality.",
            "Review manifests are not executable contracts until source types "
            "and backend are bound.",
        ]
    )
    return AdvancedDiscoveryReport(
        public_assets, tuple(dedup.values()), tuple(warnings), b
    )
