# Changelog

## Unreleased

### Audit fixes — Phase 6 (English messages and infinity policy)

- **Behaviour change:** every user-facing string produced by the library
  is now English: recommendation titles, rationales, caveats and
  prerequisites of both advisors, and the dimension catalogue
  (`DimensionSpec.name_es` is now `name`; `dimension_es` in legacy result
  dictionaries is now `dimension_name`). Code that matched Spanish titles
  must be updated.
- `missing_policy.infinities_are_invalid` is enforced: when true, an
  infinite value in a float field fails every rule on that field on
  DuckDB, Polars and Spark; the pandas planner blocks such rules as
  `UNSUPPORTED` instead of passing `+inf` silently. Default stays false
  (infinities compare as numbers).

### Audit fixes — Phase 5 (documentation and validation)

- New `docs/statistical_validity.md`: identification bounds vs confidence
  intervals, per-row uniqueness, thresholds as census proportions, missing
  values as declared policy, quantified discovery evidence, drift as a
  heuristic, no composite index, tested engine equivalence.
- New `benchmarks/discovery_accuracy.py` with ground truth: discovery
  precision/recall under injected violations and HyperLogLog bias/RMSE
  versus theory, with a smoke test in `tests_validation/`.
- README capability table aligned with what is tested.

### Deferred (not in this branch)

- Splitting `advanced_discovery.py` by source (`sources/csv.py`, ...): a
  pure code move, left for its own commit so that it does not mix with
  semantic changes.
- Spark execution could not be run in the fixing environment; the Spark
  paths changed only where shared code changed and must be run in CI.

### Audit fixes — Phase 4 (API robustness and engineering)

- `QualitySuite.add()` validates before mutating; a rejected check leaves
  the suite unchanged (audit H3). `schema_columns` reports duplicate
  column names instead of hiding them behind set comparison (audit H4).
- The no-raw-values test excludes the report timestamp, whose
  microseconds could contain the probed value (audit H8).
- Reports carry `library_version` and a `sources` audit trail (file path,
  size and modification time, or in-memory type) next to
  `contract_sha256`; thresholds were already per rule.
- Every report, contract and suite file is written atomically (temporary
  file + `os.replace`): a crash mid-write leaves the previous file intact.
- `quaxdim --version`. Known errors still exit with code 2 and a failing
  native execution with code 1.
- Optional dependencies get upper bounds (`pandas<3`, `duckdb<3`,
  `polars<3`); Polars 1.x and 2.x and DuckDB 2.0 are tested.
- Verified: the wheel and the sdist do not ship `assets/` (the restricted
  logo stays out of PyPI).

### Audit fixes — Phase 3 (discovery heuristics)

- New `heuristics` module: one set of name semantics and thresholds for
  both advisors (`discover` and `discover_advanced`), exposed as
  `HeuristicThresholds` and `DiscoveryBudget.thresholds`. The divergent
  values are unified: closed-domain share 0.25 (was 0.25 / 0.35), code
  width up to 40 (was 30 / 40).
- Semantic roles and start/end pairs match name **tokens** (snake_case,
  camelCase, spaces, accents folded) instead of substrings. `country`,
  `account_balance`, `tamaño_empresa` and `candidate_id` no longer get
  wrong roles, and start/end fields pair only when they share the event
  root (audit A3).
- Key candidates use a denominator without blanks, so a few blanks no
  longer hide a unique key (audit A1).
- The HLL uniqueness hypothesis tolerates `z · relative_std_error` below a
  ratio of one, following `hll_precision`, instead of a fixed
  `[0.94, 1.06]` window (audit A2).
- Sample-based evidence is quantified: rule-of-three upper bounds for
  patterns and ISO dates, Good–Turing unseen-category share for domains,
  minimum detectable duplicate share for composite keys, and repeated
  determinants plus the g3 error for functional dependencies, which now
  need at least three repeated determinants (audit M1–M3).
- ISO date hypotheses need 30 matching values for `high` confidence
  (three used to suffice).

### Audit fixes — Phase 2 (reading and engine parity)

