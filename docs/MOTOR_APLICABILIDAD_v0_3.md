# Motor de aplicabilidad — especificación operativa v0.3

**Estado:** implementado en Python/pandas como *preflight* del contrato universal v0.2. El motor **NO ejecuta** reglas v0.2; solo decide su preparación y elegibilidad. Coexiste con el ejecutor v0.1 (`QualitySuite`) para sus propias reglas; todavía no existe un compilador automático v0.2→v0.1.

## 1. Contrato de decisión

Por cada regla declarada produce: `rule_id`, `asset_id`, `metric_id`, `evaluator`, `status`, `reasons` (códigos estables y explicación), `eligible_rows`, `total_rows`, `dimension_ids`, `source`.

Estados disponibles:

| Estado | Significado | Interpretación |
|---|---|---|
| `READY` | Métodos/insumos declarados disponibles para el evaluador registrado | **No significa cumplimiento** |
| `RECOMMENDED` | Posible control sugerido de modo no vinculante | No ejecutar sin aprobación |
| `NEEDS_CONTEXT` | Falta declaración semántica, campo, unidad o configuración | Solicitar datos del contrato |
| `NEEDS_EVIDENCE` | Referencia o evidencia no ligada, no contrastada o incompatible | Conservar estado pendiente |
| `NEEDS_DATA` | Activo sin DataFrame de ejecución | No inventar población |
| `AMBIGUOUS` | Fechas, variantes, predicados o contextos no pueden resolverse sin arbitrariedad | Detener ese control |
| `NO_ELIGIBLE_DATA` | Población elegible vacía con filtro verificable | No informar 100% |
| `NOT_APPLICABLE` | Exclusión explícita, debidamente justificada | No confundir con conformidad |
| `UNSUPPORTED` | Evaluador, forma u operación no soportada por backend | Requiere adaptador |
| `BLOCKED_RESOURCE` | Presupuesto de memoria declarado insuficiente | Replanificar ejecución |
| `BLOCKED_DEPENDENCY` | Depende de un control cuyo preflight no está listo | Resolver prerequisito |
| `INVALID_CONTRACT` | Incompatibilidad estructural, referencia rota o ciclo de dependencias | Corregir contrato |

**Matiz:** el estado `NO_ELIGIBLE_DATA` indica ausencia de casos cubiertos **en estos datos observados**, no necesariamente la irrelevancia de la regla o calidad del universo. Su precedencia sobre evidencia faltante se aplica cuando la población cero está demostrada.

## 2. Orden de evaluación

1. Validar JSON Schema y unicidad de identificadores.
2. Construir mapa de activos, dimensiones, métricas, reglas, referencias y capacidades.
3. Revisar tipos de operación, nivel, cardinalidad de campos y forma del activo.
4. Verificar declaraciones y presencia efectiva de campos en el DataFrame.
5. Verificar reglas particulares (clave compuesta, duración máxima, roles temporales).
6. Exigir referencias enlazadas, versionadas y explícitamente atestadas por el invocador.
7. Resolver filtros de regla (`when`, `when_period`) y calcular población elegible.
8. Resolver variantes activas por período/discriminante, con prioridad y detección de solapamientos.
9. Propagar ciclos y dependencias no preparadas.
10. Agregar sugerencias opt-in sin convertirlas en controles obligatorios.

Una decisión puede conservar **múltiples razones** aunque tenga un solo estado. Las prioridades del estado expresan gravedad técnica del preflight y no son puntuaciones de calidad.

## 3. Capacidades operativas del backend actual

| Evaluador declarado | Forma | Alcances | Condiciones para quedar listo |
|---|---|---|---|
| `builtin.presence` | tabla | valor, campo, registro | Campo(s) declarado(s) y presentes; variantes resolubles |
| `builtin.composite_uniqueness` | tabla | entidad, tabla | Clave declarada, sin repeticiones, campos escalares |
| `builtin.max_delay` | tabla | registro, conjunto | Dos marcas temporales, escala de demora y límite no negativo; fechas no ambiguas |
| `reference.lookup_temporal` | tabla | valor, campo | Campos escalares, referencia elegida, datos de referencia suministrados y atestados, vigencia compatible |

