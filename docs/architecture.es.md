# Arquitectura conceptual — contrato universal de dimensiones y métricas

**Versión de diseño:** 0.2.0 · **Naturaleza:** especificación, no implementación · **Idioma:** español

## 1. Principios irrenunciables

1. **Dimensión != métrica != regla != diagnóstico.** Una dimensión expresa un atributo de calidad; una métrica define una observación/medición; una regla determina cuándo y dónde medir; un diagnóstico interpreta las consecuencias.
2. **Formato != tipo físico != tipo lógico != significado != política de uso.** No inferir semántica exclusivamente del `dtype`.
3. **Calidad relativa al propósito:** un activo admite distintos perfiles según usuario, período, regulación y finalidad.
4. **Catalogar no implica medir:** toda dimensión puede tener estado `unmeasured` por falta de evidencia o método, sin falsa conformidad.
5. **No destructivo:** preservar valor original, representación original, orden, tipos y origen. Cualquier vista normalizada será derivada y trazable.
6. **Comparabilidad condicionada:** no agregar métricas incompatibles, contar repetidamente un mismo defecto ni contrastar puntuaciones obtenidas con contratos diferentes como si fueran homogéneas.
7. **Cobertura explícita:** divulgar qué activos, columnas, filas, períodos, particiones y dimensiones quedaron fuera o indeterminados.
8. **Trazabilidad y reproducibilidad:** versiones de configuración, código, catálogos, evidencias y decisiones; semillas y márgenes de error en métodos aproximados.
9. **Abierto por construcción:** nuevos tipos, fuentes, estructuras, dimensiones y evaluadores mediante plugins/adaptadores y registro de capacidades.
10. **Seguridad por defecto:** mínimos privilegios, ejemplos enmascarados, control de acceso, sin exponer valores sensibles en logs.

**Base doctrinal:** Mahanti (2019), capítulos 3–4 (dimensiones y medición objetiva/subjetiva) y capítulo 6 (evaluación, mejora y seguimiento). **Aporte de diseño propuesto:** los contratos, las máquinas de estado, la separación por capas, el planificador y los plugins.

## 2. Niveles jerárquicos del dato

- Valor/celda y representación léxica (por ejemplo `"00123"`)
- Campo/columna / propiedad / ruta JSON
- Registro / entidad / nodo / mensaje / evento
- Grupo o partición (país, año, fuente, cohorte)
- Tabla / documento / colección / serie / matriz / raster
- Conjunto de tablas / relaciones / grafo / modelo
- Activo, fuente o producto y versión
- Sistema o servicio (incluye disponibilidad, seguridad y recuperación)
- Uso/usuario/contexto organizacional

**Regla:** toda métrica declara nivel, población elegible, denominador, evidencia y política para indeterminados. Las métricas de sistema o usuario no deben simularse mediante `DataFrame`.

## 3. Modelo de representación: seis capas

| Capa | Pregunta | Ejemplo | Nunca asumir |
|---|---|---|---|
| 0. Transporte/contenedor | ¿De dónde viene? | Excel, SQL, HTTP, Kafka, PDF | Abrir = comprender |
| 1. Estructura | ¿Qué organización tiene? | Tabla, árbol, texto, grafo, stream | Todo son filas |
| 2. Almacenamiento | ¿Cómo guarda bits/bytes? | int64, UTF-8, binary, decimal128 | `object` = texto |
| 3. Lógico | ¿Qué valor representa? | Date, Enum, Struct, List, UUID | int = cardinal |
| 4. Semántico | ¿Qué significa? | código, monto, descripción | dtype = significado |
| 5. Contextual | ¿Bajo qué reglas? | catálogo vigente, precisión, idioma | Un estándar vale siempre |

La capa 0 puede tener propiedades de formato (delimitador, codificación, extensión, compresión, hoja, rango, dialecto SQL, metadatos de parquet). Las capas 2–5 se asignan **por campo y opcionalmente por partición temporal/fuente**, no solo por tabla.

## 4. Familias de datos y formatos previstos

**Estructurados tabulares:** pandas, Polars, PyArrow, DataFrame API; CSV/TSV/PSV, ancho fijo, Excel XLS/XLSX/XLSB, ODS, Parquet, Arrow IPC/Feather, ORC, Avro, SQL/OLAP, DuckDB, SQLite, PostgreSQL y similares; RDS/RData, Stata DTA, SPSS SAV, SAS, HDF5 y MATLAB como conectores opcionales.

