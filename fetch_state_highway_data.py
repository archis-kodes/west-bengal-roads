"""
fetch_state_highway_data.py
============================
Same approach as fetch_data.py, but for STATE highways (SH) instead of
national highways (NH).

Produces:
  data/sh_clipped.geojson   - state highway segments, clipped to the
                               West Bengal boundary, with a normalized
                               "ref" field like "SH4", "SH12A", etc.

Reuses data/wb_boundary.geojson if it already exists (from running
fetch_data.py first); otherwise fetches it fresh via Nominatim.

Reads highways from the same local PBF as fetch_data.py
(data/india-latest.osm.pbf) via GDAL's OSM driver -- no new
dependencies, no live Overpass calls.

Why this is messier than the NH version:
  OSM's state-highway tagging in India is much less standardized than
  NH tagging. Refs show up as "SH 4", "SH-4", "SH4", "WB SH 4", etc,
  and the road class varies (secondary most often, but also tertiary,
  primary, even trunk in places). This script normalizes whatever it
  finds into "SH<number><suffix>" form (e.g. "SH12A") so it lines up
  with your official list, and flags anything it can't match.

Usage:
    python fetch_state_highway_data.py
"""

import os
import re
import sys

import geopandas as gpd
import osmnx as ox

OUT_DIR = "data"
BOUNDARY_PATH = os.path.join(OUT_DIR, "wb_boundary.geojson")
SH_PATH = os.path.join(OUT_DIR, "sh_clipped.geojson")
PBF_PATH = os.path.join(r"D:\NH_WB_PROJECT\files\data\india-latest.osm.pbf")

QUERY_BUFFER_DEG = 0.3

# Broader than the NH classes -- SH routes in WB are tagged all over the
# map depending on how well-maintained/widened that stretch is.
HIGHWAY_CLASSES = [
    "trunk", "trunk_link",
    "primary", "primary_link",
    "secondary", "secondary_link",
    "tertiary", "tertiary_link",
]

# Official SH numbers from the WB Traffic Police "List of State Highways
# in West Bengal" table (route sections summed per SH number).
WB_SH_REFS = {
    "SH1", "SH2", "SH3", "SH4", "SH4A", "SH5", "SH6", "SH7", "SH8",
    "SH9", "SH10", "SH10A", "SH11", "SH11A", "SH12", "SH12A", "SH13",
    "SH14", "SH15",
}

REF_PATTERN = re.compile(r'"ref"=>"([^"]*)"')
NETWORK_PATTERN = re.compile(r'"network"=>"([^"]*)"')

# Matches "SH 4", "SH-4", "SH4", "SH 12A", "WB SH 4" etc and captures the
# number(+letter) part.
SH_NUMBER_PATTERN = re.compile(r'SH[\s\-]?0*(\d+[A-Z]?)', re.IGNORECASE)


def log(msg):
    print(f"[fetch_state_highway_data] {msg}")


def get_boundary() -> gpd.GeoDataFrame:
    if os.path.exists(BOUNDARY_PATH):
        log(f"Using existing {BOUNDARY_PATH}")
        return gpd.read_file(BOUNDARY_PATH)

    log("No existing boundary file found -- resolving via Nominatim...")
    wb = ox.geocode_to_gdf("West Bengal, India")
    if wb.empty:
        sys.exit("Could not resolve 'West Bengal, India' via Nominatim.")
    keep = [c for c in ["geometry", "display_name", "bbox_north", "bbox_south",
                         "bbox_east", "bbox_west"] if c in wb.columns]
    wb = wb[keep]
    wb = wb.set_crs(epsg=4326, allow_override=True)
    wb.to_file(BOUNDARY_PATH, driver="GeoJSON")
    log(f"Wrote {BOUNDARY_PATH}")
    return wb


def extract_tag(other_tags, pattern):
    if not isinstance(other_tags, str):
        return None
    m = pattern.search(other_tags)
    return m.group(1) if m else None


