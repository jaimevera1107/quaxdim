"""Smoke test of the accuracy benchmark (ground truth, small sizes)."""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))

from discovery_accuracy import (  # noqa: E402
    discovery_experiment,
    hll_experiment,
)


def test_discovery_never_proposes_a_rule_the_data_violates():
    rows = discovery_experiment(1500, [0.0, 0.05], seed=7)
    for row in rows:
        assert row["precision"] == 1.0, row["wrong_rules"]
    # Without violations the only miss is the start<=end pair, which the
    # lexical CSV profile cannot type (dates stay strings).
    assert rows[0]["missed_rules"] == [
        [
            "builtin.column_compare",
            "fecha_inicio_contrato",
            "fecha_fin_contrato",
        ]
    ]
    assert rows[1]["recall"] == 1.0


def test_hll_error_matches_theory_within_factor_two():
    rows = hll_experiment([1000, 10_000], [10], trials=4, seed=7)
    for row in rows:
        assert (
            row["rmse_relative"] <= 2 * row["theoretical_relative_std_error"]
        )
        assert abs(row["mean_relative_bias"]) < 0.05


def test_benchmark_cli_writes_json(tmp_path):
    from discovery_accuracy import main

    out = tmp_path / "acc.json"
    assert main(["--rows", "600", "--trials", "1", "--output", str(out)]) == 0
    assert out.exists()


