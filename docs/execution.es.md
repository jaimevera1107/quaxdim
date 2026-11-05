# Quality Dimensions v0.8 — Ejecución nativa y recomendaciones con revisión

## Alcance comprobable

La versión v0.8 mantiene la API de v0.7.1 y añade una **ruta alternativa** de ejecución nativa, `plan_optimized` y `evaluate_optimized`, para reglas portables y expresamente aprobadas. La ruta masiva **no importa pandas**. El descubrimiento `discover_advanced` continúa siendo consultivo y no ejecuta reglas.

El motor no intenta reproducir todas las semánticas del ejecutor heredado de pandas. Cuando la regla excede el subconjunto seguro, retorna `UNSUPPORTED` o `ERROR` con motivo y **nunca PASS**. DuckDB, Polars y Spark se ejecutan de forma nativa, sin `toPandas`, `collect` de filas ni materialización completa en Python; únicamente se recuperan contadores agregados. La ruta CSV usa el módulo `csv` y conserva valores lexicales. La unicidad exacta de CSV tiene un límite explícito de claves almacenadas en memoria.

## Flujo de usuario

1. `discover_advanced(source, budget=DiscoveryBudget(...))`: perfilamiento nativo; nuevas estadísticas de códigos (longitud mínima/máxima, valores compuestos solo por dígitos). No se inventa catálogo ni se valida una clave por su HLL.
2. Revisar `report.recommendations` y `report.capability_coverage()`. Revisar advertencias y contexto.
3. `build_reviewed_contract(report, approve=[ids], purpose='...', thresholds={id:1.0})`: convierte **solo** sugerencias elegidas, con umbral explícito, al contrato v0.2; devuelve un rastro independiente de aprobación.
4. `plan_optimized(contract,data={'asset':source})`: inspección estática sin ejecutar trabajos de datos, produce motivos de bloqueo y estimación de trabajo.
5. `evaluate_optimized(...)`: ejecuta controles compatibles y produce conteos enteros y tasas con límites de incertidumbre.
6. El usuario conserva resultados, revisa fallos y decide si habilita nuevas reglas o cambia el contrato.

### Ejemplo mínimo, sin pandas

```python
from quaxdim import discover_advanced, DiscoveryBudget, build_reviewed_contract, plan_optimized, evaluate_optimized

source = 'datos.csv'
r = discover_advanced(source, budget=DiscoveryBudget(mode='balanced'))
approved = [a.id for a in r.recommendations if a.evaluator == 'builtin.presence' and a.fields == ('CODE_ID',)]
if not approved:
    raise ValueError('No se obtuvo una sugerencia de completitud para CODE_ID')
contract, review = build_reviewed_contract(r, approve=approved,
    purpose='Publicación estadística', thresholds={i: 1.0 for i in approved})
plan = plan_optimized(contract, data={'dataset':source})
print(plan.to_dict())
report = evaluate_optimized(contract, data={'dataset':source})
print(report.to_json('evaluacion.json'))
```

### Terminal

```powershell
quaxdim advise "D:\\datos\\archivo.csv" --asset dataset --mode balanced --output recomendaciones.json
quaxdim native-plan "D:\\datos\\archivo.csv" --asset dataset --contract contrato.json --output plan.json
quaxdim native-execute "D:\\datos\\archivo.csv" --asset dataset --contract contrato.json --output medicion.json
```

## Matriz de motor y controles (v0.8 experimental)

| Regla | CSV streaming | DuckDB | Polars Lazy | Spark clásico batch |
|---|---|---|---|---|
| Presencia | Sí | Sí | Sí | Sí |
| Dominio pequeño declarado | Texto | Sí, tipos compatibles | Sí, tipos compatibles | Sí, tipos compatibles |
| Patrón ASCII de ancho fijo | Sí | Sí | Sí | Sí |
| Rango numérico | No, sin conversión implícita | Sí, tipado | Sí, tipado | Sí, tipado |
| Comparación entre columnas numéricas | No, léxico | Sí | Sí | Sí |
| Unicidad compuesta exacta | Sí, límite de memoria | Sí, agrupa | Sí, agrupa | Sí, shuffle |
| Catálogos externos temporales | No | No | No | No |
| Fechas ambiguas / calendarios / zonas | No | No | No | No |
| Reglas condicionales / dependientes | No | No | No | No |
| Streaming continuo / Spark Connect | No | No | No | No |

