"""
NH Highway Degradation Aggregator
==================================

Reads the per-point table in `nh_weather_data.db` (produced by main.py),
takes each point's LATEST cumulative_degradation value, sums that up per
highway, multiplies by 15,000, and stores the result in a new database
`highway_degradation.db`.

Why "latest per point"?
    Each point accumulates its own running total over time (see main.py).
    Summing every historical row would double-count days already folded
    into cumulative_degradation. So for each (highway_name, km) point we
    take only its most recent date's cumulative_degradation, then sum
    those across all points on a highway.

Usage
-----
    python aggregate_highway_degradation.py \
        --source-db nh_weather_data.db \
        --dest-db highway_degradation.db
"""

import argparse
import sqlite3
from datetime import datetime, timezone


def get_latest_cumulative_per_point(conn: sqlite3.Connection) -> list[tuple]:
    """Return (highway_name, km, cumulative_degradation) for each point's most recent date."""
    query = """
        SELECT w.highway_name, w.km, w.cumulative_degradation
        FROM nh_weather_degradation w
        INNER JOIN (
            SELECT highway_name, km, MAX(date) AS max_date
            FROM nh_weather_degradation
            GROUP BY highway_name, km
        ) latest
        ON w.highway_name = latest.highway_name
       AND w.km = latest.km
       AND w.date = latest.max_date
    """
    return conn.execute(query).fetchall()


def aggregate_by_highway(rows: list[tuple], multiplier: float = 15000) -> dict:
    """Sum cumulative_degradation per highway, then multiply by `multiplier`."""
    sums: dict[str, float] = {}
    point_counts: dict[str, int] = {}
    for highway_name, km, cumulative_degradation in rows:
        cumulative_degradation = cumulative_degradation or 0.0
        sums[highway_name] = sums.get(highway_name, 0.0) + cumulative_degradation
        point_counts[highway_name] = point_counts.get(highway_name, 0) + 1

    results = {}
    for highway_name, total in sums.items():
        results[highway_name] = {
            "sum_cumulative_degradation": round(total, 4),
            "point_count": point_counts[highway_name],
            "weighted_degradation": round(total * multiplier, 4),
        }
    return results


def init_dest_db(dest_db: str) -> sqlite3.Connection:
    conn = sqlite3.connect(dest_db)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nh_highway_degradation (
            highway_name TEXT PRIMARY KEY,
            point_count INTEGER,
            sum_cumulative_degradation REAL,
            weighted_degradation REAL,
            last_updated TEXT
        )
        """
    )
    conn.commit()
    return conn


def write_results(conn: sqlite3.Connection, results: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for highway_name, vals in results.items():
        conn.execute(
            """
            INSERT INTO nh_highway_degradation
                (highway_name, point_count, sum_cumulative_degradation, weighted_degradation, last_updated)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(highway_name) DO UPDATE SET
                point_count = excluded.point_count,
                sum_cumulative_degradation = excluded.sum_cumulative_degradation,
                weighted_degradation = excluded.weighted_degradation,
                last_updated = excluded.last_updated
            """,
            (
                highway_name,
                vals["point_count"],
                vals["sum_cumulative_degradation"],
                vals["weighted_degradation"],
                now,
            ),
        )
    conn.commit()


def main():
    parser = argparse.ArgumentParser(description="Aggregate per-point degradation into per-highway totals")
    parser.add_argument("--source-db", default="database/nh_weather_data.db", help="Path to the per-point weather DB")
    parser.add_argument("--dest-db", default="database/highway_degradation.db", help="Path to the output highway-level DB")
    parser.add_argument("--multiplier", type=float, default=15000, help="Multiplier applied to the summed cumulative degradation (default 15000)")
    args = parser.parse_args()

    src_conn = sqlite3.connect(args.source_db)
    rows = get_latest_cumulative_per_point(src_conn)
    src_conn.close()

    if not rows:
        print(f"No data found in {args.source_db}. Run main.py first to populate it.")
        return

    results = aggregate_by_highway(rows, multiplier=args.multiplier)

    dest_conn = init_dest_db(args.dest_db)
    write_results(dest_conn, results)
    dest_conn.close()

    print(f"Aggregated {len(rows)} points across {len(results)} highways -> {args.dest_db}\n")
    print(f"{'Highway':<15}{'Points':>8}{'Sum Cum. Degradation':>24}{'x Multiplier':>18}")
    for highway_name, vals in sorted(results.items()):
        print(
            f"{highway_name:<15}{vals['point_count']:>8}"
            f"{vals['sum_cumulative_degradation']:>24}"
            f"{vals['weighted_degradation']:>18}"
        )


if __name__ == "__main__":
    main()
