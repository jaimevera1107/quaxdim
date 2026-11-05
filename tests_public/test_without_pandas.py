"""The documented core workflow must work with pandas and numpy absent.

pandas and numpy are optional extras. These tests run the README quickstart
and the CLI in a subprocess where both modules are made unimportable, so a
regression (for example a new top-level ``import pandas``) fails here even
when pandas is installed in the development environment.

This module must not import pandas or numpy itself: CI runs it in a minimal
environment that has neither.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BLOCK_OPTIONAL = (
    "import sys\nsys.modules['pandas'] = None\nsys.modules['numpy'] = None\n"
)
CSV_TEXT = "ID,name,age\n001,ana,30\n002,,41\n003,luis,\n004,eva,22\n"


def _run(code: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONNOUSERSITE="1")
    return subprocess.run(
        [sys.executable, "-c", BLOCK_OPTIONAL + textwrap.dedent(code)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture()
def workdir(tmp_path: Path) -> Path:
    (tmp_path / "data.csv").write_text(CSV_TEXT, encoding="utf-8")
    return tmp_path


def test_every_public_export_imports_without_pandas(workdir: Path) -> None:
    result = _run(
        """
        import quaxdim
        for name in quaxdim.__all__:
            getattr(quaxdim, name)
        """,
        workdir,
    )
    assert result.returncode == 0, result.stderr


def test_pandas_features_fail_with_actionable_message(workdir: Path) -> None:
    result = _run(
        """
        import quaxdim
        try:
            quaxdim.profile(None)
        except ImportError as exc:
            assert 'quaxdim[pandas]' in str(exc), str(exc)
        else:
            raise SystemExit('expected ImportError')
        """,
        workdir,
    )
    assert result.returncode == 0, result.stderr


def test_readme_quickstart_runs_without_pandas(workdir: Path) -> None:
    result = _run(
        """
        from quaxdim import (
            build_reviewed_contract, discover_advanced, evaluate_optimized,
        )
        advice = discover_advanced("data.csv", asset_id="dataset")
        selected = [
            r.id for r in advice.recommendations
            if r.evaluator == "builtin.presence" and r.fields == ("ID",)
        ]
        assert selected, "no presence recommendation for ID"
        contract, _audit = build_reviewed_contract(
            advice,
            approve=selected,
            purpose="Reproducible quality assessment",
            thresholds={rid: 1.0 for rid in selected},
        )
        report = evaluate_optimized(contract, data={"dataset": "data.csv"})
        results = report.to_dict()["results"]
        assert results and all(r["status"] == "PASS" for r in results), results
        import json
        json.dump(contract, open("contract.json", "w"))
        """,
        workdir,
    )
    assert result.returncode == 0, result.stderr
    assert (workdir / "contract.json").is_file()


def test_cli_native_commands_run_without_pandas(workdir: Path) -> None:
    setup = _run(
        """
        import json
        from quaxdim import build_reviewed_contract, discover_advanced
        advice = discover_advanced("data.csv", asset_id="dataset")
        ids = [r.id for r in advice.recommendations
               if r.evaluator == "builtin.presence" and r.fields == ("ID",)]
        contract, _ = build_reviewed_contract(
            advice, approve=ids, purpose="cli test",
            thresholds={i: 1.0 for i in ids},
        )
        json.dump(contract, open("contract.json", "w"))
        """,
        workdir,
    )
    assert setup.returncode == 0, setup.stderr

    for command in ("native-plan", "native-execute"):
        result = _run(
            f"""
            from quaxdim.cli import main
            raise SystemExit(main([
                "{command}", "data.csv", "--contract", "contract.json",
                "--asset", "dataset", "--output", "{command}.json",
            ]))
            """,
            workdir,
        )
        assert result.returncode == 0, f"{command}: {result.stderr}"
        assert json.loads((workdir / f"{command}.json").read_text())
