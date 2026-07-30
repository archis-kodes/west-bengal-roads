/**
 * degradation.js
 * ----------------
 * Loads `database/highway_degradation.db` (produced by
 * aggregate_highway_degradation.py) directly in the browser using sql.js
 * (a WASM build of SQLite), and exposes `updateDegradedValue(highwayRef)`,
 * called from script.js's showHighwayPanel() to fill in #hw-degraded.
 *
 * NOTE: this only works when the page is served over http(s)
 * (e.g. `python -m http.server`), not opened as a file:// URL, since
 * both fetch() and sql.js's WASM loader require it.
 */

const DEGRADATION_DB_PATH = "database/highway_degradation.db";

let _degradationDbPromise = null;

async function loadDegradationDb() {
  if (!_degradationDbPromise) {
    _degradationDbPromise = (async () => {
      const SQL = await initSqlJs({
        locateFile: (file) =>
          `https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.10.3/${file}`,
      });
      const buf = await fetch(DEGRADATION_DB_PATH).then((r) => {
        if (!r.ok) {
          throw new Error(`Could not load ${DEGRADATION_DB_PATH} (HTTP ${r.status})`);
        }
        return r.arrayBuffer();
      });
      return new SQL.Database(new Uint8Array(buf));
    })();
  }
  return _degradationDbPromise;
}

/**
 * Look up weighted_degradation for a highway ref (e.g. "NH12").
 * Tries an exact match on highway_name first, then a normalized
 * (whitespace-stripped, uppercased) match, since the ref format in
 * the map data ("NH12") may differ slightly from highway_name in the
 * DB ("NH 31D" style entries, for example).
 */
async function getDegradedValue(highwayRef) {
  const db = await loadDegradationDb();

  const exact = db.exec(
    "SELECT weighted_degradation FROM nh_highway_degradation WHERE highway_name = ? LIMIT 1",
    [highwayRef]
  );
  if (exact.length && exact[0].values.length) {
    return exact[0].values[0][0];
  }

  const normalizedTarget = highwayRef.replace(/\s+/g, "").toUpperCase();
  const all = db.exec("SELECT highway_name, weighted_degradation FROM nh_highway_degradation");
  if (all.length) {
    for (const [name, value] of all[0].values) {
      if (String(name).replace(/\s+/g, "").toUpperCase() === normalizedTarget) {
        return value;
      }
    }
  }

  return null;
}

/**
 * Fetches the degraded value for `highwayRef` and writes it into
 * #hw-degraded in the highway card.
 */
async function updateDegradedValue(highwayRef) {
  const el = document.getElementById("hw-degraded");
  if (!el) return;

  el.textContent = "loading…";
  try {
    const value = await getDegradedValue(highwayRef);
    el.textContent =
      value === null || value === undefined
        ? "No data"
        : "₹" +
          Number(value).toLocaleString("en-IN", { maximumFractionDigits: 2 });
  } catch (err) {
    console.error("Failed to load degraded value:", err);
    el.textContent = "Unavailable";
  }
}
