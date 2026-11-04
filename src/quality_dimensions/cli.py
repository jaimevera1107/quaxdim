"""Minimal local CLI; no network access or remote execution."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from .advanced_discovery import discover_advanced, DiscoveryBudget


def read_data(path: str):
    import pandas as pd
    suffix = Path(path).suffix.casefold()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(path)
    raise ValueError("Only .csv, .parquet, .xlsx, .xls inputs are supported")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qd", description="Auditable data-quality contracts")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("dimensions", help="List the conceptual dimension catalog")
    p_adv = sub.add_parser("advise", help="Out-of-core rule recommendations; no pandas materialization")
    p_adv.add_argument("data", help="CSV (streaming) or Parquet (DuckDB optional)")
    p_adv.add_argument("--asset", default="dataset")
    p_adv.add_argument("--engine", choices=["auto","csv_stream","duckdb","polars"], default="auto")
    p_adv.add_argument("--mode", choices=["schema","balanced","deep"],default="balanced")
    p_adv.add_argument("--sample-rows",type=int,default=1500)
    p_adv.add_argument("--max-columns",type=int,default=64)
    p_adv.add_argument("--delimiter",default=",")
    p_adv.add_argument("--output",default="advanced_recommendations.json")
    p_profile = sub.add_parser("profile", help="Inspect data (no assumptions or mutation)")
    p_profile.add_argument("data")
    p_suggest = sub.add_parser("suggest", help="Discover candidate types, patterns and controls (advisory only)")
    p_suggest.add_argument("data")
    p_suggest.add_argument("--asset", default="dataset")
    p_suggest.add_argument("--output", default="recommendations.json")
    p_suggest.add_argument("--sample-rows", type=int, default=10000)
    p_draft = sub.add_parser("draft", help="Export only explicitly approved suggestions as a contract")
    p_draft.add_argument("data")
    p_draft.add_argument("--asset", default="dataset")
    p_draft.add_argument("--approve", action="append", required=True,
                         help="Approved suggestion ID (repeat flag); use 'qd suggest' to obtain IDs")
    p_draft.add_argument("--purpose", required=True)
    p_draft.add_argument("--output", default="approved_contract.yaml")
    p_draft.add_argument("--sample-rows", type=int, default=10000)
    p_plan = sub.add_parser("plan", help="Plan contract v0.2 applicability (does NOT evaluate quality)")
    p_plan.add_argument("data", help="CSV, Parquet or Excel input; CSV is read lexically as text")
    p_plan.add_argument("--contract", required=True, help="Contract v0.2 JSON/YAML")
    p_plan.add_argument("--asset", required=True, help="Asset ID in contract")
    p_plan.add_argument("--output", default="applicability_plan.json")
    p_plan.add_argument("--suggest", action="store_true", help="Append advisory completeness checks")
    p_plan.add_argument("--evidence-json", help="Optional JSON map of evidence ID to explicit verified bindings")
    p_execute = sub.add_parser("execute", help="Execute supported contract-v0.2 metrics after preflight")
    p_execute.add_argument("data", help="Table input: CSV, Parquet, or Excel (see lexical caveats)")
    p_execute.add_argument("--contract", required=True, help="Contract v0.2 JSON/YAML")
    p_execute.add_argument("--asset", required=True, help="Asset ID in contract")
    p_execute.add_argument("--evidence-json", help="Map of reference ID to explicitly verified value bindings")
    p_execute.add_argument("--output", default="execution_report.json")
    p_native_plan = sub.add_parser("native-plan", help="Plan native out-of-core execution, no data materialization")
    p_native_plan.add_argument("data", help="CSV/TSV/gzip/Parquet path")
    p_native_plan.add_argument("--contract", required=True)
    p_native_plan.add_argument("--asset", required=True)
    p_native_plan.add_argument("--engine", choices=["auto","csv_stream","duckdb","polars"],default="auto")
    p_native_plan.add_argument("--output",default="native_plan.json")
    p_native_exec = sub.add_parser("native-execute", help="Execute portable quality rules natively; no pandas")
    p_native_exec.add_argument("data",help="CSV/TSV/gzip/Parquet path")
    p_native_exec.add_argument("--contract",required=True)
    p_native_exec.add_argument("--asset",required=True)
    p_native_exec.add_argument("--engine",choices=["auto","csv_stream","duckdb","polars"],default="auto")
    p_native_exec.add_argument("--output",default="native_report.json")
    p_native_exec.add_argument("--max-exact-keys",type=int,default=1000000)
    p_run = sub.add_parser("run", help="Evaluate YAML checks (legacy v0.1 interface)")
    p_run.add_argument("data")
    p_run.add_argument("--config", required=True)
    p_run.add_argument("--output", default="quality_report.json")
    p_run.add_argument("--reference", action="append", default=[], metavar="NAME=PATH")
    p_run.add_argument("--metadata", help="Optional JSON file with source metadata")
    p_run.add_argument("--as-of", help="Freeze the clock for reproducible freshness checks")
    args = parser.parse_args(argv)
    try:
        if args.command == "advise":
            if len(args.delimiter)!=1:raise ValueError("CSV delimiter must be one character")
            budget=DiscoveryBudget(mode=args.mode,max_sample_rows=args.sample_rows,
                                   max_profile_columns=args.max_columns)
            result=discover_advanced(args.data,asset_id=args.asset,engine=args.engine,
                                     budget=budget,csv_delimiter=args.delimiter)
            result.to_json(args.output)
            breakdown={}
            for rec in result.recommendations:
                breakdown[rec.kind]=breakdown.get(rec.kind,0)+1
            print(f"Backend={result.assets[args.asset]['backend']}; rows={result.assets[args.asset]['rows']}; "
                  f"proposals={len(result.recommendations)} ({breakdown}); report={args.output}")
            print("No rule was automatically approved or executed.")
            return 0
        if args.command in ("native-plan", "native-execute"):
            from .applicability import load_contract
            from .optimized import plan_optimized, evaluate_optimized
            contract=load_contract(args.contract)
            params={"data":{args.asset:args.data},"engine":args.engine}
            if args.command == "native-plan":
                out=plan_optimized(contract,**params)
                out.to_json(args.output)
                print(json.dumps(out.to_dict(),ensure_ascii=False,indent=2))
                return 0 if all(x.status=='READY' for x in out.rules) else 1
            out=evaluate_optimized(contract,**params,max_exact_keys=args.max_exact_keys)
            out.to_json(args.output)
            print(json.dumps([{"rule_id":r.rule_id,"status":r.status,"measured_rate":r.measured_rate,
                               "coverage":r.assessment_coverage} for r in out.results],ensure_ascii=False,indent=2))
            print(f"Overall: {'PASS' if out.passed else 'NOT PASS'}; report={args.output}")
            return 0 if out.passed else 1
        if args.command == "dimensions":
            from .catalog import list_dimensions
            print(json.dumps(list_dimensions(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "profile":
            from .profile import profile
            print(profile(read_data(args.data)).to_string(index=False))
            return 0
        if args.command in ("suggest", "draft"):
            from .discovery import discover
            import pandas as pd
            path = Path(args.data)
            if path.suffix.lower() == '.csv':
                # Avoid stripping leading zeros or treating NA as missing without consent.
                data = pd.read_csv(path, dtype='string', keep_default_na=False)
            else:
                data = read_data(args.data)
            discovery = discover(data, asset_id=args.asset, sample_rows=args.sample_rows)
            if args.command == 'suggest':
                discovery.to_json(args.output)
                print(discovery.to_frame().to_string(index=False))
                print(f"Advisory suggestions only; no rule enabled. report={args.output}")
            else:
                result = discovery.to_contract(approve=args.approve, purpose=args.purpose)
                import yaml
                Path(args.output).write_text(yaml.safe_dump(result, allow_unicode=True, sort_keys=False),
                                             encoding='utf-8')
                print(f"Explicitly approved contract: {len(result['rules'])} rules; file={args.output}")
            return 0
        if args.command == "plan":
            from .applicability import ApplicabilityPlanner, load_contract
            import pandas as pd
            contract = load_contract(args.contract)
            source_path = Path(args.data)
            if source_path.suffix.lower() == ".csv":
                # Lexical values such as 0001 remain strings; dates need explicit patterns.
                frame = pd.read_csv(source_path, dtype="string", keep_default_na=False)
            else:
                # XLSX presentation formats may not survive pandas import: inspect original
                # workbook separately before assuming semantics of formatted cells.
                frame = read_data(args.data)
            evidence = (json.loads(Path(args.evidence_json).read_text(encoding="utf-8"))
                        if args.evidence_json else {})
            plan = ApplicabilityPlanner().plan(contract, data={args.asset: frame},
                                               evidence=evidence, suggest=args.suggest)
            plan.to_json(args.output)
            print(plan.to_frame()[["rule_id", "status", "reasons", "eligible_rows"]].to_string(index=False))
            print(f"Preflight only (no quality rules executed); report={args.output}")
            return 0 if all(x.status in {"READY", "NOT_APPLICABLE", "RECOMMENDED", "NO_ELIGIBLE_DATA"}
                            for x in plan.decisions) else 1
        if args.command == "execute":
            from .applicability import load_contract
            from .execution import ContractExecutor
            import pandas as pd
            contract = load_contract(args.contract)
            path = Path(args.data)
            if path.suffix.lower() == ".csv":
                frame = pd.read_csv(path, dtype="string", keep_default_na=False)
            else:
                frame = read_data(args.data)
            ev = (json.loads(Path(args.evidence_json).read_text(encoding="utf-8"))
                  if args.evidence_json else {})
            report = ContractExecutor().run(contract, data={args.asset: frame}, evidence=ev)
            report.to_json(args.output)
            print(report.to_frame()[["rule_id", "status", "measured_rate",
                                     "assessment_coverage", "reasons"]].to_string(index=False))
            print(f"Overall: {'PASS' if report.passed else 'NOT PASS'}; report={args.output}")
            return 0 if report.passed else 1
        from .core import QualitySuite
        refs = {}
        for spec in args.reference:
            name, sep, path = spec.partition("=")
            if not sep or not name or not path:
                raise ValueError("--reference must be NAME=PATH")
            refs[name] = read_data(path)
        metadata = json.loads(Path(args.metadata).read_text(encoding="utf-8")) if args.metadata else {}
        report = QualitySuite.from_yaml(args.config).run(read_data(args.data), references=refs,
                                                        metadata=metadata, as_of=args.as_of)
        report.to_json(args.output)
        print(report.to_frame().to_string(index=False))
        print(f"Result: {'PASS' if report.passed else 'NOT PASS'}; report={args.output}")
        return 0 if report.passed else 1
    except (ValueError, OSError, TypeError, KeyError, ImportError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
