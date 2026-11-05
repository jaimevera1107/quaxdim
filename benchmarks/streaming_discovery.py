"""Synthetic bounded-RSS benchmark for CSV discovery; runs in isolated process.

Generation is outside the measured discovery interval. No user data used.
"""

from __future__ import annotations
import argparse
import csv
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time


def run(rows: int, output: Path, sample_rows=1000, columns=8):
    from quaxdim import DiscoveryBudget, discover_advanced
    import psutil

    if rows < 0 or columns < 1:
        raise ValueError("rows and columns must be positive")
    with tempfile.TemporaryDirectory(prefix="qd_bench_") as td:
        p = Path(td) / "benchmark.csv"
        heads = [
            "ROW_ID",
            "MPO_ID",
            "OCU_ID",
            "TOTAL",
            "YEAR",
            "TRIMESTRE",
            "ESTADO",
            "FECHA",
        ]
        heads += [f"EXTRA_{k}" for k in range(max(0, columns - len(heads)))]
        heads = heads[:columns]
        with p.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(heads)
            for i in range(rows):
                vals = [
                    f"{i:012d}",
                    f"{i % 1122:05d}",
                    f"{i % 600:04d}",
                    str(i % 3000),
                    str(2020 + i % 7),
                    str(i % 4 + 1),
                    "ACTIVO" if i % 3 else "INACTIVO",
                    "2026-01-01",
                ]
                vals += [
                    str(i % 19) for _ in range(max(0, columns - len(vals)))
                ]
                w.writerow(vals[:columns])
        file_bytes = p.stat().st_size
        process = psutil.Process(os.getpid())
        max_rss = [process.memory_info().rss]
        done = threading.Event()

        def monitor():
            while not done.wait(0.03):
                try:
                    max_rss[0] = max(max_rss[0], process.memory_info().rss)
                except psutil.Error:
                    break

        t = threading.Thread(target=monitor, daemon=True)
        t.start()
        start = time.perf_counter()
        try:
            report = discover_advanced(
                p,
                asset_id="benchmark",
                budget=DiscoveryBudget(
                    mode="deep",
                    max_sample_rows=sample_rows,
                    max_profile_columns=min(columns, 64),
                ),
            )
        finally:
            elapsed = time.perf_counter() - start
            done.set()
            t.join(timeout=1)
            max_rss[0] = max(max_rss[0], process.memory_info().rss)
        actual = report.assets["benchmark"]["rows"]
        if actual != rows:
            raise AssertionError(
                f"benchmark expected {rows} rows but got {actual}"
            )
        plan = report.resource_plan()["benchmark"]
        if plan["full_passes"] != 1:
            raise AssertionError("expected exactly one pass")
        payload = {
            "status": "PASS",
            "rows": rows,
            "columns": columns,
            "file_bytes": file_bytes,
            "elapsed_seconds": round(elapsed, 4),
            "rows_per_second": round(rows / max(elapsed, 1e-9), 1),
            "peak_process_rss_mb": round(max_rss[0] / 1048576, 2),
            "source_backend": report.assets["benchmark"]["backend"],
            "recommendations": len(report.recommendations),
            "full_passes": plan["full_passes"],
            "sample_cap": sample_rows,
            "disclaimer": "Process RSS, not total Spark cluster memory; local synthetic CSV only.",
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=100_000)
    ap.add_argument("--columns", type=int, default=8)
    ap.add_argument("--sample", type=int, default=1000)
    ap.add_argument("--output", type=Path, required=True)
    a = ap.parse_args()
    run(a.rows, a.output, a.sample, a.columns)
