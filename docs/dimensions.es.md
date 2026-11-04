# Visión multidimensional de calidad de datos

Esta guía utiliza como referencia principal a Rupa Mahanti (2019), *Data Quality: Dimensions, Measurement, Strategy, Management, and Governance*, capítulos 3 y 4. Los nombres en español son traducciones de trabajo y la agrupación temática es una propuesta de arquitectura para la librería, no una clasificación literal del libro. La tabla 3.5 identifica 30 conceptos (incluida cardinalidad); siete entradas adicionales amplían el catálogo con características de ISO/IEC 25012.

**Punto metodológico:** una dimensión es un concepto de calidad; una regla es una afirmación evaluable; una métrica es la operacionalización con su denominador; la evidencia la valida; un reporte comunica el resultado. No deben confundirse.

| Identificador | Dimensión | Familia | Naturaleza orientativa | Posible evaluación / requisito |
|---|---|---|---|---|
| `accessibility` | Accesibilidad | access | subjective | assessment + prueba de acceso |
| `accuracy` | Exactitud | intrinsic | objective | custom + referencia de verdad |
| `believability` | Verosimilitud | trust | subjective | assessment con encuesta |
| `credibility` | Credibilidad | trust | subjective | assessment sobre proveedor |
| `trustworthiness` | Confiabilidad de la fuente | trust | subjective | assessment + linaje/fuentes |
| `timeliness` | Oportunidad | temporal | objective | delay_days |
| `currency` | Actualidad | temporal | objective | freshness_days |
| `volatility` | Volatilidad | temporal | objective | custom + histórico (pendiente) |
| `correctness` | Corrección | intrinsic | objective | custom + fuente de verdad |
| `precision` | Precisión | intrinsic | objective | custom + tolerancias/unidades (pendiente) |
| `reliability` | Fiabilidad | trust | mixed | assessment + pruebas longitudinales |
| `consistency` | Consistencia | integrity | objective | compare / sum_equals |
| `integrity` | Integridad relacional | integrity | objective | foreign_key |
| `completeness` | Completitud | intrinsic | objective | required / schema_columns / coverage |
| `validity` | Validez o conformidad | intrinsic | objective | dtype / allowed / regex / between |
| `interpretability` | Interpretabilidad | usability | subjective | assessment + glosario |
| `security` | Seguridad | access | objective | prueba de acceso externa (pendiente) |
| `conciseness` | Concisión | usability | subjective | assessment con usuarios |
| `reputation` | Reputación | trust | subjective | assessment de usuarios |
| `uniqueness` | Unicidad | integrity | objective | unique |
| `duplication` | Duplicación | integrity | objective | unique + política de identidad |
| `cardinality` | Cardinalidad | structure | objective | cardinality |
| `relevance` | Relevancia | usability | mixed | assessment + benchmark de propósito |
| `manipulability` | Facilidad de manipulación | usability | subjective | assessment con tareas |
| `objectivity` | Objetividad | trust | subjective | evaluación de sesgo (pendiente) |
| `traceability` | Trazabilidad y linaje | trust | mixed | metadata_fields + historial externo |
| `data_specification` | Especificación de datos | structure | objective | schema_columns / metadata_fields |
| `coverage` | Cobertura | structure | objective | coverage con universo esperado |
| `granularity` | Granularidad | structure | objective | unique con clave declarada |
| `redundancy` | Redundancia | structure | objective | metadatos de inventario (pendiente) |
| `compliance` | Cumplimiento | governance | context-dependent | prueba de conformidad externa (pendiente) |
| `confidentiality` | Confidencialidad | access | objective | auditoría de permisos (pendiente) |
| `efficiency` | Eficiencia | system | objective | benchmark recursos/latencia (pendiente) |
| `understandability` | Comprensibilidad | usability | mixed | assessment + glosario |
| `availability` | Disponibilidad | system | objective | SLA externo (pendiente) |
| `portability` | Portabilidad | system | objective | prueba de migración (pendiente) |
| `recoverability` | Recuperabilidad | system | objective | prueba de restauración (pendiente) |

## Reglas imprescindibles

1. **No todo es automático.** Un DataFrame no determina por sí solo qué valores son verdaderos, relevantes, objetivos o confiables. La calidad objetiva suele exigir un contrato, una referencia o un universo; la subjetiva requiere instrumentos y contexto.
2. **Cobertura distinta de completitud.** Tener todas las columnas llenas no demuestra que estén representadas las unidades esperadas. `coverage` evalúa la proporción de entidades de un universo de referencia encontradas en los datos.
3. **Precisión distinta de exactitud.** Muchos decimales no garantizan acercamiento al valor verdadero. La primera requiere resolución y tolerancia; la segunda, un patrón de verdad.
4. **Duplicación distinta de cardinalidad.** Pocas categorías pueden ser legítimas en una variable; la violación de unicidad depende de la clave del registro y de la granularidad esperada.
5. **Actualidad distinta de oportunidad.** Dato reciente no necesariamente llegó en plazo y un dato entregado oportunamente puede ser obsoleto para cierto uso.
6. **Nunca calcular un promedio universal por defecto.** Se requiere escoger dimensiones y pesos, considerando impacto, criticidad, dependencia, sector y uso; un promedio simple puede engañar.
7. **Las dimensiones interactúan.** Mejorar un atributo puede perjudicar otro (p. ej., seguridad y accesibilidad); documentar compromisos y prioridades es parte de la gestión.
8. **Hay que admitir la evidencia ausente.** `NOT_EVALUATED` es distinto de `NOT_APPLICABLE`, de `SKIP` (cero casos elegibles) y de `ERROR` (no se pudo ejecutar). Nunca transformar errores en éxitos.
9. **Ninguna evaluación modifica los datos.** Imputaciones y reparaciones necesitan otro módulo optativo con auditoría, versiones y pruebas de regresión.
10. **Evaluación por escala.** Validar la métrica al nivel apropiado: celda, registro, columna, tabla, población, fuente, proceso y sistema. Una misma regla no debe extrapolarse automáticamente entre escalas.

## Estrategia de desarrollo

- **Etapa inicial (este ZIP):** catálogo 37, evaluadores explícitos en pandas, soporte a referencias externas, evaluaciones subjetivas aportadas por el usuario, y reporte auditable. No se implementan todos los medidores todavía.
- **Etapa de expansión:** catálogo de reglas por dimensión, comprobaciones distribucionales, comparaciones entre cortes, plazos variables, perfiles de dominios, reglas declarativas entre múltiples tablas y multiescala.
- **Etapa operativa:** backends Polars, SQL y DuckDB; lectura por lotes; caché, planes de ejecución, optimización por columna; validaciones diferenciales/temporales; conectores para evidencias de plataforma.
- **Etapa de gobernanza:** catalogación de activos, linaje, jerarquía de controles, pesos justificados, coste del error, auditoría de decisiones, dashboards y seguimiento continuo.

## Fuentes

- Mahanti, R. (2019). *Data Quality: Dimensions, Measurement, Strategy, Management, and Governance*, pp. 77, 126–132. ASQ Quality Press. https://asq.org/quality-press/display-item?item=H1552
- ISO/IEC 25012:2008. https://www.iso.org/standard/35736.html
- ISO/IEC 25024:2015. https://www.iso.org/standard/35749.html
