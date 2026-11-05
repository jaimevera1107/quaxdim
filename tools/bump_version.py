"""Set the package version for the next release.

The version lives in ``src/quaxdim/_version.py`` only; ``pyproject.toml``
reads it dynamically. Run while preparing a *new* version and never reuse a
version that already exists on PyPI.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "src" / "quaxdim" / "_version.py"
VERSION_PATTERN = re.compile(r'^__version__ = "([^"]+)"$', re.MULTILINE)
RELEASE_PATTERN = re.compile(r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+|\.post\d+)?")


def main(new_version: str) -> int:
    """Replace the version in ``_version.py`` and in README mentions."""
    if not RELEASE_PATTERN.fullmatch(new_version):
        raise ValueError(
            f"Expected PEP 440-like release version, got {new_version}"
        )
    text = VERSION_FILE.read_text(encoding="utf-8")
    match = VERSION_PATTERN.search(text)
    if match is None:
        raise ValueError(f"No __version__ assignment found in {VERSION_FILE}")
    old_version = match.group(1)
    if old_version == new_version:
        raise ValueError("New version must differ from current version")

    VERSION_FILE.write_text(
        text.replace(old_version, new_version), encoding="utf-8"
    )
    readme = ROOT / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace(old_version, new_version),
        encoding="utf-8",
    )
    print(
        f"Updated release {old_version} -> {new_version}. "
        "Update CHANGELOG.md and rerun all tests."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
