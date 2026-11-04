# Recomendador avanzado y escalable — v0.7.0.dev0

## Decisión de arquitectura

**Pandas no es el motor de análisis de volumen.** La interfaz heredada permanece por compatibilidad, pero `discover_advanced` opera sobre CSV/TSV mediante un lector secuencial de memoria acotada o usa agregaciones del motor de los datos (Spark clásico, DuckDB, Polars Lazy). La API de recomendación no importa pandas durante su inicialización. Las importaciones de motores son diferidas.

## Contrato de descubrimiento

```
fuente(s) → esquema → selección por presupuesto → agregados compactos
          → reservorio opcional y acotado (solo CSV local)
          → señales semánticas → candidatos multidimensionales
          → evidencia + limitaciones + costo de verificación
          → cola de revisión → plan de aprobación (NO contrato ejecutable)
```

Las sugerencias no validan la calidad por sí solas. Cada recomendación declara:

- `kind`: `rule`, `semantic`, `context`, `missing_policy`, `hypothesis`, `relationship`, `reference`.
- `dimension`, `evaluator`, campos, evidencia y justificación.
- `confidence`: escala cualitativa **no calibrada**.
- `priority`: orden de atención **no severidad de un defecto**.
- `cost`: esquema, agregado, escaneo exacto o dependencia externa.
- `prerequisites` y `caveats`, incluyendo diferencias entre pruebas exactas y muestras.
- `approval_required=True` para todas las sugerencias.

No hay imposición por defecto de reglas sobre claves, dominios cerrados, obligatoriedad o relaciones intertablas.

## Presupuestos y niveles

| Modo | Operaciones | Garantías |
|---|---|---|
| `schema` | Consulta encabezados/esquema | No inspecciona filas CSV; no ejecuta acciones Spark. DuckDB puede leer metadatos o inferencia inicial de CSV. |
| `balanced` | Una pasada de CSV o un agregado en motor | Conteos, nulos físicos, blancos, estimadores de cardinalidad y candidatos conservadores. |
| `deep` | Mismo agregado; análisis adicional sobre reservorio acotado | Sugerencias de claves compuestas y dependencias en muestra CSV, jamás certificación global. Spark/DuckDB/Polars todavía no recogen muestras adicionales. |

Los límites se declaran en `DiscoveryBudget`: columnas perfiladas, número de filas en muestra, longitud máxima retenida de texto, posibles claves, pares de clave, capacidad de conjuntos exactos, dominios y precisión del estimador. Con CSV, el perfil conserva columnas no inspeccionadas marcándolas explícitamente como no perfiladas.

El estimador HyperLogLog tiene memoria acotada y un error estándar aproximado de `1.04 / sqrt(m)`. **Nunca se utiliza para demostrar unicidad**. Para datos CSV reducidos, las claves simples pueden obtener un recuento exacto de distintos hasta `max_exact_key_values`; al superar ese límite dejan de contar como verificación exacta. Las claves compuestas se proponen únicamente sobre la muestra, nunca como válidas globalmente.

## Adaptadores

| Entrada | Motor real | Movimiento de datos al proceso Python | Estado de prueba local |
|---|---|---|---|
| CSV/TSV | `csv` estándar, por streaming | Una fila y reservorio acotado | Probado |
| CSV.gz/TSV.gz | `gzip` + `csv`, por streaming | Una fila y reservorio acotado | Probado |
| Parquet/CSV | DuckDB (opcional) | Una fila agregada | Código preparado, **sin prueba real** en este entorno |
| Polars DataFrame/LazyFrame, CSV/Parquet | Expresiones de Polars (opcional) | Una fila agregada | Código preparado, **sin prueba real** en este entorno |
| PySpark DataFrame clásico (batch) | Agregados de Spark | Una fila agregada | Código preparado, **sin prueba real** en este entorno |
| Spark Connect/Structured Streaming | — | — | Rechazado explícitamente |

Los adaptadores operan **sin `toPandas()` ni conversiones de la tabla original**. El catálogo de 37 dimensiones es conceptual, no implica que todas tengan métricas implementadas. Los adaptadores nuevos generan propuestas; el ejecutor universal para todos los motores sigue pendiente.

## Recomendaciones incorporadas

