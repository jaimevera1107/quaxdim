"""Small, explicit backend dispatcher for contract v0.2.

No automatic conversion between pandas and Spark. Mixed-engine runs are
rejected to prevent cross-engine coercion and incorrect relationships.
"""
from __future__ import annotations
from typing import Any, Mapping
import pandas as pd

from .execution import ContractExecutor
from .spark_backend import SparkContractExecutor, SparkBackendUnavailable, _spark_modules


def evaluate_contract(contract: Mapping[str, Any], *, data: Mapping[str, Any],
                      evidence: Mapping[str, Any] | None = None):
    """Execute on pandas or classic PySpark DataFrame, without converting rows.

    If Spark is not installed but a Spark-looking DataFrame is supplied,
    raise a precise dependency error. Unknown/mixed backends are rejected.
    """
    if not data:
        raise ValueError('Pass at least one dataset to evaluate_contract')
    if all(isinstance(df, pd.DataFrame) for df in data.values()):
        return ContractExecutor().run(contract, data=data, evidence=evidence)
    if any(type(df).__module__.startswith('pyspark.') for df in data.values()):
        DataFrame, _, _ = _spark_modules()
        if not all(isinstance(df, DataFrame) for df in data.values()):
            raise TypeError('Mixed pandas/Spark assets are not supported in one execution')
        return SparkContractExecutor().run(contract, data=data, evidence=evidence)
    raise TypeError('Unknown DataFrame type; supported: pandas and classic PySpark')
