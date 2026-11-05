# Changelog

## Unreleased

- **Breaking (never published):** a single `quaxdim` package. The earlier
  `quality_dimensions` import path and the `qd` command were removed; the
  package had not been released to PyPI, so no migration is needed.
- pandas and numpy are now truly optional. Previously the README quickstart
  commands `native-plan` / `native-execute` and several public names failed
  on a core install because modules imported pandas at import time. They are
  bound lazily and raise an `ImportError` naming the `pandas` extra.
- Version is defined once in `quaxdim/_version.py`; `pyproject.toml` reads it.
- Ship `py.typed`; remove the unused `legacy` extra and the duplicated
  contract JSON schema (the packaged copy is the single source).
- Example contracts renamed to `quality_contract.{json,yaml}`.
- CI: add a core-only (no pandas) job, wheel-content checks and an
  informational lint/type-check job.

## 0.8.0rc1 — 2026-11-04

- Publish under the **QuaxDim** brand with owner-supplied artwork.
- Add official `quaxdim` Python namespace and CLI command; retain legacy
  `quality_dimensions` imports and `qd` alias.
- Add packaging metadata, source/wheel manifest, CI, release workflow with
  PyPI OIDC Trusted Publishing, Windows test kit and bilingual docs.
- Preserve the experimental v0.8 core: streamed discovery, native optimized
  planner and portable evaluation; no claim of full dimension coverage.
- No changes intended to the underlying metric semantics.

## 0.8.0.dev0 — pre-publication build

- Native execution planning for CSV, DuckDB, Polars and Spark with explicit
  approvals and resource limits; broader discovery recommendations.
- Optional pandas compatibility layer.

## Earlier experimental builds (0.1–0.7.1)

Initial dimension catalog, contracts, applicability planner, evaluators,
recommendation system and native adapters. See [docs](docs/) for details.
