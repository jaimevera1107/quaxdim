"""v0.8 fair-ish native execution workload using exactly the same CSV fixture.

* generation is timed separately from initialization and execution
* quality rules and expected output are identical across engines
* only aggregate counters return, without pandas or collection of records
* local speed is NOT a distributed Spark capacity claim
"""
from __future__ import annotations
import argparse
import csv
import json
import os
from pathlib import Path
import tempfile
import time
from quaxdim import evaluate_optimized


def contract():
    return {'contract_version':'0.2.0',
            'assets':[{'id':'test','format':'csv','shape':'table','fields':[
                {'name':'CODE_ID','physical_type':'utf8','logical_type':'string'},
                {'name':'STATUS','physical_type':'utf8','logical_type':'string'}]}],
            'metrics':[
                {'id':'m_presence','dimension_ids':['completeness'],'kind':'conformance','unit':'ratio',
                 'evaluator':'builtin.presence','null_policy':'include_as_failure'},
                {'id':'m_allowed','dimension_ids':['validity'],'kind':'conformance','unit':'ratio',
                 'evaluator':'builtin.allowed_values','null_policy':'indeterminate'},
                {'id':'m_pattern','dimension_ids':['validity'],'kind':'conformance','unit':'ratio',
                 'evaluator':'builtin.regex_fullmatch','null_policy':'indeterminate'}],
            'rules':[
                {'id':'presence','asset_id':'test','metric_id':'m_presence',
                 'target':{'level':'value','fields':['CODE_ID']},'expectation':{'operator':'not_null'},
                 'threshold':{'operator':'gte','value':1.0}},
                {'id':'allowed','asset_id':'test','metric_id':'m_allowed',
                 'target':{'level':'value','fields':['STATUS']},
                 'expectation':{'operator':'in_set','values':['A','B']},
                 'threshold':{'operator':'gte','value':1.0}},
                {'id':'pattern','asset_id':'test','metric_id':'m_pattern',
                 'target':{'level':'value','fields':['CODE_ID']},
                 'expectation':{'operator':'regex','pattern':'[0-9]{8}'},
                 'threshold':{'operator':'gte','value':1.0}}]}


def fixture(path:Path, n:int):
    with path.open('w',encoding='utf-8',newline='') as h:
        w=csv.writer(h);w.writerow(('CODE_ID','STATUS'))
        for i in range(n):
            code=f'{i:08d}' if i%2009 else f'X{i:07d}'
            status='BAD' if i%997==0 else ('A' if i%2 else 'B')
            w.writerow((code,status))


def run(engine,n,output):
    if n<=0 or n>100_000_000:raise ValueError('rows out of range')
    with tempfile.TemporaryDirectory(prefix='qd_v08_') as temp:
        source_file=Path(temp)/'workload.csv'
        start=time.perf_counter();fixture(source_file,n);prep_files=time.perf_counter()-start
        spark=None
        try:
            start=time.perf_counter()
            if engine in ('csv_stream','duckdb'):
                src=source_file
            elif engine=='polars':
                import polars as pl
                src=pl.scan_csv(source_file,infer_schema_length=0)
            elif engine=='spark':
                from pyspark.sql import SparkSession
                from pyspark.sql.types import StructType,StructField,StringType
                spark=(SparkSession.builder.master('local[2]').appName('quaxdim-benchmark')
                       .config('spark.ui.enabled','false').getOrCreate())
                schema=StructType([StructField('CODE_ID',StringType()),StructField('STATUS',StringType())])
                src=spark.read.option('header','true').schema(schema).csv(str(source_file))
            else:raise ValueError('engine must be csv_stream, duckdb, polars, spark')
            setup=time.perf_counter()-start
            start=time.perf_counter()
            report=evaluate_optimized(contract(),data={'test':src},engine=engine)
            evaluation=time.perf_counter()-start
            if any(x.status in {'ERROR','UNSUPPORTED','INVALID_CONTRACT'} for x in report.results):
                raise AssertionError('Execution blocked: '+report.to_json())
            assert all(x.total_rows==n for x in report.results)
            expected_bad_status=(n-1)//997+1
            expected_bad_code=(n-1)//2009+1
            result_by_id={x.rule_id:x for x in report.results}
            assert result_by_id['allowed'].failed_units==expected_bad_status
            assert result_by_id['pattern'].failed_units==expected_bad_code
            assert result_by_id['presence'].passed_units==n
            payload={'status':'PASS','engine':engine,'rows':n,'source_bytes':source_file.stat().st_size,
                     'fixture_seconds':round(prep_files,3),'engine_setup_seconds':round(setup,3),
                     'execution_seconds':round(evaluation,3),'rows_per_second':round(n/max(evaluation,1e-9)),
                     'rules':{r.rule_id:{'status':r.status,'passed':r.passed_units,'failed':r.failed_units,
                                           'indeterminate':r.indeterminate_units} for r in report.results},
                     'planned_passes':report.plan.scan_groups['test']['estimated_full_passes'],
                     'raw_rows_collected':0,
                     'notes':'Same CSV fixture and same 3 rules; Spark local and start measured separately. '
                             'Execution times do not measure full cluster throughput; CSV parsing differs by engine.'}
        finally:
            if spark is not None:spark.stop()
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps(payload,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--engine',choices=['csv_stream','duckdb','polars','spark'],required=True)
    parser.add_argument('--rows',type=int,required=True)
    parser.add_argument('--output',type=Path,required=True)
    ns=parser.parse_args()
    run(ns.engine,ns.rows,ns.output)