**Jerárquicos y semiestructurados:** JSON, JSON Lines, XML, YAML, TOML, BSON, documentos NoSQL, GraphQL, mensajes Protobuf/Avro, objetos anidados. Los controles deben apuntar a rutas (`$.items[*].code`) y contar correctamente objetos/elementos ausentes y nulos.

**Eventos y flujos:** Kafka, archivos de log, CDC, colas, telemetría e IoT. Definir event time, ingestion time, processing time, orden, duplicados y tolerancia a eventos tardíos.

**Series y multidimensionales:** panel, series temporales, matrices, arrays N-dimensionales, tensores, series irregulares, datos espaciales y geográficos (GeoJSON, GeoParquet, GeoPackage, shapefile, GeoTIFF). Requieren granularidades y unidades propias.

**Grafos:** nodos, enlaces, propiedades y multiplicidad; métricas de conectividad, integridad de relaciones, identidad y topología.

**No estructurados y multimodales (mediante plugins):** texto, HTML, PDF, DOCX, OCR, imágenes, audio, video, anotaciones y embeddings. La validación requerirá métodos específicos y, cuando proceda, referencia externa o revisión humana. No prometer exactitud semántica automática universal.

**Desconocidos o propietarios:** registrar tipo/format `unknown` y evaluar solo controles seguros, o activar adaptadores personalizados; nunca inferir que todo soporte está implementado.

## 5. Tipos de valores: taxonomía extensible

### 5.1. Escalares y representaciones
- **Ausentes:** SQL NULL, JSON null, NA/NaN de librerías, marcador vacío, celda no existente, celda con fórmula vacía, `NaT`, sentinel (`-999`, `S/D`, `N/A`) cuando esté expresamente configurado. **NaN, infinito, cadena vacía, espacios y cero son fenómenos distintos.**
- **Booleanos:** true/false, S/N, yes/no, 0/1, ternarios, desconocido; contexto y vocabulario local.
- **Enteros:** con/sin signo, 8/16/32/64/128 bits, grandes enteros, overflow, códigos numéricos con ceros iniciales.
- **Decimales:** decimal de precisión/escala declaradas (finanzas), flotantes IEEE (32/64), float16/bfloat16 donde exista, `NaN`, ±∞, `-0.0`, error de representación y tolerancia; racional, complejo, moneda y porcentaje como extensiones lógicas/semánticas.
- **Texto:** Unicode, ASCII/Latin-1/UTF-8/UTF-16, normalización NFC/NFD, acentos, mayúsculas, caracteres invisibles, emojis, escritura RTL, bytes inválidos, control characters, idioma y transliteración; cadenas vacías vs ausentes.
- **Binario:** bytes, hashes, firmas, BLOB, imágenes comprimidas, geometrias WKB, formatos propietarios.
- **Temporal:** fecha local, hora local, fecha-hora ingenua, instante UTC, timestamp con offset, zona IANA, duración, período, intervalo, trimestre, semana ISO, año fiscal, fechas parciales, precision temporal, calendarios no gregorianos cuando se admita; marcas `event_at`, `observed_at`, `ingested_at`, `updated_at`, `effective_at`, `published_at`, `as_of`.
- **Identificadores:** UUID, ULID, teléfono, email, IP, URL/URI, RUT/NIT, código postal, códigos territoriales/catálogos; pueden ser `string` aunque parezcan números.

### 5.2. Compuestos y especiales
- Enum/categorical: nominal u ordinal; categorías abiertas/cerradas, jerarquías, códigos legados y correlativas.
- Array/list: orden y longitud variable, lista vacía vs nula, unicidad de elementos, homogeneidad.
- Struct/record: campos opcionales, requeridos, atributos extra y rutas anidadas.
- Map/dictionary: claves duplicadas, nulas o inválidas, diccionario de códigos, mapeos heterogéneos.
- Union/variant/mixed: múltiples tipos auténticos por observación y **variantes condicionadas por período o fuente**.
- Set, vector, sparse vector, tensor/matrix, geometría, raster, grafo y documento.
- `unknown`: tipo no identificado y razones de indeterminación.

### 5.3. Tipos semánticos (extensibles)
`id`, `code`, `label`, `description`, `name`, `free_text`, `enum`, `nominal`, `ordinal`, `count`, `measure`, `money`, `unit_measure`, `ratio`, `percentage`, `probability`, `currency_code`, `date_event`, `timestamp_ingest`, `duration`, `period`, `geo_code`, `latitude`, `longitude`, `geometry`, `postal_address`, `phone`, `email`, `url`, `json_document`, `binary_payload`, `image`, `audio`, `video`, `embedding`, `sensitive_identifier`, `pii`, `unknown`.

