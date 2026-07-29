"""
build_km_points.py
===================
One-time post-processing step. Reads the clipped national-highway lines
produced by fetch_data.py (data/nh_clipped.geojson) and, for each
highway, walks its geometry to produce a point every 1 km, then writes
the result to a local SQLite database.

No network access needed -- everything here comes from geometry we
already have on disk.

Output: data/highways.db
  table km_points(highway_name TEXT, km REAL, latitude REAL, longitude REAL)

Also writes data/km_points.csv with the same rows, for quick inspection.

Usage:
    python build_km_points.py
"""

import csv
import math
import os
import sqlite3

import geopandas as gpd
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import linemerge

NH_PATH = "data/sh_clipped.geojson"
DB_PATH = "data/state_highways.db"
CSV_PATH = "data/sh_km_points.csv"

STEP_M = 1000  # 1 km, measured in metres after projecting

# UTM zone 45N -- a good equal-length projection for West Bengal, so
# distances measured on it are accurate in metres.
METRIC_CRS = "EPSG:32645"


def explode_lines(geoms):
    """Flatten a mixed list of LineString/MultiLineString geometries into
    a flat list of plain LineStrings. gpd.clip() can turn a single way
    into a MultiLineString when it's cut into disconnected pieces right
    at the boundary edge -- linemerge() can't accept those directly."""
    flat = []
    for g in geoms:
        if g is None or g.is_empty:
            continue
        if isinstance(g, LineString):
            flat.append(g)
        elif isinstance(g, MultiLineString):
            flat.extend(part for part in g.geoms if not part.is_empty)
        # any other geometry type (shouldn't occur here) is skipped
    return flat


def order_and_merge(geoms):
    """Turn a list of (possibly fragmented, possibly out-of-order)
    LineStrings into a single ordered chain of LineString pieces,
    stitched end to end by nearest endpoint."""
    geoms = explode_lines(geoms)
    if not geoms:
        return []

    merged = linemerge(geoms)

    if isinstance(merged, LineString):
        return [merged]

    pieces = list(merged.geoms) if isinstance(merged, MultiLineString) else list(geoms)
    if not pieces:
        return []

    ordered = [pieces.pop(0)]
    while pieces:
        tail = ordered[-1].coords[-1]
        best_i, best_dist, best_flip = None, math.inf, False
        for i, p in enumerate(pieces):
            for flip, pt in [(False, p.coords[0]), (True, p.coords[-1])]:
                d = math.dist(tail, pt)
                if d < best_dist:
                    best_i, best_dist, best_flip = i, d, flip
        nxt = pieces.pop(best_i)
        if best_flip:
            nxt = LineString(list(nxt.coords)[::-1])
        ordered.append(nxt)
    return ordered


def densify(pieces, step_m):
    """Walk the ordered pieces and emit (km, Point) every step_m metres
    of actual line length. Gaps between disconnected pieces are jumped
    over, not counted towards the distance."""
    points = []
    km_accum = 0.0
    carry = 0.0  # leftover distance rolled over from the previous piece

    for piece in pieces:
        length = piece.length
        d = carry
        while d < length:
            points.append((km_accum, piece.interpolate(d)))
            km_accum += step_m / 1000
            d += step_m
        carry = d - length

    # always include the final point of the route
    if pieces:
        last_pt = pieces[-1].coords[-1]
        points.append((round(km_accum, 2), Point(last_pt)))

    return points


def main():
    if not os.path.exists(NH_PATH):
        raise SystemExit(f"{NH_PATH} not found -- run fetch_data.py first.")

    gdf = gpd.read_file(NH_PATH)
    if gdf.empty or "ref" not in gdf.columns:
        raise SystemExit(f"{NH_PATH} has no usable 'ref'-tagged features.")

    gdf_metric = gdf.to_crs(METRIC_CRS)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("DROP TABLE IF EXISTS km_points")
    conn.execute(
        "CREATE TABLE km_points ("
        "highway_name TEXT, km REAL, latitude REAL, longitude REAL)"
    )

    all_rows = []

    for ref, group in gdf_metric.groupby("ref"):
        pieces = order_and_merge(list(group.geometry))
        raw_points = densify(pieces, STEP_M)
        if not raw_points:
            continue

        # Batch-reproject all points for this highway back to lat/lon in
        # one call (much faster than converting one point at a time).
        kms = [km for km, _ in raw_points]
        pts_metric = gpd.GeoSeries([pt for _, pt in raw_points], crs=METRIC_CRS)
        pts_geo = pts_metric.to_crs(epsg=4326)

        rows = [
            (ref, round(km, 2), pt.y, pt.x)
            for km, pt in zip(kms, pts_geo)
        ]
        all_rows.extend(rows)
        print(f"[build_km_points] {ref}: {len(rows)} points "
              f"(0 - {rows[-1][1]} km)")

    conn.executemany("INSERT INTO km_points VALUES (?, ?, ?, ?)", all_rows)
    conn.execute("CREATE INDEX idx_km_points_highway ON km_points(highway_name, km)")
    conn.commit()
    conn.close()
    print(f"[build_km_points] wrote {DB_PATH} ({len(all_rows)} rows total)")

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["highway_name", "km", "latitude", "longitude"])
        writer.writerows(all_rows)
    print(f"[build_km_points] wrote {CSV_PATH}")


if __name__ == "__main__":
    main()
