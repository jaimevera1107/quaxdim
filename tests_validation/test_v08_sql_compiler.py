"""SQL compiler arithmetic and escaping tested independently of optional DuckDB."""

import re
import sqlite3
from quaxdim.optimized import _as_aggregate_sql, _lit, _esc


def test_sql_aggregate_results_and_safe_literals():
    db = sqlite3.connect(":memory:")
    db.create_function(
        "regexp_full_match",
        2,
        lambda x, p: x is not None and re.fullmatch(p, x) is not None,
    )
    db.execute('CREATE TABLE "q"("CODE_ID" TEXT,"STATUS" TEXT)')
    db.executemany(
        'INSERT INTO "q" VALUES (?,?)',
        [("0001", "A"), ("0002", "O'K"), ("00x3", "A"), (None, "A")],
    )
    asset = {
        "fields": [
            {"name": "CODE_ID", "logical_type": "string"},
            {"name": "STATUS", "logical_type": "string"},
        ]
    }
    metrics = {
        "present": {"evaluator": "builtin.presence"},
        "allowed": {
            "evaluator": "builtin.allowed_values",
            "null_policy": "indeterminate",
        },
        "regex": {
            "evaluator": "builtin.regex_fullmatch",
            "null_policy": "indeterminate",
        },
    }
    rules = [
        {
            "id": "r1",
            "metric_id": "present",
            "target": {"fields": ["CODE_ID"]},
            "expectation": {"operator": "not_null"},
        },
        {
            "id": "r2",
            "metric_id": "allowed",
            "target": {"fields": ["STATUS"]},
            "expectation": {"operator": "in_set", "values": ["A", "O'K"]},
        },
        {
            "id": "r3",
            "metric_id": "regex",
            "target": {"fields": ["CODE_ID"]},
            "expectation": {"operator": "regex", "pattern": "[0-9]{4}"},
        },
    ]
    sql = _as_aggregate_sql(rules, metrics, asset, frozenset())
    c = db.execute(f'SELECT {sql} FROM "q"')
    record = dict(zip([x[0] for x in c.description], c.fetchone()))
    assert record["_total"] == 4
    assert record["_r0_pass"] == 3 and record["_r0_fail"] == 1
    assert record["_r1_pass"] == 4 and record["_r1_fail"] == 0
    assert (
        record["_r2_pass"] == 2
        and record["_r2_fail"] == 1
        and record["_r2_unknown"] == 1
    )
    assert _lit("'; DROP TABLE q; --") == "'''; DROP TABLE q; --'"
    assert _esc('column"evil') == '"column""evil"'
    assert db.execute("SELECT COUNT(*) FROM q").fetchone()[0] == 4
    db.close()