**No deben convertirse automáticamente**: `"00001" → 1`, `"1,234" → 1.234/1234`, `"01/02/2025" → fecha`, `"S" → True`, `"0000-00-00" → NA`, `"-1" → NA`. Son decisiones configurables y con evidencia.

## 6. Estilos, variaciones y representaciones mixtas

Cada campo acepta **variantes de representación** con predicados de activación por período, origen, versión, partición o discriminador. Ejemplos:

1. Salario numérico hasta 2018; bandas salariales desde 2019.
2. Códigos de actividad conforme a versión histórica del catálogo; varias versiones válidas de manera condicionada.
3. Fechas ISO, `DD/MM/YYYY`, texto de mes o seriales de Excel; la prioridad de análisis debe ser declarada y las ambigüedades reportadas.
4. Separadores `,` y `.` según región; espacios duros, signo menos Unicode, moneda al principio o al final.
5. Identificadores alfanuméricos con guiones, ceros o validación de dígito de control.
6. JSON parcialmente estructurado con claves faltantes, nulas o duplicadas.
7. Columnas con objeto heterogéneo por origen; no clasificar toda la columna como error sin discriminar caso.
8. Datos longitudinales con revisiones oficiales (vintage) y reglas vigentes en cada fecha.

Una variante tiene `when`, `logical_type`, `semantic_type`, `representation`, `missing`, `validators`, `version`, `priority`. Dos variantes solapadas deben generar ambigüedad, no una elección silenciosa salvo precedencia explícita.

### 6.1. Estilos de representación (capa separada)

En este diseño, **estilo** puede referirse a convenciones de nombres y a propiedades de presentación. El motor debe conservarlas y, solo cuando una regla lo exija, convertirlas en criterios de calidad:

- **Encabezados y nombres:** `snake_case`, `camelCase`, `PascalCase`, `SCREAMING_SNAKE_CASE`, minúsculas, abreviaturas, tildes, espacios y caracteres reservados; preservación de colisiones después de normalizar (`A B` vs `A_B`).
- **Aspecto textual:** mayúsculas/minúsculas, acentos, puntuación, saltos de línea, tabulaciones, ancho de caracteres, emoji, transliteración, normalización Unicode, idiomas y collation.
- **Números y fechas:** formato mostrado vs valor almacenado (por ejemplo porcentaje `15%` frente a `0.15`, fecha mostrada `31/12/25` frente a serial, moneda y símbolo regional).
- **Hojas de cálculo:** nombre/orden de hojas, rangos, filas o columnas ocultas, celdas combinadas, formatos personalizados, color, estilo, validación de datos, comentarios, nombres definidos y fórmulas con valor cacheado. No confundir una celda visualmente vacía con una celda realmente nula ni ignorar una fila por estar oculta.
- **SQL:** dialecto, nombres entrecomillados, sensibilidad a mayúsculas, `collation`, aliases, estilo de sangría y convenciones de consulta. Los controles de estilo SQL se ejecutan en un módulo separado del diagnóstico de datos.
- **Documentos y multimodales:** títulos, secciones, anotaciones, metadatos y elementos visuales. El aspecto estético no constituye automáticamente calidad del dato.

**Política:** `presentation_metadata` y `display_format` se preservan por el adaptador cuando existan; se mide su conformidad únicamente con requisitos específicos. El usuario podrá decidir si la presentación tiene significado de negocio (por ejemplo, color de celdas como semáforo), caso en que se crea un campo derivado trazable y explícito.

## 7. Dimensión, métrica y regla: contrato formal

### 7.1. Dimensión

- `id`, `version`, nombre, sinónimos y descripción operacional.
- `framework` y referencias metodológicas.
- `family`, `related_dimensions`, `inverse_of`, `overlaps_with`, `depends_on` (no contabilidad doble).
- `levels` elegibles: value, field, record, entity, table, dataset, collection, stream, system, use.
- `evidence_modes` recomendados: internal, contextual, external, subjective, mixed.
- `default_metrics`: identificadores, sin suponer que siempre aplican.

### 7.2. Métrica

