# Backend Spark experimental (v0.5.0.dev0)

## Alcance

`SparkContractExecutor` es un ejecutor **nativo del DataFrame API clásico de PySpark**. Los datos nunca se convierten implícitamente a pandas, no se recogen filas para evaluarlas en el driver y no se usan UDF Python. Solo se devuelve al driver una fila agregada de contadores por regla. Las referencias literales tienen un límite explícito de 1.000 valores.

Mantiene el esquema del contrato v0.2 y devuelve `ContractExecutionReport` y `ContractRuleResult` como el backend pandas. El resultado no contiene posiciones de registros, porque en Spark no existe un orden global estable de filas. No hay modificación de los DataFrames de entrada.

| Evaluador | Operación Spark | Alcance v0.5 |
|---|---|---|
| `builtin.presence` | agregaciones columnares | nulos nativos, NaN numéricos, tokens escalares y vacíos sencillos; sin casefold Unicode exacto |
| `builtin.composite_uniqueness` | `groupBy` + agregación | claves escalares; faltantes como indeterminados/excluidos según contrato |
| `builtin.max_delay` | `datediff` / `unix_micros` | fechas `DateType` y días civiles, o timestamps `TimestampType` de instantes UTC |
| `reference.lookup_temporal` | `isin` | *solo catálogo escalar* acotado, con ámbito temporal global por `DateType`; no catálogos con vigencias individuales |

Las expresiones `when` y `exceptions` usan lógica SQL de tres valores, evitando `eval`, interpolación SQL y coerción a falso de comparaciones con NULL o NaN. Las métricas con denominador cero informan `NO_ELIGIBLE_DATA`. Los resultados indeterminados generan límites inferior/superior de tasa, no un falso PASS.

## Restricciones honestas

- Las referencias con vigencias **por código**, catálogos grandes, claves anidadas, selección de `variants`, `when_period` con strings o timestamps, normalización por `casefold`, y políticas de ausencia condicionadas **aún no están implementadas** en el backend Spark. Se registran como `UNSUPPORTED`, `NEEDS_CONTEXT` o `NEEDS_EVIDENCE`, nunca como PASS.
- Los contratos con `normalization: trim` usan el `trim` nativo de Spark (eliminación de espacios SQL); **no se garantiza equivalencia Unicode completa** con `str.strip` de pandas. Para comparación exacta entre motores use una política `none` o defina/valide explícitamente un repertorio léxico común.
- `Spark Connect`, Structured Streaming, Delta Lake, Iceberg, Hudi, particionamiento incremental y catalogación masiva son extensiones futuras no probadas.
- La precisión temporal de `calendar_days` sobre timestamps y `instant` sobre marcas locales requiere políticas explícitas adicionales y queda bloqueada; no se forzarán conversiones de zona horaria.
- Una referencia `verified: true` es una declaración del consumidor de la librería, no una verificación externa independiente.
- Las acciones de Spark pueden materializar los datos varias veces por regla. La optimización *multi-metric pushdown* y persistencia selectiva son tareas posteriores. No se han publicado benchmarks ni garantías de producción.
- Sin PySpark instalado, el módulo es importable y al intentar ejecutarlo indica `SparkBackendUnavailable` y cómo instalar el extra.

## Instalación

```bash
python -m pip install -e '.[spark,test]'
python examples/spark_execute.py
pytest -q tests/test_spark_backend.py
```

El paquete declara `pyspark>=3.5,<5` como dependencia **opcional**, no requerida para usuarios pandas. La preparación real del entorno Spark depende de la instalación y su versión; en la serie Spark 4 se requiere Java 17+ y Python 3.10+ según la documentación oficial actual.

## Despacho automático (mismo contrato)

```python
from quality_dimensions import evaluate_contract
report = evaluate_contract(contract, data={'mercado_laboral': spark_dataframe}, evidence=evidence)
```

Si los valores son pandas, utiliza el ejecutor pandas. Si son DataFrames clásicos de PySpark, usa Spark. Las mezclas de motores se rechazan; no hay conversión a pandas ni transferencia de registros en el dispatcher.

## Ejemplo

```python
from quality_dimensions import SparkContractExecutor, load_contract

contract = load_contract('contracts/contrato_calidad.json')
report = SparkContractExecutor().run(
    contract,
    data={'mercado_laboral': spark_dataframe},
    evidence={'ocupaciones_2025': {
        'values': ['001', '003'], 'version': '2025', 'verified': True,
    }},
)
print(report.to_frame()[['rule_id','status','measured_rate','assessment_coverage']])
```

**Importante:** el contrato de ejemplo original utiliza una representación con `trim_casefold` y fechas `local_datetime` + `instant` para demostrar casos complejos de pandas. Ese contrato se bloqueará parcialmente en Spark por diseño. Ejecute `examples/spark_execute.py` para ver una variante deliberadamente compatible con su subconjunto ya implementado.

## Validación

Se reejecutaron las 100 pruebas del backend pandas. Se añadieron cinco pruebas de integración Spark de ejecución real, preparadas para usar `local[2]`; **quedaron omitidas en el entorno actual porque PySpark no está instalado y no está disponible una descarga de dependencias**. La compilación sintáctica del backend Spark y el empaquetado se verificaron, pero **la ejecución efectiva sobre Spark sigue pendiente de validación**. No se puede afirmar paridad operacional hasta correr esas pruebas en un entorno Spark real.
