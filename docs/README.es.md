# QuaxDim — Dimensiones de la calidad de datos

[Volver al README principal](../README.md)

QuaxDim evalúa la calidad de datos con criterios **contextuales, trazables
y reproducibles**. Su catálogo es multidimensional, pero las métricas
disponibles forman un subconjunto del marco conceptual: no se proclama
implementación de dimensiones que aún requieren evidencia o trabajo humano.

## Uso inicial

```powershell
py -m pip install "quaxdim[duckdb,polars]"
quaxdim advise "D:\Datos\base.csv" --mode deep --output recomendaciones.json
```

La detección automática **solo recomienda**, no convierte observaciones estadísticas
en reglas obligatorias. Las propuestas deben aprobarse y tener umbrales definidos.

## Gobernanza y metodología

Las librerías de validación suelen empezar cuando alguien ya decidió las
reglas. QuaxDim modela esa decisión: el contrato aprobado registra qué se
acordó, para qué, quién lo aprobó, quién responde cuando falla y qué tan
crítico es el dato. Es el artefacto de gobernanza.

El bloque `governance` del contrato es opcional y no cambia ningún veredicto:

```yaml
governance:
  data_owner: "Dirección de Empleo y Trabajo"
  approved_by: "jvera"
  approved_at: "2026-11-06"
  review_cycle: quarterly
  criticality: analytical
  rules:
    employee_id_unique:
      steward: "equipo-SIGA"
      on_failure: block_publication
      criticality: regulatory_reporting
```

Con él, cada resultado lleva su `criticality`, `steward` y `on_failure`, y el
reporte pone primero los problemas del dato crítico. Los IDs de
`governance.rules` deben existir en el contrato, y un contrato con el ciclo
de revisión vencido emite `StaleContractWarning`.

`report.to_methodology("dama_uk_2013")` genera el informe metodológico: qué
se evaluó, con qué medidas y adaptaciones, y qué dimensiones no se
evaluaron y por qué (`missing_rule`, `missing_label`, etc.). Es trazabilidad,
no certificación: nunca afirma conformidad con un estándar. Desde la CLI,
`--methodology metodologia.md --profile dama_uk_2013`.

Detalle completo, en inglés: [docs/governance.md](governance.md).

## Motores

- **CSV/TSV/gzip:** lectura secuencial sin cargar tablas completas en pandas.
- **DuckDB:** consultas agregadas y procesamiento nativo para archivos y tablas compatibles.
- **Polars:** ejecución perezosa mediante expresiones nativas.
- **Spark:** procesamiento batch distribuido clásico con PySpark; requiere Java compatible.
- **pandas:** capa opcional para compatibilidad con análisis existentes.

No se garantiza equivalencia entre motores para reglas fuera del subconjunto
portable documentado. Los formatos del catálogo son posibilidades de extensión,
no una lista de adaptadores implementados.

## Pruebas y mantenimiento

Instala las dependencias de desarrollo y ejecuta las pruebas:

```powershell
python -m pip install -e ".[test]"
python -m pytest -q tests tests_validation tests_public
```

Las pruebas de DuckDB, Polars y Spark requieren sus dependencias opcionales.
Los benchmarks reproducibles están en [`benchmarks/`](../benchmarks/).
La automatización de GitHub → PyPI está documentada en [RELEASE.md](../RELEASE.md).
