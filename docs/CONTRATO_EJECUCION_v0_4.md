# Contrato de ejecución v0.4 — semántica y garantías

**Estado:** prototipo de ejecución real sobre `pandas.DataFrame`. Integra, sin reemplazar, el planificador v0.3 y los checks v0.1. **No se considera todavía un motor universal ni un producto listo para producción.**

## 1. Frontera de ejecución

`ContractExecutor.run(contract, data={asset_id: dataframe}, evidence={...})` ejecuta únicamente las reglas con `READY` en un **preflight recalculado en el momento**.

Los controles no `READY` regresan sin puntuación: `NEEDS_EVIDENCE`, `NEEDS_CONTEXT`, `AMBIGUOUS`, `UNSUPPORTED`, `NO_ELIGIBLE_DATA`, `INVALID_CONTRACT`, `BLOCKED_*` o `NOT_APPLICABLE`. Si ocurre un error del evaluador, el resultado es `ERROR`, jamás `PASS`.

Una dependencia explícita exige que la regla anterior obtenga `PASS`; una métrica con estado distinto bloquea su dependiente (`BLOCKED_DEPENDENCY`). El planificador ya detecta ciclos.

## 2. Evaluadores conectados (4)

| Evaluador | Nivel | Unidad de denominador | Detalle |
|---|---|---|---|
| `builtin.presence` | value, field, record | valor o registro, según nivel | Ausencia física, tokens, normalización declarada; un registro cumple si todos sus campos requeridos están presentes |
| `builtin.composite_uniqueness` | entity, table | registro con clave compuesta | **Todos** los miembros de un grupo duplicado incumplen; `keep=False`; claves incompletas obedecen política de nulos |
| `builtin.max_delay` | record, dataset | registro | Diferencia entre relojes según unidad; retrasos negativos incumplen; el calendario civil usa zona explícita |
| `reference.lookup_temporal` | value, field | valor no ausente por defecto | Catálogos escalares o entradas con `value`, `valid_from`, `valid_to`; vigencia por código y por fila |

**Límite actual:** campos escalares de tablas pandas. No se ejecutan automáticamente métricas sobre SQL, Polars, Arrow, audio, imágenes, PDF, datos geoespaciales, grafos o estructuras profundamente anidadas. Están contemplados como futuras extensiones de la arquitectura, no como backends efectivos.

## 3. Semántica de observaciones

Cada unidad evaluable recibe exactamente un estado interno:

- `PASS` (cumple una regla), `FAIL` (la incumple), `UNKNOWN` (no es posible decidir) o `EXCLUDED` (fuera de alcance o excluida con política explícita).
- Las condiciones `when` se combinan con **AND**; las `exceptions` se combinan con **OR**. Son predicados de tres valores. Un nulo en un predicado no se considera falso por defecto.
- Los filtros temporales son inclusivos y sus fechas requieren representación inequívoca. El preflight actual bloquea toda la regla si no puede establecer con seguridad la elegibilidad temporal.
- Una regla sin observaciones evaluables reporta `NO_ELIGIBLE_DATA`, nunca cumplimiento del 100 %.
- El reporte muestra **posiciones base cero**, no valores originales ni identificadores personales en los ejemplos de fallos. Si se usa un DataFrame con índice repetido, los conteos no cambian.

Las configuraciones de `null_policy` pueden ser `exclude`, `include_as_failure`, `include_as_pass` o `indeterminate`, salvo en presencia (`not_null`), donde un nulo es el incumplimiento que justamente se busca medir. `custom` da `ERROR` hasta que exista una implementación autorizada.

El `unknown_policy` puede ser `indeterminate` (conservador), `fail` o `exclude`; sus consecuencias sobre los denominadores son explícitas. **No se excluyen indeterminaciones silenciosamente.**

## 4. Denominadores, tasas y umbrales

Para cada regla:

- `evaluated = passed_units + failed_units`
- `eligible_units = evaluated + indeterminate_units`
- `measured_rate = passed_units / evaluated`, si `evaluated > 0`
- `assessment_coverage = evaluated / eligible_units`, si `eligible_units > 0`
- `lower_rate = passed_units / eligible_units`, si `eligible_units > 0`
- `upper_rate = (passed_units + indeterminate_units) / eligible_units`, si `eligible_units > 0`

