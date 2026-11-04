# Validación inicial

Versión experimental 0.1.0, backend pandas. No equivale a validación de producción.

- 19 pruebas unitarias: superadas localmente con `PYTHONPATH=src python -m pytest -q`.
- Se cubren: catálogo 30+7, valores nulos tipados/sentinelas, unicidad por clave (incluidos índices duplicados y filtros), omisión de vacíos, ausencia de fuente de referencia, cobertura de universo, evidencia de evaluación subjetiva, columnas 100% vacías, error de configuración, tipos mixtos, controles de esquema y metadatos, peso explícito, no aplicabilidad, y contabilidad numerador/denominador.
- Microprueba local: 200.000 registros sintéticos, 5 reglas simples vectorizadas, 0,195 segundos. Esta cifra **no** es una garantía ni un benchmark reproducible entre equipos. No cubre regex, grandes combinaciones de referencias, IO, latencia SQL o escenarios de memoria limitada.

## Trabajo pendiente para publicar

Pruebas de propiedades, casos límite extensivos (tipos Arrow/Pandas nullable, locale, timezones, fechas ambiguas), fuentes grandes, concurrencia, distribución en varios procesos, streaming, privacidad de muestras de errores, seguridad de patrones regex, trazabilidad de evidencias, backend Polars/SQL, evaluación de marcos subjetivos, versionado de contratos y métricas en producción.
