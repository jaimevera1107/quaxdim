from pathlib import Path
import tomllib
root = Path(__file__).resolve().parents[1]
with (root / "pyproject.toml").open("rb") as file:
    print(tomllib.load(file)["project"]["version"])
