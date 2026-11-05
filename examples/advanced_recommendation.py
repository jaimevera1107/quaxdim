"""Run: python examples/advanced_recommendation.py ruta/datos.csv"""

import sys
from quaxdim import discover_advanced, DiscoveryBudget

if len(sys.argv) < 2:
    raise SystemExit(
        "Uso: python examples/advanced_recommendation.py datos.csv"
    )
report = discover_advanced(
    sys.argv[1], asset_id="datos", budget=DiscoveryBudget(mode="deep")
)
report.to_json("quality_advice.json")
print("Recursos:", report.resource_plan())
print("Recomendaciones prioritarias:")
for rec in report.review_queue(priority="high"):
    print(f"- {rec.kind} | {rec.title} | {rec.cost} | {rec.id}")
print("No se han aprobado ni ejecutado reglas automáticamente.")
