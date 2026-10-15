"""Fire hotspot data service.

Demo mode (default): reads data/fire_records.json (clearly labelled SAMPLE data).
Live mode: if FIRMS_MAP_KEY is set, downloads recent observations from the
official NASA FIRMS API. If that fails, we fall back to demo mode and say so.
"""
import csv
import io
import json
import os

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAMPLE_FILE = os.path.join(BASE_DIR, "data", "fire_records.json")
FIRMS_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/{area}/{days}"


def category_from_confidence(value):
    """Convert confidence (number 0-100, or VIIRS letters l/n/h) to an alert category."""
    text = str(value).strip().lower()
    if text in ("h", "high"):
        return "High"
    if text in ("n", "nominal", "medium"):
        return "Medium"
    if text in ("l", "low"):
        return "Low"
    try:
        number = float(text)
    except ValueError:
        return "Low"
    return "High" if number >= 80 else "Medium" if number >= 50 else "Low"


def load_sample():
    with open(SAMPLE_FILE, encoding="utf-8") as f:
        return json.load(f)


def fetch_live(key):
    url = FIRMS_URL.format(
        key=key,
        source=os.getenv("FIRMS_SOURCE", "VIIRS_SNPP_NRT"),
        area=os.getenv("FIRMS_AREA", "68,6,98,36"),
        days=os.getenv("FIRMS_DAYS", "2"),
    )
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(response.text)))
    if rows and "latitude" not in rows[0]:
        raise ValueError(response.text[:120])  # FIRMS returns plain-text error messages
    records = []
    for i, row in enumerate(rows[:500], start=1):
        conf = row.get("confidence", "")
        lat, lon = float(row["latitude"]), float(row["longitude"])
        records.append({
            "id": "L-%03d" % i, "location": "%.2f, %.2f" % (lat, lon),
            "latitude": lat, "longitude": lon, "date": row.get("acq_date", ""),
            "confidence": conf, "frp": row.get("frp", ""),
            "category": category_from_confidence(conf),
            "source": "NASA FIRMS (live)", "is_sample": False,
        })
    return records


def get_fire_data():
    """Return {'mode': 'demo'|'live', 'note': str, 'records': [...]}."""
    key = os.getenv("FIRMS_MAP_KEY", "").strip()
    if not key:
        return {"mode": "demo", "records": load_sample(),
                "note": "Demonstration mode: these are SAMPLE records, not real current events. "
                        "Add a NASA FIRMS key in .env to use live data."}
    try:
        return {"mode": "live", "records": fetch_live(key),
                "note": "Live observed hotspots from NASA FIRMS."}
    except Exception:  # network problems, invalid key, bad response ...
        return {"mode": "demo", "records": load_sample(),
                "note": "Live NASA FIRMS data is unavailable (check your key and internet connection). "
                        "Showing SAMPLE demonstration records instead."}
