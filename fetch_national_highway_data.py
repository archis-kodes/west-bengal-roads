"""
fetch_data.py
=============
Prepares the two GeoJSON files the web map (index.html / script.js) needs:

  data/wb_boundary.geojson   - administrative boundary of West Bengal
  data/nh_clipped.geojson    - national highway segments, clipped exactly
                                to the West Bengal boundary

Data sources:
  - Boundary: OSM via Nominatim (a single lightweight geocoding request,
    through osmnx.geocode_to_gdf).
  - Highways: a LOCAL OSM extract (.osm.pbf), read directly through
    GDAL's built-in "OSM" vector driver via geopandas.read_file(). This
    is the same GDAL that geopandas already uses to read/write GeoJSON,
    so it needs no extra packages -- specifically no pyrosm/cykhash,
    which has no prebuilt Windows wheel and needs a C++ compiler.

One-time setup before running this script:
  1. Download an OSM extract that covers West Bengal, e.g. the India
     extract from Geofabrik:
     https://download.geofabrik.de/asia/india-latest.osm.pbf
  2. Save it as: data/india-latest.osm.pbf
     (or point PBF_PATH below at wherever you saved it)

Usage:
    pip install -r requirements.txt
    python fetch_data.py
"""

import os
import re
import sys

import geopandas as gpd
import osmnx as ox

OUT_DIR = "data"
BOUNDARY_PATH = os.path.join(OUT_DIR, "wb_boundary.geojson")
HIGHWAYS_PATH = os.path.join(OUT_DIR, "nh_clipped.geojson")
PBF_PATH = os.path.join(OUT_DIR, "india-latest.osm.pbf")

GEOFABRIK_URL = "https://download.geofabrik.de/asia/india-latest.osm.pbf"

# Buffer (in degrees) applied around the WB boundary when reading from
# the PBF, so highways crossing the border are captured in full before
# being clipped.
QUERY_BUFFER_DEG = 0.3

HIGHWAY_CLASSES = ["trunk", "trunk_link", "primary", "primary_link"]

# Official NH numbers running through West Bengal (WB Traffic Police list).
WB_NH_REFS = {
    "NH2", "NH6", "NH31", "NH31A", "NH31C", "NH32", "NH34", "NH35",
    "NH41", "NH55", "NH60", "NH60A", "NH80", "NH81", "NH117", "NH116B",
}

REF_PATTERN = re.compile(r'"ref"=>"([^"]*)"')
NETWORK_PATTERN = re.compile(r'"network"=>"([^"]*)"')


def log(msg):
    print(f"[fetch_data] {msg}")


def get_boundary() -> gpd.GeoDataFrame:
    log("Resolving West Bengal boundary via Nominatim (single lightweight request)...")
    wb = ox.geocode_to_gdf("West Bengal, India")
    if wb.empty:
        sys.exit("Could not resolve 'West Bengal, India' via Nominatim.")
    keep = [c for c in ["geometry", "display_name", "bbox_north", "bbox_south",
                         "bbox_east", "bbox_west"] if c in wb.columns]
    wb = wb[keep]
    wb = wb.set_crs(epsg=4326, allow_override=True)
    return wb


def extract_tag(other_tags, pattern):
    if not isinstance(other_tags, str):
        return None
    m = pattern.search(other_tags)
    return m.group(1) if m else None


def get_highways_from_pbf(pbf_path: str, boundary_geom) -> gpd.GeoDataFrame:
    if not os.path.exists(pbf_path):
        sys.exit(
            f"Missing {pbf_path}.\n\n"
            f"Download an OSM extract covering West Bengal (e.g. the India "
            f"extract from {GEOFABRIK_URL}) and save it as:\n  {pbf_path}\n"
            f"then re-run this script."
        )

    # Let GDAL's OSM driver use a larger intermediate SQLite file --
    # needed for a whole-country PBF, otherwise it can hit its default
    # 100MB temp-file cap partway through.
    os.environ.setdefault("OSM_MAX_TMPFILE_SIZE", "4000")
    os.environ.setdefault("OGR_INTERLEAVED_READING", "YES")

    minx, miny, maxx, maxy = boundary_geom.buffer(QUERY_BUFFER_DEG).bounds
    highway_list = "', '".join(HIGHWAY_CLASSES)
    where_clause = f"highway IN ('{highway_list}')"

    log(f"Reading {pbf_path} locally via GDAL's OSM driver "
        f"(bbox-filtered, where {where_clause})...")
    log("This can take a few minutes the first time on a whole-country file.")

    try:
        lines = gpd.read_file(
            pbf_path,
            layer="lines",
            bbox=(minx, miny, maxx, maxy),
            where=where_clause,
        )
    except Exception as e:
        sys.exit(
            f"Could not read the PBF via GDAL's OSM driver: {e}\n\n"
            f"Your GDAL build may not include OSM driver support. As a "
            f"fallback, install QGIS (bundles a full GDAL) or the OSGeo4W "
            f"installer, then run this script using that Python."
        )

    if lines.empty:
        sys.exit("No trunk/primary ways found in the PBF for this area -- "
                  "check that the file actually covers West Bengal.")

    lines = lines.set_crs(epsg=4326, allow_override=True)

    if "other_tags" in lines.columns:
        lines["ref"] = lines["other_tags"].apply(lambda t: extract_tag(t, REF_PATTERN))
        lines["network"] = lines["other_tags"].apply(lambda t: extract_tag(t, NETWORK_PATTERN))
    else:
        lines["ref"] = None
        lines["network"] = None

    def is_national(row):
        ref = str(row.get("ref") or "").upper().replace(" ", "")
        network = str(row.get("network") or "").upper()
        return ref in WB_NH_REFS or "NH" in ref or network == "IN:NH"

    mask = lines.apply(is_national, axis=1)
    nh = lines[mask].copy()

    if nh.empty:
        log("WARNING: nothing matched an NH ref/network; keeping all "
            "trunk/primary roads in the area instead.")
        nh = lines.copy()

    keep_cols = [c for c in ["osm_id", "name", "ref", "network", "highway", "geometry"]
                 if c in nh.columns]
    return nh[keep_cols]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    wb = get_boundary()
    wb.to_file(BOUNDARY_PATH, driver="GeoJSON")
    log(f"Wrote {BOUNDARY_PATH}")

    boundary_geom = wb.geometry.iloc[0]

    nh = get_highways_from_pbf(PBF_PATH, boundary_geom)
    log(f"Read {len(nh)} candidate highway segments; clipping to West Bengal boundary...")

    clipped = gpd.clip(nh, wb)
    clipped = clipped[~clipped.geometry.is_empty]
    clipped = clipped[clipped.geometry.type.isin(["LineString", "MultiLineString"])]

    clipped.to_file(HIGHWAYS_PATH, driver="GeoJSON")
    log(f"Wrote {HIGHWAYS_PATH} ({len(clipped)} clipped segments)")

    found_refs = set(clipped["ref"].dropna().astype(str).str.upper().str.replace(" ", ""))
    missing = WB_NH_REFS - found_refs
    if missing:
        log(f"NOTE: no segments found for: {sorted(missing)} "
            f"(check ref/network tagging in OSM for these routes)")

    log("Done. Next: python build_km_points.py")


if __name__ == "__main__":
    main()
