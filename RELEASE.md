# QuaxDim release process

**Start with [INICIO_AQUI.md](INICIO_AQUI.md).** It explains GitHub setup, Trusted Publishing, TestPyPI and the PowerShell scripts.

**Source of truth:** `main` and tags/releases in GitHub. **Distribution:** PyPI. `release.yml` only publishes when a GitHub Release is published, its version equals `pyproject.toml`, mandatory engine tests run, and the protected `pypi` environment is approved.

## Initial release

- Run `scripts/01_Subir_a_GitHub.ps1` and verify `CI` on the new public repository.
- Create the `pypi` GitHub environment and configure a required reviewer.
- Register a pending PyPI Trusted Publisher: project `quaxdim`, owner `jaimevera1107`, repository `quaxdim`, workflow `release.yml`, environment `pypi`.
- Run `scripts/02_Publicar_Release.ps1` and authorize the protected environment after tests pass.
- Run `scripts/03_Verificar_PyPI.ps1` to verify installation.

## Subsequent releases

- `python tools/bump_version.py 0.8.0rc2` (for example).
- Update `CHANGELOG.md`, test, commit and push to `main`.
- Wait for successful CI for that commit.
- Run the release script; approve the `pypi` environment after the guarded validation job passes.

**Do not** reuse the same version number to replace an uploaded PyPI file. The workflow never uses long-lived PyPI API keys.