**Nota:** El motor heredado pandas/Spark contiene otras capacidades que no deben suponerse compatibles en la ruta optimizada. La declaración de compatibilidad es por evaluador **y configuración**, no solo por nombre del evaluador.

## Garantías y restricciones

- Ninguna sugerencia de autodetección se ejecuta sin aprobación explícita y umbral.
- El planificador no afirma calidad; `READY` significa *compatible para ejecutar*.
- Las estadísticas de cardinalidad HLL son aproximaciones y no constituyen prueba de unicidad.
- Los resultados separan unidades aprobadas, fallidas, indeterminadas y excluidas. Los límites de tasa usan el total evaluable; con elegibles=0 se informa `NO_ELIGIBLE_DATA`.
- Se rechazan conversiones implícitas de texto a números y fechas, políticas desconocidas de valores faltantes, predicados condicionados y tipos anidados.
- Los patrones portables se restringen a expresiones ASCII simples de longitud fija; no se promete equivalencia general de motores regex.
- Las reglas SQL escapan nombres y literales; las fuentes de archivos se proporcionan como rutas, no consultas SQL arbitrarias.
- Los datos de origen no son modificados por los evaluadores. Las estadísticas no incluyen filas ni muestras de valores.
- Para caracteres Unicode, colaciones, `NaN`, zonas horarias y nulos implícitos en lectores de CSV, **no hay equivalencia universal validada**. Se debe usar un origen tipado (Parquet) y contratos explícitos cuando esos casos importen. Las políticas de trim/whitespace quedan bloqueadas entre motores por falta de equivalencia.
- El plan es estimativo; una clave compuesta requiere trabajo adicional de agrupación y puede consumir RAM o provocar shuffle distribuido. `estimated_full_passes` no reemplaza métricas reales de planes físicos del motor.
- Los límites de CSV exactos solo afectan a memoria de claves, no a la lectura de las filas; ante el presupuesto excedido se retorna ERROR, no una tasa parcial.

## Pruebas y rendimiento

Para ejecutar todas las regresiones: `python -m pytest -q tests tests_validation tests_public`.
Los benchmarks de escala están en [`benchmarks/`](../benchmarks/) y generan resultados fuera del repositorio.


`python benchmarks/evaluation_backends.py --engine csv_stream --rows 1000000 --output bench.json` inicia el mismo escenario (misma fuente generada, tres reglas, idénticos resultados esperados) a 1, 5 y 10 millones de filas por motor; **se genera un archivo independiente para cada motor** y el tiempo de creación se separa del de ejecución. Puede demandar espacio en disco y tardar varios minutos por motor, especialmente Spark local. El rendimiento de Spark en una estación de trabajo no sustituye pruebas en clúster.

### Estado de validación a fecha de elaboración

- **Sí ejecutado en este entorno:** pruebas heredadas pandas/CSV, controles nativos CSV, CLI y benchmark CSV de un millón de filas.
- **No ejecutado en este entorno:** motores opcionales DuckDB, Polars y PySpark; las pruebas están entregadas para Windows con `pytest.importorskip` y no se contabilizan como superadas.
- **No se afirma:** equivalencia completa de todas las métricas, compatibilidad Spark Connect/Structured Streaming, ni buen desempeño para cien millones de registros sin medirlo.

## Referencias técnicas

Las implementaciones nativas se apoyan en las interfaces públicas de agregación de DuckDB, Polars LazyFrame y PySpark. Las API y restricciones deben volver a verificarse en las versiones instaladas en la máquina de pruebas.