**Importante:** los evaluadores citados existen como **capacidades de planificación**. El planificador aún no invoca sus cálculos de calidad. Su incorporación en el backend deberá producir resultados separados `PASS/FAIL/UNKNOWN` y reportar denominadores, exclusiones y cobertura.

## 4. Tipos, formatos y estilos

- El contrato v0.2 cataloga 56 formatos/fuentes y 55 escenarios límite; el backend de preflight actualmente recibe solo DataFrames de pandas (`shape=table`). El resto es **hoja de ruta**, no soporte ejecutable.
- CSV leído con `qd plan` preserva todos los campos como texto y desactiva la conversión de marcadores por defecto. La fecha textual requiere un patrón explícito. XLSX conserva valores de pandas, **no** garantiza conservar el formato visual de Excel ni ceros generados exclusivamente por formato de celda.
- La definición de tipo físico, lógico y semántico sigue separada. Los tipos `variant/mixed` se admiten para resolver **aplicabilidad temporal**, pero evaluadores de unicidad/catálogo escalar no procesan automáticamente variantes heterogéneas.
- Fechas con múltiples patrones declarados, tiempos nulos en predicados de vigencia, solapamientos de variantes de igual prioridad o particiones no cubiertas quedan `AMBIGUOUS`.
- Los valores `NA`, `-999`, `NaN`, `0`, espacios y nulos físicos no se equiparan por defecto. La política de ausencia del contrato se aplicará en la etapa futura de ejecución, no se utiliza para transformar la entrada al planificador.
- Una regla `max_delay` entre `local_datetime` e `instant` exige una zona declarada, pero el control de consistencia entre relojes y los cambios DST requieren validaciones ampliadas en futuros evaluadores.

## 5. Evidencia

Una referencia mencionada en `references` **no aporta los datos**. La llamada Python acepta `evidence={"catalogo": {"values": [...], "version": "...", "verified": true}}`. `verified` es **una declaración de la persona/sistema que invoca**, no un proceso de autentificación externa realizado por la librería. Registrar fuente, hash o prueba independiente será obligatorio antes de atribuir nivel auditado a una medición externa.

Las versiones declaradas se comparan contra el catálogo del contrato. Cuando una regla temporal exige un período no cubierto por la vigencia de la referencia, permanece pendiente de evidencia.

## 6. Integración, pruebas y evolución

El componente expone `ApplicabilityPlanner.plan(contract, data={asset_id: DataFrame}, evidence={...}, suggest=False)` y devuelve `EvaluationPlan`, exportable a JSON o DataFrame.

Para evolucionar sin romper compatibilidad:

- Definir plugins `Capability` versionados con predicados de soporte claros, incluyendo backend y costes.
- Añadir adaptadores SQL/DuckDB/Polars/Arrow que produzcan planes equivalentes sobre el mismo contrato.
- Separar perfiles de entrada y validación de los datos originales; introducir `FieldResolver` con representaciones léxicas verificables.
- Incorporar pruebas de conformidad de resultado entre motores, grupos de partición y flujo incremental.
- Integrar ejecutores v0.2 con garantías de denominador, máscaras `PASS/FAIL/UNKNOWN`, trazabilidad e índices opt-in.
- Añadir contratos específicos de texto, PDF, imagen, audio y grafos **sin simular que cada uno es un DataFrame**.

## 7. Ejemplo

```python
import pandas as pd
from quality_dimensions import ApplicabilityPlanner, load_contract

c = load_contract('contracts/contrato_calidad.yaml')
frame = pd.read_parquet('mercado.parquet')
plan = ApplicabilityPlanner().plan(c, data={'mercado_laboral': frame}, suggest=True)
print(plan.to_frame())
plan.to_json('aplicabilidad.json')
```

En CLI:

```bash
qd plan datos.csv --contract contracts/contrato_calidad.yaml --asset mercado_laboral --output aplicabilidad.json
```

**Interpretación:** `READY` solo autoriza el siguiente paso de ejecución; `NEEDS_EVIDENCE` obliga a vincular una referencia. No se emite un porcentaje de calidad.
