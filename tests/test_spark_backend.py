"""Optional, engine-real integration tests; skip if pyspark is not installed.

No mock can establish equivalence with Spark SQL execution, so these tests
must be run on a machine with pyspark, Java, and a usable local Spark runtime.
"""
from copy import deepcopy
from datetime import date
from pathlib import Path
import json
import pytest

from quaxdim import SparkContractExecutor

ROOT = Path(__file__).resolve().parents[1]


def _contract():
    c = json.loads((ROOT / 'contracts/quality_contract.json').read_text(encoding='utf-8'))
    for f in c['assets'][0]['fields']:
        if f['name'] == 'OCU_ID':
            f['missing_policy']['normalization'] = 'none'
        if f['name'] in {'FECHA_EVENTO', 'FECHA_CAPTURA'}:
            f['physical_type'] = 'date32'
            f['logical_type'] = 'date'
            f.pop('representation', None)
    return c


@pytest.fixture(scope='module')
def spark():
    pyspark = pytest.importorskip('pyspark', reason='PySpark not installed in this environment')
    from pyspark.sql import SparkSession
    session = SparkSession.builder.master('local[2]').appName('quality-dimensions-tests').getOrCreate()
    try:
        yield session
    finally:
        session.stop()


def _data(spark):
    from pyspark.sql import types as T
    schema = T.StructType([
        T.StructField('YEAR', T.IntegerType()),
        T.StructField('QUARTER', T.IntegerType()),
        T.StructField('MPO_ID', T.StringType()),
        T.StructField('OCU_ID', T.StringType()),
        T.StructField('FECHA_EVENTO', T.DateType()),
        T.StructField('FECHA_CAPTURA', T.DateType()),
    ])
    return spark.createDataFrame([
        (2025, 1, '05001', '001', date(2025,2,1), date(2025,2,4)),
        (2025, 1, '05001', None, date(2025,3,1), date(2025,3,4)),
        (2025, 1, '05002', '003', date(2025,4,1), date(2025,4,4)),
    ], schema=schema)


def _run(spark, c=None, ev=None):
    c = c or _contract()
    if ev is None:
        ev = {'ocupaciones_2025': {'values':['001','003'], 'verified':True, 'version':'2025'}}
    return SparkContractExecutor().run(c, data={'mercado_laboral':_data(spark)}, evidence=ev)


def test_native_spark_four_metrics(spark):
    report = _run(spark)
    rows = {x.rule_id: x for x in report.results}
    assert rows['OCU_ID_required'].status == 'FAIL'
    assert (rows['OCU_ID_required'].passed_units, rows['OCU_ID_required'].failed_units) == (2,1)
    assert rows['OCU_ID_2025_catalog'].status == 'PASS'
    assert rows['grain_unique'].status == 'INDETERMINATE'
    assert rows['grain_unique'].indeterminate_units == 1
    assert rows['grain_unique'].lower_rate == pytest.approx(2/3)
    assert rows['capture_within_7_days'].status == 'PASS'
    assert rows['capture_within_7_days'].total_rows == 3


def test_unsupported_unicode_casefold_is_not_silent(spark):
    c = _contract()
    f = next(x for x in c['assets'][0]['fields'] if x['name'] == 'OCU_ID')
    f['missing_policy']['normalization'] = 'trim_casefold'
    rows = {x.rule_id: x for x in _run(spark, c).results}
    for rid in ('OCU_ID_required','grain_unique','OCU_ID_2025_catalog'):
        assert rows[rid].status == 'UNSUPPORTED'
        assert rows[rid].measured_rate is None


def test_missing_evidence_fails_closed(spark):
    rows = {x.rule_id: x for x in _run(spark, ev={}).results}
    assert rows['OCU_ID_2025_catalog'].status == 'NEEDS_EVIDENCE'
    assert rows['OCU_ID_2025_catalog'].measured_rate is None


def test_cycle_does_not_execute(spark):
    c = _contract()
    c['rules'][0]['dependencies'] = ['grain_unique']
    c['rules'][2]['dependencies'] = ['OCU_ID_required']
    rows = {x.rule_id: x for x in _run(spark, c).results}
    assert rows['OCU_ID_required'].status == 'INVALID_CONTRACT'
    assert rows['grain_unique'].status == 'INVALID_CONTRACT'


def test_no_valid_records_does_not_pass(spark):
    c = _contract()
    df = _data(spark).filter('1 = 0')
    report = SparkContractExecutor().run(c, data={'mercado_laboral': df},
        evidence={'ocupaciones_2025': {'values':['001','003'], 'verified':True, 'version':'2025'}})
    assert not report.passed
    assert all(x.status != 'PASS' for x in report.results)


def test_native_spark_allowed_values_and_range(spark):
    c = _contract()
    c['metrics'].extend([
        {'id':'m_allowed','dimension_ids':['validity'],'version':'1.0.0',
         'kind':'conformance','target_levels':['value'],'evaluator':'builtin.allowed_values',
         'unit':'ratio','direction':'higher_is_better','denominator_unit':'value',
         'null_policy':'indeterminate','unknown_policy':'indeterminate'},
        {'id':'m_range','dimension_ids':['validity'],'version':'1.0.0',
         'kind':'conformance','target_levels':['value'],'evaluator':'builtin.value_range',
         'unit':'ratio','direction':'higher_is_better','denominator_unit':'value',
         'null_policy':'indeterminate','unknown_policy':'indeterminate'}])
    c['rules'].extend([
        {'id':'catalog_approved','version':'1.0.0','asset_id':'mercado_laboral',
         'metric_id':'m_allowed','target':{'level':'value','fields':['OCU_ID']},
         'expectation':{'operator':'in_set','values':['001','003']},
         'threshold':{'operator':'gte','value':1.0}},
        {'id':'year_bounds','version':'1.0.0','asset_id':'mercado_laboral',
         'metric_id':'m_range','target':{'level':'value','fields':['YEAR']},
         'expectation':{'operator':'between','lower':2020,'upper':2026},
         'threshold':{'operator':'gte','value':1.0}}])
    rows = {x.rule_id:x for x in _run(spark,c).results}
    assert rows['catalog_approved'].status == 'INDETERMINATE'
    assert rows['catalog_approved'].passed_units == 2
    assert rows['catalog_approved'].indeterminate_units == 1
    assert rows['year_bounds'].status == 'PASS'
    assert rows['year_bounds'].passed_units == 3


def test_spark_schema_and_aggregated_discovery(spark):
    from quaxdim import discover
    df = _data(spark)
    r = discover(df, asset_id='mercado_laboral')
    assert r.backend == 'spark' and r.row_count is None
    assert r.warnings and all(x.kind == 'rule' for x in r.recommendations)
    measured = discover(df, asset_id='mercado_laboral', spark_mode='aggregate')
    assert measured.row_count == 3
    o = next(x for x in measured.profile if x['name']=='OCU_ID')
    assert o['intrinsic_nulls'] == 1 and o['non_null'] == 2
