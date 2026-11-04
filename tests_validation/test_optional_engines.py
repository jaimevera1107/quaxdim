"""Actual engine integration checks; skip when dependency unavailable, never mock.

Run with `python -m pytest -q tests_validation/test_optional_engines.py`.
"""
from __future__ import annotations
import csv
from pathlib import Path
import pytest
from quality_dimensions import DiscoveryBudget, discover_advanced


def _csv(tmp_path):
    p = tmp_path / 'engine_fixture.csv'
    with p.open('w', encoding='utf-8', newline='') as out:
        w = csv.writer(out)
        w.writerow(['PERSON_ID', 'CLASE', 'VALOR'])
        w.writerows((f'{i:06d}', 'A' if i % 3 else 'B', str(i % 17)) for i in range(101))
    return p


def _expect(report, *, backend, n=101):
    d = report.assets['dataset']
    assert d['backend'] == backend
    assert d['rows'] == n
    assert len(d['columns']) == 3
    assert all(c['observations_profiled'] == n for c in d['columns'])
    assert report.recommendations
    assert all(r.to_dict()['approval_required'] for r in report.recommendations)
    if backend != 'csv_stream':
        assert report.resource_plan()['dataset']['raw_rows_returned_to_driver'] == 0


def test_duckdb_csv_schema_and_aggregate(tmp_path):
    pytest.importorskip('duckdb')
    p = _csv(tmp_path)
    schema = discover_advanced(p, engine='duckdb', budget=DiscoveryBudget(mode='schema'))
    assert schema.assets['dataset']['rows'] is None
    report = discover_advanced(p, engine='duckdb', budget=DiscoveryBudget(mode='balanced'))
    _expect(report, backend='duckdb')


def test_duckdb_parquet_native(tmp_path):
    duckdb = pytest.importorskip('duckdb')
    p = _csv(tmp_path)
    out = tmp_path / 'fixture.parquet'
    with duckdb.connect() as conn:
        # Local tiny fixture only. No pandas materialization.
        source_sql = str(p).replace("'", "''")
        target_sql = str(out).replace("'", "''")
        conn.execute(f"COPY (SELECT * FROM read_csv('{source_sql}', all_varchar=true)) TO '{target_sql}' (FORMAT PARQUET)")
    report = discover_advanced(out, engine='duckdb', budget=DiscoveryBudget(mode='balanced'))
    _expect(report, backend='duckdb')


def test_polars_lazy_csv_and_dataframe(tmp_path):
    pl = pytest.importorskip('polars')
    p = _csv(tmp_path)
    lazy = pl.scan_csv(p, infer_schema_length=0)
    a = discover_advanced(lazy, budget=DiscoveryBudget(mode='balanced'))
    _expect(a, backend='polars_lazy')
    b = discover_advanced(pl.DataFrame({'PERSON_ID':['01','02'], 'CLASE':['A','B'],'VALOR':['1','2']}),
                          budget=DiscoveryBudget(mode='balanced'))
    _expect(b, backend='polars_lazy', n=2)


def test_polars_parquet_lazy(tmp_path):
    pl = pytest.importorskip('polars')
    p = _csv(tmp_path)
    out = tmp_path / 'fixture.parquet'
    pl.scan_csv(p, infer_schema_length=0).sink_parquet(out)
    r = discover_advanced(pl.scan_parquet(out), budget=DiscoveryBudget(mode='balanced'))
    _expect(r, backend='polars_lazy')


@pytest.fixture(scope='module')
def spark():
    pytest.importorskip('pyspark')
    from pyspark.sql import SparkSession
    spark = (SparkSession.builder.master('local[2]')
             .appName('quality-dimensions-acceptance')
             .config('spark.ui.enabled','false')
             .getOrCreate())
    try:
        yield spark
    finally:
        spark.stop()


def test_spark_schema_and_distributed_aggregate(spark):
    from pyspark.sql import functions as F
    df = spark.range(101).select(
        F.lpad(F.col('id').cast('string'), 6, '0').alias('PERSON_ID'),
        F.when((F.col('id') % 3) == 0, 'B').otherwise('A').alias('CLASE'),
        (F.col('id') % 17).alias('VALOR'))
    schema = discover_advanced(df, budget=DiscoveryBudget(mode='schema'))
    assert schema.assets['dataset']['rows'] is None
    r = discover_advanced(df, budget=DiscoveryBudget(mode='balanced'))
    _expect(r, backend='spark')
    assert r.resource_plan()['dataset']['distributed_jobs'] == 1


def test_spark_large_input_uses_aggregates(spark):
    from pyspark.sql import functions as F
    df = spark.range(100_001).select(F.col('id').alias('PERSON_ID'), F.lit('A').alias('CLASE'))
    report = discover_advanced(df, budget=DiscoveryBudget(mode='balanced'))
    asset = report.assets['dataset']
    assert asset['rows'] == 100_001
    assert report.resource_plan()['dataset']['raw_rows_returned_to_driver'] == 0
    assert report.resource_plan()['dataset']['distributed_jobs'] == 1
