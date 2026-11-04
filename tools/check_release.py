from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]

def check():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ver = metadata["project"]["version"]
    assert metadata["project"]["name"] == "quaxdim", "Wrong PyPI distribution name"
    assert ver and ver[0].isdigit(), "Invalid version"
    for p in ("src/quaxdim/__init__.py", "src/quality_dimensions/__init__.py"):
        data = (ROOT / p).read_text(encoding="utf-8")
        assert '__version__ = "' + ver + '"' in data, f"Version mismatch: {p}"
    req = ("assets/quaxdim-logo.png", "LICENSE", "README.md", ".gitignore",
           "SECURITY.md", "CONTRIBUTING.md", "RELEASE.md",
           "contracts/quality-contract.schema.json", "docs/architecture.es.md",
           "scripts/publish_release.ps1", "scripts/verify_pypi.ps1",
           "tools/check_native_release_tests.py",
           ".github/workflows/ci.yml", ".github/workflows/release.yml",
           "tests_public/test_public_api.py")
    for item in req:
        assert (ROOT / item).is_file(), f"Missing artifact: {item}"
    logo = (ROOT / "assets/quaxdim-logo.png").read_bytes()
    assert logo.startswith(b"\x89PNG\r\n\x1a\n") and len(logo) > 50_000, "Invalid brand image"
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "id-token: write" in release and "pypa/gh-action-pypi-publish" in release
    assert "environment:" in release and "pypi" in release
    print("QuaxDim release checklist: PASS")
    print("Version:", ver)
    print("Logo SHA-256:", hashlib.sha256(logo).hexdigest())
    print("Note: PyPI name availability and external engine CI runs must be verified separately.")
    return 0

if __name__ == "__main__":
    try:sys.exit(check())
    except Exception as e:
        print(f"QuaxDim release checklist: FAIL — {e}", file=sys.stderr)
        sys.exit(1)
