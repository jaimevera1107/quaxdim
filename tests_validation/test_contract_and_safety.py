"""Configuration, compatibility and privacy smoke tests."""
import json
from pathlib import Path
import pytest
from quality_dimensions import list_dimensions, list_evaluators, DiscoveryBudget, discover_advanced

ROOT=Path(__file__).resolve().parents[1]


def test_bundled_contract_is_json_schema_valid():
    jsonschema = pytest.importorskip('jsonschema')
    schema = json.loads((ROOT/'contracts/quality-contract.schema.json').read_text(encoding='utf-8'))
    data = json.loads((ROOT/'contracts/contrato_calidad.json').read_text(encoding='utf-8'))
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(data)


def test_dimension_ids_and_evaluators_stable():
    dims = list_dimensions()
    assert len(dims) >= 30
    assert len(list_evaluators()) >= 8


def test_bad_budget_parameters_are_rejected():
    for name,val in (('max_sample_rows',0),('hll_precision',4),('max_profile_columns',-1)):
        with pytest.raises(ValueError):
            DiscoveryBudget(**{name:val})


def test_csv_input_is_unchanged_and_report_is_non_destructive(tmp_path):
    p=tmp_path/'raw.csv'
    p.write_bytes(b'CODIGO_ID,NOMBRE\n00001,Test\n00002,Test2\n')
    before = p.read_bytes()
    output=discover_advanced(p)
    assert p.read_bytes()==before
    assert not output.approved_plan([],purpose='review')['automatic_approval']


def test_unapproved_report_does_not_expose_configuration(tmp_path):
    p=tmp_path/'simple.csv'
    p.write_text('PERSON_ID,ESTADO\n001,ACTIVO\n002,ACTIVO\n',encoding='utf-8')
    r=discover_advanced(p)
    for item in r.to_dict()['recommendations']:
        assert 'expectation' not in item
        assert item['approval_required'] is True


def test_advanced_discovery_top_level_import_is_lazy():
    import subprocess,sys
    code = ('import sys; import quality_dimensions; '
            'assert "pandas" not in sys.modules; '
            'assert "pyspark" not in sys.modules; '
            'assert "polars" not in sys.modules; '
            'assert "duckdb" not in sys.modules; '
            'from quality_dimensions import DiscoveryBudget, discover_advanced; '
            'assert "pandas" not in sys.modules')
    run=subprocess.run([sys.executable,'-S','-c',code],capture_output=True,text=True,timeout=30)
    assert run.returncode==0, run.stderr
