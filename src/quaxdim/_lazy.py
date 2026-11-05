"""Lazy access to optional third-party modules.

pandas and numpy are optional extras (``pip install "quaxdim[pandas]"``).
Modules that need them bind a :class:`LazyModule` at import time, so the
package stays importable without them and fails only when a pandas-based
feature is actually used, with an actionable message.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any


class LazyModule:
    """Proxy that imports ``module_name`` on first attribute access.

    Args:
        module_name: Importable module name, for example ``"pandas"``.
        extra: Name of the ``quaxdim`` extra that installs the module.
    """

    def __init__(self, module_name: str, extra: str) -> None:
        self._module_name = module_name
        self._extra = extra
        self._module: ModuleType | None = None

    def _load(self) -> ModuleType:
        if self._module is None:
            try:
                self._module = importlib.import_module(self._module_name)
            except ImportError as exc:
                raise ImportError(
                    f"This feature requires {self._module_name!r}. "
                    f'Install it with: pip install "quaxdim[{self._extra}]"'
                ) from exc
        return self._module

    def __getattr__(self, name: str) -> Any:
        return getattr(self._load(), name)

    def __repr__(self) -> str:
        state = "loaded" if self._module is not None else "not loaded"
        return f"<LazyModule {self._module_name!r} ({state})>"
