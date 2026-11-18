# Governance in QuaxDim

QuaxDim implements the first two quality processes of the governance model
described in Karkošková (2023), *A data quality management model for financial
institutions*, Information Systems Management 40(1):

| Process | What it covers | In QuaxDim |
|---|---|---|
| **DQ01** | Defining, approving and implementing quality controls | The discover → approve → evaluate workflow |
| **DQ02** | Automated monitoring and reporting | CLI with exit codes, `to_frame()`, JSON reports |
| DQ03 | Error handling and root-cause analysis | Out of scope; see below |
| DQ04 | Error prioritisation | Partial: `criticality` and `on_failure` metadata |
| DQ05 | Process performance review | Out of scope |
| DQ06 | Financial reconciliation | Out of scope (different product) |

The design decision is deliberate: DQ03–DQ05 require state that persists
between executions (open tickets, assignments, history).  A library that runs
and terminates has nowhere to keep that state without becoming a service.
QuaxDim instead emits the information those processes need—owner, steward,
criticality, recommended action—so a ticketing system or dashboard can consume
it without reimplementing the parsing.

---

## The governance block

The `governance` block in a contract is **entirely optional**.  Omitting it
produces a report identical to the one QuaxDim produced before 0.10.0.

```yaml
governance:                      # optional; has no effect on verdicts
  data_owner: "Dirección de Empleo y Trabajo"
  approved_by: "jvera"
  approved_at: "2026-11-06"      # ISO-8601 date
  review_cycle: quarterly        # monthly | quarterly | biannual | annual
  criticality: regulatory_reporting  # see below
  rules:
    employee_id_unique:
      steward: "equipo-SIGA"
      on_failure: block_publication  # see below
```

None of these fields changes a verdict.  They annotate the report so that
downstream systems can apply DQ03–DQ05 themselves.

### criticality

Classifies the data as a whole.  Controls the sort order of results in the
report: more critical items surface first.

| Value | Meaning |
|---|---|
| `regulatory_reporting` | Feeds an external regulatory report; highest priority |
| `operational` | Drives a production system |
| `analytical` | Feeds analytics or BI; tolerates short delays |
| `exploratory` | Experimental; lowest priority |

### on_failure

Recommended action for the calling system when this rule fails.  QuaxDim
records it and does nothing else.

| Value | Meaning |
|---|---|
| `block_publication` | Stop the pipeline before publishing the data |
| `open_ticket` | Create an issue in the team's tracker |
| `notify` | Send an alert |
| `record_only` | Log the failure; take no immediate action |

### review_cycle

Informative.  QuaxDim does not schedule reviews.  When `approved_at` plus the
review cycle is in the past, `evaluate_optimized` and `evaluate_contract` emit
a `StaleContractWarning`:

```python
import warnings
from quaxdim._governance_apply import StaleContractWarning

warnings.filterwarnings("error", category=StaleContractWarning)
```

---

## What the report carries

Every result in the report gains three extra fields when a governance block is
present:

| Field | Source |
|---|---|
| `steward` | `governance.rules[rule_id].steward` |
| `on_failure` | `governance.rules[rule_id].on_failure` |
| `criticality` | `governance.criticality` (contract-level) |

```python
report = evaluate_optimized(contract, data={"employees": "data.csv"})

for r in report.results:
    print(r.rule_id, r.status, r.steward, r.on_failure, r.criticality)
```

`to_frame()` includes these columns automatically.

The report itself exposes the full governance block:

```python
print(report.governance.data_owner)
print(report.governance.is_overdue())
d = report.to_dict()   # "governance" key included
```

---

## Sort order

Results are sorted so that the most actionable items appear first:

1. `ERROR` / `INVALID_CONTRACT` — the rule could not run at all.
2. `INDETERMINATE` — the outcome is unknown (input was missing or unparseable).
3. `FAIL` — the threshold was not met.
4. `UNSUPPORTED` / `NO_ELIGIBLE_DATA` — no measurement was possible.
5. `PASS`

Within each tier, higher criticality precedes lower criticality.  Within the
same criticality, the original evaluation order is preserved.

---

## What QuaxDim deliberately does not do

- **Manage tickets, dashboards or incident workflows.** Those belong to
  systems that have persistent state and a UI.
- **Assert ISO compliance.** No certification exists for a library, and
  comprar la norma does not change that.
- **Schedule reviews.** `review_cycle` is metadata; the calling system
  decides when to run reviews.
- **Change verdicts based on criticality.** A `regulatory_reporting` FAIL is
  still a FAIL; it is not promoted to ERROR.

---

## References

Karkošková, S. (2023). *A data quality management model for financial
institutions*. Information Systems Management, 40(1), 24–41.
https://doi.org/10.1080/10580530.2021.1913880
