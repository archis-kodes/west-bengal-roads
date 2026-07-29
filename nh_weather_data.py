"""
NH Weather Degradation Tracker
================================

Reads a CSV of national-highway km-points, samples every Nth point
(~1000 out of ~5000), fetches today's weather for each sampled point
from the OpenWeatherMap "Current Weather" API, computes a daily road
"Degradation Value" per formula.md, and stores everything (including a
running "Cumulative Degradation" per point) in a SQLite database.

Usage
-----
    export OWM_API_KEY="your_openweathermap_api_key"
    python main.py --csv nh_km_points.csv --db nh_weather_data.db

Run it once a day (e.g. via cron) to keep the cumulative degradation
figures growing over time.

Notes
-----
* OpenWeatherMap's free tier allows 60 calls/minute. With ~1000 sampled
  points this script paces itself at ~1 call/second (~17 minutes total)
  by default. Adjust --rate-limit-sleep if you're on a paid tier.
* A point is uniquely identified by (highway_name, km). Re-running the
  script on the same calendar date overwrites that day's row instead of
  double-counting it in the cumulative total.
"""

import argparse
import os
import sqlite3
import sys
import time
from datetime import date, datetime

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()  # reads .env in the current working directory (or one you point it to)

OWM_URL = "https://api.openweathermap.org/data/2.5/weather"


# --------------------------------------------------------------------------
# Step 1: Sample the CSV
# --------------------------------------------------------------------------
def load_sampled_points(csv_path: str, step: int = 5) -> pd.DataFrame:
    """Read the km-points CSV and keep every `step`-th row (strictly <= 1/step of rows)."""
    df = pd.read_csv(csv_path)
    required_cols = {"highway_name", "km", "latitude", "longitude"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"CSV is missing required columns: {missing}")

    sampled = df.iloc[::step].reset_index(drop=True)
    print(f"Loaded {len(df)} rows -> sampled {len(sampled)} rows (every {step}th row).")
    return sampled


