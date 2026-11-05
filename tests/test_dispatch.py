import pandas as pd
import pytest
from quaxdim import evaluate_contract, SparkContractExecutor
from quaxdim.runtime import _spark_modules
from quaxdim.spark_backend import SparkBackendUnavailable


def test_dispatch_rejects_empty_or_unknown():
    with pytest.raises(ValueError, match="at least one"):
        evaluate_contract({}, data={})
    with pytest.raises(TypeError, match="Unknown DataFrame"):
        evaluate_contract({}, data={"x": object()})


def test_dispatch_routes_pandas(monkeypatch):
    class DummyExecutor:
        def run(self, *args, **kwargs):
            return "pandas-route"

    monkeypatch.setattr("quaxdim.runtime.ContractExecutor", DummyExecutor)
    assert (
        evaluate_contract({}, data={"x": pd.DataFrame({"a": [1]})})
        == "pandas-route"
    )


def test_importable_without_pyspark():
    assert SparkContractExecutor is not None
    try:
        _spark_modules()
    except SparkBackendUnavailable as exc:
        assert "spark" in str(exc).lower()
