"""Actual v0.8 integration against stdlib CSV (no pandas dependency)."""
import csv
import json
from pathlib import Path
import pytest
from quaxdim import (discover_advanced, DiscoveryBudget,
    build_reviewed_contract, ApprovalError, evaluate_optimized, plan_optimized)


def _asset(*names):
    return {'id':'data','format':'csv','shape':'table','fields':[
        {'name':x,'physical_type':'utf8','logical_type':'string'} for x in names]}


def _contract(*items, fields=('CODE_ID','STATUS')):
    ms=[];rs=[];ds={}
    for rid,ev,target,expect,null_policy,threshold,dim in items:
        mid='m_'+rid
        level='entity' if ev=='builtin.composite_uniqueness' else 'value'
        ms.append({'id':mid,'dimension_ids':[dim],'version':'1.0',
                   'kind':'conformance','target_levels':[level],
                   'evaluator':ev,'unit':'ratio','direction':'higher_is_better',
                   'denominator_unit':'record' if level=='entity' else 'value',
                   'null_policy':null_policy})
        rs.append({'id':rid,'version':'1.0','asset_id':'data','metric_id':mid,
                   'target':{'level':level,'fields':target},'expectation':expect,
                   'threshold':{'operator':'gte','value':threshold}})
        ds[dim]={'id':dim,'name':dim,'framework':'mahanti','version':'1.0','levels':[level],
                 'measurement_modes':['internal']}
    return {'contract_version':'0.2.0','assets':[_asset(*fields)],
            'dimensions':list(ds.values()),'metrics':ms,'rules':rs}


def _csv(tmp_path, rows, cols=('CODE_ID','STATUS')):
    p=tmp_path/'fixture.csv'
    with p.open('w',encoding='utf-8',newline='') as h:
        w=csv.writer(h);w.writerow(cols);w.writerows(rows)
    return p


def test_fused_rules_one_stream_and_trivalued(tmp_path):
    p=_csv(tmp_path,[('001','A'),('002','B'),('002','BAD'),('','A'),('003','A')])
    con=_contract(
        ('present','builtin.presence',['CODE_ID'],{'operator':'not_null'},'include_as_failure',.8,'completeness'),
        ('allowed','builtin.allowed_values',['STATUS'],{'operator':'in_set','values':['A','B']},'indeterminate',.8,'validity'),
        ('duplicate','builtin.composite_uniqueness',['CODE_ID'],{'operator':'composite_unique','key_fields':['CODE_ID']},'indeterminate',.8,'uniqueness'))
    con['assets'][0]['fields'][0]['missing_policy']={'empty_string':True}
    plan=plan_optimized(con,data={'data':p})
    assert plan.scan_groups['data']['fused_aggregates']==2
    assert plan.scan_groups['data']['fused_batches']==1
    assert plan.scan_groups['data']['estimated_full_passes']==1
    assert plan.scan_groups['data']['exact_key_checks']==1
    assert plan.scan_groups['data']['raw_rows_collected']==0
    rep=evaluate_optimized(con,data={'data':p})
    byid={x.rule_id:x for x in rep.results}
    assert (byid['present'].passed_units,byid['present'].failed_units)==(4,1)
    assert (byid['allowed'].passed_units,byid['allowed'].failed_units)==(4,1)
    assert (byid['duplicate'].passed_units,byid['duplicate'].failed_units,byid['duplicate'].indeterminate_units)==(2,2,1)
    assert byid['duplicate'].status=='FAIL'
    assert byid['duplicate'].lower_rate==pytest.approx(.4)
    assert byid['duplicate'].upper_rate==pytest.approx(.6)
    assert not rep.passed
    assert all(r.total_rows==5 for r in rep.results)
    assert '001' not in rep.to_json()  # no raw samples


def test_approval_is_explicit_and_schema_valid(tmp_path):
    p=_csv(tmp_path,[(f'{i:04d}','A') for i in range(30)])
    report=discover_advanced(p,budget=DiscoveryBudget(mode='deep'))
    q=[r for r in report.recommendations if r.kind=='rule' and r.evaluator=='builtin.presence' and r.fields==('CODE_ID',)]
    assert len(q)==1
    with pytest.raises(ApprovalError):build_reviewed_contract(report,approve=[q[0].id],purpose='Audit',thresholds={})
    contract,trail=build_reviewed_contract(report,approve=[q[0].id],purpose='Audit',thresholds={q[0].id:1.0})
    import jsonschema
    schema=json.loads((Path(__file__).parents[1]/'src'/'quaxdim'/'schemas'/'quality-contract.schema.json').read_text())
    jsonschema.validate(contract,schema)
    assert not trail['automatic_approval']
    out=evaluate_optimized(contract,data={'dataset':p})
    assert out.passed and out.results[0].status=='PASS'


def test_discovery_code_width_full_csv(tmp_path):
    p=_csv(tmp_path,[(f'{i:06d}','A') for i in range(200)])
    r=discover_advanced(p,budget=DiscoveryBudget(mode='balanced'))
    c=next(x for x in r.assets['dataset']['columns'] if x['name']=='CODE_ID')
    assert c['lexical_min_length']==c['lexical_max_length']==6
    assert c['lexical_digits_only']==200
    assert r.capability_coverage()['dataset']['families']['builtin.presence']==2
    assert r.to_json().find('000001')==-1


def test_unsupported_is_not_pass(tmp_path):
    p=_csv(tmp_path,[(str(x),'A') for x in range(3)])
    con=_contract(('v','builtin.value_range',['CODE_ID'],{'operator':'min','value':0},'indeterminate',1.0,'validity'))
    con['assets'][0]['fields'][0]['logical_type']='integer'
    out=evaluate_optimized(con,data={'data':p})
    assert out.results[0].status=='UNSUPPORTED'
    assert not out.passed


def test_csv_exact_key_budget_does_not_silently_approve(tmp_path):
    p=_csv(tmp_path,[(str(x),'A') for x in range(10)])
    con=_contract(('unique','builtin.composite_uniqueness',['CODE_ID'],{'operator':'composite_unique','key_fields':['CODE_ID']},'indeterminate',1.0,'uniqueness'))
    out=evaluate_optimized(con,data={'data':p},max_exact_keys=3)
    assert out.results[0].status=='ERROR'
    assert 'MemoryError' in out.results[0].reasons[0]


def test_no_automatic_acceptance_of_wrong_regex(tmp_path):
    p=_csv(tmp_path,[('0001','A'),('0002','A')])
    con=_contract(('rx','builtin.regex_fullmatch',['CODE_ID'],{'operator':'regex','pattern':'.*'},'indeterminate',1.0,'validity'))
    out=evaluate_optimized(con,data={'data':p})
    assert out.results[0].status=='UNSUPPORTED'


def test_gzip_and_tsv_stream(tmp_path):
    import gzip
    p=tmp_path/'s.tsv.gz'
    with gzip.open(p,'wt',encoding='utf-8',newline='') as h:
        h.write('CODE_ID\tSTATUS\n0001\tA\n0002\tB\n')
    con=_contract(('present','builtin.presence',['CODE_ID'],{'operator':'not_null'},'indeterminate',1.0,'completeness'))
    assert evaluate_optimized(con,data={'data':p}).passed


def test_empty_dataset_unmeasurable(tmp_path):
    p=_csv(tmp_path,[])
    con=_contract(('present','builtin.presence',['CODE_ID'],{'operator':'not_null'},'indeterminate',1.0,'completeness'))
    assert evaluate_optimized(con,data={'data':p}).results[0].status=='NO_ELIGIBLE_DATA'
