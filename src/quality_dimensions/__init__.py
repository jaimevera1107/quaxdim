"""Quality Dimensions: lazily-loaded public API.

The high-volume advisor does not import pandas, Spark, Polars or DuckDB merely
because the top-level package was imported. Engines load only when requested.
"""
from importlib import import_module

__version__ = "0.8.0rc1"

_EXPORTS = {
    **{k:"core" for k in ("Check","QualitySuite","QualityReport","RuleResult")},
    "profile":"profile",
    **{k:"catalog" for k in ("CATALOG","DimensionSpec","list_dimensions")},
    **{k:"applicability" for k in ("ApplicabilityPlanner","EvaluationPlan","Decision","Reason","Capability","ContractError","load_contract")},
    **{k:"execution" for k in ("ContractExecutor","ContractExecutionReport","ContractRuleResult","EvaluationError")},
    **{k:"spark_backend" for k in ("SparkContractExecutor","SparkBackendUnavailable")},
    "evaluate_contract":"runtime",
    **{k:"optimized" for k in ("plan_optimized","evaluate_optimized","OptimizedPlan","OptimizedReport")},
    **{k:"approval" for k in ("build_reviewed_contract","ApprovalError")},
    **{k:"discovery" for k in ("discover","DiscoveryReport","Recommendation")},
    **{k:"evaluator_registry" for k in ("list_evaluators","REGISTRY")},
    **{k:"advanced_discovery" for k in ("discover_advanced","DiscoveryBudget","AdvancedDiscoveryReport","Advice")},
}
__all__ = list(_EXPORTS)


def __getattr__(name: str):
    if name in _EXPORTS:
        value=getattr(import_module('.'+_EXPORTS[name],__name__),name)
        globals()[name]=value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(_EXPORTS))
