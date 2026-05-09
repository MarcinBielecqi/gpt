const DEFAULT_OBJECT_LIMIT = 120;
const MAX_CHUNKS_PER_VIEW = 80;
const manifestUrl = "all_parcels_data/manifest.json";
const trailOverlayUrl = "trail_bbox_overlay.json";

const palette = [
  "#1e7d46", "#c25a33", "#2662a6", "#9b6b16", "#7a4fb2", "#0f766e",
  "#b42359", "#5f7f24", "#bd6b00", "#3b5b92", "#8a6f2a", "#26805f",
];

const state = {
  manifest: null,
  chunkCache: new Map(),
  parcelLayer: L.layerGroup(),
  trailLayer: L.layerGroup(),
  renderToken: 0,
  drawnParcels: [],
  activeBase: null,
  trailOverlay: null,
};

const map = L.map("map", { preferCanvas: true, zoomControl: true });
const baseLayers = {
  street: L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 20,
    attribution: "&copy; OpenStreetMap contributors",
  }),
  satellite: L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 20,
    attribution: "Tiles &copy; Esri",
  }),
};
state.activeBase = baseLayers.street.addTo(map);
state.parcelLayer.addTo(map);
state.trailLayer.addTo(map);

const el = {
  error: document.getElementById("error"),
  status: document.getElementById("status"),
  total: document.getElementById("statTotal"),
  visible: document.getElementById("statVisible"),
  rendered: document.getElementById("statRendered"),
  mode: document.getElementById("statMode"),
  fitOnLoad: document.getElementById("fitOnLoad"),
  showTrailBoxes: document.getElementById("showTrailBoxes"),
  baseLayer: document.getElementById("baseLayer"),
  colorBy: document.getElementById("colorBy"),
  objectLimit: document.getElementById("objectLimit"),
  limitValue: document.getElementById("limitValue"),
  denseJump: document.getElementById("denseJump"),
};

function objectLimit() {
  const value = Number.parseInt(el.objectLimit.value, 10);
  if (!Number.isFinite(value)) return DEFAULT_OBJECT_LIMIT;
  return Math.max(5, Math.min(1000, value));
}

function formatInt(value) {
  return new Intl.NumberFormat("pl-PL").format(value || 0);
}

function formatArea(m2) {
  if (!m2) return "brak area_m2";
  if (m2 >= 10000) return `${(m2 / 10000).toLocaleString("pl-PL", { maximumFractionDigits: 2 })} ha`;
  return `${Math.round(m2).toLocaleString("pl-PL")} m²`;
}

function showError(message) {
  el.error.textContent = message;
  el.error.style.display = "block";
}

function bboxToBounds(bbox) {
  return L.latLngBounds([bbox[0], bbox[1]], [bbox[2], bbox[3]]);
}

function bboxAreaKm2(bbox) {
  const midLat = (bbox[0] + bbox[2]) / 2;
  const height = Math.abs(bbox[2] - bbox[0]) * 111.32;
  const width = Math.abs(bbox[3] - bbox[1]) * 111.32 * Math.cos(midLat * Math.PI / 180);
  return Math.max(0.000001, width * height);
}

function intersects(item, bounds) {
  return bboxToBounds(item.bbox).intersects(bounds);
}

function distanceToCenter(item, center) {
  const itemCenter = L.latLng(item.center[0], item.center[1]);
  return itemCenter.distanceTo(center);
}

