"""Read-only, opt-in assessment of a real user file; no raw records exported."""
import argparse
import json
from pathlib import Path
from quality_dimensions import DiscoveryBudget, discover_advanced


def main():
    p=argparse.ArgumentParser(description='Read-only advanced discovery on a real file')
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--engine',choices=['auto','csv_stream','duckdb','polars'],default='auto')
    p.add_argument('--mode',choices=['schema','balanced','deep'],default='balanced')
    p.add_argument('--sample',type=int,default=1500)
    p.add_argument('--max-columns',type=int,default=64)
    args=p.parse_args()
    if not args.input.is_file():raise FileNotFoundError(args.input)
    if args.input.resolve()==args.output.resolve():raise ValueError('output must not overwrite input')
    report=discover_advanced(args.input,asset_id='real_data',engine=args.engine,budget=DiscoveryBudget(
        mode=args.mode,max_sample_rows=args.sample,max_profile_columns=args.max_columns))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    report.to_json(args.output)
    # Avoid printing real path, raw values or samples in the summary.
    print(json.dumps({'backend':report.assets['real_data']['backend'],
         'rows':report.assets['real_data']['rows'],
         'columns':len(report.assets['real_data']['columns']),
         'proposals':len(report.recommendations),
         'approval_required':True,
         'output_file':args.output.name},ensure_ascii=False))

if __name__=='__main__':main()