# --------------------------------------------------------------------------
# Step 2: Fetch weather for a single lat/lon
# --------------------------------------------------------------------------
def fetch_weather(lat: float, lon: float, api_key: str, timeout: int = 15) -> dict:
    """Call OpenWeatherMap current-weather endpoint. Returns extracted fields."""
    params = {
        "lat": lat,
        "lon": lon,
        "appid": api_key,
        "units": "metric",  # temp in Celsius, matches formula.md thresholds
    }
    resp = requests.get(OWM_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    temperature = data.get("main", {}).get("temp")
    humidity = data.get("main", {}).get("humidity")
    # Rain volume for the last 1h, in mm. Absent entirely if it's not raining.
    rain = data.get("rain", {}).get("1h", 0.0)

    return {
        "temperature": temperature,
        "humidity": humidity,
        "rainfall": rain,
    }


# --------------------------------------------------------------------------
# Step 3: Degradation formula (per formula.md)
# --------------------------------------------------------------------------
def calculate_degradation(temperature: float, humidity: float, rainfall: float) -> float:
    t_norm = max(0.0, temperature - 25) / 20
    t_norm = min(t_norm, 1.0)  # clip above 45C

    h_norm = max(0.0, humidity - 60) / 40
    h_norm = min(h_norm, 1.0)  # clip above 100%

    r_norm = min(rainfall / 100, 1.0)

    degradation = 100 * (0.50 * r_norm + 0.30 * t_norm + 0.20 * h_norm)
    return round(degradation, 4)


# --------------------------------------------------------------------------
# Step 4: SQLite storage
# --------------------------------------------------------------------------
def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nh_weather_degradation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            highway_name TEXT NOT NULL,
            km REAL NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            date TEXT NOT NULL,
            temperature REAL,
            humidity REAL,
            rainfall REAL,
            degradation_value REAL,
            cumulative_degradation REAL,
            UNIQUE(highway_name, km, date)
        )
        """
    )
    conn.commit()
    return conn


def get_previous_cumulative(conn: sqlite3.Connection, highway_name: str, km: float, today: str) -> float:
    """Latest cumulative_degradation for this point on a date before today."""
    row = conn.execute(
        """
        SELECT cumulative_degradation FROM nh_weather_degradation
        WHERE highway_name = ? AND km = ? AND date < ?
        ORDER BY date DESC LIMIT 1
        """,
        (highway_name, km, today),
    ).fetchone()
    return row[0] if row else 0.0


def upsert_point(conn: sqlite3.Connection, record: dict) -> None:
    conn.execute(
        """
        INSERT INTO nh_weather_degradation
            (highway_name, km, latitude, longitude, date,
             temperature, humidity, rainfall, degradation_value, cumulative_degradation)
        VALUES (:highway_name, :km, :latitude, :longitude, :date,
                :temperature, :humidity, :rainfall, :degradation_value, :cumulative_degradation)
        ON CONFLICT(highway_name, km, date) DO UPDATE SET
            latitude = excluded.latitude,
            longitude = excluded.longitude,
            temperature = excluded.temperature,
            humidity = excluded.humidity,
            rainfall = excluded.rainfall,
            degradation_value = excluded.degradation_value,
            cumulative_degradation = excluded.cumulative_degradation
        """,
        record,
    )


# --------------------------------------------------------------------------
# Main driver
# --------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="NH road weather-degradation tracker")
    parser.add_argument("--csv", default="data/nh_km_points.csv", help="Path to nh_km_points.csv")
    parser.add_argument("--db", default="data/nh_weather_data.db", help="Path to SQLite DB file")
    parser.add_argument("--step", type=int, default=5, help="Take every Nth row (default 5)")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("OWM_API_KEY") or os.environ.get("OPENWEATHER_API_KEY"),
        help="OpenWeatherMap API key (or set OWM_API_KEY / OPENWEATHER_API_KEY in .env or as an env var)",
    )
    parser.add_argument(
        "--rate-limit-sleep",
        type=float,
        default=1.05,
        help="Seconds to sleep between API calls (default 1.05s ~= 57 calls/min, safe for free tier)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Optional cap on number of points (for testing)")
    args = parser.parse_args()

    if not args.api_key:
        sys.exit(
            "ERROR: No OpenWeatherMap API key provided. "
            "Set OWM_API_KEY env var or pass --api-key."
        )

    points = load_sampled_points(args.csv, step=args.step)
    if args.limit:
        points = points.head(args.limit)

    today = date.today().isoformat()
    conn = init_db(args.db)

    success, failed = 0, 0
    for i, row in points.iterrows():
        highway_name = row["highway_name"]
        km = float(row["km"])
        lat = float(row["latitude"])
        lon = float(row["longitude"])

        try:
            weather = fetch_weather(lat, lon, args.api_key)
            if weather["temperature"] is None or weather["humidity"] is None:
                raise ValueError("Incomplete weather data returned")

            degradation = calculate_degradation(
                weather["temperature"], weather["humidity"], weather["rainfall"]
            )
            prev_cumulative = get_previous_cumulative(conn, highway_name, km, today)
            cumulative = round(prev_cumulative + degradation, 4)

            record = {
                "highway_name": highway_name,
                "km": km,
                "latitude": lat,
                "longitude": lon,
                "date": today,
                "temperature": weather["temperature"],
                "humidity": weather["humidity"],
                "rainfall": weather["rainfall"],
                "degradation_value": degradation,
                "cumulative_degradation": cumulative,
            }
            upsert_point(conn, record)
            conn.commit()
            success += 1

        except Exception as e:
            failed += 1
            print(f"  [WARN] {highway_name} km {km} ({lat},{lon}) failed: {e}")

        if (i + 1) % 25 == 0 or (i + 1) == len(points):
            print(f"Progress: {i + 1}/{len(points)} points processed "
                  f"({success} ok, {failed} failed)")

        time.sleep(args.rate_limit_sleep)

    conn.close()
    print(f"\nDone. {success} points saved to {args.db} for {today}. {failed} failed.")


if __name__ == "__main__":
    main()
