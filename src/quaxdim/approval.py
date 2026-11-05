"""Explicit human approval: transform conservative suggestions into v0.2 contracts.

No suggestion is approved by discovery. No threshold is silently invented.
A generated contract preserves field semantics, review source, and provenance.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Mapping

from .optimized import _PORTABLE


class ApprovalError(ValueError):
    pass


def build_reviewed_contract(report, *, approve: list[str], purpose: str,
                            thresholds: Mapping[str, float | Mapping[str, Any]],
                            semantic_overrides: Mapping[str, Mapping[str, Any]] | None = None):
    """Return a schema-conformant contract for named recommendations only.

    `approve` contains stable recommendation IDs. `thresholds` maps each ID to
    an explicit [0,1] acceptance ratio or a full threshold specification.
    Semantic overrides map `asset.field` to trusted field declaration updates.
    """
    if not purpose or not purpose.strip():raise ApprovalError('Purpose required')
    if not approve or len(set(approve))!=len(approve):raise ApprovalError('Unique nonempty approval list required')
    recs={r.id:r for r in report.recommendations}
    if any(k not in recs for k in approve):raise ApprovalError('Unknown recommendation ID')
    if any(k not in thresholds for k in approve):raise ApprovalError('Explicit threshold required for every approval')
    override=semantic_overrides or {}
    assets=[]
    for aid,a in report.assets.items():
        fields=[]
        for col in a['columns']:
            name=col['name']
            f={'name':name,'physical_type':col['physical_type'],
               'logical_type':col['logical_type'],'nullable':True}
            add=override.get(f'{aid}.{name}',{})
            if 'name' in add and add['name']!=name:raise ApprovalError('Cannot rename a field through semantic_overrides')
            f.update(add)
            fields.append(f)
        assets.append({'id':aid,'format':{'csv_stream':'csv','duckdb':'parquet','polars_lazy':'parquet','spark':'parquet'}.
                       get(a['backend'],'unknown'), 'shape':'table','fields':fields})
    dimension_by_id={};metrics=[];rules=[];notes=[]
    for rid in approve:
        rec=recs[rid]
        if rec.kind!='rule' or rec.evaluator not in _PORTABLE or not rec.expectation:
            raise ApprovalError(f'{rid}: recommendation is a hypothesis/context request, not an executable rule')
        threshold=thresholds[rid]
        if isinstance(threshold,(float,int)) and not isinstance(threshold,bool):threshold={'operator':'gte','value':float(threshold)}
        if not isinstance(threshold,Mapping) or threshold.get('operator') not in {'gte','gt','eq','lte','lt','between'}:
            raise ApprovalError('Invalid explicit threshold')
        if not isinstance(threshold.get('value'),(int,float)) or not 0<=threshold['value']<=1:
            raise ApprovalError('Threshold must be 0..1')
        metricid='metric_'+rid.removeprefix('recommend_')
        dimension=rec.dimension
        if not dimension:raise ApprovalError('Dimension required for executable recommendation')
        level = ('entity' if rec.evaluator=='builtin.composite_uniqueness' else
                 'record' if rec.evaluator=='builtin.column_compare' else 'value')
        dimension_by_id[dimension]={'id':dimension,'name':dimension.replace('_',' ').title(),
                                    'framework':'mahanti_or_extension','version':'1.0.0',
                                    'levels':[level],'measurement_modes':['internal']}
        metrics.append({'id':metricid,'dimension_ids':[dimension],'version':'1.0.0',
                        'kind':'conformance','target_levels':[level],
                        'evaluator':rec.evaluator,'unit':'ratio','direction':'higher_is_better',
                        'denominator_unit':'record' if level in {'entity','record'} else 'value',
                        'null_policy':'include_as_failure' if rec.evaluator=='builtin.presence' else 'indeterminate',
                        'unknown_policy':'indeterminate','evidence_mode':'internal','aggregation':'none'})
        rules.append({'id':'rule_'+rid.removeprefix('recommend_'),'version':'1.0.0',
                      'asset_id':rec.asset_id,'metric_id':metricid,
                      'target':{'level':level,'fields':list(rec.fields)},
                      'expectation':dict(rec.expectation),'threshold':dict(threshold),
                      'severity':'medium','failure_action':'report'})
        notes.append({'recommendation_id':rid,'rule_id':rules[-1]['id'],
                      'evidence':rec.evidence,'caveats':list(rec.caveats),'confirmed_by_user':True})
    result={'contract_version':'0.2.0','assets':assets,'dimensions':list(dimension_by_id.values()),
            'metrics':metrics,'rules':rules,'references':[],
            'evaluation':{'id':'approved_'+sha256('|'.join(approve).encode()).hexdigest()[:12],
                          'purpose':purpose,'mode':'evaluate','preserve_original':True,
                          'accuracy_mode':'exact','ambiguous_parse':'indeterminate',
                          'data_disclosure':'no_raw_values'}}
    # Notes are returned separately because the standard v0.2 contract is closed.
    return result,{'review_date':datetime.now(timezone.utc).isoformat(),
                   'purpose':purpose,'approved':notes,'automatic_approval':False,
                   'warning':'User approval does not validate semantic correctness or engine support.'}