function hashText(text) {
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function colorKey(parcel) {
  const mode = el.colorBy.value;
  return parcel[mode] || "unknown";
}

function colorFor(parcel) {
  const key = colorKey(parcel);
  return palette[hashText(key) % palette.length];
}

function candidateGroups(bounds) {
  const center = bounds.getCenter();
  return state.manifest.groups.precinct
    .filter((group) => group.chunk && intersects(group, bounds))
    .sort((a, b) => distanceToCenter(a, center) - distanceToCenter(b, center));
}

async function loadChunk(group) {
  if (state.chunkCache.has(group.chunk)) return state.chunkCache.get(group.chunk);
  const response = await fetch(`all_parcels_data/${group.chunk}`);
  if (!response.ok) throw new Error(`${group.chunk}: HTTP ${response.status}`);
  const payload = await response.json();
  state.chunkCache.set(group.chunk, payload);
  return payload;
}

function drawParcel(parcel) {
  if (!parcel.geometry.length) return false;
  const color = colorFor(parcel);
  for (const rings of parcel.geometry) {
    const layer = L.polygon(rings, {
      color,
      weight: 2,
      opacity: 0.98,
      fillColor: color,
      fillOpacity: el.baseLayer.value === "satellite" ? 0.34 : 0.24,
    });
    layer.bindPopup(
      `<div class="parcel-popup"><b>${parcel.number}</b>` +
      `${parcel.commune} / ${parcel.precinct}<br>` +
      `${formatArea(parcel.area_m2)}<br>` +
      `Kolor: ${colorKey(parcel)}<br>` +
      `<small>${parcel.id}</small></div>`
    );
    layer.addTo(state.parcelLayer);
  }
  return true;
}

function redrawCurrentParcels() {
  state.parcelLayer.clearLayers();
  let rendered = 0;
  for (const parcel of state.drawnParcels) {
    if (drawParcel(parcel)) rendered += 1;
  }
  el.rendered.textContent = formatInt(rendered);
}

async function parcelsForView(bounds, token) {
  const groups = candidateGroups(bounds).slice(0, MAX_CHUNKS_PER_VIEW);
  const chunks = [];
  for (const group of groups) {
    chunks.push(await loadChunk(group));
    if (token !== state.renderToken) return { parcels: [], groups, canceled: true };
  }
  const center = bounds.getCenter();
  const parcels = chunks
    .flatMap((chunk) => chunk.parcels)
    .filter((parcel) => intersects(parcel, bounds))
    .sort((a, b) => distanceToCenter(a, center) - distanceToCenter(b, center));
  return { parcels, groups, canceled: false };
}

async function render() {
  if (!state.manifest) return;
  const token = ++state.renderToken;
  const bounds = map.getBounds();
  const limit = objectLimit();
  el.limitValue.textContent = formatInt(limit);
  el.mode.textContent = "ładowanie";
  el.status.textContent = "Ładowanie poligonów dla aktualnego widoku...";

  try {
    const { parcels, groups, canceled } = await parcelsForView(bounds, token);
    if (canceled || token !== state.renderToken) return;
    state.drawnParcels = parcels.slice(0, limit);
    redrawCurrentParcels();
    el.visible.textContent = formatInt(parcels.length);
    el.mode.textContent = "poligony";
    const chunkText = groups.length === MAX_CHUNKS_PER_VIEW ? `${groups.length}+` : `${groups.length}`;
    if (!parcels.length) {
      el.status.textContent = `Brak działek przecinających aktualny widok. Sprawdzone chunki: ${chunkText}.`;
    } else if (parcels.length > limit) {
      el.status.textContent = `${formatInt(parcels.length)} działek przecina widok. Rysuję ${formatInt(state.drawnParcels.length)} najbliżej środka mapy.`;
    } else {
      el.status.textContent = `${formatInt(parcels.length)} działek przecina widok. Rysuję wszystkie znalezione poligony.`;
    }
  } catch (error) {
    showError(`Nie udało się załadować poligonów: ${error.message}`);
  }
}

function debounce(fn, delay = 120) {
  let timer = 0;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

function populateDenseJump() {
  const groups = state.manifest.groups.precinct
    .filter((group) => group.count >= 20)
    .map((group) => ({ group, density: group.count / bboxAreaKm2(group.bbox) }))
    .sort((a, b) => b.density - a.density)
    .slice(0, 14);
  for (const { group, density } of groups) {
    const option = document.createElement("option");
    option.value = group.key;
    option.textContent = `${group.label} · ${formatInt(group.count)} dz. · ${density.toFixed(1)}/km²`;
    el.denseJump.appendChild(option);
  }
}

function drawTrailOverlay() {
  state.trailLayer.clearLayers();
  if (!state.trailOverlay || !el.showTrailBoxes.checked) return;
  if (state.trailOverlay.route?.length) {
    L.polyline(state.trailOverlay.route, {
      color: "#d11f4c",
      weight: 4,
      opacity: 0.85,
    }).addTo(state.trailLayer);
  }
  for (const box of state.trailOverlay.boxes || []) {
    const bounds = bboxToBounds(box.bbox);
    const rectangle = L.rectangle(bounds, {
      color: "#d11f4c",
      weight: 2,
      opacity: 0.9,
      fillColor: "#d11f4c",
      fillOpacity: 0.08,
    });
    rectangle.bindPopup(`<b>Bbox ${box.index}</b><br>${box.size_km} km box<br>center: ${box.center[0]}, ${box.center[1]}`);
    rectangle.addTo(state.trailLayer);
    L.marker(box.center, {
      interactive: false,
      icon: L.divIcon({
        className: "group-label",
        html: `#${box.index}`,
        iconSize: [32, 18],
        iconAnchor: [16, 9],
      }),
    }).addTo(state.trailLayer);
  }
}

function switchBaseLayer() {
  if (state.activeBase) map.removeLayer(state.activeBase);
  state.activeBase = baseLayers[el.baseLayer.value].addTo(map);
  redrawCurrentParcels();
}

async function init() {
  try {
    const response = await fetch(manifestUrl);
    if (!response.ok) throw new Error(`manifest.json: HTTP ${response.status}`);
    state.manifest = await response.json();
    const trailResponse = await fetch(trailOverlayUrl);
    if (trailResponse.ok) state.trailOverlay = await trailResponse.json();
    el.total.textContent = formatInt(state.manifest.parcel_count);
    el.mode.textContent = "start";
    populateDenseJump();
    map.fitBounds(bboxToBounds(state.manifest.bounds), { padding: [24, 24] });
    map.on("moveend zoomend", debounce(render));
    el.objectLimit.addEventListener("input", debounce(render, 80));
    el.colorBy.addEventListener("change", redrawCurrentParcels);
    el.baseLayer.addEventListener("change", switchBaseLayer);
    el.showTrailBoxes.addEventListener("change", drawTrailOverlay);
    el.denseJump.addEventListener("change", () => {
      const group = state.manifest.groups.precinct.find((item) => item.key === el.denseJump.value);
      if (group) map.fitBounds(bboxToBounds(group.bbox), { padding: [48, 48] });
    });
    drawTrailOverlay();
    await render();
  } catch (error) {
    showError(`Nie udało się uruchomić viewera: ${error.message}. Uruchom go przez lokalny serwer HTTP, nie bezpośrednio z file:///.`);
  }
}

init();
