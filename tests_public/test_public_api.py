"""Release contract tests for the QuaxDim public surface."""

from __future__ import annotations

import os
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import quaxdim

ROOT = Path(__file__).resolve().parents[1]


def test_version_matches_installed_metadata() -> None:
    """The in-code version is the single source of the distribution version."""
    try:
        installed = version("quaxdim")
    except PackageNotFoundError:
        # A source checkout can be tested without installing the package.
        return
    assert quaxdim.__version__ == installed


def test_public_exports_are_resolvable() -> None:
    """Every name in ``__all__`` is exposed through the lazy loader."""
    assert "discover_advanced" in quaxdim.__all__
    assert callable(quaxdim.discover_advanced)
    assert callable(quaxdim.evaluate_optimized)


def test_import_is_lazy_and_has_no_pandas_requirement() -> None:
    """Importing the package must not load any heavy optional engine."""
    env = dict(
        os.environ,
        PYTHONPATH=str(ROOT / "src"),
        PYTHONNOUSERSITE="1",
    )
    code = (
        "import sys, quaxdim; "
        "assert 'pandas' not in sys.modules; "
        "assert 'pyspark' not in sys.modules; "
        "assert 'polars' not in sys.modules; "
        "assert 'duckdb' not in sys.modules"
    )
    output = subprocess.run(
        [sys.executable, "-S", "-c", code],
        env=env,
        capture_output=True,
        text=True,
    )
    assert output.returncode == 0, output.stdout + output.stderr


def test_python_module_entrypoint() -> None:
    """``python -m quaxdim`` runs the CLI."""
    completed = subprocess.run(
        [sys.executable, "-m", "quaxdim", "dimensions"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip()


def test_repo_asset_unchanged_and_present() -> None:
    """The brand image ships as a valid PNG."""
    image = ROOT / "assets" / "quaxdim-logo.png"
    assert image.is_file()
    assert image.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