- `id`, `version`, `dimension_ids`, nombre, propósito, clase `conformance|descriptive|anomaly|survey|system`.
- `target_levels`, requisitos de tipo/semántica, parámetros.
- `eligibility` y definición de población; `null_policy`, `unknown_policy`, `exceptions`.
- `formula` o `evaluator` (referencia a evaluador registrado, no código arbitrario).
- `numerator`, `denominator`, `unit`, `direction` (higher_is_better, lower_is_better, target, neutral).
- `measurement_mode`, evidencias requeridas y método de incertidumbre.
- `aggregation`, `overlap_group`, comparabilidad entre períodos.
- `complexity_hint`, capacidad de exacto/aproximado/incremental, sensibilidad y política de divulgación.

### 7.3. Regla

- `id`, `version`, `metric_id`, `asset_id`, targets y expresión declarativa segura.
- `when`: condiciones por fila, grupo, fuente o período.
- `expectation`: rangos, catálogos, regex, relaciones, ventanas temporales, composición de claves.
- `threshold`, `severity`, `failure_action`, `on_unmeasured`.
- `references` (hash, versión, fecha de vigencia), dependencias y precedencia.
- `evidence_scope`, `sampling` si aplica, políticas de excepciones.

### 7.4. Perfil de evaluación

- `purpose`, `population`, `as_of`, `analysis_mode` (`explore|evaluate|monitor|audit`).
- `accuracy_mode` (`exact|approximate|auto`); `budgets` memoria/tiempo/I/O.
- `missing_default`, `ambiguous_parse` (`indeterminate|error|explicit_priority`).
- `data_disclosure` (`no_raw_values|masked_samples|authorized_raw`).
- `sampling` y `seed`, versiones, salidas, requisitos de aprobación.

## 8. Ciclo de evaluación

`Registrar fuentes → inspeccionar fidelidad de lectura → perfilar estructura y tipos → proponer semántica → determinar aplicabilidad → validar dependencias/evidencias → planificar cálculos → evaluar → reconciliar hallazgos → medir cobertura → emitir informes → monitorear y versionar`.

La primera fase debe comprobar integridad de ingestión: recuentos de filas, bytes, delimitadores, encoding, hojas, fórmulas, objetos extra, truncamientos, duplicación en importación. Un cargador defectuoso puede inventar problemas o borrar información antes del perfilamiento.

## 9. Máquina de estados y denominadores

### 9.1. Estado por observación

- `pass`: cumple.
- `fail`: incumple.
- `indeterminate`: candidata a evaluación, pero evidencia/interpretación insuficiente.
- `excluded`: fuera del denominador por una exclusión documentada.

### 9.2. Estado por regla y dimensión

- `evaluated`, `partially_evaluated`, `not_applicable`, `missing_evidence`, `not_supported`, `not_executed`, `technical_error`, `blocked_dependency`.
- `not_applicable` **no es** `pass`; `technical_error` **no es** `pass`.

### 9.3. Denominadores

`eligible = passes + fails + indeterminates`

`assessed = passes + fails`

`conformance = passes / assessed` **solo si assessed > 0**.

`evaluable_coverage = assessed / eligible` **solo si eligible > 0**.

Mostrar ambos indicadores siempre que sea pertinente. Una tasa de conformidad del 100% con cobertura evaluable del 1% no debe presentarse como calidad comprobada del total.

El sistema registrará unidad del denominador: filas, entidades, valores, fechas, grupos, documentos, eventos, relaciones o respuestas de encuesta.

## 10. Aplicabilidad e incertidumbre

Una métrica es **aplicable** solo si concurren: a) compatibilidad técnica, b) pertinencia semántica, c) población definible, d) evidencia suficiente, e) método implementado.

La librería distinguirá `applicability` de `executability`. Una dimensión puede ser relevante, pero no ejecutable por ausencia de fuentes oficiales o permisos. La detección de anomalías estadística nunca certificará exactitud sin referencia.

Los métodos aproximados declararán `estimation_method`, tamaño muestral, probabilidad de inclusión, semilla, error/margen cuando exista y posibilidad de sesgo. Los resultados estimados no deben sustituir silenciosamente controles obligatorios exactos.

## 11. Resultado de ejecución: evidencia auditable

Cada `RuleResult` tendrá: identificadores de ejecución/regla/métrica/dimensión, estado, población elegible, evaluable y excluida, aprobados/reprobados/indeterminados, tasa, cobertura, umbral, unidad, evidencia y referencia, condiciones efectivas, mensaje estructurado, gravedad, histograma de fallos, ejemplos permitidos, hash de fuente/configuración y tiempo.

Cada `Finding` tendrá ubicación o identidad lógica, causa probable separada de causa confirmada, validez temporal, fuente y referencia utilizadas, sensibilidad, solapamientos y tratamiento recomendado.

