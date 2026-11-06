"""Accuracy benchmark for discovery and the HLL sketch, with ground truth.

Speed benchmarks already exist (``evaluation_backends.py``); this one
answers the question the audits asked: *how often is discovery right?*

Two experiments, both deterministic:

1. **Discovery precision/recall.** Synthetic tables are generated with a
   known set of true properties (unique keys, fixed-width codes, closed
   domains, start<=end pairs, non-negative counts) and a controlled share
   of injected violations. Each recommendation of ``discover_advanced`` is
   matched against the ground truth and precision/recall are reported per
   hypothesis type.
2. **HLL bias and relative error.** ``ApproxCardinality`` is run across
   cardinalities and precisions; mean relative bias and RMSE are compared
   with the theoretical ``1.04 / sqrt(m)``.

Run::

    python benchmarks/discovery_accuracy.py --output accuracy.json
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import tempfile
from pathlib import Path
from typing import Any

from quaxdim import DiscoveryBudget, discover_advanced
from quaxdim.advanced_discovery import ApproxCardinality

_STATUSES = ["ACTIVO", "INACTIVO", "SUSPENDIDO", "RETIRADO"]


def _write_table(
    path: Path, rows: int, violation_share: float, rng: random.Random
) -> dict[str, set[tuple[str, ...]]]:
    """Write one synthetic table and return its true properties."""
    header = [
        "CODE_ID",
        "ESTADO",
        "fecha_inicio_contrato",
        "fecha_fin_contrato",
        "numero_vacantes",
        "nombre_empresa",
    ]
    violations = int(rows * violation_share)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for i in range(rows):
            code = f"{i:06d}"
            status = rng.choice(_STATUSES)
            start = 2000 + rng.randrange(20)
            end = start + rng.randrange(1, 5)
            count = str(rng.randrange(0, 500))
            if i < violations:
                # Inject one violation per row, rotating through the rules.
                kind = i % 4
                if kind == 0:
                    code = f"{rng.randrange(violations):06d}"  # duplicate
                elif kind == 1:
                    status = "OTRO"  # outside the closed domain
                elif kind == 2:
                    end = start - 1  # end before start
                else:
                    count = str(-rng.randrange(1, 50))  # negative count
            writer.writerow(
                [
                    code,
                    status,
                    f"{start}-01-01",
                    f"{end}-01-01",
                    count,
                    f"Empresa {rng.randrange(50)}",
                ]
            )
    # Rules the written data actually satisfies. Presence and the code
    # width hold always; the observed domain of ESTADO always holds (with
    # violations it merely includes "OTRO", which only a catalogue could
    # reject); uniqueness and the date order hold only without violations.
    truth = {("builtin.presence", name) for name in header}
    truth.add(("builtin.regex_fullmatch", "CODE_ID"))
    truth.add(("builtin.allowed_values", "ESTADO"))
    if violations == 0:
        truth.add(("builtin.composite_uniqueness", "CODE_ID"))
        truth.add(
            (
                "builtin.column_compare",
                "fecha_inicio_contrato",
                "fecha_fin_contrato",
            )
        )
    return {"rules": truth}


def discovery_experiment(
    rows: int, shares: list[float], seed: int
) -> list[dict[str, Any]]:
    """Measure precision and recall of rule recommendations."""
    rng = random.Random(seed)
    results = []
    with tempfile.TemporaryDirectory() as folder:
        for share in shares:
            path = Path(folder) / f"table_{share}.csv"
            truth = _write_table(path, rows, share, rng)["rules"]
            report = discover_advanced(
                path, budget=DiscoveryBudget(mode="deep", seed=seed)
            )
            proposed = {
                (r.evaluator, *r.fields)
                for r in report.recommendations
                if r.kind == "rule" and r.evaluator
            }
            true_positive = len(proposed & truth)
            false_positive = len(proposed - truth)
            false_negative = len(truth - proposed)
            precision = (
                true_positive / (true_positive + false_positive)
                if true_positive + false_positive
                else None
            )
            recall = (
                true_positive / (true_positive + false_negative)
                if true_positive + false_negative
                else None
            )
            results.append(
                {
                    "rows": rows,
                    "violation_share": share,
                    "true_rules": sorted(map(list, truth)),
                    "proposed_rules": sorted(map(list, proposed)),
                    "missed_rules": sorted(map(list, truth - proposed)),
                    "wrong_rules": sorted(map(list, proposed - truth)),
                    "precision": precision,
                    "recall": recall,
                }
            )
    return results


def hll_experiment(
    cardinalities: list[int], precisions: list[int], trials: int, seed: int
) -> list[dict[str, Any]]:
    """Measure HLL bias and RMSE against the theoretical error."""
    rows = []
    for precision in precisions:
        theoretical = 1.04 / math.sqrt(1 << precision)
        for cardinality in cardinalities:
            errors = []
            for trial in range(trials):
                sketch = ApproxCardinality(precision)
                for i in range(cardinality):
                    sketch.add(f"{seed}-{trial}-{i}")
                errors.append(sketch.estimate() / cardinality - 1)
            rows.append(
                {
                    "precision": precision,
                    "cardinality": cardinality,
                    "trials": trials,
                    "mean_relative_bias": sum(errors) / trials,
                    "rmse_relative": math.sqrt(
                        sum(e * e for e in errors) / trials
                    ),
                    "theoretical_relative_std_error": theoretical,
                }
            )
    return rows


def main(argv: list[str] | None = None) -> int:
    """Run both experiments and write a JSON report."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--rows", type=int, default=5000)
    parser.add_argument("--trials", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default="discovery_accuracy.json")
    args = parser.parse_args(argv)
    report = {
        "discovery": discovery_experiment(
            args.rows, [0.0, 0.01, 0.05], args.seed
        ),
        "hll": hll_experiment(
            [100, 1000, 10_000, 100_000], [5, 10, 14], args.trials, args.seed
        ),
    }
    Path(args.output).write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    for row in report["discovery"]:
        print(
            f"violations={row['violation_share']:.0%} "
            f"precision={row['precision']} recall={row['recall']}"
        )
    for row in report["hll"]:
        print(
            f"p={row['precision']} n={row['cardinality']} "
            f"bias={row['mean_relative_bias']:+.4f} "
            f"rmse={row['rmse_relative']:.4f} "
            f"theory={row['theoretical_relative_std_error']:.4f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
