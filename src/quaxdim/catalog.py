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
    name_es: str
    family: str
    approach: str  # objective, subjective, mixed, context-dependent
    standard: str
    evidence_hint: str


# (id, Spanish label, family, approach, initial evidence requirement)
_MAHANTI = [
    (
        "accessibility",
        "Accesibilidad",
        "access",
        "subjective",
        "Pruebas de acceso/encuestas",
    ),
    (
        "accuracy",
        "Exactitud",
        "intrinsic",
        "objective",
        "Fuente de verdad contrastable",
    ),
    (
        "believability",
        "Verosimilitud",
        "trust",
        "subjective",
        "Juicio de consumidores de datos",
    ),
    (
        "credibility",
        "Credibilidad",
        "trust",
        "subjective",
        "Evaluación de la fuente",
    ),
    (
        "trustworthiness",
        "Confiabilidad de la fuente",
        "trust",
        "subjective",
        "Procedencia y evaluación de fuente",
    ),
    (
        "timeliness",
        "Oportunidad",
        "temporal",
        "objective",
        "Fechas de evento/recepción y plazo",
    ),
    (
        "currency",
        "Actualidad",
        "temporal",
        "objective",
        "Fecha de actualización y horizonte esperado",
    ),
    (
        "volatility",
        "Volatilidad",
        "temporal",
        "objective",
        "Series históricas/cambios por período",
    ),
    (
        "correctness",
        "Corrección",
        "intrinsic",
        "objective",
        "Reglas que permitan determinar errores",
    ),
    (
        "precision",
        "Precisión",
        "intrinsic",
        "objective",
        "Unidad, resolución y tolerancia esperada",
    ),
    (
        "reliability",
        "Fiabilidad",
        "trust",
        "mixed",
        "Evidencia de uso más pruebas de estabilidad",
    ),
    (
        "consistency",
        "Consistencia",
        "integrity",
        "objective",
        "Reglas entre campos/fuentes",
    ),
    (
        "integrity",
        "Integridad relacional",
        "integrity",
        "objective",
        "Claves y tablas de referencia",
    ),
    (
        "completeness",
        "Completitud",
        "intrinsic",
        "objective",
        "Campos/entidades esperados",
    ),
    (
        "validity",
        "Validez o conformidad",
        "intrinsic",
        "objective",
        "Dominio, patrón y contrato de tipos",
    ),
    (
        "interpretability",
        "Interpretabilidad",
        "usability",
        "subjective",
        "Evaluación de usuarios y glosario",
    ),
    (
        "security",
        "Seguridad",
        "access",
        "objective",
        "Pruebas de permisos y controles",
    ),
    (
        "conciseness",
        "Concisión",
        "usability",
        "subjective",
        "Evaluación de representación",
    ),
    (
        "reputation",
        "Reputación",
        "trust",
        "subjective",
        "Encuesta/valoración de fuentes",
    ),
    (
        "uniqueness",
        "Unicidad",
        "integrity",
        "objective",
        "Clave de identidad/granularidad",
    ),
    (
        "duplication",
        "Duplicación",
        "integrity",
        "objective",
        "Clave de identidad y política de duplicados",
    ),
    (
        "cardinality",
        "Cardinalidad",
        "structure",
        "objective",
        "Valores distintos/contrato de cardinalidad",
    ),
    (
        "relevance",
        "Relevancia",
        "usability",
        "mixed",
        "Propósito del uso y universo relevante",
    ),
    (
        "manipulability",
        "Facilidad de manipulación",
        "usability",
        "subjective",
        "Prueba de uso de datos",
    ),
    (
        "objectivity",
        "Objetividad",
        "trust",
        "subjective",
        "Auditoría de sesgos y evaluación humana",
    ),
    (
        "traceability",
        "Trazabilidad y linaje",
        "trust",
        "mixed",
        "Metadatos y registro de transformaciones",
    ),
    (
        "data_specification",
        "Especificación de datos",
        "structure",
        "objective",
        "Diccionario/contrato/metadata",
    ),
    (
        "coverage",
        "Cobertura",
        "structure",
        "objective",
        "Universo completo esperado",
    ),
    (
        "granularity",
        "Granularidad",
        "structure",
        "objective",
        "Unidad de observación declarada",
    ),
    (
        "redundancy",
        "Redundancia",
        "structure",
        "objective",
        "Inventario de copias/sistemas",
    ),
]

_ISO_EXTRA = [
    (
        "compliance",
        "Cumplimiento",
        "governance",
        "context-dependent",
        "Normativa o especificación aplicable",
    ),
    (
        "confidentiality",
        "Confidencialidad",
        "access",
        "objective",
        "Control de acceso efectivo",
    ),
    (
        "efficiency",
        "Eficiencia",
        "system",
        "objective",
        "Mediciones de recursos/latencia",
    ),
    (
        "understandability",
        "Comprensibilidad",
        "usability",
        "mixed",
        "Glosarios y evaluación de usuarios",
    ),
    (
        "availability",
        "Disponibilidad",
        "system",
        "objective",
        "Métricas de disponibilidad (SLA)",
    ),
    (
        "portability",
        "Portabilidad",
        "system",
        "objective",
        "Pruebas de transferencia entre sistemas",
    ),
    (
        "recoverability",
        "Recuperabilidad",
        "system",
        "objective",
        "Pruebas de recuperación/restauración",
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