Cada `RunResult` tendrá metadatos del entorno, impacto de ingestión, matriz de aplicabilidad, registros de cambios y versión de todos los artefactos. No consolidar resultados de reglas incompatibles; un índice compuesto solo con función, dominio, pesos y justificación explícita.

## 12. Rendimiento y cumplimiento de capacidades

El motor usará un planificador por capacidades: `read`, `inspect`, `profile`, `evaluate`, `monitor`. Para cada adaptador se declarará si puede conservar tipos originales, filtros pushdown, lectura por chunks, proyecciones de columnas, evaluación en origen, streaming, estadísticas aproximadas y auditoría reproducible.

Estrategias: evaluación perezosa, reutilización de nulos y cardinalidad, filtros por partición, un único scan por bloque de reglas, precompilación, hashing para identidad, joins verificados para integridad, ejecución vectorizada, perfiles incrementales y control de backpressure en streams.

En estructuras anidadas, el denominador debe especificar si se cuentan padres, hijos o ambos. En streams la unidad puede ser evento o ventana temporal. La lectura SQL debe utilizar consultas parametrizadas y evitar ejecución de expresiones arbitrarias o secretos en logs.

## 13. Catálogo dimensional completo

El paquete 0.1.0 registra treinta conceptos de Mahanti y siete adiciones ISO: el **número de dimensiones del catálogo no equivale al número de evaluadores implementados**. Cada entrada se mapeará a métricas específicas y requerimientos de evidencia.

El diseño conserva que el libro no establece una taxonomía universal cerrada (capítulo 3, pp. 126–128). Se recomienda permitir nuevos `frameworks` y `dimensions` por espacio de nombres, por ejemplo `mahanti.accuracy`, `iso25012.accuracy`, `custom.geospatial_topology`, sin afirmar equivalencia exacta solo porque los nombres se parecen.

## 14. Casos donde NO se debe aparentar calidad

- Un catálogo no disponible: `missing_evidence`, no `validity=100%`.
- Dos fuentes con idéntico error: `consistency=pass` no autoriza `accuracy=pass`.
- Valor `NaN` en un campo decimal: distinguir nulo, NaN físico y valor inválido según contrato.
- Dataset vacío: no reportar completitud 100% sin analizar universo esperado.
- Datos repetidos intencionalmente (transacciones): no deduplicarlos por coincidencia de monto.
- Parquet con timestamp local: no asumir instante UTC sin información adecuada.
- XLSX con fórmula y valor cacheado obsoleto: diferenciar texto de fórmula, evaluación y valor disponible.
- Datos agregados: no imponer integridad fila-a-fila de una fuente transaccional.
- Fecha de captura posterior a fecha de evento: no diagnosticar retraso sin SLA y semántica de reloj.
- Muestra sesgada: una tasa de defectos muestral no describe automáticamente todo el universo.
- Trazabilidad parcial: no certificar linaje completo.
- Encuesta de un usuario: reputación subjetiva no equivale a fiabilidad objetiva.
- Dato protegido por permisos: no leer ni mostrar valores en reportes no autorizados.

## 15. Política de evolución

- Versionar el esquema de contratos (SemVer), los evaluadores y las referencias por separado.
- Conservar migradores explícitos de versiones anteriores; alertar sobre cambios que afectan semántica.
- Registrar capacidades implementadas en `capabilities()`; jamás anunciar como soportadas las previstas.
- Validar `schema` sintáctico y después coherencia semántica entre IDs, tipos, dependencias, rangos, referencias y estados.
- Mantener una batería de conformidad con casos de borde y casos adversariales para cada adaptador y tipo.

## 16. Prioridad de implementación

P0: núcleo de contratos, tipos (incluido mixed/unknown), null policy, adaptador pandas, CSV/Parquet/Excel, métricas básicas, resultados auditable, tests.

P1: variantes temporales, tipos semánticos, catálogos vigentes, SQL/Polars/Arrow, checks multi-tabla, perfilado y motor de evidencia.

P2: JSON/XML anidados, streams, geoespaciales, grafos, encuestas/gobernanza y métricas contextuales avanzadas.

P3: contenido no estructurado y multimodal, motor de plugins más amplio y ejecución distribuida.

**Criterio de aceptación central:** amplitud de taxonomía + honestidad sobre capacidades reales + resultados reproducibles. La posibilidad de representar un caso en el contrato es distinta de la disponibilidad de un evaluador que lo procese.