Los controles con indeterminaciones se clasifican `PASS` **solo** si cualquier resolución posible de las indeterminaciones satisface el umbral; `FAIL` si ninguna puede satisfacerlo; de lo contrario `INDETERMINATE`.

`threshold.operator` soporta `gte`, `gt`, `lte`, `lt`, `eq` y `between` sobre tasas en `[0, 1]`. Los límites inferior/superior son **límites lógicos de una proporción**, no intervalos de confianza estadísticos.

No se produce índice global automático. `dimension_summary()` cuenta estados por dimensión sin promediar magnitudes incomparables ni duplicar por conveniencia hallazgos entre dimensiones.

## 5. Datos, fechas y referencias

- Nunca se modifican las tablas originales ni las referencias suministradas.
- Los códigos se comparan como fueron suministrados: `"001"` es diferente de `1`.
- Los tokens de ausencia de texto se reconocen según la política declarada (`trim`, `casefold`, etc.); no hay coerción universal.
- Las fechas textuales deben tener un patrón explícito. Las marcas temporales sin zona se interpretan únicamente cuando el contrato aporta la zona necesaria.
- Para `calendar_days`, se usa el calendario civil en `extensions.calendar_timezone` o en la zona del reloj inicial; no se interpretan los días como intervalos de 24 horas. Para `hours` y `seconds` se calculan instantes físicos.
- Referencias limitadas temporalmente requieren `when_period` explícito. La versión declarada en el contrato debe estar también presente y coincidir en la evidencia suministrada.
- Las referencias externas solo se aceptan con `verified: true` **atestiguado por quien las suministra**. La librería no autentica por sí misma las fuentes ni consulta internet.
- Un catálogo con vigencias individuales acepta entradas como `{"value":"001","valid_from":"2025-01-01","valid_to":"2025-12-31"}`. Requiere `when_period.time_field` o `expectation.extensions.time_field`.

**Advertencia CSV:** `qd execute` carga CSV como texto para preservar ceros iniciales. Por tanto, un contrato con reloj textual debe declarar `representation.date_patterns`. No interpreta formatos regionales implícitamente. Al leer Excel, los formatos de presentación visual, ceros generados por estilo y fórmulas sin valor precalculado no siempre sobreviven la lectura del motor.

## 6. Uso

```bash
pip install -e '.[test]'
python examples/execute_contract.py
pytest -q
```

```python
from quality_dimensions import ContractExecutor, load_contract
contract = load_contract('contracts/contrato_calidad.yaml')
report = ContractExecutor().run(
    contract,
    data={'mercado_laboral': dataframe},
    evidence={'ocupaciones_2025': {'values': ['001'], 'version': '2025', 'verified': True}},
)
print(report.to_frame())
print(report.dimension_summary())
report.to_json('quality_results.json')
```

En CLI:

```bash
qd execute datos.csv --contract contracts/contrato_calidad.yaml --asset mercado_laboral --evidence-json evidence.json --output report.json
```

## 7. Limitaciones pendientes

1. Adición de nuevos evaluadores (tipos, rangos, patrones, relaciones, reconciliaciones, distribuciones, cobertura) al contrato v0.2. **Los 17 checks del ejecutor anterior v0.1 no se traducen automáticamente**.
2. Perfiles temporales de interpretación y coerción léxica segura según variantes declaradas, no solo validación de aplicabilidad.
3. Auditoría de procedencia criptográfica de datos y referencias: se genera SHA-256 del contrato, pero no un hash integral de los datos.
4. Ejecución incremental, backend SQL/Polars, costes de memoria, estimaciones empíricas y optimización de 1M+ registros.
5. Validación externa objetiva de evidencia; seguridad, accesibilidad, reputación y dimensiones subjetivas requieren conectores y evidencia humana.
6. Reglas de selección con fechas parcialmente ausentes: por seguridad el planificador bloquea la regla, aunque el ejecutor ya maneja indeterminación por otras causas.
7. `unknown_policy` y exclusiones requieren aprobación metodológica; al excluir desconocidos puede disminuir la población reportada.

**Pruebas incluidas:** regresiones previas y escenarios de vigencia, índices repetidos, filtros, excepciones, semántica de nulos, intervalos de tasas, tiempos locales, evidencias y ejecuciones no destructivas.
