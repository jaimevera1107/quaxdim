"""Experimental, native PySpark DataFrame backend for contract v0.2.

No pandas conversion, row collection, Python UDF, or implicit coercion of
unsupported semantics. A single-row aggregate per metric returns counts only.
The source DataFrame is never modified. No stable row ordering is assumed.

Runtime requirements: pyspark installed separately; classic DataFrame API.
This module is importable when pyspark is not installed.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
from typing import Any, Mapping
import json
import math

from .evaluator_registry import supports_backend
from .applicability import (ApplicabilityPlanner, EvaluationPlan, Decision, Reason,
                            BUILTIN_CAPABILITIES)
from .execution import (ContractExecutionReport, ContractRuleResult,
                        EvaluationError, _failure_status)


class SparkBackendUnavailable(ImportError):
    """The optional PySpark dependency is unavailable."""


def _spark_modules():
    try:
        from pyspark.sql import DataFrame, functions as F, types as T
    except ImportError as exc:
        raise SparkBackendUnavailable('Install optional extra: pip install ".[spark]"') from exc
    return DataFrame, F, T


def _col(name: str):
    _, F, _ = _spark_modules()
    # Column names are data, never fragments of SQL expressions.
    return F.col('`' + name.replace('`', '``') + '`')


def _value_is_missing(df, field):
    _, F, T = _spark_modules()
    col = _col(field)
    result = col.isNull()
    if isinstance(df.schema[field].dataType, (T.DoubleType, T.FloatType)):
        result = result | F.isnan(col)
    return result


def _pred(df, predicates: Mapping[str, Any] | list[Mapping[str, Any]], *, mode: str):
    """Spark SQL three-valued predicate expression (no raw SQL / eval)."""
    _, F, _ = _spark_modules()
    if isinstance(predicates, Mapping):
        predicates = [predicates]
    if not isinstance(predicates, list) or mode not in {'all', 'any'}:
        raise EvaluationError('Invalid Spark predicate collection')
    out = F.lit(mode == 'all')
    for p in predicates:
        field, op = p.get('field'), p.get('op')
        if field not in df.columns:
            raise EvaluationError(f'Unknown predicate field: {field}')
        c = _col(field)
        if op in {'exists', 'not_null'}:
            term = c.isNotNull()
        elif op in {'not_exists', 'is_null'}:
            term = c.isNull()
        elif op in {'in', 'not_in'}:
            vals = p.get('values', [])
            if not isinstance(vals, list) or any(x is None or isinstance(x, (dict, list)) for x in vals):
                raise EvaluationError('IN requires non-null scalar values')
            term = c.isin(*vals) if vals else F.lit(False)
            if op == 'not_in':
                term = ~term
        elif op == 'between':
            if p.get('from') is None or p.get('to') is None:
                raise EvaluationError('BETWEEN requires both bounds')
            term = c.between(p['from'], p['to'])
        elif op in {'eq', 'ne', 'lt', 'lte', 'gt', 'gte'}:
            val = p.get('value')
            if val is None or isinstance(val, (dict, list)):
                raise EvaluationError('Comparison requires a non-null scalar')
            v = F.lit(val)
            term = {'eq': lambda: c == v, 'ne': lambda: c != v,
                    'lt': lambda: c < v, 'lte': lambda: c <= v,
                    'gt': lambda: c > v, 'gte': lambda: c >= v}[op]()
        else:
            raise EvaluationError(f'Predicate operator not supported: {op}')
        if op not in {'exists', 'not_null', 'not_exists', 'is_null'}:
            term = F.when(_value_is_missing(df, field),
                          F.lit(None).cast('boolean')).otherwise(term)
        out = (out & term) if mode == 'all' else (out | term)
    return out


def _date_type(df, field: str) -> bool:
    _, _, T = _spark_modules()
    return isinstance(df.schema[field].dataType, T.DateType)


def _scope_expr(df, rule):
    """Boolean or NULL; NULL means unknown eligibility, not excluded."""
    _, F, _ = _spark_modules()
    expr = F.lit(True)
    if rule.get('when'):
        expr = expr & _pred(df, rule['when'], mode='all')
    w = rule.get('when_period')
    if w:
        field = w.get('time_field')
        if field not in df.columns or not _date_type(df, field):
            raise EvaluationError('Spark temporal scope requires a native DateType field')
        start, end = date.fromisoformat(w['from']), date.fromisoformat(w['to'])
        if start > end:
            raise EvaluationError('Inverted date window')
        expr = expr & _col(field).between(F.lit(start), F.lit(end))
    if rule.get('exceptions'):
        expr = expr & ~_pred(df, rule['exceptions'], mode='any')
    return expr


def _missing_expr(df, field: str, declaration: Mapping[str, Any]):
    _, F, T = _spark_modules()
    c = _col(field)
    dtype = df.schema[field].dataType
    missing = c.isNull()
    if isinstance(dtype, (T.FloatType, T.DoubleType)):
        missing = missing | F.isnan(c)
    p = declaration.get('missing_policy', {})
    if p.get('when'):
        raise EvaluationError('Conditional missingness is not supported by Spark yet')
    norm = p.get('normalization', 'none')
    if norm not in {'none', 'trim'}:
        # Python's Unicode casefold != Spark SQL lower. Fail closed, don't lie.
        raise EvaluationError('Spark does not guarantee exact Unicode casefold semantics')
    if isinstance(dtype, T.StringType):
        s = F.trim(c) if norm == 'trim' else c
        if p.get('empty_string'):
            missing = missing | (c == '')
        if p.get('whitespace_only'):
            missing = missing | (F.trim(c) == '')
        strings = [x for x in p.get('tokens', []) if isinstance(x, str)]
        if strings:
            if norm == 'trim':
                strings = [x.strip() for x in strings]
            missing = missing | s.isin(*strings)
    else:
        if p.get('empty_string') or p.get('whitespace_only'):
            raise EvaluationError('String missingness policy on non-string field')
        numeric = [x for x in p.get('tokens', []) if isinstance(x, (int, float)) and not isinstance(x, bool)]
        if numeric and isinstance(dtype, T.NumericType):
            missing = missing | c.isin(*numeric)
    return F.coalesce(missing, F.lit(True))


def _sum_bool(expr):
    _, F, _ = _spark_modules()
    return F.coalesce(F.sum(F.when(expr, F.lit(1)).otherwise(F.lit(0))), F.lit(0))


def _aggregate(df, **expressions) -> dict[str, int]:
    row = df.agg(*[expr.alias(key) for key, expr in expressions.items()]).first()
    return {key: int(row[key] or 0) for key in expressions}


def _null_value_state(policy: str):
    return {'exclude': -2, 'include_as_failure': 0,
            'include_as_pass': 1, 'indeterminate': -1}[policy]


def _state(scope, value_ok, is_missing, *, null_policy: str, unknown_policy: str):
    _, F, _ = _spark_modules()
    # Treat selection unknown separately; it is not necessarily a bad value.
    raw = (F.when(scope.isNull(), F.lit(-1))
           .when(~scope, F.lit(-2))
           .when(is_missing, F.lit(_null_value_state(null_policy)))
           .when(value_ok.isNull(), F.lit(-1))
           .when(value_ok, F.lit(1)).otherwise(F.lit(0)))
    if unknown_policy == 'fail':
        raw = F.when(raw == -1, F.lit(0)).otherwise(raw)
    elif unknown_policy == 'exclude':
        raw = F.when(raw == -1, F.lit(-2)).otherwise(raw)
    return raw


def _summary_from_state(df, state, scope) -> dict[str, int]:
    _, F, _ = _spark_modules()
    return _aggregate(df, total=F.count(F.lit(1)),
                      scoped=_sum_bool(scope.eqNullSafe(F.lit(True))),
                      scope_unknown=_sum_bool(scope.isNull()),
                      scope_excluded=_sum_bool(scope.eqNullSafe(F.lit(False))),
                      passed=_sum_bool(state == 1), failed=_sum_bool(state == 0),
                      unknown=_sum_bool(state == -1), excluded=_sum_bool(state == -2))


def _date_delay_expr(df, fields, rule):
    _, F, T = _spark_modules()
    ext = rule['expectation'].get('extensions', {})
    start, end, unit = ext['start'], ext['end'], ext['unit']
    a, b = df.schema[start].dataType, df.schema[end].dataType
    if isinstance(a, T.DateType) and isinstance(b, T.DateType) and unit == 'calendar_days':
        delta = F.datediff(_col(end), _col(start))
    elif (isinstance(a, T.TimestampType) and isinstance(b, T.TimestampType)
          and unit in {'hours', 'seconds'}):
        if any(fields[x].get('logical_type') != 'instant' for x in (start, end)):
            raise EvaluationError('Spark TimestampType delay requires two declared instants')
        if df.sparkSession.conf.get('spark.sql.session.timeZone', 'UTC') != 'UTC':
            raise EvaluationError('Spark instant comparison requires UTC session timezone')
        delta = (F.unix_micros(_col(end)) - F.unix_micros(_col(start))) / F.lit(1_000_000.0)
        if unit == 'hours':
            delta = delta / F.lit(3600.0)
    else:
        raise EvaluationError('Spark delay supports native DateType calendar days or UTC TimestampType instants')
    ceiling = float(rule['expectation']['value'])
    if not math.isfinite(ceiling) or ceiling < 0:
        raise EvaluationError('Delay tolerance must be finite and nonnegative')
    return (delta >= F.lit(0)) & (delta <= F.lit(ceiling)), _col(start).isNull() | _col(end).isNull()


class SparkContractExecutor:
    """Spark classic DataFrame evaluation, without collecting records to driver.

    Implemented: presence, scoped composite uniqueness, native date/timestamp
    maximum delay, small scalar catalog lookups. Temporal catalog entries,
    locale-sensitive normalization, parsing dates from strings, Connect and
    nested/map keys are deliberately UNSUPPORTED, not faked.
    """
    def __init__(self, *, max_catalog_literals: int = 1000):
        if max_catalog_literals < 1:
            raise ValueError('max_catalog_literals must be positive')
        self.max_catalog_literals = max_catalog_literals

    def _preflight(self, contract, data, evidence):
        # Reuse v0.4's robust contract/ID/dependency schema checks; these do
        # not need pandas data and the resulting pandas decisions are discarded.
        baseline = ApplicabilityPlanner().plan(contract)
        baseline_decisions = {d.rule_id: d for d in baseline.decisions}
        DataFrame, F, T = _spark_modules()
        assets = {x['id']: x for x in contract.get('assets', [])}
        metrics = {x['id']: x for x in contract.get('metrics', [])}
        refs = {x['id']: x for x in contract.get('references', [])}
        decisions = []
        for rule in contract.get('rules', []):
            rid, aid, mid = rule['id'], rule['asset_id'], rule['metric_id']
            a, m, df = assets.get(aid), metrics.get(mid), data.get(aid)
            evaluator = m.get('evaluator', '') if m else ''
            status, reasons = 'READY', []
            def mark(state, code, message):
                nonlocal status
                if status == 'READY':
                    status = state
                reasons.append(Reason(code, message))
            if baseline_decisions[rid].status == 'INVALID_CONTRACT':
                status = 'INVALID_CONTRACT'
                reasons.extend(baseline_decisions[rid].reasons)
            if not a or not m:
                mark('INVALID_CONTRACT', 'MISSING_REFERENCE', 'Unknown asset or metric')
            if df is None:
                mark('NEEDS_DATA', 'DATA_UNBOUND', 'No Spark DataFrame supplied')
            elif not isinstance(df, DataFrame):
                mark('UNSUPPORTED', 'NOT_CLASSIC_SPARK_DF', 'Classic pyspark.sql.DataFrame required')
            elif len(df.columns) != len(set(df.columns)):
                mark('INVALID_CONTRACT', 'DUPLICATE_COLUMNS', 'Duplicate column names')
            if rule.get('extensions', {}).get('enabled') is False:
                why = rule.get('extensions', {}).get('reason')
                if not why or not str(why).strip():
                    mark('INVALID_CONTRACT', 'DISABLED_WITHOUT_REASON', 'Disabled rule needs rationale')
                elif status == 'READY':
                    status = 'NOT_APPLICABLE'
                    reasons.append(Reason('EXPLICIT_EXCLUSION', why))
            if status in {'READY', 'NEEDS_DATA'} and a and m:
                names = rule.get('target', {}).get('fields', [])
                declared = {f['name']: f for f in a.get('fields', [])}
                cap = BUILTIN_CAPABILITIES.get(evaluator)
                if not cap or not supports_backend(evaluator, 'spark'):
                    mark('UNSUPPORTED', 'EVALUATOR_UNKNOWN', 'Evaluator not implemented in Spark')
                elif (rule['target']['level'] not in cap.levels or
                      rule['target']['level'] not in m.get('target_levels', []) or
                      rule['expectation']['operator'] not in cap.operators or
                      len(names) < cap.min_fields or
                      (cap.max_fields is not None and len(names) > cap.max_fields)):
                    mark('INVALID_CONTRACT', 'CAPABILITY_MISMATCH', 'Evaluator/level/fields mismatch')
                if a['shape'] != 'table':
                    mark('UNSUPPORTED', 'SHAPE_UNSUPPORTED', 'Only tabular assets')
                if m.get('unit') != 'ratio' or m.get('kind') != 'conformance' or not rule.get('threshold'):
                    mark('UNSUPPORTED', 'METRIC_UNSUPPORTED', 'Only conformance ratios with thresholds')
                if m.get('null_policy', 'indeterminate') not in {
                        'exclude', 'include_as_failure', 'include_as_pass', 'indeterminate'}:
                    mark('UNSUPPORTED', 'NULL_POLICY_UNSUPPORTED', 'Unsupported null policy')
                if m.get('unknown_policy', 'indeterminate') not in {
                        'indeterminate', 'fail', 'exclude'}:
                    mark('UNSUPPORTED', 'UNKNOWN_POLICY_UNSUPPORTED', 'Unsupported unknown policy')
                missing = set(names) - set(declared)
                if df is not None and isinstance(df, DataFrame):
                    missing |= set(names) - set(df.columns)
                if missing:
                    mark('INVALID_CONTRACT', 'MISSING_COLUMNS', str(sorted(missing)))
                if not missing:
                    for name in names:
                        f = declared[name]
                        if f.get('variants'):
                            mark('UNSUPPORTED', 'VARIANTS_UNSUPPORTED', 'Spark variant selection is not yet implemented')
                        p = f.get('missing_policy', {})
                        if p.get('when') or p.get('normalization', 'none') not in {'none', 'trim'}:
                            mark('UNSUPPORTED', 'MISSING_POLICY_UNSUPPORTED',
                                 'Conditional tokens and Unicode casefold not proven on Spark')
                if df is not None and isinstance(df, DataFrame):
                    try:
                        _scope_expr(df, rule)
                        if evaluator == 'builtin.max_delay':
                            _date_delay_expr(df, declared, rule)
                        if evaluator == 'builtin.composite_uniqueness':
                            if any(isinstance(df.schema[x].dataType, (T.ArrayType, T.MapType, T.StructType))
                                   for x in names):
                                raise EvaluationError('Nested keys not supported on Spark')
                    except (EvaluationError, KeyError, ValueError, TypeError) as exc:
                        mark('UNSUPPORTED', 'UNSAFE_SPARK_SEMANTICS', str(exc))
                if evaluator == 'builtin.allowed_values':
                    vals = rule.get('expectation', {}).get('values')
                    if not isinstance(vals, list) or not vals or len(vals) > self.max_catalog_literals:
                        mark('UNSUPPORTED', 'ALLOWED_VALUES_LIMIT', 'Declare 1..max_catalog_literals scalar values')
                    elif any(isinstance(v, (dict, list, set)) or v is None for v in vals):
                        mark('UNSUPPORTED', 'ALLOWED_VALUES_TYPE', 'Scalar non-null values only')
                    elif df is not None and isinstance(df, DataFrame) and names and names[0] in df.columns:
                        dt = df.schema[names[0]].dataType
                        if isinstance(dt, T.StringType):
                            compatible = all(type(v) is str for v in vals)
                        elif isinstance(dt, (T.ByteType, T.ShortType, T.IntegerType, T.LongType)):
                            compatible = all(type(v) is int for v in vals)
                        elif isinstance(dt, T.BooleanType):
                            compatible = all(type(v) is bool for v in vals)
                        else:
                            compatible = False
                        if not compatible:
                            mark('UNSUPPORTED', 'TYPE_MISMATCH', 'Strict string/integer/boolean domains only')
                if evaluator == 'builtin.value_range':
                    ex = rule.get('expectation', {})
                    op = ex.get('operator')
                    lo = ex.get('lower') if op == 'between' else ex.get('value')
                    hi = ex.get('upper') if op == 'between' else ex.get('value')
                    candidates = ([lo, hi] if op == 'between' else [lo])
                    if any(type(v) not in (float, int) or not math.isfinite(v) for v in candidates):
                        mark('INVALID_CONTRACT', 'RANGE_BOUND', 'Finite numeric bounds are required')
                    elif op == 'between' and lo > hi:
                        mark('INVALID_CONTRACT', 'RANGE_ORDER', 'Lower bound must be <= upper bound')
                    elif df is not None and isinstance(df, DataFrame) and names and names[0] in df.columns:
                        dt = df.schema[names[0]].dataType
                        if not isinstance(dt, (T.ByteType, T.ShortType, T.IntegerType, T.LongType,
                                               T.FloatType, T.DoubleType, T.DecimalType)):
                            mark('UNSUPPORTED', 'RANGE_TYPE', 'Spark range requires native numeric type')
                if evaluator == 'reference.lookup_temporal':
                    refid = rule.get('expectation', {}).get('reference_id')
                    entry = evidence.get(refid)
                    spec = refs.get(refid)
                    if spec is None:
                        mark('INVALID_CONTRACT', 'REF_UNDECLARED', 'Catalog ID not declared')
                    elif not isinstance(entry, Mapping) or entry.get('verified') is not True or not entry.get('values'):
                        mark('NEEDS_EVIDENCE', 'REF_UNVERIFIED', 'Reference must provide verified values')
                    elif spec.get('version') and entry.get('version') != spec['version']:
                        mark('NEEDS_EVIDENCE', 'REF_VERSION_MISMATCH', 'Reference version mismatch')
                    elif spec.get('valid_period') and not rule.get('when_period'):
                        mark('NEEDS_CONTEXT', 'REFERENCE_SCOPE_UNBOUND', 'Time-limited reference needs rule window')
                    else:
                        vals = entry['values']
                        if not isinstance(vals, (list, tuple, set)) or len(vals) > self.max_catalog_literals:
                            mark('UNSUPPORTED', 'LARGE_REFERENCE', 'Small scalar list only in Spark v0.5')
                        elif any(isinstance(x, (Mapping, list, set)) or x is None for x in vals):
                            mark('UNSUPPORTED', 'TEMPORAL_REFERENCE', 'Per-code temporal reference not yet implemented')
                        elif df is not None and isinstance(df, DataFrame) and names:
                            target_type = df.schema[names[0]].dataType
                            if isinstance(target_type, T.StringType):
                                compatible = all(isinstance(x, str) for x in vals)
                            elif isinstance(target_type, (T.ByteType,T.ShortType,T.IntegerType,T.LongType)):
                                compatible = all(type(x) is int for x in vals)
                            elif isinstance(target_type, (T.FloatType,T.DoubleType,T.DecimalType)):
                                compatible = all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in vals)
                            elif isinstance(target_type, T.BooleanType):
                                compatible = all(type(x) is bool for x in vals)
                            else:
                                compatible = False
                            if not compatible:
                                mark('UNSUPPORTED', 'CATALOG_TYPE', 'Catalog values must match Spark field type strictly')
                        elif (spec.get('valid_period') and rule.get('when_period')
                              and (rule['when_period']['from'] < spec['valid_period']['from']
                                   or rule['when_period']['to'] > spec['valid_period']['to'])):
                            mark('NEEDS_EVIDENCE', 'REFERENCE_PERIOD', 'Reference does not cover required interval')
            decisions.append(Decision(rid, aid, mid, evaluator, status, tuple(reasons),
                                      dimension_ids=tuple(m.get('dimension_ids', ())) if m else ()))
        # Explicitly identify every node belonging to a dependency cycle.
        index = {x.rule_id: x for x in decisions}
        deps = {x['id']: x.get('dependencies', []) for x in contract.get('rules', [])}
        graph_state, stack, cycle_members = {}, [], set()
        def identify_cycles(rid):
            if graph_state.get(rid) == 1:
                cycle_members.update(stack[stack.index(rid):])
                return
            if graph_state.get(rid) == 2 or rid not in index:
                return
            graph_state[rid] = 1
            stack.append(rid)
            for predecessor in deps.get(rid, []):
                identify_cycles(predecessor)
            stack.pop()
            graph_state[rid] = 2
        for rid in deps:
            identify_cycles(rid)
        memo = {}
        def visit(rid):
            if rid in memo:
                return memo[rid]
            d = index[rid]
            if rid in cycle_members:
                d = Decision(d.rule_id, d.asset_id, d.metric_id, d.evaluator,
                             'INVALID_CONTRACT', (Reason('DEPENDENCY_CYCLE', 'Circular dependency'),),
                             dimension_ids=d.dimension_ids)
            else:
                bad = [p for p in deps.get(rid, [])
                       if p not in index or visit(p).status != 'READY']
                if bad and d.status == 'READY':
                    d = Decision(d.rule_id, d.asset_id, d.metric_id, d.evaluator,
                                 'BLOCKED_DEPENDENCY', (Reason('PREREQUISITE', str(bad)),),
                                 dimension_ids=d.dimension_ids)
            memo[rid] = d
            return d
        return EvaluationPlan(tuple(visit(x.rule_id) for x in decisions),
                              str(contract.get('contract_version', 'unknown')))

    def run(self, contract: Mapping[str, Any], *, data: Mapping[str, Any],
            evidence: Mapping[str, Any] | None = None) -> ContractExecutionReport:
        evidence = evidence or {}
        plan = self._preflight(contract, data, evidence)
        metrics = {m['id']: m for m in contract['metrics']}
        rules = {r['id']: r for r in contract['rules']}
        assets = {a['id']: a for a in contract['assets']}
        decisions = {d.rule_id: d for d in plan.decisions}
        completed = {}
        def execute(rid):
            if rid in completed:
                return completed[rid]
            rule, d = rules[rid], decisions[rid]
            if d.status != 'READY':
                result = self._blocked(d, rule)
            elif any(execute(p).status != 'PASS' for p in rule.get('dependencies', [])):
                result = self._blocked(d, rule, status='BLOCKED_DEPENDENCY', reason='Prerequisite did not pass')
            else:
                try:
                    result = self._measure(rule, metrics[d.metric_id], assets[d.asset_id],
                                           data[d.asset_id], evidence)
                except Exception as exc:
                    # Spark's distributed errors can be backend specific; no fail-open.
                    # Backend exception text may contain sensitive data values.
                    result = self._blocked(d, rule, status='ERROR',
                                           reason=f'Spark operation failed ({type(exc).__name__}); see restricted executor logs')
            completed[rid] = result
            return result
        for rid in rules:
            execute(rid)
        digest = sha256(json.dumps(contract, sort_keys=True, ensure_ascii=False,
                                   separators=(',', ':'), default=str).encode('utf-8')).hexdigest()
        return ContractExecutionReport(str(contract.get('contract_version', 'unknown')), digest,
                                       datetime.now(timezone.utc).isoformat(), plan,
                                       tuple(completed[r] for r in rules))

    @staticmethod
    def _blocked(d, rule, status=None, reason=None):
        from .execution import ContractExecutor
        return ContractExecutor._blocked(d, rule, status=status, reason=reason)

    def _measure(self, rule, metric, asset, df, evidence):
        _, F, _ = _spark_modules()
        fields = {f['name']: f for f in asset['fields']}
        evaluator = metric['evaluator']
        null_policy = metric.get('null_policy', 'indeterminate')
        unknown_policy = metric.get('unknown_policy', 'indeterminate')
        if null_policy not in {'exclude', 'include_as_failure', 'include_as_pass', 'indeterminate'}:
            raise EvaluationError(f'Unknown null policy: {null_policy}')
        if unknown_policy not in {'indeterminate', 'fail', 'exclude'}:
            raise EvaluationError(f'Unknown unknown policy: {unknown_policy}')
        names = rule['target']['fields']
        scope = _scope_expr(df, rule)
        if evaluator == 'builtin.presence':
            unit = 'record' if rule['target']['level'] == 'record' else 'value'
            misses = [_missing_expr(df, x, fields[x]) for x in names]
            if unit == 'record':
                combined = F.lit(False)
                for miss in misses:
                    combined = combined | miss
                exprs = [_state(scope, ~combined, F.lit(False),
                                null_policy='include_as_failure', unknown_policy=unknown_policy)]
            else:
                exprs = [_state(scope, ~miss, F.lit(False),
                                null_policy='include_as_failure', unknown_policy=unknown_policy) for miss in misses]
            raw = [_summary_from_state(df, s, scope) for s in exprs]
            counts = {k: (raw[0][k] if k in {'total','scoped','scope_unknown','scope_excluded'}
                          else sum(z[k] for z in raw)) for k in raw[0]}
        elif evaluator == 'builtin.composite_uniqueness':
            unit = 'record'
            missing = F.lit(False)
            for x in names:
                missing = missing | _missing_expr(df, x, fields[x])
            null_st = _null_value_state(null_policy)
            state_nonvalue = (F.when(scope.isNull(), F.lit(-1))
                              .when(~scope, F.lit(-2))
                              .when(missing, F.lit(null_st)).otherwise(F.lit(99)))
            if unknown_policy == 'fail':
                state_nonvalue = F.when(state_nonvalue == -1, F.lit(0)).otherwise(state_nonvalue)
            elif unknown_policy == 'exclude':
                state_nonvalue = F.when(state_nonvalue == -1, F.lit(-2)).otherwise(state_nonvalue)
            base = _summary_from_state(df, state_nonvalue, scope)
            non_null_keys = df.filter(scope & ~missing).select(*[_col(x) for x in names])
            group_col = '__dq_internal_count'
            while group_col in names:
                group_col += '_'
            grouped = non_null_keys.groupBy(*[_col(x) for x in names]).agg(F.count(F.lit(1)).alias(group_col))
            grouped_agg = _aggregate(grouped,
                                     passed=_sum_bool(_col(group_col) == 1),
                                     failed=F.coalesce(F.sum(F.when(_col(group_col) > 1,
                                                                      _col(group_col)).otherwise(0)), F.lit(0)))
            counts = dict(base)
            counts['passed'] += grouped_agg['passed']
            counts['failed'] += grouped_agg['failed']
        elif evaluator == 'builtin.max_delay':
            unit = 'record'
            ok, missing = _date_delay_expr(df, fields, rule)
            state = _state(scope, ok, missing, null_policy=null_policy, unknown_policy=unknown_policy)
            counts = _summary_from_state(df, state, scope)
        elif evaluator == 'builtin.allowed_values':
            unit = 'value'
            vals = rule['expectation']['values']
            target = names[0]
            ok = _col(target).isin(*vals)
            state = _state(scope, ok, _missing_expr(df, target, fields[target]),
                           null_policy=null_policy, unknown_policy=unknown_policy)
            counts = _summary_from_state(df, state, scope)
        elif evaluator == 'builtin.value_range':
            unit = 'value'
            x = _col(names[0])
            ex = rule['expectation']
            op = ex['operator']
            if op == 'between':
                ok = x.between(F.lit(ex['lower']), F.lit(ex['upper']))
            elif op == 'min':
                ok = x >= F.lit(ex['value'])
            else:
                ok = x <= F.lit(ex['value'])
            state = _state(scope, ok, _missing_expr(df, names[0], fields[names[0]]),
                           null_policy=null_policy, unknown_policy=unknown_policy)
            counts = _summary_from_state(df, state, scope)
        elif evaluator == 'reference.lookup_temporal':
            unit = 'value' 
            vals = list(evidence[rule['expectation']['reference_id']]['values'])
            target = names[0]
            ok = _col(target).isin(*vals)
            state = _state(scope, ok, _missing_expr(df, target, fields[target]),
                           null_policy=null_policy, unknown_policy=unknown_policy)
            counts = _summary_from_state(df, state, scope)
        else:
            raise EvaluationError(f'Unknown evaluator: {evaluator}')
        passed, failed, unknown, excluded = (counts[x] for x in ('passed','failed','unknown','excluded'))
        eligible = passed + failed + unknown
        observed = passed + failed
        measured = passed / observed if observed else None
        lower = passed / eligible if eligible else None
        upper = (passed + unknown) / eligible if eligible else None
        status = _failure_status(lower, upper, rule['threshold']) if eligible else 'NO_ELIGIBLE_DATA'
        return ContractRuleResult(rule_id=rule['id'], asset_id=rule['asset_id'],
            metric_id=metric['id'], dimension_ids=tuple(metric.get('dimension_ids', ())),
            status=status, planner_status='READY', total_rows=counts['total'],
            scoped_rows=counts['scoped'],
            excluded_rows=counts['scope_excluded'],
            unit=unit, eligible_units=eligible, passed_units=passed, failed_units=failed,
            indeterminate_units=unknown, excluded_units=excluded,
            measured_rate=measured, lower_rate=lower, upper_rate=upper,
            assessment_coverage=(observed / eligible if eligible else None),
            threshold=dict(rule['threshold']), severity=rule.get('severity','medium'),
            failure_action=rule.get('failure_action','report'),
            # Spark does not guarantee stable row positions/order. Never invent them.
            sample_failed_positions=(), sample_indeterminate_positions=(),
            reasons=('spark_native_aggregate; no implicit pandas conversion',))
