"""QuaxDim — scalable and auditable data-quality evaluation.

This public namespace delegates to the established, lazily-loaded
``quality_dimensions`` namespace for backward compatibility.
"""
from importlib import import_module
from quality_dimensions import __all__ as _exports

__version__ = "0.8.0rc1"
__all__ = tuple(_exports)

def __getattr__(name: str):
    if name in __all__:
        value = getattr(import_module("quality_dimensions"), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

def __dir__():
    return sorted(set(globals()) | set(__all__))
