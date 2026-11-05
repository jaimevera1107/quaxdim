from __future__ import annotations
import argparse
import hashlib
import re
from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]

def check():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["name"] == "quaxdim", "Wrong PyPI distribution name"
    assert "version" in metadata["project"].get("dynamic", []), "Version must be dynamic"
    version_src = (ROOT / "src/quaxdim/_version.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"$', version_src, re.M)
    assert match, "Missing __version__ in src/quaxdim/_version.py"
    ver = match.group(1)
    assert ver and ver[0].isdigit(), "Invalid version"
    req = ("assets/quaxdim-logo.png", "LICENSE", "README.md", ".gitignore",
           "SECURITY.md", "CONTRIBUTING.md", "RELEASE.md",
           "src/quaxdim/schemas/quality-contract.schema.json", "docs/architecture.es.md",
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
