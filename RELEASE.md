# Publishing QuaxDim

GitHub `main` is the source of truth. GitHub Releases trigger `.github/workflows/release.yml`.
The release workflow validates core and optional engines, builds distributions and publishes to
PyPI using a short-lived OIDC token after approval of the protected `pypi` environment.
**Do not push a tag or create a release until CI is green.**

## Configure once

1. In GitHub, create the `pypi` environment with required reviewer approval.
2. On PyPI, register the Trusted Publisher for project `quaxdim`:
   owner `jaimevera1107`, repository `quaxdim`, workflow `release.yml`, environment `pypi`.
3. Check [GitHub Actions](https://github.com/jaimevera1107/quaxdim/actions/workflows/ci.yml).

## Release

1. Update the version (only in `src/quaxdim/_version.py`) using `python tools/bump_version.py 0.8.0rc2` (example), then update `CHANGELOG.md`.
2. Run `python -m pip install -e ".[test]"` and `python -m pytest -q tests tests_validation tests_public`.
3. Run `python tools/check_release.py`, then commit and push to `main`.
4. Verify CI passed for the exact commit.
5. Run `powershell -ExecutionPolicy Bypass -File .\scripts\publish_release.ps1`
   or create an annotated tag and publish a matching GitHub Release manually.
6. Approve the `pypi` environment only after the release validation job passes.
7. Check installation with `powershell -ExecutionPolicy Bypass -File .\scripts\verify_pypi.ps1`.

For TestPyPI, configure a separate `testpypi` environment and Trusted Publisher for
`.github/workflows/testpypi.yml`, then trigger that workflow manually.

Version numbers are immutable once published to PyPI. The old one-time GitHub repo creation
helper and machine-specific QA launchers are intentionally excluded from the repository.
