# Contributing to QuaxDim

Issues and pull requests are welcome. Keep bug reports reproducible and anonymize
all data. For new quality rules, document **what is measured**, eligible denominator,
null/unknown policy, failure behavior, evidence needs and expected backend semantics.

## Local tests

```bash
python -m pip install -e '.[dev]'
python -m ruff format src tests && python -m ruff check src && python -m mypy src
python -m pytest -q tests tests_validation tests_public
```

Optional engines are tested separately with `.[duckdb,polars,spark,parquet]`.
Never claim backend parity from a skipped test. Run Spark tests with a supported Java.

## Pull request checklist

- Tests cover passes, failures, missing/indeterminate outcomes and invalid contracts.
- No implicit casts or destructive mutations.
- Performance impact and extra scans are documented.
- New rules are conservative if backends disagree.
- Public API/docs/changelog are updated when needed.

Code is licensed MIT. The duck logo is a separately managed brand asset.
