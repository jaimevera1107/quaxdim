"""Public API acceptance checks: no optional analytics engine or pandas required."""
from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path
import subprocess
import sys

import pytest
from quaxdim import DiscoveryBudget, discover_advanced, list_evaluators


def write_csv(path: Path, rows, header=('CODIGO_ID', 'CATEGORIA', 'ANIO', 'VALOR', 'FECHA_INICIO', 'FECHA_FIN'), sep=','):
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle, delimiter=sep)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def test_streaming_determinism_and_no_auto_approval(tmp_path):
    p = write_csv(tmp_path / 'base.csv', [
        (f'{i:06d}', 'A' if i % 2 else 'B', str(2025), str(i % 10), '2025-01-01', '2025-01-02')
        for i in range(150)
    ])
    opts = DiscoveryBudget(mode='deep', max_sample_rows=40, seed=135)
    a, b = (discover_advanced(p, budget=opts) for _ in range(2))
    assert a.to_dict() == b.to_dict()
    assert a.assets['dataset']['rows'] == 150
    assert a.resource_plan()['dataset']['full_passes'] == 1
    assert a.assets['dataset']['columns'][0]['physical_type'] == 'utf8'
    assert all(x.to_dict()['approval_required'] for x in a.recommendations)
    assert a.approved_plan([], purpose='Inspeccion')['automatic_approval'] is False


def test_types_kept_lexical_and_no_private_values_in_report(tmp_path):
    p = write_csv(tmp_path / 'datos.csv', [
        ('000001', 'CONFIDENCIAL_TOKEN_9FC3', '2025', '000.50', '01/02/2025', '03/02/2025'),
        ('000002', 'A', '2025', '-1', '02/02/2025', '04/02/2025'),
    ])
    report = discover_advanced(p, budget=DiscoveryBudget(mode='deep'))
    export = report.to_json()
    assert 'CONFIDENCIAL_TOKEN_9FC3' not in export
    assert '000001' not in export
    assert '01/02/2025' not in export
    assert report.assets['dataset']['columns'][0]['logical_type'] == 'string'


def test_schema_only_does_not_count_rows(tmp_path):
    p = write_csv(tmp_path / 'schema.csv', [('1', 'A', 2025, 1, 'a', 'b')])
    result = discover_advanced(p, budget=DiscoveryBudget(mode='schema'))
    assert result.assets['dataset']['rows'] is None
    assert all(col['nulls'] is None for col in result.assets['dataset']['columns'])
    assert result.resource_plan()['dataset']['full_passes'] == 0


def test_invalid_and_duplicate_headers_fail_closed(tmp_path):
    p = tmp_path / 'bad.csv'
    p.write_text('A,A\n1,2\n', encoding='utf-8')
    with pytest.raises(ValueError, match='unique'):
        discover_advanced(p)


def test_empty_data_and_unicode_headers(tmp_path):
    p = tmp_path / 'unicode.csv'
    p.write_text('CÓDIGO_ID,DESCRIPCIÓN,FECHA\n', encoding='utf-8-sig')
    report = discover_advanced(p)
    assert report.assets['dataset']['rows'] == 0
    assert len(report.assets['dataset']['columns']) == 3


def test_tsv_gzip_and_csv_same_row_count(tmp_path):
    rows = [(f'{i:06d}', 'A', '2025', '2', '2025-01-01', '2025-01-02') for i in range(300)]
    p1 = write_csv(tmp_path / 'base.csv', rows)
    p2 = write_csv(tmp_path / 'base.tsv', rows, sep='\t')
    p3 = tmp_path / 'base.csv.gz'
    with p1.open('rb') as src, gzip.open(p3, 'wb') as dst:
        import shutil
        shutil.copyfileobj(src, dst)
    assert [discover_advanced(p).assets['dataset']['rows'] for p in [p1, p2, p3]] == [300] * 3


def test_missing_fields_warning_not_silent(tmp_path):
    p = tmp_path / 'malformed.csv'
    p.write_text('CODIGO_ID,CATEGORIA\n1,A\n2\n3,B,extra\n', encoding='utf-8')
    report = discover_advanced(p)
    assert any('missing/extra' in msg for msg in report.warnings)


def test_mixed_sources_report_relationships_as_hypotheses(tmp_path):
    a = write_csv(tmp_path / 'a.csv', [('01', 'A', 2025, 1, 'a', 'b')])
    b = write_csv(tmp_path / 'b.csv', [('02', 'B', 2025, 2, 'a', 'b')])
    result = discover_advanced({'source_a': a, 'source_b': b}, budget=DiscoveryBudget(mode='schema'))
    assert len(result.assets) == 2
    assert all(r.to_dict()['approval_required'] for r in result.recommendations)


def test_cli_advise_produces_json(tmp_path):
    p = write_csv(tmp_path / 'input.csv', [('001', 'A', '2025', '1', '2025-01-01', '2025-01-02')])
    output = tmp_path / 'out.json'
    result = subprocess.run(
        [sys.executable, '-m', 'quaxdim.cli', 'advise', str(p), '--asset', 'input',
         '--mode', 'deep', '--output', str(output)],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90,
    )
    assert result.returncode == 0, f'{result.stdout}\n{result.stderr}'
    document = json.loads(output.read_text(encoding='utf-8'))
    assert document['assets']['input']['rows'] == 1


def test_registry_does_not_claim_every_backend():
    specs = list_evaluators()
    assert specs
    assert any('spark' in row['backends'] for row in specs)
    assert any('spark' not in row['backends'] for row in specs)
