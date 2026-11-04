# Publicar QuaxDim en GitHub y sincronizar versiones con PyPI

Esta carpeta es el **repositorio Git listo para subir**. La publicación pública no se ha ejecutado por ti.

## A. Preparación en Windows (una vez)

1. Instala Git y GitHub CLI, si no los tienes:
   ```powershell
   winget install --id Git.Git --exact
   winget install --id GitHub.cli --exact
   ```
2. Abre **una terminal PowerShell nueva** e inicia sesión: `gh auth login`.
3. Abre PowerShell en la carpeta de este proyecto, donde está `pyproject.toml`.
4. Ejecuta:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\01_Subir_a_GitHub.ps1
   ```
   Crea el repositorio **público** `jaimevera1107/quaxdim` si aún no existe; sube `main` y crea los entornos `pypi` y `testpypi`. **No publica en PyPI**.

## B. Autorizar PyPI (una sola vez)

1. En GitHub: **Settings > Environments > pypi**, activa `Required reviewers` y designa un revisor autorizado. Verifica que el flujo `CI` de `main` pase.
2. En tu cuenta de [PyPI > Publishing](https://pypi.org/manage/account/publishing/) crea un **pending Trusted Publisher** con exactamente estos datos:

   | Campo PyPI | Valor |
   | --- | --- |
   | PyPI project name | `quaxdim` |
   | GitHub owner | `jaimevera1107` |
   | Repository | `quaxdim` |
   | Workflow filename | `release.yml` |
   | Environment | `pypi` |

3. No necesitas contraseñas ni tokens en GitHub. El nombre PyPI no queda reservado hasta el primer envío correcto.

## C. Publicar la primera versión

Después de verificar el CI y el publicador de confianza:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\02_Publicar_Release.ps1
```

El script exige el último `main` sincronizado, pruebas CI aprobadas para ese commit y confirmación humana. Crea el tag `v0.8.0rc1` y una GitHub Release marcada como *prerelease*.

La GitHub Release activa `.github/workflows/release.yml`: ejecuta las pruebas incluyendo DuckDB/Polars/Spark, construye las distribuciones y se detiene para la aprobación del entorno `pypi`. Al aprobarse, carga los artefactos a PyPI mediante OIDC.

Comprueba en [GitHub Actions](https://github.com/jaimevera1107/quaxdim/actions/workflows/release.yml) y [PyPI QuaxDim](https://pypi.org/project/quaxdim/).

## D. Comprobar desde una instalación limpia

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\03_Verificar_PyPI.ps1
```

O directamente: `python -m pip install quaxdim==0.8.0rc1`.

**Nota:** `0.8.0rc1` es una versión preliminar. Para instalar sin fijar versión, `pip install --pre quaxdim` si solo existen prereleases o se desea una versión preliminar. Es más seguro fijar `==0.8.0rc1` para esta primera prueba.

## E. Cada versión posterior (sin configurar PyPI de nuevo)

1. Cambia la versión, por ejemplo:
   ```powershell
   python tools/bump_version.py 0.8.0rc2
   ```
2. Añade las notas de la versión nueva a `CHANGELOG.md` y ejecuta pruebas.
3. `git add .; git commit -m "release: QuaxDim 0.8.0rc2"; git push origin main`.
4. Verifica el CI y ejecuta `scripts/02_Publicar_Release.ps1`.
5. Aprueba el entorno `pypi` después de que las pruebas de release pasen.

GitHub → PyPI se sincroniza **por release/versiones, no por commits**. Nunca se reutilizan archivos/versiones publicados en PyPI; si hay una corrección, incrementa la versión.

## F. Ensayo opcional en TestPyPI

En https://test.pypi.org/manage/account/publishing/ registra un pending publisher separado, con `quaxdim`, owner `jaimevera1107`, repo `quaxdim`, `testpypi.yml` y environment `testpypi`. Ejecuta el workflow manual **Publish to TestPyPI** en GitHub Actions.

TestPyPI no garantiza la reserva del nombre en PyPI principal. Para instalar desde TestPyPI sin depender de que sus dependencias estén allí: instala primero `PyYAML jsonschema` desde PyPI, luego instala QuaxDim desde TestPyPI con `--no-deps`.

## G. Precauciones

- El logo se conserva como archivo original y no debe redistribuirse como recurso genérico fuera de esta marca.
- No subas datos privados, secretos ni archivos reales de prueba.
- Los trabajos de Spark en CI necesitan Java y tiempo adicional. Si fallan, PyPI **no debe** publicarse.
- `PyPI` es un repositorio de artefactos, **no** un espejo del repositorio GitHub.
- En `0.8.0rc1`, 37 dimensiones constituyen un catálogo conceptual, no 37 evaluadores completos.
- Revisa el nombre del autor y el titular del copyright en `pyproject.toml` y `LICENSE` antes del primer envío.

Fuentes:
- https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/
- https://docs.pypi.org/trusted-publishers/using-a-publisher/
- https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/
