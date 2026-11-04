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

## Motores

- **CSV/TSV/gzip:** lectura secuencial sin cargar tablas completas en pandas.
- **DuckDB:** consultas agregadas y procesamiento nativo para archivos y tablas compatibles.
- **Polars:** ejecución perezosa mediante expresiones nativas.
- **Spark:** procesamiento batch distribuido clásico con PySpark; requiere Java compatible.
- **pandas:** capa opcional para compatibilidad con análisis existentes.

No se garantiza equivalencia entre motores para reglas fuera del subconjunto
portable documentado. Los formatos del catálogo son posibilidades de extensión,
no una lista de adaptadores implementados.

## Ejecutar pruebas en Windows

Extrae el repositorio y ejecuta `Ejecutar_Pruebas_Completas.cmd` desde la carpeta
principal. Los reportes quedan en `reportes/`, que no se publica en GitHub.

Para instalar desde GitHub o publicar en PyPI consulta
[la guía de publicación](INSTRUCCIONES_PUBLICACION.md).
