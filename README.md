<div align="center">

![QuaxDim — Dimensions of Data Quality](https://raw.githubusercontent.com/jaimevera1107/quaxdim/main/assets/quaxdim-logo.png)

# QuaxDim

**Dimensions of Data Quality**  
Scalable · Context-aware · Explainable · Auditable

[![CI](https://github.com/jaimevera1107/quaxdim/actions/workflows/ci.yml/badge.svg)](https://github.com/jaimevera1107/quaxdim/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/quaxdim?label=PyPI&cacheSeconds=3600)](https://pypi.org/project/quaxdim/)
[![Python](https://img.shields.io/pypi/pyversions/quaxdim?label=Python&cacheSeconds=3600)](https://pypi.org/project/quaxdim/)
[![License: MIT](https://img.shields.io/badge/license-MIT-5922a7)](https://github.com/jaimevera1107/quaxdim/blob/main/LICENSE)

[English](https://github.com/jaimevera1107/quaxdim/blob/main/README.md) · [Español](https://github.com/jaimevera1107/quaxdim/blob/main/docs/README.es.md) · [Documentation](https://github.com/jaimevera1107/quaxdim/tree/main/docs/) · [Contributing](https://github.com/jaimevera1107/quaxdim/blob/main/CONTRIBUTING.md)

</div>

---

QuaxDim measures data quality against **contracts you approve**, and tells
you exactly what it could and could not verify. Three ideas run through the
whole library:

- **Nothing is enforced without a person approving it.** Discovery proposes
  hypotheses with evidence; you turn the ones you trust into rules.
- **Unknown is not failure.** Every result separates passed, failed,
  indeterminate (the input was missing or unparseable) and excluded (out of
  scope). A verdict is never produced by guessing.
- **The same contract gives the same answer on every engine.** A CSV stream,
  DuckDB, Polars and Spark are held to identical semantics by a parity test
  matrix; what cannot be made equivalent is blocked, not approximated.

The dimension vocabulary follows Rupa Mahanti's *Data Quality: Dimensions,
Measurement, Strategy, Management, and Governance* (2019) and ISO/IEC 25012.
The catalogue is conceptual: it does not claim a metric for every dimension.

## Contents

1. [Install](#install)
2. [Five-minute workflow](#five-minute-workflow)
3. [Reading a report](#reading-a-report)
4. [Writing a contract by hand](#writing-a-contract-by-hand)
5. [Engines and supported rules](#engines-and-supported-rules)
6. [Tuning discovery](#tuning-discovery)
7. [Command line](#command-line)
8. [pandas APIs](#pandas-apis)
9. [What QuaxDim does not do](#what-quaxdim-does-not-do)
10. [Documentation](#documentation), [development](#development), [license](#license-and-references)

## Install

```bash
python -m pip install quaxdim                     # core: CSV streaming, no pandas
python -m pip install "quaxdim[duckdb,polars]"    # native engines for CSV and Parquet
python -m pip install "quaxdim[spark]"            # classic PySpark batch
python -m pip install "quaxdim[pandas]"           # in-memory pandas APIs
```

The core install depends only on PyYAML and jsonschema. Everything in the
five-minute workflow below works with it. Python 3.10 or newer.

## Five-minute workflow

The examples use [`examples/employees.csv`](https://github.com/jaimevera1107/quaxdim/blob/main/examples/employees.csv):

```csv
employee_id,status,hire_date,termination_date,headcount
00017,ACTIVE,2021-03-01,,12
00018,ACTIVE,2022-07-15,,8
00019,INACTIVE,2019-01-10,2023-05-31,0
00020,ACTIVE,2020-11-02,,5
,ACTIVE,2021-01-01,,3
00018,ACTIVE,2022-07-15,,8
```

### 1. Discover what the data suggests

```python
from quaxdim import DiscoveryBudget, discover_advanced

advice = discover_advanced(
    "examples/employees.csv",
    asset_id="employees",
    budget=DiscoveryBudget(mode="deep"),
)
for a in advice.recommendations:
    print(a.id, a.kind, a.fields, a.title, a.confidence)
```

```text
recommend_b08c796f8996cc6d rule ('employee_id',) Review whether employee_id is required medium
recommend_bd8e437ebb3dd306 rule ('employee_id',) Confirm the code width of employee_id low
recommend_58d3208e26086dd8 rule ('status',) Review whether status is required low
recommend_5d3a67e5069b2305 semantic ('hire_date',) Confirm the temporal interpretation of hire_date low
...
```

Discovery streams the CSV once with bounded memory; it never loads the file
into pandas and never stores raw values in the report. Each recommendation
carries `evidence`, `rationale`, `caveats` and `prerequisites`. `kind` is
`rule` when it can be executed, `semantic` or `hypothesis` when it needs a
decision or a bigger scan first. Confidence labels are ordinal, not
probabilities; the evidence holds the numbers that matter (see
[Tuning discovery](#tuning-discovery)).

### 2. Approve the recommendations you trust

```python
from quaxdim import build_reviewed_contract

chosen = [
    a.id for a in advice.recommendations
    if a.kind == "rule" and a.fields == ("employee_id",)
]
contract, audit = build_reviewed_contract(
    advice,
    approve=chosen,                               # explicit IDs only
    thresholds={rid: 1.0 for rid in chosen},      # explicit threshold per rule
    purpose="Monthly HR extract acceptance",      # recorded in the contract
)
```

`build_reviewed_contract` refuses anything that is not an executable rule and
anything without a threshold. The resulting contract is schema-valid and
carries the CSV dialect discovery used (`read_options`), so evaluation reads
the file exactly the same way. Save it with `yaml.safe_dump(contract)` and
keep it under version control: it is the artefact an auditor will ask for.

### 3. Plan, then execute

```python
from quaxdim import evaluate_optimized, plan_optimized

plan = plan_optimized(contract, data={"employees": "examples/employees.csv"})
for r in plan.rules:
    print(r.rule_id, r.status, r.reason)       # READY, or why not

report = evaluate_optimized(contract, data={"employees": "examples/employees.csv"})
for r in report.results:
    print(r.rule_id, r.status, r.passed_units, r.failed_units,
          r.indeterminate_units, r.lower_rate, r.upper_rate)
report.to_json("report.json")
```

`plan_optimized` is free: it validates the contract and tells you, per rule,
whether the chosen engine can run it (`READY`), cannot run it faithfully
(`UNSUPPORTED`), or the contract is inconsistent (`INVALID_CONTRACT`).
Evaluation runs only `READY` rules, one pass over the data, and returns
counts, never rows.

`data` maps each asset ID to its source: a CSV/TSV path (optionally gzipped),
a Parquet path (DuckDB), a Polars `LazyFrame`/`DataFrame`, or a Spark
`DataFrame`. The engine is picked from the source; force one with
`engine="csv_stream" | "duckdb" | "polars" | "spark"`.

## Reading a report

Running the hand-written contract from the next section on the sample file
gives:

| rule | status | passed | failed | indeterminate | measured | lower | upper |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `employee_id_present` | FAIL | 5 | 1 | 0 | 0.833 | 0.833 | 0.833 |
| `status_in_catalogue` | PASS | 6 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `employee_id_unique` | FAIL | 3 | 2 | 1 | 0.600 | 0.333 | 0.667 |

How to read each column:

- **passed / failed / indeterminate / excluded**: units (values, rows or
  keys) in each outcome. *Indeterminate* means the input was missing or
  unparseable, so the outcome is unknown; *excluded* means out of scope.
- **measured_rate** is `passed / (passed + failed)`: the rate among the units
  that could be judged.
- **lower_rate / upper_rate** are the worst and best rates compatible with
  every possible resolution of the indeterminate units. They are
  identification bounds, not confidence intervals: evaluation is a census.
  For uniqueness the lower bound accounts for an unknown key colliding with
  a unique one, which is why the third row has `lower = 1/3` with only one
  blank key.
- **assessment_coverage** is the share of eligible units the measured rate
  covers.
- **status** is `PASS` when the lower bound meets the threshold, `FAIL` when
  the upper bound misses it, and `INDETERMINATE` in between. `ERROR`,
  `UNSUPPORTED`, `INVALID_CONTRACT` and `NO_ELIGIBLE_DATA` say the rule was
  not measured, and why.

The report also records `contract_sha256`, `library_version`, `generated_at`
and a `sources` block (file path, size and modification time) so a verdict
can be traced to the exact inputs that produced it.

## Writing a contract by hand

Discovery is optional. A contract is a YAML or JSON document validated
against the bundled schema (`src/quaxdim/schemas/quality-contract.schema.json`).
The full example is in [`examples/contract_employees.yaml`](https://github.com/jaimevera1107/quaxdim/blob/main/examples/contract_employees.yaml);
the parts you will edit most are these:

```yaml
contract_version: "0.2.0"

assets:
  - id: employees
    format: csv
    shape: table
    read_options:              # how the CSV is read; identical on every engine
      delimiter: ","
      encoding: utf-8-sig
    fields:
      - name: employee_id
        physical_type: utf8
        logical_type: string
        nullable: true
        missing_policy:        # what counts as "absent" in this field
          empty_string: true
          tokens: ["N/A", "ND"]

metrics:
  - id: m_unique
    dimension_ids: [uniqueness]
    evaluator: builtin.composite_uniqueness
    target_levels: [entity]
    denominator_unit: entity
    null_policy: indeterminate   # missing key -> unknown, not a failure
    # ... (version, kind, unit, direction, unknown_policy, evidence_mode, aggregation)

rules:
  - id: employee_id_unique
    asset_id: employees
    metric_id: m_unique
    target: {level: entity, fields: [employee_id]}
    expectation: {operator: composite_unique, key_fields: [employee_id]}
    threshold: {operator: gte, value: 0.99}
    # ... (version, severity, failure_action)
```

Things worth knowing when you write one:

- **`missing_policy`** decides the denominator. `empty_string` (default
  `true`), `tokens`, `nan_is_missing` (default `true`) and
  `infinities_are_invalid` (default `false`) apply identically on every
  engine; `whitespace_only` and `normalization: trim` run on the CSV engine
  only and are blocked elsewhere.
- **`null_policy`** on the metric decides what a missing input means for the
  rule: `include_as_failure`, `indeterminate` or `exclude`.
- **`read_options`** (`delimiter`, `encoding`, `quote_char`, `max_field_chars`)
  belong to the asset, so every engine reads the same table. Malformed rows,
  duplicate or empty headers and oversized fields are errors, never missing
  values. Engines that cannot decode the declared encoding block the rule.
- **Thresholds are proportions of a census.** `value: 1.0` means every unit
  must pass; `0.99` tolerates one percent. There is no sampling.
- **Uniqueness is per row.** A key that appears twice yields two failures.

Load a file-based contract with `quaxdim.load_contract(path)` or run it from
the CLI below.

## Engines and supported rules

| Rule | CSV stream | DuckDB | Polars | Spark |
| --- | --- | --- | --- | --- |
| `builtin.presence` | yes | yes | yes | yes |
| `builtin.allowed_values` (small declared set) | text | yes, typed | yes, typed | yes, typed |
| `builtin.regex_fullmatch` (ASCII, fixed width) | yes | yes | yes | yes |
| `builtin.value_range` (numeric) | no | yes, typed | yes, typed | yes, typed |
| `builtin.column_compare` (numeric) | no | yes | yes | yes |
| `builtin.composite_uniqueness` (exact) | yes, memory cap | yes | yes | yes, shuffle |

"No" means the planner reports `UNSUPPORTED` rather than converting types
behind your back: the CSV engine keeps every value lexical, so `00017` is
never turned into `17`. Parity across the first three engines is enforced by
`tests_validation/test_read_options_parity.py` on text edge cases (BOM, CRLF,
quoted line breaks, blanks, tokens, gzip) and numeric ones (NaN, ±inf, null).
Spark is tested in CI with Java 21.

## Tuning discovery

`DiscoveryBudget` bounds the work; `HeuristicThresholds` decides what gets
proposed. Both are explicit, and the defaults are shared by the pandas and
native advisors.

```python
from quaxdim import DiscoveryBudget
from quaxdim.heuristics import HeuristicThresholds

budget = DiscoveryBudget(
    mode="deep",                 # schema | balanced | deep
    max_sample_rows=1500,        # reservoir size for sample-based hypotheses
    max_profile_columns=64,
    hll_precision=10,            # cardinality sketch; error ~ 1.04 / sqrt(2**p)
    seed=42,
    thresholds=HeuristicThresholds(
        max_category_unique_share=0.25,   # closed-domain candidates
        min_fd_repeated_determinants=3,   # functional dependencies
        hll_z=2.5,                        # tolerance below ratio 1, in HLL std errors
    ),
)
```

Column names are interpreted by **tokens**, not substrings: `account_balance`
is not a count, `country` is not either, `tamaño_empresa` is not a date, and
`fecha_inicio_contrato` pairs with `fecha_fin_contrato` but not with
`fecha_fin_vigencia`. The vocabularies live in `quaxdim/heuristics.py`.

Every sample-based recommendation quantifies what the sample can support:

| evidence field | meaning |
| --- | --- |
| `other_width_share_upper_95` | rule of three: zero exceptions in *n* values bound the exception share below 3/*n* |
| `unseen_category_share` | Good–Turing estimate of the share in categories the sample never saw |
| `min_detectable_duplicate_share` | smallest duplicate share the reservoir would reveal at ~95%; `1.0` means "no evidence" |
| `repeated_determinants`, `g3` | support for a functional dependency and its error |
| `approx_relative_error` | standard error of the HyperLogLog estimate |

`benchmarks/discovery_accuracy.py` measures precision and recall against
injected violations; on the reference scenario discovery proposes no rule the
data violates.

## Command line

```bash
quaxdim dimensions                                   # the conceptual catalogue
quaxdim advise data.csv --asset employees --mode deep --output recommendations.json
quaxdim native-plan    data.csv --asset employees --contract contract.yaml --output plan.json
quaxdim native-execute data.csv --asset employees --contract contract.yaml --output report.json \
    --engine auto   # auto | csv_stream | duckdb | polars
quaxdim --version
```

`native-execute` exits with `0` when every measured rule passes and `1`
otherwise, so it drops into a pipeline step as is. Known errors (bad
contract, unreadable file) exit with `2` and a message on stderr. Reports are
written atomically.

Delimiters other than a comma go in the contract's `read_options`; for
discovery pass `--delimiter`. `.tsv` files default to tab.

## pandas APIs

With `quaxdim[pandas]` the same contracts run in memory on a `DataFrame`:

```python
import pandas as pd
from quaxdim import ApplicabilityPlanner, evaluate_contract, load_contract

contract = load_contract("examples/contract_employees.yaml")
frame = pd.read_csv("examples/employees.csv", dtype="string", keep_default_na=False)
plan = ApplicabilityPlanner().plan(contract, data={"employees": frame})
report = evaluate_contract(contract, data={"employees": frame})
```

Read CSVs with `dtype="string", keep_default_na=False` as above; otherwise
pandas strips leading zeros and turns `NA` into a null before QuaxDim sees it.
`discover(frame)` is the pandas counterpart of `discover_advanced`.

`QualitySuite` and `Check`, the original declarative API, remain available
but are **deprecated**; they emit a `DeprecationWarning` and will be removed
in a future major release. Their `freshness_days` and `delay_days` rules now
require explicit `date_format` and `timezone` parameters, because formats are
never inferred and UTC is never assumed.

## What QuaxDim does not do

- It does not evaluate a sample in place of the data, and it has no mode
  with confidence intervals; rates are census measurements with bounds.
- It does not combine rules into a single quality score; dimensions are not
  commensurable.
- It does not infer date formats, time zones, locales or numeric conversions.
  Ambiguous dates (`04/11/2026`) are reported as needing a decision.
- It does not control false discoveries across the many hypotheses discovery
  tests; that is one more reason none is approved automatically.
- Drift detection against a baseline profile is a heuristic alarm, not a
  statistical test.
- Spark Connect, Structured Streaming and conditional rules are not
  supported.

The reasoning behind each limit is in
[docs/statistical_validity.md](https://github.com/jaimevera1107/quaxdim/blob/main/docs/statistical_validity.md).

## Documentation

- [Statistical validity and limitations](https://github.com/jaimevera1107/quaxdim/blob/main/docs/statistical_validity.md)
- [Native execution and planner](https://github.com/jaimevera1107/quaxdim/blob/main/docs/execution.es.md) (Spanish)
- [Dimensions and methodology](https://github.com/jaimevera1107/quaxdim/blob/main/docs/dimensions.es.md) (Spanish)
- [Architecture and universal contracts](https://github.com/jaimevera1107/quaxdim/blob/main/docs/architecture.es.md) (Spanish)
- [Changelog](https://github.com/jaimevera1107/quaxdim/blob/main/CHANGELOG.md) · [Release process](https://github.com/jaimevera1107/quaxdim/blob/main/RELEASE.md) · [Security policy](https://github.com/jaimevera1107/quaxdim/blob/main/SECURITY.md)

## Development

```bash
python -m pip install -e ".[dev,duckdb,polars,regex]"
python -m pytest -q
ruff check src && mypy
```

Spark tests need `.[spark]` and Java 21; they are skipped when PySpark is
absent and run in CI.

## License and references

MIT-licensed code. The project logo has separate brand usage restrictions; see
[assets/README.md](https://github.com/jaimevera1107/quaxdim/blob/main/assets/README.md). Conceptual inspiration: R. Mahanti,
*Data Quality: Dimensions, Measurement, Strategy, Management, and Governance*,
ASQ Quality Press, 2019.