- New `asset.read_options` (schema addition, backward compatible):
  `delimiter`, `encoding`, `quote_char`, `header`, `malformed_rows`,
  `max_field_chars`. Discovery records the dialect it used and
  `build_reviewed_contract` copies it into the contract, so evaluation reads
  the same table (audit H6). Previously `discover_advanced(csv_delimiter=)`
  had no counterpart in `evaluate_optimized`.
- DuckDB reads CSV with an explicit, strict dialect (`auto_detect=false`,
  `strict_mode=true`); its sniffer could read a file as a single column.
- **Behaviour change:** malformed CSV is an ERROR on every engine: rows with
  too few or too many fields, duplicate or empty header names, oversized
  fields. Before, `csv_stream` counted a short row's missing field as an
  ordinary missing value, and DuckDB/Polars silently renamed duplicate
  headers (`A_1`, `A_duplicated_0`); Polars padded short rows.
- Engines that cannot decode the declared encoding block the rule
  (`UNSUPPORTED`) instead of misreading text.
- Discovery's DuckDB path uses the same strict dialect; discovery on
  `csv_stream` still profiles malformed files and warns, because it is
  exploratory, but the resulting contract refuses them at evaluation.
- New parity matrix `tests_validation/test_read_options_parity.py` (BOM,
  CRLF, quoted line breaks and delimiters, doubled quotes, gzip, tokens,
  blanks, duplicates, NaN/inf/null on Parquet) across csv_stream, DuckDB
  and Polars.

### Audit fixes — Phase 1 (verdict correctness)

- **Behaviour change:** uniqueness bounds are no longer row-local. An
  unknown key may collide with a unique key and turn both rows into
  failures, so the worst case is `max(passed - unknown, 0) / eligible`.
  Rules that used to PASS with unknown keys can now be INDETERMINATE
  (audit C1). Each evaluator declares its bound model in the registry.
- **Behaviour change:** `plan_optimized` / `evaluate_optimized` validate
  the contract against the JSON Schema and reject repeated IDs, like the
  pandas planner. Hand-written contracts without `evaluation` or with
  non-semver versions are now rejected with `ContractError` (audit H1).
- Uniqueness rules whose `expectation.key_fields` differ from
  `target.fields` are INVALID_CONTRACT on every engine; previously the
  optimized engine evaluated a different key and could pass (audit H5).
- Metrics referring to undeclared dimensions are INVALID_CONTRACT on the
  optimized engine too.
- **Behaviour change:** empty strings in text fields are missing by
  default on every engine; declare `missing_policy.empty_string: false`
  to keep `""` as a value. DuckDB and Polars CSV readers no longer turn
  empty fields into NULL behind the policy's back (audit H7).
- NaN detection follows the real engine type and honours
  `missing_policy.nan_is_missing` (default true), instead of trusting the
  declared `physical_type` (audit H2). The SQL predicate also covers a
  DuckDB 2.0 case where `isnan` returns false for NaN read from Parquet.
- `QualitySuite` is deprecated. `freshness_days` and `delay_days` now
  require `params.date_format` and, for naive timestamps,
  `params.timezone`; formats are never inferred and UTC is never assumed.
  Values that do not match the format make the check ERROR instead of
  counting as late (audit C2).
- Typing: aggregate queries fail loudly if no row comes back; Polars 2
  `collect` compatibility. `mypy src` is clean again.
- New regression suite `tests_validation/test_audit_regressions.py`.

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
- CI: add a core-only (no pandas) job, wheel-content checks and a blocking
  lint/type-check job.
- Code quality: formatted to PEP 8 (79 columns); `ruff` (E, W, F, I, N, UP,
  B, D1) and `mypy` (`disallow_untyped_defs`) are clean and enforced in CI;
  every function is annotated and every public class, method and function has
  a docstring.
- Fix: closures that captured loop variables, unused locals, `zip` without
  `strict`, and a variable reused with two types in `_polars_run`.
- Fix: error messages pointed to a removed package name; they now name the
  `quaxdim` extras.
- Behaviour made explicit: an approved rule without a dimension raises
  `ValueError`; `SparkBackendUnavailable` is now `SparkBackendUnavailableError`.

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

## 0.9.0 — 2026-11-06

Fixes every finding of the three external audits of 0.8.0rc1. Several
changes alter verdicts or outputs of existing contracts; each is marked
**Behaviour change** below. Discovery output is now English.