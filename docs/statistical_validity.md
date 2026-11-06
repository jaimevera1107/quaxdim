# Statistical validity and limitations

This page states what QuaxDim's numbers mean and what they do not. Every
item here comes from the external audits of 0.8.0rc1 and is enforced by
tests in `tests_validation/`.

## What a pass rate is

A rule is evaluated on every eligible unit (a row, a value or a key),
never on a sample. The report separates four counts: `passed`, `failed`,
`indeterminate` (the input was missing or unparseable, so the outcome is
unknown) and `excluded` (out of scope or not applicable). The measured rate
is `passed / (passed + failed)`; `assessment_coverage` says how much of the
eligible data that rate actually covers.

## Bounds are identification bounds, not confidence intervals

`lower_rate` and `upper_rate` are the worst and best pass rates compatible
with the unknown units taking any value. They carry no probability and say
nothing about sampling error, because evaluation is a census. They depend
on how a rule behaves (`quaxdim.bounds`):

| Rule kind | Example | Lower bound | Why |
| --- | --- | --- | --- |
| Row-local | presence, range, regex, allowed values | `P / (P+F+U)` | An unknown unit can at worst fail itself |
| Pairwise collision | uniqueness | `max(P−U, 0) / (P+F+U)` | An unknown key may equal a unique key and turn *both* rows into failures |

`upper_rate` is `(P+U) / (P+F+U)` in both cases. The uniqueness bound is
conservative for composite keys with partially missing values.

A verdict is `PASS` only when the lower bound meets the threshold, `FAIL`
only when the upper bound misses it, and `INDETERMINATE` otherwise.

## Uniqueness is measured per row

The uniqueness rate is the share of rows whose key appears exactly once
(`keep=False` in pandas terms). Two rows sharing a key are two failures,
not one surplus record. Thresholds must be read accordingly: a table with
one duplicated pair among 10 rows has a uniqueness rate of 0.8, not 0.9.

## Thresholds are proportions of a census

`threshold: {operator: gte, value: 0.99}` means "at least 99% of eligible
units pass". It is not a confidence level and there is no sampling. A
business rule that must hold for every record is written with `value: 1.0`.
QuaxDim never evaluates a sample of the data in place of the data; a
sample-based mode with confidence intervals is not implemented.

## Missing values are a declared policy, not a reader accident

Which tokens count as missing (`""`, `N/A`, whitespace, NaN) is declared
per field in `missing_policy`; the defaults are the same on every engine
(`quaxdim.missingness`). How a CSV is read is declared per asset in
`read_options`, and structurally malformed rows are errors, not missing
values. Infinite values are compared as numbers: `+inf >= 0` passes a
minimum rule. The contract field `infinities_are_invalid` is reserved and
not yet enforced by any engine.

## Discovery produces hypotheses, not facts

Recommendations come from bounded profiles and reservoir samples
(`quaxdim.heuristics`). Their confidence labels are not calibrated
probabilities. Where a hypothesis rests on a sample, its evidence carries a
quantity that bounds what the sample can say:

| Evidence field | Meaning |
| --- | --- |
| `other_width_share_upper_95`, `non_iso_share_upper_95` | Rule of three: with zero exceptions in *n* values, the population share of exceptions is below 3/*n* at ~95% |
| `unseen_category_share` | Good–Turing estimate of the population share in categories the sample never saw |
| `min_detectable_duplicate_share` | Smallest share of duplicated rows a reservoir sample would reveal at ~95%; 1.0 means the sample is no evidence at all |
| `repeated_determinants`, `g3` | Support for a functional dependency: only repeated determinants can contradict it; `g3` is the share of pairs to drop for it to hold |
| `approx_relative_error` | Standard error of the HyperLogLog cardinality estimate, `1.04 / sqrt(2^precision)` |

The HyperLogLog sketch is descriptive only; a uniqueness *hypothesis* from
it always requires an exact scan before becoming a rule. Discovery tests
many hypotheses on the same sample; no multiple-comparison control is
applied, which is another reason none of them is approved automatically.

A matching regular expression proves lexical form, not correctness: a code
that fits `[0-9]{5}` may still not exist in the official catalogue.

## Drift is a heuristic alarm

Comparisons with a baseline profile flag changes in shape or cardinality.
They are not statistical tests and carry no false-alarm rate.

## There is no composite quality index

QuaxDim reports one rate per rule and never combines rules into a single
score: dimensions are not commensurable and a weighted average would hide
which rule failed.

## Engine equivalence is tested, not assumed

`tests_validation/test_read_options_parity.py` runs the same contract on
the standard-library CSV reader, DuckDB and Polars and requires identical
counts (BOM, CRLF, quoted line breaks, blanks, tokens, gzip; NaN, inf and
null on Parquet). Semantics with no verified equivalence (whitespace
trimming, non-UTF-8 text on Polars) are blocked with `UNSUPPORTED` rather
than approximated. Spark parity must be run in the target environment.

## Accuracy benchmarks

`benchmarks/discovery_accuracy.py` generates tables with known properties
and injected violations and reports discovery precision and recall per
rule, plus HyperLogLog bias and RMSE against theory. On the reference
scenario discovery proposes no rule the data violates (precision 1.0); the
one miss is the start/end date pair on lexical CSV, where dates are not
typed.
