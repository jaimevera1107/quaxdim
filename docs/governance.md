# Governance and methodology

QuaxDim models the **decision** behind a quality control, not only its
measurement. A contract records what was approved, for what purpose, by
whom, who answers when it fails and how critical the data are. The approved
contract is the **governance artefact**; the discover → approve flow is the
**control definition process**.

## Where QuaxDim sits in a governance model

Karkošková (2023) describes six data-quality processes, validated in a
financial institution:

| Process | What it covers | In QuaxDim |
| --- | --- | --- |
| **DQ01** | Defining, approving and implementing controls | The discover → approve → evaluate flow, plus the `governance` block |
| **DQ02** | Automated monitoring and reporting | CLI exit codes, JSON reports, `to_frame()`, the methodology document |
| DQ03 | Error handling and root-cause analysis | Not done; the report carries the steward and the recommended action |
| DQ04 | Error prioritisation | Partly: `criticality` orders the report so critical problems come first |
| DQ05 | Review of process performance | Not done |
| DQ06 | Financial reconciliation | Out of scope |

The boundary is deliberate. DQ03 to DQ05 need state that outlives a run:
open tickets, assignments, history. A library that runs and exits has
nowhere to keep it without becoming a service, and that space is already
served by dedicated platforms. QuaxDim instead emits what those processes
need, so a ticketing system or a dashboard can consume it without parsing
the contract again.

## The `governance` block

The block is **optional**. A contract without it produces the same report,
in the same order, as before it existed.

```yaml
governance:
  data_owner: "Dirección de Empleo y Trabajo"
  approved_by: "jvera"
  approved_at: "2026-11-06"           # a calendar date
  review_cycle: quarterly             # monthly | quarterly | biannual | annual
  criticality: analytical             # applies to every rule...
  rules:
    employee_id_unique:
      steward: "equipo-SIGA"
      on_failure: block_publication
      criticality: regulatory_reporting  # ...unless the rule overrides it
```

None of these fields changes a verdict.

| Field | Meaning |
| --- | --- |
| `data_owner` | Who is accountable for the data |
| `approved_by`, `approved_at` | Who approved this contract, and when |
| `review_cycle` | How often the contract should be reviewed; QuaxDim schedules nothing |
| `criticality` | `regulatory_reporting`, `operational`, `analytical` or `exploratory`, most critical first |
| `rules.<id>.steward` | Team or person who answers for the rule |
| `rules.<id>.on_failure` | Recommended action: `block_publication`, `open_ticket`, `notify` or `record_only`. QuaxDim records it and acts on none |
| `rules.<id>.criticality` | Overrides the contract criticality for this rule |

### Validation

Every engine checks the block before reading any data:

- the JSON Schema rejects unknown keys and values outside the enumerations;
- `approved_at` must be a real date (`2026-02-30` is rejected);
- every ID under `rules` must be a rule of the contract. A typo would
  otherwise attach a steward to nothing, silently.

A problem raises `GovernanceError`, which is a `ContractError`.

### Stale contracts

When `approved_at` plus the review cycle lies in the past, evaluation emits a
`StaleContractWarning`. It is a warning, never a failure: the measurements
are still valid, but the rules may no longer reflect what was agreed. Where a
stale contract must stop a pipeline:

```python
import warnings
from quaxdim import StaleContractWarning

warnings.filterwarnings("error", category=StaleContractWarning)
```

## What the report carries

With a governance block, every result gains `criticality`, `steward` and
`on_failure`; `to_frame()` shows them right after `status`, and the CLI
prints them. `report.governance` holds the whole block, and `to_dict()` /
`to_json()` include it.

Results are ordered by attention:

1. Results that need attention come before `PASS` and `NOT_APPLICABLE`.
2. Among those, more critical data first. A `regulatory_reporting` rule
   that could not be measured outranks an `analytical` failure: in
   regulatory reporting "could not measure it" is worse news than "it
   failed" on secondary data.
3. Within the same criticality: errors, then unknown outcomes
   (`INDETERMINATE`, `NEEDS_EVIDENCE`, `UNSUPPORTED`), then `FAIL`.
