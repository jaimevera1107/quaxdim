"""Reproducible v0.4 execution, without reading any proprietary sources."""

from pathlib import Path
import pandas as pd
from quaxdim import ContractExecutor, load_contract

ROOT = Path(__file__).resolve().parents[1]
contract = load_contract(ROOT / "contracts" / "quality_contract.yaml")
data = pd.DataFrame(
    {
        "YEAR": [2025, 2025, 2025],
        "QUARTER": [1, 1, 1],
        "MPO_ID": ["05001", "05001", "05002"],
        "OCU_ID": ["001", None, "003"],
        "SUELDO_MES": ["Entre 1 y 2", "Entre 2 y 3", "Entre 3 y 4"],
        "FECHA_EVENTO": pd.to_datetime(
            ["2025-02-01", "2025-03-01", "2025-04-01"]
        ),
        "FECHA_CAPTURA": pd.to_datetime(
            ["2025-02-04", "2025-03-04", "2025-04-04"]
        ),
    }
)
# This is a SYNTHETIC catalog. 'verified' is an assertion by the caller,
# not independent verification by the library.
evidence = {
    "ocupaciones_2025": {
        "values": ["001", "003"],
        "verified": True,
        "version": "2025",
    }
}
report = ContractExecutor().run(
    contract, data={"mercado_laboral": data}, evidence=evidence
)
print(
    report.to_frame()[
        [
            "rule_id",
            "status",
            "passed_units",
            "failed_units",
            "indeterminate_units",
            "excluded_units",
            "measured_rate",
        ]
    ].to_string(index=False)
)
print("Global PASS:", report.passed)
report.to_json(ROOT / "examples" / "execution_demo_report.json")
