"""Regresiones para máscaras de nulos y presupuestos compatibles con pandas 3."""

from __future__ import annotations
import pandas as pd
from quaxdim.execution import _is_missing


def test_missing_mask_is_independently_writable():
    data = pd.Series([None, "ND", " ", "001"])
    declaration = {
        "missing_policy": {"whitespace_only": True, "tokens": ["ND"]}
    }
    mask = _is_missing(data, declaration)
    assert mask.tolist() == [True, True, True, False]
    assert mask.flags.writeable is True
    mask[0] = False
    assert pd.isna(data.iloc[0])


def test_missing_tokens_do_not_modify_source():
    data = pd.Series(["ND", "001", None, " "])
    before = data.copy(deep=True)
    declaration = {
        "missing_policy": {"whitespace_only": True, "tokens": ["ND"]}
    }
    assert _is_missing(data, declaration).tolist() == [True, False, True, True]
    pd.testing.assert_series_equal(data, before)


def test_missing_policy_survives_readonly_boolean_views(monkeypatch):
    original = pd.Series.to_numpy

    def simulate_pandas3_readonly(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if kwargs.get("dtype") is bool and not kwargs.get("copy", False):
            result = result.copy()
            result.flags.writeable = False
        return result

    monkeypatch.setattr(pd.Series, "to_numpy", simulate_pandas3_readonly)
    values = pd.Series(["ND", " ", "001", None])
    policy = {"missing_policy": {"whitespace_only": True, "tokens": ["ND"]}}
    result = _is_missing(values, policy)
    assert result.tolist() == [True, True, False, True]