4. Ties, and every passing rule, keep the contract's order.

## The methodology document

`report.to_methodology(profile=None)` writes the document that ISO/IEC 25024
asks an assessment to state: what was evaluated, on which entity, with which
measures and adaptations, and what was left out and why.

```python
doc = report.to_methodology("dama_uk_2013")
Path("methodology.md").write_text(doc.to_markdown(), encoding="utf-8")
```

From the CLI, add `--methodology methodology.md --profile dama_uk_2013` to
`execute` or `native-execute`.

The document contains:

- the contract version and SHA-256, the QuaxDim version and the governance;
- the target entities (the sources of the audit trail);
- per dimension, every rule with its measure, its G/K/N category, threshold,
  status and bounds, and the **adaptations**: how missing inputs were
  treated and any note declared in the rule's `standard_refs`;
- the dimensions **not evaluated**, each with a typed reason;
- the rules whose measure falls outside the frame of reference.

Its wording is fixed: measures are "derived from" and "aligned with" the
cited references "as interpreted by QuaxDim". It never says "compliant" or
"certified", and a test enforces that: no certification exists for a
library.

No score is computed. A dimension that could not be assessed never enters
an aggregate, because there is none.

### Measures and profiles

The measure registry (`quaxdim measures`, `quaxdim.list_measures()`) gives
every measure an ID, the dimension it measures, its inputs, the evaluators
that implement it and a category from the G/K/N scheme of Jung et al.:

| Category | Meaning | Example |
| --- | --- | --- |
| G | Computable from the data alone | `qd.completeness.presence` |
| K | Needs knowledge someone must supply: a key, a domain, a reference, timestamps | `qd.uniqueness.composite_key` |
| N | Needs external ground truth; QuaxDim cannot compute it | `qd.accuracy.authoritative_match` |

The category decides mechanically whether a dimension can be assessed. When
it cannot, the reason is one of:

| Reason | When |
| --- | --- |
| `missing_rule` | No approved rule measures the dimension |
| `missing_reference` | The rules need a reference table that was not supplied |
| `missing_timestamp` | The measure needs event and receipt timestamps |
| `missing_label` | The measure needs ground truth per record |
| `missing_target_population` | The expected population was not declared |
| `system_dependent` | Rules exist but the engine could not measure them |

One profile ships: **`dama_uk_2013`**, the six primary dimensions of DAMA UK
(Completeness, Uniqueness, Timeliness, Validity, Accuracy, Consistency).
Dimension names are cited; definitions are not reproduced. ISO/IEC 25012 and
25024 profiles are not included yet: several public measure identifiers
conflict between sources and need a licensed copy of the standard to check.
The measure IDs are QuaxDim's own (`qd.*`) for the same reason.

### Citing a standard in a rule

A rule can carry citable references. They are metadata: they never change a
verdict.

```yaml
rules:
  - id: employee_id_present
    # ...
    standard_refs:
      - standard: "Internal data policy 2026"
        clause: "4.2"
        interpretation: "as interpreted by QuaxDim"
        verification: public_text   # public_text | licensed_copy |
                                    # derived_from_literature |
                                    # conflicting_public_sources
        note: "employee IDs must never be blank"
```

The `note` appears among the adaptations of the methodology document.

## What QuaxDim deliberately does not do

- Manage tickets, dashboards or incident workflows (DQ03 to DQ05).
- Claim conformance with any standard.
- Schedule reviews; `review_cycle` is information.
- Change a verdict because of criticality: a critical `FAIL` is still a
  `FAIL`, it is only shown first.
- Recommend thresholds "from the standard": ISO/IEC 25024 itself does not
  define ranges or grades.

## References

- Karkošková, S. (2023). Data governance model to enhance data quality in
  financial institutions. *Information Systems Management*, 40(1).
  <https://doi.org/10.1080/10580530.2022.2042628>
- DAMA UK Working Group (2013). *The Six Primary Dimensions for Data Quality
  Assessment*.
- Jung et al. *Implementation Guidelines for Data Quality Metrics*.
  arXiv:2610.10919.
