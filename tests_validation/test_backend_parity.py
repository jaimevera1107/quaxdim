"""Check only semantics which are documented as comparable across engines.

Here all values are non-null and nonempty, so the CSV lexical vs SQL NULL
policies do not create a false expectation of cross-backend equivalence.
"""
from __future__ import annotations
import csv
from pathlib import Path
import pytest
from quality_dimensions import discover_advanced, DiscoveryBudget


def test_duckdb_polars_csv_basic_parity(tmp_path):
    pytest.importorskip('duckdb')
    pytest.importorskip('polars')
    p=Path(tmp_path)/'parity.csv'
    with p.open('w',newline='',encoding='utf-8') as h:
        w=csv.writer(h)
        w.writerow(['PERSON_ID','OCU_NOMBRE','COUNT_TOTAL'])
        w.writerows((f'{i:06d}', f'Occupation{i%9}', str(i%20+1)) for i in range(1001))
    options=DiscoveryBudget(mode='balanced')
    data={engine: discover_advanced(p,engine=engine,budget=options).assets['dataset']
          for engine in ('csv_stream','duckdb','polars')}
    for name,d in data.items():
        assert d['rows']==1001, name
        assert [col['name'] for col in d['columns']]==['PERSON_ID','OCU_NOMBRE','COUNT_TOTAL']
        assert all(col['observations_profiled']==1001 for col in d['columns'])
        assert all(col['nulls']==0 for col in d['columns'])
        assert all(col['blanks']==0 for col in d['columns'])
    # Approximate cardinality is deliberately NOT compared for exact equality.
