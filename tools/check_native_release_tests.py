"""Fail a release when critical native backend integration tests are skipped or absent."""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REQUIRED = {
    "test_v08_duckdb_native_parity",
    "test_v08_polars_lazy_native_parity",
    "test_v08_spark_native_parity",
    "test_v08_duckdb_parquet_numeric",
    "test_v08_polars_parquet_numeric",
    "test_v08_spark_parquet_numeric",
    "test_duckdb_csv_schema_and_aggregate",
    "test_duckdb_parquet_native",
    "test_polars_lazy_csv_and_dataframe",
    "test_polars_parquet_lazy",
    "test_spark_schema_and_distributed_aggregate",
    "test_spark_large_input_uses_aggregates",
}


def main(path: str) -> int:
    tree = ET.parse(Path(path))
    seen = {}
    for case in tree.findall(".//testcase"):
        name = case.get("name", "").split("[")[0]
        if name in REQUIRED:
            seen[name] = next((kind for kind in ("failure", "error", "skipped") if case.find(kind) is not None), "passed")
    failures = [f"{name}: {seen.get(name, 'MISSING')}" for name in sorted(REQUIRED) if seen.get(name) != "passed"]
    print(f"Mandatory native tests: {len(seen)}/{len(REQUIRED)} found")
    if failures:
        print("Cannot publish: mandatory native backend tests were not all verified:")
        for value in failures:
            print(" -", value)
        return 1
    print("Native backend release checks: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "release-tests.xml"))