- Obligación de presencia por variable, sin asumir obligatoriedad.
- Marcadores de ausencia (`ND`, `NAN`, etc.) como política pendiente de confirmación.
- Semántica de códigos con anchura fija y ceros iniciales, sin alterarlos.
- Campos de conteo y posibles restricciones numéricas, pero CSV sigue siendo lexical hasta definir conversión.
- Fechas y su necesidad de locale, calendario y zona horaria; cadenas ambiguas no se convierten.
- Claves simples exactas dentro del límite, o **hipótesis** con cardinalidad aproximada fuera del límite.
- Claves compuestas candidatas a partir de muestra, excluyendo combinaciones no mínimas triviales.
- Dependencias funcionales candidatas código → nombre cuando hay códigos repetidos con correspondencia estable en muestra.
- Campos potencialmente relacionados entre activos, solo por similitud de nombres (no verificación referencial).
- Reglas candidatas contra catálogos **aportados como metadatos** (`reference_hints`), no verificadas hasta vincular la fuente.
- Desviaciones importantes en proporción de ausencias frente a perfiles históricos (`baseline_profiles`), sin asumir error.
- Recuerdos de aprobaciones/rechazos anteriores (`prior_reviews`) que priorizan la revisión, **nunca aprueban automáticamente**.

## API local (CSV streaming)

```python
from quality_dimensions import discover_advanced, DiscoveryBudget

report = discover_advanced(
    'datos.csv',
    asset_id='laboral',
    budget=DiscoveryBudget(mode='deep', max_sample_rows=1500,
                           max_profile_columns=64, max_exact_key_values=20_000),
    reference_hints={
        'laboral.OCU_ID': {'reference_id': 'CATALOGO_OCUPACIONES', 'version': '2025'}
    },
)

report.to_json('propuestas.json')              # Sin valores crudos de muestras
print(report.resource_plan())                  # Pasadas y jobs efectuados
for item in report.review_queue(priority='high'):
    print(item.id, item.kind, item.title, item.cost)

# La aprobación produce MANIFIESTO DE REVISIÓN, no ejecución.
manifest = report.approved_plan(
    [report.recommendations[0].id],
    purpose='Diagnóstico previo a publicación',
)
```

## API distribuida (Spark clásico)

```python
from quality_dimensions import discover_advanced, DiscoveryBudget

# df_spark: pyspark.sql.DataFrame en batch
result = discover_advanced(
    df_spark,
    budget=DiscoveryBudget(mode='balanced', max_profile_columns=48),
)
# Una acción de agregación distribuida; solo 1 fila agregada al driver.
result.to_json('advice_spark.json')
```

Un modo `schema` sobre Spark se limita a inspección de esquema y no desencadena job.

## API DuckDB y Polars (opcionales)

```python
# Requiere instalar DuckDB.
report = discover_advanced('datos.parquet', engine='duckdb')

# Requiere Polars.
import polars as pl
lf = pl.scan_parquet('datos.parquet')
report = discover_advanced(lf, engine='polars')
```

## CLI para datos voluminosos

```sh
qd advise datos.csv --asset datos --mode deep --sample-rows 1500 --max-columns 64 --output report.json
qd advise datos.parquet --engine duckdb --mode balanced --output report.json
```

`qd suggest` de v0.6 sigue existiendo por compatibilidad y **carga el archivo en pandas**; para archivos grandes, usar **`qd advise`**.

## Garantías y límites

1. Los informes no contienen el reservorio de filas ni los valores de categorías por defecto. `reveal_configuration=True` incluye dominios sugeridos y tokens de ausencia: **puede revelar valores sensibles**.
2. Los recursos por columna son acotados, pero una sola fila CSV de longitud enorme puede consumir memoria; depende del parser y del tamaño físico del registro.
3. El costo de CPU sigue siendo proporcional a datos y columnas inspeccionadas; streaming acota memoria, no hace gratis el escaneo.
4. Las verificaciones de cardinalidad aproximada, dependencias sobre muestra y correspondencia de nombres **no son certificados** de calidad.
5. Los formatos CSV requieren parámetros explícitos si su codificación o dialecto no es estándar. TSV usa tabulador por defecto según extensión.
6. Spark Connect, streaming, Delta/Iceberg/Hudi, tablas federadas y clústeres reales requieren pruebas independientes.
7. El sistema actual no crea automáticamente contratos ejecutables universales; `approved_plan()` devuelve un manifiesto que necesita enlazar los tipos, el motor y las referencias.
8. No se han ejecutado pruebas locales reales de DuckDB, Polars ni PySpark porque no están instalados y el entorno de construcción no dispone de acceso a su distribución.
9. Los resultados entre motores deben validarse con pruebas diferenciales antes de afirmar equivalencia (NULL/NaN, tiempo, unicode, coma decimal, zonas horarias, collation).

## Continuación técnica prioritaria

1. Test de integración real DuckDB, Polars y PySpark, incluidas bases Parquet particionadas y casos Unicode/temporales.
2. Ejecutor nativo con denominadores y políticas trivaluadas equivalentes por motor; evitar la unión de datos distintos mediante conversión a pandas.
3. Pushdown de filtros, perfiles por particiones, cacheo de agregados y incrementalidad con firmas de versión.
4. Aprendizaje de reglas revisadas con versiones, procedencia y ámbito, sin autoaprobar políticas de negocio.
5. Registro central de evidencias y conectores externos para catálogos y linaje.
