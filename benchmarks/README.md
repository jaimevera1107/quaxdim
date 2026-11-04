# Benchmarks (development only)

These scripts measure synthetic workloads and are not part of the installed QuaxDim API.
Run from the repository root after installing the package and the relevant optional backends.

- `streaming_discovery.py`: CSV discovery throughput and RSS (`psutil` required).
- `discovery_backends.py`: native DuckDB, Polars, and Spark discovery (`psutil` required).
- `evaluation_backends.py`: the same three approved rules across CSV, DuckDB, Polars and Spark.

Example:

```bash
python benchmarks/evaluation_backends.py --engine csv_stream --rows 100000 --output benchmark-result.json
```

Synthetic benchmarks are not evidence of distributed Spark throughput. The `--output` result should
be written outside the repository, or under an ignored `benchmark-results/` directory.