def normalize_sh_ref(raw_ref, network):
    """Try to pull a clean 'SH<n><letter>' identifier out of whatever OSM
    put in ref/network. Returns None if nothing SH-like is found."""
    for source in (raw_ref, network):
        if not source:
            continue
        m = SH_NUMBER_PATTERN.search(str(source))
        if m:
            return f"SH{m.group(1).upper()}"
    return None


def get_state_highways_from_pbf(pbf_path: str, boundary_geom) -> gpd.GeoDataFrame:
    if not os.path.exists(pbf_path):
        sys.exit(
            f"Missing {pbf_path}.\n"
            f"Use the same PBF you already downloaded for fetch_data.py "
            f"(save/symlink it at {pbf_path})."
        )

    os.environ.setdefault("OSM_MAX_TMPFILE_SIZE", "4000")
    os.environ.setdefault("OGR_INTERLEAVED_READING", "YES")

    minx, miny, maxx, maxy = boundary_geom.buffer(QUERY_BUFFER_DEG).bounds
    highway_list = "', '".join(HIGHWAY_CLASSES)
    where_clause = f"highway IN ('{highway_list}')"

    log(f"Reading {pbf_path} locally via GDAL's OSM driver "
        f"(bbox-filtered, where {where_clause})...")
    log("This can take a few minutes on a whole-country file.")

    try:
        lines = gpd.read_file(
            pbf_path,
            layer="lines",
            bbox=(minx, miny, maxx, maxy),
            where=where_clause,
        )
    except Exception as e:
        sys.exit(f"Could not read the PBF via GDAL's OSM driver: {e}")

    if lines.empty:
        sys.exit("No secondary/tertiary/trunk/primary ways found in the PBF "
                  "for this area -- check that the file covers West Bengal.")

    lines = lines.set_crs(epsg=4326, allow_override=True)

    if "other_tags" in lines.columns:
        lines["raw_ref"] = lines["other_tags"].apply(lambda t: extract_tag(t, REF_PATTERN))
        lines["network"] = lines["other_tags"].apply(lambda t: extract_tag(t, NETWORK_PATTERN))
    else:
        lines["raw_ref"] = None
        lines["network"] = None

    lines["ref"] = lines.apply(
        lambda row: normalize_sh_ref(row.get("raw_ref"), row.get("network")), axis=1
    )

    sh = lines[lines["ref"].notna()].copy()

    if sh.empty:
        sys.exit("No SH-tagged ways found at all -- check ref/network tagging "
                  "in this area of the PBF.")

    keep_cols = [c for c in ["osm_id", "name", "ref", "raw_ref", "network", "highway", "geometry"]
                 if c in sh.columns]
    return sh[keep_cols]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    wb = get_boundary()
    boundary_geom = wb.geometry.iloc[0]

    sh = get_state_highways_from_pbf(PBF_PATH, boundary_geom)
    log(f"Read {len(sh)} candidate state-highway segments; clipping to WB boundary...")

    clipped = gpd.clip(sh, wb)
    clipped = clipped[~clipped.geometry.is_empty]
    clipped = clipped[clipped.geometry.type.isin(["LineString", "MultiLineString"])]

    clipped.to_file(SH_PATH, driver="GeoJSON")
    log(f"Wrote {SH_PATH} ({len(clipped)} clipped segments)")

    found_refs = set(clipped["ref"].dropna().astype(str).str.upper())
    missing = WB_SH_REFS - found_refs
    unexpected = found_refs - WB_SH_REFS
    if missing:
        log(f"NOTE: no segments found for: {sorted(missing)} "
            f"(check ref/network tagging in OSM for these routes)")
    if unexpected:
        log(f"NOTE: found refs not in your official SH list -- worth a look: "
            f"{sorted(unexpected)}")

    log("Done. Next: point build_km_points.py at data/sh_clipped.geojson "
        "(or run a copy of it for SH) to generate km points.")


if __name__ == "__main__":
    main()
