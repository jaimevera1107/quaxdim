"""Run from repository root: python examples/plan_applicability.py"""

from pathlib import Path
import pandas as pd
from quaxdim import ApplicabilityPlanner, load_contract

root = Path(__file__).resolve().parents[1]
contract = load_contract(root / "contracts" / "quality_contract.yaml")
data = pd.DataFrame(
    {
        "YEAR": [2025, 2025, 2025],
        "QUARTER": [1, 1, 1],
        "MPO_ID": ["05001", "05001", "05002"],
        "OCU_ID": ["001", None, "003"],
        "SUELDO_MES": ["Hasta 2 SMLV", "Entre 2 y 4 SMLV", "Más de 4 SMLV"],
        "FECHA_EVENTO": pd.to_datetime(
            ["2025-02-01", "2025-03-01", "2025-04-01"]
        ),
        "FECHA_CAPTURA": pd.to_datetime(
            ["2025-02-03", "2025-03-04", "2025-04-04"]
        ),
    }
)
planner = ApplicabilityPlanner()
plan = planner.plan(contract, data={"mercado_laboral": data}, suggest=True)
print(
    plan.to_frame()[
        ["rule_id", "status", "reasons", "eligible_rows", "source"]
    ].to_string(index=False)
)
print("\nEstado global (preflight, NO calidad):", plan.summary())
plan.to_json(root / "examples" / "resultado_plan_demo.json")
