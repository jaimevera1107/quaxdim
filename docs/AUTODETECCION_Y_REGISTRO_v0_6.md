# Motor de descubrimiento y recomendación v0.6 — contrato conceptual y técnico

## Objetivo

Reducir drásticamente la carga de configuración de calidad de datos, sin sustituir reglas de negocio desconocidas por deducciones injustificadas. **Descubrir ≠ aprobar ≠ ejecutar**.

Flujo: `dataset → perfilado no destructivo → hipótesis justificadas → aprobación explícita → contrato v0.2 → preflight → ejecución → reporte`.

## Capacidades verificadas (pandas)

| Detección | Cómo decide | Lo que NO infiere |
|---|---|---|
| Completitud candidata | Por cada columna informa nulos físicos | Que la columna deba ser obligatoria |
| Clave candidata | Nombre tipo ID y unicidad observada completa | Que deba ser única por definición |
| Código numérico textual | Patrón ASCII de ancho fijo dentro de muestra | Que nunca cambie en otros períodos |
| Dominio categórico | Cardinalidad reducida en la muestra | Que el universo de categorías sea cerrado |
| Conteo no negativo | Nombre compatible con magnitud de conteo | Que no existan ajustes o saldos negativos válidos |
| Orden entre fechas | Par de campos inicio/fin ya tipados | Que los nombres prueben su semántica |
| Marcador de ausencia | Cadenas candidatas, como `ND` | Que la cadena sea realmente nula |
| Fechas textuales ISO/regionales | Coincidencia léxica estricta | Conversión de texto, calendario o zona horaria |
| Tipo mixto/columna vacía | Inspección conservadora del tipo | Que haya error o que deba coercionarse |

**Todas las sugerencias requieren aprobación.** Las hipótesis que no generan una regla ejecutable requieren tratamiento explícito separado. Los marcadores de ausencia solo se aplican como política cuando el usuario los aprueba.

Confianza `low/medium/high` es **ordinal y heurística**, no una probabilidad de acierto ni una validación estadística calibrada.

La muestra pandas es determinista con semilla y límite de filas configurable. Las estadísticas de no nulos y clave candidata usan todas las filas pandas; los patrones y categorías proceden de una muestra. Para bases enormes, la muestra puede ser representativa insuficiente, por lo que el reporte la identifica.

## Privacidad

`DiscoveryReport.to_json()` oculta de forma predeterminada configuraciones que puedan contener categorías reales. `to_json(include_configuration=True)` expone esos valores solo por solicitud explícita; hay que tratar ese resultado como potencialmente sensible. Los informes de ejecución existentes guardan posiciones de filas y métricas, no valores.

## Semántica de la aprobación

`DiscoveryReport.to_contract(approve=[...], purpose="...")` valida identificadores, conserva las columnas sin imponer nulidad, genera métricas conformes a v0.2 y traduce **solo las sugerencias aprobadas**. Aplica umbral de 100 % por defecto (ajustable mediante `thresholds={id: 0.95}`) exclusivamente después de aprobación. Nunca se crea un contrato de ejecución al llamar solo `discover`.

Las dimensiones disponibles en el catálogo conceptual (37) **no implican** que las 37 tengan métricas automáticas.

## Registro compartido

`evaluator_registry.REGISTRY` declara operadores, unidades de observación y soporte por backend. Existe un único lugar para descubrir capacidades declaradas. Los kernels pandas y Spark siguen especializados por motor y deben demostrar equivalencia antes de ampliar los contratos compartidos.

| Evaluador | Pandas | Spark clásico |
|---|---|---|
| `builtin.presence` | probado | experimental |
| `builtin.composite_uniqueness` | probado | experimental |
| `builtin.max_delay` | probado | experimental, subconjunto |
| `reference.lookup_temporal` | probado | experimental, catálogos escalares pequeños |
| `builtin.allowed_values` | probado | experimental, escalares simples |
| `builtin.value_range` | probado | experimental, numéricos nativos |
| `builtin.regex_fullmatch` | probado con `regex` opcional | no declarado |
| `builtin.column_compare` | probado | no declarado |

**Spark**: no convertir a pandas; `discover(spark_df)` hace inspección de esquema sin lanzar jobs; `discover(spark_df, spark_mode='aggregate')` hace una agregación distribuida y solo trae un registro de estadísticas. La segunda opción debe invocarse conscientemente, pues requiere cómputo y `approx_count_distinct` es aproximado. Ninguna de las dos certifica unicidad ni deduce catálogos cerrados. Las pruebas Spark se suministran, pero no se ejecutaron localmente por falta de PySpark.

## Restricciones y extensión futura

- Los códigos con ceros iniciales siguen siendo texto. La importación CLI de CSV conserva los valores léxicos (tipos numéricos de CSV no se asumen); hay que configurar la interpretación en una etapa posterior.
- Se mantiene la imposibilidad de derivar exactitud, representatividad poblacional, confiabilidad de fuentes y otras dimensiones de un DataFrame aislado.
- La detección de dominios por muestra puede omitir categorías válidas; revisar catálogos oficiales y sus vigencias.
- Los regex requieren dependencia opcional `regex`, con timeout y límites de longitud. La similitud de motores para regex Unicode no se presume.
- Spark Connect, streaming, Polars, DuckDB, SQL y datos no tabulares no están certificados en esta versión.
- El registro de metadatos no es todavía un sistema externo de plugins de terceros.
- Los límites computacionales e indicadores de representatividad deberán ampliarse en futuras iteraciones.

## Uso programático

```python
from quality_dimensions import discover, evaluate_contract

advice = discover(df, asset_id='datos', sample_rows=10000, seed=42)
print(advice.to_frame())                     # no aprobación ni ejecución
advice.to_json('sugerencias.json')          # sin categorías en bruto

reviewed_ids = [advice.recommendations[0].id]  # ejemplo: selección humana
contract = advice.to_contract(approve=reviewed_ids,
                              purpose='requisitos de publicación')
result = evaluate_contract(contract, data={'datos': df})
print(result.to_frame())
```

## CLI

```shell
qd suggest datos.csv --asset datos --output sugerencias.json
qd draft datos.csv --asset datos --approve suggest_XXXXXXXXXXXX \
   --purpose 'requisitos de publicación' --output contrato_aprobado.yaml
qd execute datos.csv --contract contrato_aprobado.yaml --asset datos --output mediciones.json
```

Para resultados comparables, el archivo y la configuración de importación deben mantenerse idénticos entre `suggest`, `draft` y `execute`; en CSV los datos se interpretan inicialmente como texto para no destruir códigos o fechas.
