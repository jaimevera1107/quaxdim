from pathlib import Path
import pandas as pd
from quality_dimensions import QualitySuite, profile

records = pd.DataFrame({
    "entity_id": ["a", "b", "c"],
    "year": [2026, 2026, 2026],
    "department": ["Bogotá", "Antioquia", "Bogotá"],
    "occupied": [5, 9, 3],
    "total": [10, 8, 4],
    "event_date": ["2026-11-01"] * 3,
    "ingested_at": ["2026-11-02"] * 3,
})
expected = pd.DataFrame({"year": [2026, 2026, 2026],
                         "department": ["Bogotá", "Antioquia", "Boyacá"]})
config = Path(__file__).with_name("quality.yaml")
report = QualitySuite.from_yaml(config).run(records,
    references={"expected_universe": expected},
    metadata={"owner": "Example author", "description": "Demonstration", "version": "1"},
    as_of="2026-11-03T00:00:00+00:00")
print(profile(records))
print(report.to_frame()[["id", "dimension", "status", "pass_rate", "message"]])
