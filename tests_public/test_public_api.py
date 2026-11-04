"""Release contract tests for the new QuaxDim public surface."""
import importlib
import os
from pathlib import Path
import subprocess
import sys

import quaxdim
import quality_dimensions

def test_versions_and_public_alias():
    from importlib.metadata import PackageNotFoundError, version
    try:
        expected = version("quaxdim")
    except PackageNotFoundError:
        # Source checkout can be tested without performing an editable install.
        import re
        text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
        expected = re.search(r'^version = "([^"]+)"', text, re.M).group(1)
    assert quaxdim.__version__ == quality_dimensions.__version__ == expected
    assert "discover_advanced" in quaxdim.__all__
    assert quaxdim.discover_advanced is quality_dimensions.discover_advanced
    assert quaxdim.evaluate_optimized is quality_dimensions.evaluate_optimized

def test_import_is_lazy_and_has_no_pandas_requirement():
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
               PYTHONNOUSERSITE="1")
    code = ("import sys, quaxdim; "
            "assert 'pandas' not in sys.modules; "
            "assert 'pyspark' not in sys.modules; "
            "assert 'polars' not in sys.modules; "
            "assert 'duckdb' not in sys.modules")
    output = subprocess.run([sys.executable,"-S","-c",code],env=env,capture_output=True,text=True)
    assert output.returncode == 0, output.stdout + output.stderr

def test_python_module_entrypoint():
    completed = subprocess.run([sys.executable,"-m","quaxdim","dimensions"],
                               capture_output=True,text=True,timeout=20)
    assert completed.returncode == 0,completed.stdout + completed.stderr
    assert completed.stdout.strip()

def test_repo_asset_unchanged_and_present():
    image = Path(__file__).resolve().parents[1] / "assets/quaxdim-logo.png"
    assert image.is_file()
    assert image.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
