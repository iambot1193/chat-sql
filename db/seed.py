"""Builds db/analytics.db from the NHTSA vPIC public API (real US vehicle data).

Free, no key. https://vpic.nhtsa.dot.gov/api/
Run once (or delete db/analytics.db and rerun) — takes a few minutes, ~500 requests.
"""

import json
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path

DB_PATH = Path(__file__).parent / "analytics.db"
BASE = "https://vpic.nhtsa.dot.gov/api/vehicles"

MAKES = [
    "Toyota", "Ford", "Honda", "Chevrolet", "Nissan", "Jeep", "Hyundai",
    "Kia", "Subaru", "Volkswagen", "BMW", "Mercedes-Benz", "Audi", "Mazda",
    "GMC", "Ram", "Dodge", "Lexus", "Tesla", "Volvo",
]
YEARS = range(2015, 2026)

SCHEMA = """
CREATE TABLE vehicles (
    id           INTEGER PRIMARY KEY,
    make         TEXT NOT NULL,
    model        TEXT NOT NULL,
    model_year   INTEGER NOT NULL,
    vehicle_type TEXT NOT NULL
);
"""


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.load(r)


def _types_for_make(make: str) -> list[str]:
    data = _get(f"{BASE}/GetVehicleTypesForMake/{make}?format=json")
    # NHTSA returns duplicate rows for some makes (e.g. Ford, Ram) — dedupe
    # or the same model/year gets fetched and inserted multiple times.
    return sorted({row["VehicleTypeName"] for row in data["Results"]})


def _models(make: str, year: int, vehicle_type: str) -> list[str]:
    vtype = urllib.parse.quote(vehicle_type, safe="")
    url = f"{BASE}/GetModelsForMakeYear/make/{make}/modelyear/{year}/vehicletype/{vtype}?format=json"
    data = _get(url)
    return sorted({row["Model_Name"] for row in data["Results"]})


def main():
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)

    rows = []
    for make in MAKES:
        types = _types_for_make(make)
        print(f"{make}: {types}")
        for year in YEARS:
            for vtype in types:
                for model in _models(make, year, vtype):
                    rows.append((make, model, year, vtype))
                time.sleep(0.05)  # be polite to a free public API

    conn.executemany(
        "INSERT INTO vehicles (make, model, model_year, vehicle_type) VALUES (?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    print(f"created {DB_PATH} with {len(rows)} rows")


if __name__ == "__main__":
    main()
