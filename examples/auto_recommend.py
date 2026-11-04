"""Synthetic example: discover -> human selection -> contract -> execution."""
from __future__ import annotations
import pandas as pd
from quality_dimensions import discover, evaluate_contract


def main():
    df = pd.DataFrame({
        'MPO_ID': ['05001', '05002', '05003', '05004'],
        'VACANTES': [15, 7, -1, 3],
        'FECHA_INICIO': pd.to_datetime(['2026-01-01','2026-02-01','2026-03-01','2026-04-01']),
        'FECHA_FIN': pd.to_datetime(['2026-01-04','2026-02-03','2026-02-28','2026-04-08']),
    })
    recommendations = discover(df, asset_id='ejemplo')
    print('\nPropuestas NO aprobadas:')
    print(recommendations.to_frame().to_string(index=False))
    # The following is an explicit demonstration approval, not default library behavior.
    approved = [r.id for r in recommendations.recommendations
                if r.evaluator in {'builtin.value_range', 'builtin.column_compare'}]
    contract = recommendations.to_contract(approve=approved,
                                            purpose='demostración con aprobación explícita')
    report = evaluate_contract(contract, data={'ejemplo': df})
    print('\nResultados de reglas aprobadas:')
    print(report.to_frame()[['rule_id','status','passed_units','failed_units',
                             'indeterminate_units']].to_string(index=False))
    assert report.results and any(r.status == 'FAIL' for r in report.results)


if __name__ == '__main__':
    main()
