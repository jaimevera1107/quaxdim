"""Real optional-backend scaling exercise; never converts full input into pandas.

For large synthetic runs, time generation separately from quality discovery.
Only bounded summary rows leave Spark, Polars and DuckDB.
"""
from __future__ import annotations
import argparse
import csv
import json
import os
from pathlib import Path
import tempfile
import threading
import time


def bench(engine, rows, output):
    import psutil
    from quaxdim import DiscoveryBudget, discover_advanced
    proc=psutil.Process(os.getpid())
    rss=[proc.memory_info().rss]
    stop=threading.Event()
    def monitor():
        while not stop.wait(.05):
            try:rss[0]=max(rss[0],proc.memory_info().rss)
            except psutil.Error:break
    thread=threading.Thread(target=monitor,daemon=True)
    thread.start()
    payload=None
    spark=None
    try:
        with tempfile.TemporaryDirectory(prefix='qd_native_bench_') as td:
            td=Path(td)
            started_generation=time.perf_counter()
            if engine=='duckdb':
                import duckdb
                p=td/'source.parquet'
                # Engine-native out-of-core generation (no Python list of rows).
                with duckdb.connect() as db:
                    target=str(p).replace("'","''")
                    db.execute(f"COPY (SELECT i AS ROW_ID, i % 600 AS OCUPACION_ID, "
                               f"i % 21 AS CONTEO_TOTAL FROM range({rows}) t(i)) "
                               f"TO '{target}' (FORMAT PARQUET)")
                source=p
            elif engine=='polars':
                # Generate a temporary CSV sequentially and read through LazyFrame.
                import polars as pl
                p=td/'source.csv'
                with p.open('w',encoding='utf-8',newline='') as h:
                    w=csv.writer(h);w.writerow(['ROW_ID','OCUPACION_ID','CONTEO_TOTAL'])
                    for i in range(rows):w.writerow([i,i%600,i%21])
                source=pl.scan_csv(p, infer_schema_length=0)
            elif engine=='spark':
                from pyspark.sql import SparkSession,functions as F
                spark=(SparkSession.builder.master('local[2]').appName('quaxdim-benchmark')
                       .config('spark.ui.enabled','false').getOrCreate())
                source=spark.range(rows).select(
                    F.col('id').alias('ROW_ID'),(F.col('id')%600).alias('OCUPACION_ID'),
                    (F.col('id')%21).alias('CONTEO_TOTAL'))
            else:raise ValueError(engine)
            generated_in=time.perf_counter()-started_generation
            started=time.perf_counter()
            out=discover_advanced(source,engine='auto' if engine=='spark' else engine,
                                  budget=DiscoveryBudget(mode='balanced',max_profile_columns=8))
            elapsed=time.perf_counter()-started
            assert out.assets['dataset']['rows']==rows, out.assets['dataset']['rows']
            assert out.resource_plan()['dataset']['full_passes']==1
            payload={'status':'PASS','engine':engine,'rows':rows,
                     'preparation_seconds':round(generated_in,3),
                     'discovery_seconds':round(elapsed,3),
                     'rows_per_discovery_second':round(rows/max(elapsed,1e-9),1),
                     'driver_or_process_peak_rss_mb':None,
                     'recommendations':len(out.recommendations),
                     'raw_rows_returned_to_driver':out.resource_plan()['dataset']['raw_rows_returned_to_driver'],
                     'limitations':'Local test only; driver RSS excludes Spark worker/executor memory.'}
    finally:
        if spark is not None:spark.stop()
        stop.set();thread.join(timeout=2)
        rss[0]=max(rss[0],proc.memory_info().rss)
    payload['driver_or_process_peak_rss_mb']=round(rss[0]/1048576,2)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps(payload,ensure_ascii=False))

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--engine',choices=['duckdb','polars','spark'],required=True)
    ap.add_argument('--rows',type=int,required=True)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    bench(args.engine,args.rows,args.output)
