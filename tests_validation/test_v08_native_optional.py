"""Engine-native parity: skipped (not passed) when engines are unavailable.
No conversion to pandas; all comparisons operate on small synthetic fixtures.
"""
import csv
from pathlib import Path
import pytest
from quality_dimensions import evaluate_optimized, discover_advanced, DiscoveryBudget
from test_v08_optimized import _contract, _csv


def _fixture(tmp_path):
    return _csv(tmp_path,[(f'{i:06d}','A' if i%7 else 'B') for i in range(45)]+[('000001','B')])


def _baseline(p):
    c=_contract(
        ('present','builtin.presence',['CODE_ID'],{'operator':'not_null'},'include_as_failure',1.0,'completeness'),
        ('allowed','builtin.allowed_values',['STATUS'],{'operator':'in_set','values':['A','B']},'indeterminate',1.0,'validity'),
        ('regex','builtin.regex_fullmatch',['CODE_ID'],{'operator':'regex','pattern':'[0-9]{6}'},'indeterminate',1.0,'validity'),
        ('unique','builtin.composite_uniqueness',['CODE_ID'],{'operator':'composite_unique','key_fields':['CODE_ID']},'indeterminate',1.0,'uniqueness'))
    baseline=evaluate_optimized(c,data={'data':p})
    return c,{x.rule_id:(x.passed_units,x.failed_units,x.indeterminate_units,x.excluded_units,x.status)
              for x in baseline.results}


def _check(c,p,source,engine):
    expected=_baseline(p)[1]
    actual=evaluate_optimized(c,data={'data':source},engine=engine)
    assert {x.rule_id:(x.passed_units,x.failed_units,x.indeterminate_units,x.excluded_units,x.status)
            for x in actual.results} == expected
    rep=discover_advanced(source,engine=engine,budget=DiscoveryBudget(mode='balanced'))
    assert rep.assets['dataset']['rows']==46
    codes=next(x for x in rep.assets['dataset']['columns'] if x['name']=='CODE_ID')
    assert codes['lexical_digits_only']==46
    assert codes['lexical_min_length']==codes['lexical_max_length']==6
    assert any(r.evaluator=='builtin.regex_fullmatch' for r in rep.recommendations)


def test_v08_duckdb_native_parity(tmp_path):
    pytest.importorskip('duckdb')
    p=_fixture(tmp_path);c,_=_baseline(p)
    _check(c,p,p,'duckdb')


def test_v08_polars_lazy_native_parity(tmp_path):
    pl=pytest.importorskip('polars')
    p=_fixture(tmp_path);c,_=_baseline(p)
    lazy=pl.scan_csv(str(p),infer_schema_length=0)
    _check(c,p,lazy,'polars')


def test_v08_spark_native_parity(tmp_path):
    pytest.importorskip('pyspark')
    from pyspark.sql import SparkSession
    try:
        spark=(SparkSession.builder.master('local[2]').appName('qd-v08-parity')
               .config('spark.ui.enabled','false').getOrCreate())
    except Exception as ex:
        pytest.skip(f'Spark unavailable: {type(ex).__name__}: {ex}')
    try:
        p=_fixture(tmp_path);c,_=_baseline(p)
        from pyspark.sql.types import StructType,StructField,StringType
        schema=StructType([StructField('CODE_ID',StringType()),StructField('STATUS',StringType())])
        df=spark.read.option('header','true').schema(schema).csv(str(p))
        _check(c,p,df,'spark')
    finally:spark.stop()


def test_v08_duckdb_parquet_numeric(tmp_path):
    duckdb=pytest.importorskip('duckdb')
    out=tmp_path/'numeric.parquet'
    with duckdb.connect() as c:
        c.execute(f"COPY (SELECT i::INTEGER AS QUANTITY, (i-1)::INTEGER AS LIMIT_VALUE FROM range(8) t(i)) "
                  f"TO '{str(out).replace(chr(39),chr(39)*2)}' (FORMAT PARQUET)")
    contract=_contract(
        ('min','builtin.value_range',['QUANTITY'],{'operator':'min','value':0},'indeterminate',1.0,'validity'),
        ('compare','builtin.column_compare',['LIMIT_VALUE','QUANTITY'],
         {'operator':'expression','extensions':{'comparison':'lte'}},'indeterminate',1.0,'consistency'),
        fields=('QUANTITY','LIMIT_VALUE'))
    for f in contract['assets'][0]['fields']:
        f.update(physical_type='INTEGER',logical_type='integer')
    report=evaluate_optimized(contract,data={'data':out},engine='duckdb')
    assert all(x.status=='PASS' for x in report.results)
    assert all(x.passed_units==8 for x in report.results)


def test_v08_polars_parquet_numeric(tmp_path):
    pl=pytest.importorskip('polars')
    lf=pl.DataFrame({'QUANTITY':list(range(8)), 'LIMIT_VALUE':list(range(-1,7))}).lazy()
    contract=_contract(
        ('min','builtin.value_range',['QUANTITY'],{'operator':'min','value':0},'indeterminate',1.0,'validity'),
        ('compare','builtin.column_compare',['LIMIT_VALUE','QUANTITY'],
         {'operator':'expression','extensions':{'comparison':'lte'}},'indeterminate',1.0,'consistency'),
        fields=('QUANTITY','LIMIT_VALUE'))
    for f in contract['assets'][0]['fields']:
        f.update(physical_type='Int64',logical_type='integer')
    report=evaluate_optimized(contract,data={'data':lf},engine='polars')
    assert all(x.status=='PASS' for x in report.results)


def test_v08_spark_parquet_numeric(tmp_path):
    pytest.importorskip('pyspark')
    from pyspark.sql import SparkSession, functions as F
    try:spark=SparkSession.builder.master('local[2]').appName('qd-v08-numeric').config('spark.ui.enabled','false').getOrCreate()
    except Exception as ex:pytest.skip(f'Spark unavailable: {ex}')
    try:
        df=spark.range(8).select(F.col('id').alias('QUANTITY'),(F.col('id')-1).alias('LIMIT_VALUE'))
        contract=_contract(
            ('min','builtin.value_range',['QUANTITY'],{'operator':'min','value':0},'indeterminate',1.0,'validity'),
            ('compare','builtin.column_compare',['LIMIT_VALUE','QUANTITY'],
             {'operator':'expression','extensions':{'comparison':'lte'}},'indeterminate',1.0,'consistency'),
            fields=('QUANTITY','LIMIT_VALUE'))
        for f in contract['assets'][0]['fields']:
            f.update(physical_type='bigint',logical_type='integer')
        report=evaluate_optimized(contract,data={'data':df},engine='spark')
        assert all(x.status=='PASS' for x in report.results)
    finally:spark.stop()
