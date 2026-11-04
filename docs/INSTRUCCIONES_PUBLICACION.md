# Guía de publicación y validación

## 1. Repositorio nuevo

El nombre previsto es `jaimevera1107/quaxdim`. Si cambia el propietario o
el repositorio, actualizar los enlaces `README.md`, `pyproject.toml` y GitHub Actions.

```powershell
git init -b main
git add .
git commit -m "chore: prepare QuaxDim 0.8.0rc1 public release"
git remote add origin https://github.com/jaimevera1107/quaxdim.git
git push -u origin main
```

**Antes**, crea el repositorio vacío en GitHub bajo la cuenta apropiada.
No subas datos personales, muestras reales sensibles, credenciales ni archivos `.env`.

## 2. Validar antes de publicar

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e ".[dev,duckdb,polars,parquet]"
python -m pytest -q tests tests_validation tests_public
python -m build
python -m twine check dist/*
```

Para validar Spark, instalar Java compatible y ejecutar
`python -m pip install -e ".[spark,test]"` seguido de las pruebas de integración.
Ejecutar también `Ejecutar_Pruebas_Completas.cmd` para un reporte consolidado.

## 3. PyPI: Trusted Publisher sin tokens permanentes

En la cuenta personal de PyPI, abre **Publishing** y agrega un *pending publisher*
para el nombre `quaxdim`, utilizando:

- Owner: `jaimevera1107`
- Repository: `quaxdim`
- Workflow: `release.yml`
- Environment: `pypi`

Un pending publisher no reserva el nombre. PyPI confirmará la disponibilidad
definitiva al primer intento de publicación. La configuración debe concordar
exactamente con el repositorio que se vaya a usar.

En GitHub, crea el environment `pypi` (Settings → Environments),
opcionalmente con revisores obligatorios.

## 3.1. Prueba opcional en TestPyPI

Registra un pending publisher en **test.pypi.org** con owner `jaimevera1107`,
repository `quaxdim`, workflow `testpypi.yml` y environment `testpypi`.
Crea el environment en GitHub y ejecuta manualmente el workflow
`Publish to TestPyPI`. Esta prueba no reserva el nombre en PyPI principal.

## 4. Publicación

- Verifica que GitHub Actions CI pase con todas las pruebas necesarias.
- Revisa la versión en `pyproject.toml` y `src/{quaxdim,quality_dimensions}/__init__.py`.
- Crea un tag `v0.8.0rc1` en el commit aprobado.
- Publica una **GitHub Release** con ese tag. Esto activa `release.yml`.
- El workflow instala, prueba, construye, verifica e intenta publicar en PyPI.
- Comprueba después que `pip install quaxdim==0.8.0rc1` funcione en otro entorno.

PyPI no admite reutilizar el mismo número de versión si ya se publicó.
Si falla una publicación, incrementa el candidato de versión antes de reintentar
si los artefactos ya fueron aceptados por el índice.

## 5. Limitaciones antes del lanzamiento estable

La paridad completa entre motores y el rendimiento de Spark distribuido
requieren validación real. El subconjunto portable no implementa todas las
dimensiones del catálogo. Mantener la etiqueta **release candidate** y no
promover a `1.0` sin nuevas pruebas de cobertura y garantías.
