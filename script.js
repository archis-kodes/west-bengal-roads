// West Bengal Highway Atlas
// Loads pre-generated GeoJSON files (produced by fetch_data.py) and renders
// them on a zoomable Leaflet + OpenStreetMap map: the state boundary, national
// highways, and state highways — with the area outside West Bengal faded out
// and a click-to-inspect panel for any highway.

const BOUNDARY_URL = "data/wb_boundary.geojson";
const NH_URL = "data/nh_clipped.geojson";
const SH_URL = "data/sh_clipped.geojson";

const map = L.map("map", {
  zoomControl: true,
  minZoom: 5,
  maxZoom: 18,
});

// Fallback view (roughly West Bengal) until the boundary loads and we can fit to it.
map.setView([23.6, 87.9], 7);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);

L.control.scale({ imperial: false }).addTo(map);

let boundaryLayer = null;
let maskLayer = null;
let nhLayer = null;
let shLayer = null;

// ---- DOM refs ----
const statusNote = document.getElementById("status-note");
const statBoundary = document.getElementById("stat-boundary");
const statNh = document.getElementById("stat-nh");
const statSh = document.getElementById("stat-sh");
const statRefs = document.getElementById("stat-refs");

const toggleBoundary = document.getElementById("toggle-boundary");
const toggleNh = document.getElementById("toggle-nh");
const toggleSh = document.getElementById("toggle-sh");
const toggleFade = document.getElementById("toggle-fade");

const statsView = document.getElementById("stats-view");
const highwayView = document.getElementById("highway-view");
const backBtn = document.getElementById("back-btn");

const hwKind = document.getElementById("hw-kind");
const hwRef = document.getElementById("hw-ref");
const hwName = document.getElementById("hw-name");
const hwClass = document.getElementById("hw-class");
const hwNetwork = document.getElementById("hw-network");
const hwSegments = document.getElementById("hw-segments");
const hwLength = document.getElementById("hw-length");
const highwayCard = document.querySelector(".highway-card");

const searchInput = document.getElementById("highway-search");
const searchGoBtn = document.getElementById("search-go");
const refList = document.getElementById("ref-list");

function setNote(text, isError = false) {
  statusNote.textContent = text;
  statusNote.style.color = isError ? "#b91c1c" : "";
}

let loadedCount = 0;
function checkDone() {
  loadedCount += 1;
  if (loadedCount >= 3) {
    setNote("Boundary and highway layers loaded from local GeoJSON files.");
  }
}

// ---- Boundary layer + fade mask ----
// The mask is a single big rectangle covering the world, with every ring of
// the West Bengal polygon(s) cut out of it as a hole (Leaflet's default
// even-odd fill rule handles the subtraction). Everything outside the state
// gets covered by a translucent scrim; everything inside is left clear.
function maskRingsFromGeometry(geometry) {
  const worldRing = [
    [85, -179.9],
    [85, 179.9],
    [-85, 179.9],
    [-85, -179.9],
  ];
  const polys = geometry.type === "MultiPolygon" ? geometry.coordinates : [geometry.coordinates];
  const rings = [worldRing];
  polys.forEach((poly) => {
    poly.forEach((ring) => {
      rings.push(ring.map(([lng, lat]) => [lat, lng]));
    });
  });
  return rings;
}

fetch(BOUNDARY_URL)
  .then((r) => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  })
  .then((geojson) => {
    boundaryLayer = L.geoJSON(geojson, {
      style: {
        color: "#0f6e5f",
        weight: 2.5,
        dashArray: "6 4",
        fill: false,
      },
    }).addTo(map);

    const feature = geojson.type === "FeatureCollection" ? geojson.features[0] : geojson;
    maskLayer = L.polygon(maskRingsFromGeometry(feature.geometry), {
      stroke: false,
      fill: true,
      fillColor: "#10201c",
      fillOpacity: 0.42,
      fillRule: "evenodd",
      interactive: false,
    }).addTo(map);
    maskLayer.bringToBack();

    map.fitBounds(boundaryLayer.getBounds(), { padding: [20, 20] });
    statBoundary.textContent = "loaded";
    checkDone();
  })
  .catch((err) => {
    statBoundary.textContent = "missing";
    setNote(`Could not load ${BOUNDARY_URL}. Run "python fetch_data.py" first to generate the data files.`, true);
    console.error(err);
  });

// ---- Highways (national + state) ----
const STYLES = {
  NH: { base: { color: "#c2410c", weight: 3, opacity: 0.9 }, active: { color: "#ea580c", weight: 6, opacity: 1 }, label: "National Highway" },
  SH: { base: { color: "#1d4ed8", weight: 2.5, opacity: 0.85 }, active: { color: "#3b82f6", weight: 5.5, opacity: 1 }, label: "State Highway" },
};

// key -> { ref, kind, name, highwayClass, network, layers: [] }
const highwayIndex = new Map();
let activeKey = null;

function kindFromRef(ref, fallbackKind) {
  if (ref && ref.startsWith("NH")) return "NH";
  if (ref && ref.startsWith("SH")) return "SH";
  return fallbackKind;
}

function prettyClass(highwayTag) {
  if (!highwayTag) return "Unclassified";
  return highwayTag.charAt(0).toUpperCase() + highwayTag.slice(1);
}

function highlight(key) {
  if (activeKey === key) return;
  clearHighlight();
  const entry = highwayIndex.get(key);
  if (!entry) return;
  const style = STYLES[entry.kind].active;
  entry.layers.forEach((l) => {
    l.setStyle(style);
    l.bringToFront();
  });
  activeKey = key;
}

