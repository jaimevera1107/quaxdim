"""Synchronize distribution and import versions for the next release.

Run while preparing a *new* version; never reuse a version already on PyPI.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    'pyproject.toml', 'src/quaxdim/__init__.py',
    'src/quality_dimensions/__init__.py', 'README.md', 'INICIO_AQUI.md',
    'docs/INSTRUCCIONES_PUBLICACION.md',
]


def main(new: str) -> int:
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:(?:a|b|rc)\d+|\.post\d+)?', new):
        raise ValueError(f'Expected PEP 440-like release version, got {new}')
    project = ROOT / 'pyproject.toml'
    old = re.search(r'^version = "([^"]+)"', project.read_text(), re.M).group(1)
    if old == new:
        raise ValueError('New version must differ from current version')
    for rel in FILES:
        file = ROOT / rel
        value = file.read_text(encoding='utf-8')
        if old not in value:
            raise ValueError(f'Expected old version not found in {rel}; no changes applied')
    for rel in FILES:
        file = ROOT / rel
        file.write_text(file.read_text(encoding='utf-8').replace(old, new), encoding='utf-8')
    print(f'Updated release {old} -> {new}. Update CHANGELOG.md and rerun all tests.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
