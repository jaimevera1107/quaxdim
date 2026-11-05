"""Advisory-only, privacy-respecting data discovery and recommendation engine.

Empirical patterns are *hypotheses*, never approved business obligations.
No user data or raw examples are emitted by the default report serializer.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ._lazy import LazyModule

if TYPE_CHECKING:
    import numpy as np
    import pandas as pd
else:
    np = LazyModule("numpy", extra="pandas")
    pd = LazyModule("pandas", extra="pandas")

from .evaluator_registry import REGISTRY

_IDENTIFIER = re.compile(
    r"(^id$|(^|[_ -])(id|uuid|guid|code|codigo|cod|sku|zip|postal)"
    r"([_ -]|$)|_id$)",
    re.I,
)
_COUNT = re.compile(
    r"(^|[_ -])(count|cantidad|total|numero|número|num|qty|quantity|"
    r"vacantes|inscritos|colocaciones|poblacion|population)([_ -]|$)",
    re.I,
)
_BEGIN = re.compile(r"(start|begin|inicio|desde)", re.I)
_END = re.compile(r"(end|finish|fin|hasta)", re.I)
_DATE_AMBIGUOUS = re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$")
_SENTINELS = frozenset(
    {
        "NA",
        "N/A",
        "NULL",
        "NONE",
        "ND",
        "S/D",
        "SIN DATO",
        "DESCONOCIDO",
        "UNKNOWN",
        "?",
        "#N/A",
    }
)
_DATE_ISO = re.compile(
    r"^\d{4}-\d{2}-\d{2}"
    r"(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$"
)


def _key(what: str, fields: tuple[str, ...]) -> str:
    digest = sha256(
        (what + "\x00" + "\x00".join(fields)).encode("utf-8")
    ).hexdigest()[:12]
    return "suggest_" + digest


def _logical(s: pd.Series) -> tuple[str, str]:
    """Infer the logical type from the physical one.

    Lexical strings are never parsed implicitly.
    """
    dtype = s.dtype
    if pd.api.types.is_bool_dtype(dtype):
        return "boolean", "bool"
    if pd.api.types.is_integer_dtype(dtype):
        return "integer", str(dtype)
    if pd.api.types.is_float_dtype(dtype):
        return "float", str(dtype)
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "datetime", str(dtype)
    if pd.api.types.is_timedelta64_dtype(dtype):
        return "duration", str(dtype)
    if isinstance(dtype, pd.CategoricalDtype):
        return "categorical", str(dtype)
    if pd.api.types.is_string_dtype(
        dtype
    ) and not pd.api.types.is_object_dtype(dtype):
        return "string", str(dtype)
    if pd.api.types.is_object_dtype(dtype):
        sample = s.dropna().head(2000)
        kinds = {type(v) for v in sample}
        if not kinds:
            return "unknown", "object"
        if kinds <= {str}:
            return "string", "object"
        if kinds <= {datetime, date, pd.Timestamp}:
            return "datetime", "object"
        if kinds <= {bool, np.bool_}:
            return "boolean", "object"
        if all(
            issubclass(t, (int, np.integer))
            and not issubclass(t, (bool, np.bool_))
            for t in kinds
        ):
            return "integer", "object"
        if all(
            issubclass(t, (float, np.floating, int, np.integer))
            and not issubclass(t, (bool, np.bool_))
            for t in kinds
        ):
            return "decimal", "object"
        return "variant", "object"
    return "unknown", str(dtype)


@dataclass(frozen=True)
class Recommendation:
    id: str
    fields: tuple[str, ...]
    kind: str  # rule | context | semantic | missing_policy
    title: str
    dimension: str | None
    evaluator: str | None
    target_level: str | None
    expectation: dict[str, Any] | None
    confidence: str  # heuristic, not calibrated probability
    rationale: str
    evidence: dict[str, Any]
    caveats: tuple[str, ...] = ()
    supported_backends: tuple[str, ...] = ()

    def to_dict(
        self, *, include_configuration: bool = False
    ) -> dict[str, Any]:
        out = {
            "id": self.id,
            "fields": list(self.fields),
            "kind": self.kind,
            "title": self.title,
            "dimension": self.dimension,
            "evaluator": self.evaluator,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "evidence": self.evidence,
            "caveats": list(self.caveats),
            "supported_backends": list(self.supported_backends),
            "approval_required": True,
        }
        if include_configuration:
            # May contain raw category values; disclose only on explicit
            # request.
            out["expectation"] = self.expectation
        return out


@dataclass(frozen=True)
class DiscoveryReport:
    asset_id: str
    backend: str
    row_count: int | None
    profile: tuple[dict[str, Any], ...]
    recommendations: tuple[Recommendation, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(
        self, *, include_configuration: bool = False
    ) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "backend": self.backend,
            "rows": self.row_count,
            "profile": list(self.profile),
            "warnings": list(self.warnings),
            "recommendations": [
                r.to_dict(include_configuration=include_configuration)
                for r in self.recommendations
            ],
        }

    def to_json(
        self,
        path: str | Path | None = None,
        *,
        include_configuration: bool = False,
    ) -> str:
        result = json.dumps(
            self.to_dict(include_configuration=include_configuration),
            ensure_ascii=False,
            indent=2,
            default=str,
            allow_nan=False,
        )
        if path is not None:
            from pathlib import Path

            Path(path).write_text(result + "\n", encoding="utf-8")
        return result

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "id": x.id,
                    "fields": ", ".join(x.fields),
                    "kind": x.kind,
                    "dimension": x.dimension,
                    "evaluator": x.evaluator,
                    "confidence": x.confidence,
                    "suggestion": x.title,
                }
                for x in self.recommendations
            ]
        )

    def to_contract(
        self,
        *,
        approve: list[str] | tuple[str, ...],
        purpose: str,
        thresholds: Mapping[str, float] | None = None,
    ) -> dict[str, Any]:
        """Translate *only explicitly approved* suggestions into contract v0.2.

        Approval confirms a proposed rule AND its hypothesized applicability;
        user
        should review any category values via include_configuration=True
        locally.
        """
        if not purpose or not purpose.strip():
            raise ValueError("A nonempty intended purpose is required")
        byid = {r.id: r for r in self.recommendations}
        if len(approve) != len(set(approve)) or any(
            rid not in byid for rid in approve
        ):
            raise ValueError("Unknown or repeated recommendation ID")
        selected = [byid[rid] for rid in approve]
        if not any(r.kind == "rule" for r in selected) or any(
            r.kind not in ("rule", "missing_policy") for r in selected
        ):
            raise ValueError(
                "Approve at least one executable rule; semantic hypotheses "
                "need separate review"
            )
        if self.backend != "pandas":
            raise ValueError(
                "Spark schema-only discovery cannot create verified field "
                "contracts"
            )
        dimensions: dict[str, dict[str, Any]] = {}
        metrics: dict[str, dict[str, Any]] = {}
        rules = []
        for s in selected:
            if s.kind == "missing_policy":
                continue
            if "pandas" not in s.supported_backends:
                raise ValueError("Suggested evaluator not available on pandas")
            if s.dimension is None:
                raise ValueError(
                    f"Selected recommendation has no dimension: {s.id}"
                )
            dim = s.dimension
            dimensions[dim] = {
                "id": dim,
                "name": dim,
                "framework": "mahanti-compatible",
                "version": "1.0.0",
                "levels": [s.target_level],
                "measurement_modes": ["contextual"],
            }
            mid = "metric_" + s.id
            metrics[mid] = {
                "id": mid,
                "dimension_ids": [dim],
                "version": "1.0.0",
                "kind": "conformance",
                "target_levels": [s.target_level],
                "evaluator": s.evaluator,
                "unit": "ratio",
                "direction": "higher_is_better",
                "denominator_unit": "record"
                if s.target_level in ("record", "entity", "table")
                else "value",
                "null_policy": "include_as_failure"
                if s.evaluator == "builtin.presence"
                else "indeterminate",
                "unknown_policy": "indeterminate",
                "evidence_mode": "contextual",
                "aggregation": "micro",
            }
            lim = (thresholds or {}).get(s.id, 1.0)
            if (
                isinstance(lim, bool)
                or not isinstance(lim, (float, int))
                or not 0 <= lim <= 1
            ):
                raise ValueError("Each threshold must be within [0, 1]")
            rules.append(
                {
                    "id": "rule_" + s.id,
                    "version": "1.0.0",
                    "asset_id": self.asset_id,
                    "metric_id": mid,
                    "description": s.title,
                    "target": {
                        "level": s.target_level,
                        "fields": list(s.fields),
                    },
                    "expectation": dict(s.expectation or {}),
                    "threshold": {"operator": "gte", "value": float(lim)},
                    "severity": "medium",
                    "failure_action": "report",
                    "extensions": {
                        "reviewed_from": s.id,
                        "reviewed_by_user": True,
                    },
                }
            )
        declarations = []
        policy_rules: dict[str, dict[str, Any]] = {}
        for x in selected:
            if x.kind != "missing_policy":
                continue
            policy = policy_rules.setdefault(x.fields[0], {})
            policy["tokens"] = sorted(
                set(policy.get("tokens", []))
                | set((x.expectation or {}).get("tokens", []))
            )
            if (x.expectation or {}).get("empty_string"):
                policy["empty_string"] = True
            if (x.expectation or {}).get("whitespace_only"):
                policy["whitespace_only"] = True
        for f in self.profile:
            definition = {
                "name": f["name"],
                "physical_type": f["physical_type"],
                "logical_type": f["logical_type"],
                "nullable": True,
            }
            if f["name"] in policy_rules:
                policy = policy_rules[f["name"]]
                definition["missing_policy"] = {
                    "tokens": policy.get("tokens", []),
                    "normalization": "trim_casefold",
                    "empty_string": policy.get("empty_string", False),
                    "whitespace_only": policy.get("whitespace_only", False),
                }
            if (
                f["logical_type"] == "datetime"
                and "datetime" in f["physical_type"]
            ):
                # Datetime data already have physical timestamp dtype; no
                # strings parsed.
                pass
            declarations.append(definition)
        contract = {
            "contract_version": "0.2.0",
            "assets": [
                {
                    "id": self.asset_id,
                    "name": self.asset_id,
                    "format": "dataframe",
                    "shape": "table",
                    "fields": declarations,
                }
            ],
            "dimensions": list(dimensions.values()),
            "metrics": list(metrics.values()),
            "rules": rules,
            "references": [],
            "evaluation": {
                "id": "eval_discovered",
                "purpose": purpose,
                "mode": "evaluate",
                "preserve_original": True,
                "accuracy_mode": "exact",
                "ambiguous_parse": "indeterminate",
                "data_disclosure": "no_raw_values",
            },
        }
        from .applicability import ApplicabilityPlanner

        ApplicabilityPlanner().plan(
            contract
        )  # schema validation; actual runtime preflight is separate
        return contract


def discover(
    data: Any,
    *,
    asset_id: str = "dataset",
    sample_rows: int = 10_000,
    seed: int = 42,
    max_categories: int = 12,
    spark_mode: str = "schema",
) -> DiscoveryReport:
    """Bounded evidence-based advisor. Never runs suggested rules or coerces
    data.

    Pandas: deterministic random sample for patterns; full column counts for
    missing/keys.
    Classic Spark: schema-only metadata, no collect and no Spark action by
    default.
    """
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", asset_id):
        raise ValueError("asset_id must match the contract ID convention")
    if sample_rows < 1 or max_categories < 2:
        raise ValueError("sample_rows>=1 and max_categories>=2 are required")
    if spark_mode not in {"schema", "aggregate"}:
        raise ValueError("spark_mode must be schema or aggregate")
    if not isinstance(data, pd.DataFrame):
        if type(data).__module__.startswith("pyspark."):
            return _discover_spark_schema(
                data, asset_id, scan=(spark_mode == "aggregate")
            )
        raise TypeError(
            "discover currently supports pandas and classic Spark DataFrames"
        )
    if data.columns.duplicated().any() or any(
        not isinstance(c, str) or not c for c in data.columns
    ):
        raise ValueError("Column names must be unique, nonempty strings")
    n = len(data)
    sampled = (
        data.sample(n=min(n, sample_rows), random_state=seed) if n else data
    )
    rows: list[dict[str, Any]] = []
    proposals: list[Recommendation] = []
    warnings = [
        "Heuristic confidence is not a calibrated probability; no rule is "
        "enabled without approval.",
        "Sample-derived domains/patterns may be incomplete or historically "
        "unstable.",
    ]
    logicals = {}
    for c in data.columns:
        source = data[c]
        sample = sampled[c]
        logical, physical = _logical(source)
        logicals[c] = logical
        nn = int(source.notna().sum())
        count_null = n - nn
        row = {
            "name": c,
            "physical_type": physical,
            "logical_type": logical,
            "non_null": nn,
            "intrinsic_nulls": count_null,
            "sample_count": len(sample),
            "sample_non_null": int(sample.notna().sum()),
        }
        rows.append(row)
        id_like = bool(_IDENTIFIER.search(c))
        const = "sample_only" if len(sample) < n else "all_rows"

        def suggest(
            kind: str,
            title: str,
            dimension: str | None = None,
            evaluator: str | None = None,
            level: str | None = None,
            expect: dict[str, Any] | None = None,
            conf: str = "low",
            rationale: str = "",
            ev: dict[str, Any] | None = None,
            caveats: Iterable[str] = (),
            column: str = c,
        ) -> None:
            fields = (column,)
            prop_id = _key((evaluator or kind) + ":" + title, fields)
            proposal = Recommendation(
                prop_id,
                fields,
                kind,
                title,
                dimension,
                evaluator,
                level,
                expect,
                conf,
                rationale,
                ev or {},
                tuple(caveats),
                tuple(sorted(REGISTRY[evaluator].backends))
                if evaluator
                else (),
            )
            proposals.append(proposal)

        # Presence is universally measurable, but never assumed mandatory.
        suggest(
            "rule",
            "Comprobar completitud de " + c,
            "completeness",
            "builtin.presence",
            "value",
            {"operator": "not_null"},
            "medium" if id_like else "low",
            "Ausencias físicas observadas; obligatoriedad no inferida.",
            {"null_count": count_null, "rows": n},
            (
                "Aprobar únicamente si el campo es obligatorio para el uso "
                "previsto.",
            ),
        )
        if logical == "variant":
            suggest(
                "context",
                "Declarar variantes/tipos mixtos de " + c,
                rationale=(
                    "Los tipos físicos heterogéneos pueden ser legítimos "
                    "según período o fuente."
                ),
                ev={"sample_count": len(sample)},
                caveats=("No convertir automáticamente.",),
            )
        if logical == "unknown":
            suggest(
                "context",
                "Definir tipo lógico de " + c,
                rationale=(
                    "La columna no aporta observaciones tipables o usa un "
                    "tipo no reconocido."
                ),
                ev={"rows": n},
            )
        if logical == "string":
            obs = sample.dropna()
            strs = obs.loc[obs.map(lambda v: isinstance(v, str))]
            if len(strs) > 0:
                empty = int(strs.map(lambda v: v.strip() == "").sum())
                if empty:
                    suggest(
                        "missing_policy",
                        "Confirmar blancos/espacios como ausencia en " + c,
                        expect={"empty_string": True, "whitespace_only": True},
                        rationale=(
                            "Hay cadenas vacías o de espacios; pueden ser "
                            "valores permitidos."
                        ),
                        ev={"sample_blanks": empty, "sample_rows": len(strs)},
                        caveats=(
                            "No marcar como ausentes sin conocer el contrato "
                            "semántico.",
                        ),
                    )
                candidate_tokens = sorted(
                    {
                        v.strip().upper()
                        for v in strs
                        if v.strip().upper() in _SENTINELS
                    }
                )
                if candidate_tokens:
                    suggest(
                        "missing_policy",
                        "Revisar marcadores de ausencia en " + c,
                        expect={"tokens": candidate_tokens},
                        rationale=(
                            "Se observaron expresiones que podrían codificar "
                            "ausencias."
                        ),
                        ev={
                            "candidate_token_count": len(candidate_tokens),
                            "sample_rows": len(strs),
                        },
                        caveats=(
                            "NA y otros códigos pueden tener significado "
                            "válido; confirmar primero.",
                        ),
                    )
                iso_count = int(
                    strs.map(lambda v: bool(_DATE_ISO.fullmatch(v))).sum()
                )
                ambiguous_count = int(
                    strs.map(
                        lambda v: bool(_DATE_AMBIGUOUS.fullmatch(v))
                    ).sum()
                )
                if ambiguous_count:
                    suggest(
                        "semantic",
                        "Confirmar formato regional de fechas en " + c,
                        rationale=(
                            "Formato DD/MM o MM/DD potencialmente ambiguo; no "
                            "se ha interpretado."
                        ),
                        ev={"ambiguous_lexical_dates": ambiguous_count},
                        caveats=(
                            "Requiere patrón, calendario y zona horaria "
                            "explícitos.",
                        ),
                    )
                elif iso_count == len(strs) and len(strs) >= 3:
                    suggest(
                        "semantic",
                        "Confirmar fecha ISO-8601 en " + c,
                        rationale=(
                            "Los valores de la muestra cumplen una forma "
                            "léxica ISO."
                        ),
                        conf="high",
                        ev={
                            "sample_matches": iso_count,
                            "sample_rows": len(strs),
                        },
                        caveats=(
                            "La apariencia léxica no garantiza fecha "
                            "calendario válida.",
                        ),
                    )
                if id_like:
                    widths = strs.map(len)
                    digit_only = strs.map(
                        lambda v: v.isascii() and v.isdecimal()
                    )
                    if (
                        len(strs) >= 3
                        and digit_only.all()
                        and widths.nunique() == 1
                        and 1 <= int(widths.iloc[0]) <= 30
                    ):
                        width = int(widths.iloc[0])
                        suggest(
                            "rule",
                            f"Confirmar patrón de código de {c}",
                            "validity",
                            "builtin.regex_fullmatch",
                            "value",
                            {
                                "operator": "regex",
                                "pattern": "[0-9]{" + str(width) + "}",
                            },
                            "medium",
                            "La muestra de códigos tiene longitud y "
                            "caracteres uniformes.",
                            {
                                "sample_rows": len(strs),
                                "width": width,
                                "scope": const,
                            },
                            (
                                "Los catálogos históricos o nuevos pueden "
                                "usar longitudes diferentes.",
                            ),
                        )
                if not id_like and len(strs) >= 12:
                    try:
                        uniques = list(strs.unique())
                    except TypeError:
                        uniques = []
                    if (
                        1 < len(uniques) <= max_categories
                        and len(uniques) / len(strs) <= 0.25
                    ):
                        suggest(
                            "rule",
                            f"Confirmar dominio cerrado de {c}",
                            "validity",
                            "builtin.allowed_values",
                            "value",
                            {"operator": "in_set", "values": uniques},
                            "low",
                            "Se observaron pocas categorías en la muestra.",
                            {
                                "sample_distinct": len(uniques),
                                "sample_rows": len(strs),
                                "scope": const,
                            },
                            (
                                "Una categoría no vista puede ser válida; "
                                "revisar antes de aprobar.",
                            ),
                        )
        if logical in {"integer", "float", "decimal"} and _COUNT.search(c):
            suggest(
                "rule",
                f"Confirmar valores no negativos de {c}",
                "validity",
                "builtin.value_range",
                "value",
                {"operator": "min", "value": 0},
                "medium",
                "El nombre sugiere una variable de conteo no negativa.",
                {"rows": n},
                (
                    "No aplica a diferencias, saldos, ajustes ni variables "
                    "firmadas.",
                ),
            )
        if (
            id_like
            and logical in {"string", "integer", "categorical"}
            and nn >= 3
        ):
            try:
                cardinality = int(source.dropna().nunique())
            except TypeError:
                cardinality = -1
            if cardinality == nn:
                suggest(
                    "rule",
                    f"Confirmar identificador único {c}",
                    "uniqueness",
                    "builtin.composite_uniqueness",
                    "table",
                    {"operator": "composite_unique", "key_fields": [c]},
                    "medium",
                    "La columna no tiene duplicados observados entre valores "
                    "presentes.",
                    {"non_null_rows": nn, "distinct_non_null": cardinality},
                    (
                        "La ausencia de duplicados no demuestra que la clave "
                        "sea única por diseño.",
                    ),
                )
    # native datetime pairs: start/inicio to end/fin with compatible names
    date_columns = [c for c, t in logicals.items() if t == "datetime"]
    begins = [c for c in date_columns if _BEGIN.search(c)]
    ends = [c for c in date_columns if _END.search(c)]
    for a in begins:
        for b in ends:
            if a == b:
                continue
            fa = re.sub(r"(?i)(start|begin|inicio|desde)", "", a).strip("_ -")
            fb = re.sub(r"(?i)(end|finish|fin|hasta)", "", b).strip("_ -")
            if fa != fb:
                continue
            ev = "builtin.column_compare"
            proposals.append(
                Recommendation(
                    _key(ev, (a, b)),
                    (a, b),
                    "rule",
                    f"Confirmar {a} <= {b}",
                    "consistency",
                    ev,
                    "record",
                    {
                        "operator": "expression",
                        "extensions": {"comparison": "lte"},
                    },
                    "medium",
                    "Los nombres sugieren inicio y fin del mismo evento.",
                    {"rows": n},
                    (
                        "Pueden existir registros excepcionales o cambios de "
                        "significado.",
                    ),
                    tuple(sorted(REGISTRY[ev].backends)),
                )
            )
    return DiscoveryReport(
        asset_id, "pandas", n, tuple(rows), tuple(proposals), tuple(warnings)
    )


def _discover_spark_schema(
    df: Any, aid: str, *, scan: bool = False
) -> DiscoveryReport:
    # No collect/count/toPandas, no execution triggered on the cluster.
    if not hasattr(df, "schema") or not hasattr(df, "columns"):
        raise TypeError("A classic Spark DataFrame with schema is required")
    names = list(df.columns)
    if len(names) != len(set(names)):
        raise ValueError("Duplicate Spark column names")
    profiles = []
    suggestions = []
    for fld in df.schema.fields:
        stype = fld.dataType.simpleString()
        logical = (
            "integer"
            if stype in {"byte", "short", "int", "bigint", "long"}
            else "float"
            if stype in {"float", "double"} or stype.startswith("decimal")
            else "string"
            if stype == "string"
            else "date"
            if stype == "date"
            else "datetime"
            if stype.startswith("timestamp")
            else "boolean"
            if stype == "boolean"
            else "variant"
        )
        profiles.append(
            {
                "name": fld.name,
                "physical_type": stype,
                "logical_type": logical,
                "nullable_by_schema": bool(fld.nullable),
                "non_null": None,
                "intrinsic_nulls": None,
            }
        )
        evaluator = "builtin.presence"
        suggestions.append(
            Recommendation(
                _key(evaluator, (fld.name,)),
                (fld.name,),
                "rule",
                "Revisar completitud de " + fld.name,
                "completeness",
                evaluator,
                "value",
                {"operator": "not_null"},
                "low",
                "El esquema no permite conocer la tasa real de ausencias.",
                {"schema_nullable": bool(fld.nullable)},
                (
                    "Requiere perfilamiento distribuido y revisión de "
                    "obligatoriedad.",
                ),
                tuple(sorted(REGISTRY[evaluator].backends)),
            )
        )
    warnings = [
        "Spark schema discovery does not certify runtime values, keys or "
        "domains.",
        "No raw records are collected. Approximate distinct counts are "
        "descriptive, not proof of uniqueness.",
    ]
    row_count = None
    if scan:
        # One bounded-width distributed aggregation; only aggregate statistics
        # go to driver.
        from pyspark.sql import functions as F

        def col(n: str) -> Any:
            return F.col("`" + n.replace("`", "``") + "`")

        expressions = [F.count(F.lit(1)).alias("_dq_rows")]
        for i, meta in enumerate(profiles):
            x = col(meta["name"])
            expressions.append(
                F.sum(F.when(x.isNull(), 1).otherwise(0)).alias(
                    f"_dq_null_{i}"
                )
            )
            if meta["logical_type"] in {
                "string",
                "integer",
                "float",
                "boolean",
                "date",
            }:
                expressions.append(
                    F.approx_count_distinct(x).alias(f"_dq_distinct_{i}")
                )
        values = (
            df.agg(*expressions).first().asDict()
        )  # one aggregate row; no raw records
        row_count = int(values["_dq_rows"])
        for i, meta in enumerate(profiles):
            meta["intrinsic_nulls"] = int(values[f"_dq_null_{i}"] or 0)
            meta["non_null"] = row_count - meta["intrinsic_nulls"]
            if f"_dq_distinct_{i}" in values:
                meta["approx_distinct"] = int(values[f"_dq_distinct_{i}"] or 0)
        warnings.append(
            "Spark aggregate mode launched a distributed job; "
            "approx_count_distinct is approximate."
        )
    else:
        warnings.append(
            "Spark schema mode launched no jobs (no count/collect)."
        )
    return DiscoveryReport(
        aid,
        "spark",
        row_count,
        tuple(profiles),
        tuple(suggestions),
        tuple(warnings),
    )