function clearHighlight() {
  if (!activeKey) return;
  const entry = highwayIndex.get(activeKey);
  if (entry) {
    const style = STYLES[entry.kind].base;
    entry.layers.forEach((l) => l.setStyle(style));
  }
  activeKey = null;
}

function segmentLengthKm(layers) {
  let meters = 0;
  layers.forEach((layer) => {
    const latlngs = layer.getLatLngs();
    for (let i = 1; i < latlngs.length; i++) {
      meters += map.distance(latlngs[i - 1], latlngs[i]);
    }
  });
  return meters / 1000;
}

function showHighwayPanel(key) {
  const entry = highwayIndex.get(key);
  if (!entry) return;

  const lengthKm = segmentLengthKm(entry.layers);

  hwKind.textContent = STYLES[entry.kind].label;
  hwRef.textContent = entry.ref;
  hwName.textContent = entry.name || "No name recorded in OpenStreetMap";
  hwClass.textContent = entry.highwayClass;
  hwNetwork.textContent = entry.network || "–";
  hwSegments.textContent = entry.layers.length.toLocaleString();
  hwLength.textContent = `${lengthKm.toFixed(1)} km`;

  highwayCard.style.setProperty("--card-accent", entry.kind === "NH" ? "var(--nh)" : "var(--sh)");

  statsView.hidden = true;
  highwayView.hidden = false;
}

function selectHighway(key, { fly = true } = {}) {
  const entry = highwayIndex.get(key);
  if (!entry) return;
  highlight(key);
  showHighwayPanel(key);
  if (fly) {
    const group = L.featureGroup(entry.layers);
    map.fitBounds(group.getBounds(), { padding: [30, 30], maxZoom: 12 });
  }
}

backBtn.addEventListener("click", () => {
  clearHighlight();
  highwayView.hidden = true;
  statsView.hidden = false;
});

function loadHighwayLayer(url, kind) {
  return fetch(url)
    .then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    })
    .then((geojson) => {
      let anonCounter = 0;
      const style = STYLES[kind];

      const layer = L.geoJSON(geojson, {
        style: style.base,
        onEachFeature: (feature, leafletLayer) => {
          const props = feature.properties || {};
          const ref = props.ref || props.raw_ref || null;
          const resolvedKind = kindFromRef(ref, kind);
          const key = ref || `${kind}-unref-${anonCounter++}`;

          if (!highwayIndex.has(key)) {
            highwayIndex.set(key, {
              ref: ref || "Unclassified segment",
              kind: resolvedKind,
              name: props.name || null,
              highwayClass: prettyClass(props.highway),
              network: props.network || null,
              layers: [],
            });
          }
          const entry = highwayIndex.get(key);
          if (!entry.name && props.name) entry.name = props.name;
          entry.layers.push(leafletLayer);

          const popupClass = resolvedKind === "SH" ? "popup-sh" : "";
          leafletLayer.bindPopup(
            `<div class="${popupClass}"><b>${entry.ref}</b>${props.name ? `<br/>${props.name}` : ""}</div>`
          );
          leafletLayer.on("mouseover", () => highlight(key));
          leafletLayer.on("mouseout", () => clearHighlight());
          leafletLayer.on("click", () => selectHighway(key));
        },
      }).addTo(map);

      const count = geojson.features ? geojson.features.length : 0;
      if (kind === "NH") {
        nhLayer = layer;
        statNh.textContent = count.toLocaleString();
      } else {
        shLayer = layer;
        statSh.textContent = count.toLocaleString();
      }

      statRefs.textContent = highwayIndex.size.toLocaleString();
      refreshDatalist();
      checkDone();
    })
    .catch((err) => {
      if (kind === "NH") statNh.textContent = "missing";
      else statSh.textContent = "missing";
      setNote(`Could not load ${url}. Run "python fetch_data.py" first to generate the data files.`, true);
      console.error(err);
    });
}

function refreshDatalist() {
  refList.innerHTML = "";
  const refs = Array.from(highwayIndex.keys())
    .filter((k) => highwayIndex.get(k).ref !== "Unclassified segment")
    .sort();
  refs.forEach((ref) => {
    const opt = document.createElement("option");
    opt.value = ref;
    refList.appendChild(opt);
  });
}

loadHighwayLayer(NH_URL, "NH");
loadHighwayLayer(SH_URL, "SH");

// ---- Search ----
function runSearch() {
  const query = searchInput.value.trim().toUpperCase();
  if (!query) return;
  if (highwayIndex.has(query)) {
    selectHighway(query);
    searchInput.style.borderColor = "";
  } else {
    searchInput.style.borderColor = "#b91c1c";
    setNote(`No highway found matching "${query}".`, true);
  }
}

searchGoBtn.addEventListener("click", runSearch);
searchInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") runSearch();
});
searchInput.addEventListener("input", () => {
  searchInput.style.borderColor = "";
});

// ---- Layer toggles ----
toggleBoundary.addEventListener("change", (e) => {
  if (!boundaryLayer) return;
  if (e.target.checked) map.addLayer(boundaryLayer);
  else map.removeLayer(boundaryLayer);
});

toggleNh.addEventListener("change", (e) => {
  if (!nhLayer) return;
  if (e.target.checked) map.addLayer(nhLayer);
  else map.removeLayer(nhLayer);
});

toggleSh.addEventListener("change", (e) => {
  if (!shLayer) return;
  if (e.target.checked) map.addLayer(shLayer);
  else map.removeLayer(shLayer);
});

toggleFade.addEventListener("change", (e) => {
  if (!maskLayer) return;
  if (e.target.checked) {
    map.addLayer(maskLayer);
    maskLayer.bringToBack();
  } else {
    map.removeLayer(maskLayer);
  }
});
