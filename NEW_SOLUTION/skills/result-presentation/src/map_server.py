from __future__ import annotations

import argparse
import json
import sqlite3
import threading
import time
import urllib.parse
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

MAP_LIMIT_MAX = 8000

CFG: dict[str, Any] = {}
STARTED_AT = time.time()

HTML = r"""<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Result Presentation Map</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    html, body, #map { height: 100%; margin: 0; }
    body {
      font-family: "Segoe UI", Tahoma, sans-serif;
      background: #f7f6ef;
      color: #18201b;
      overflow: hidden;
    }
    #map { background: #e9ecdf; }
    .panel {
      background: rgba(255, 255, 250, .94);
      border: 1px solid rgba(88, 111, 92, .35);
      box-shadow: 0 14px 40px rgba(34, 46, 35, .18);
      backdrop-filter: blur(10px);
    }
    .top {
      position: absolute;
      z-index: 1000;
      top: 14px;
      left: 14px;
      right: 14px;
      display: flex;
      gap: 10px;
      pointer-events: none;
    }
    .top > * { pointer-events: auto; }
    .summary {
      width: min(520px, calc(100vw - 28px));
      padding: 12px 14px;
    }
    .controls {
      margin-left: auto;
      min-width: 250px;
      padding: 10px;
    }
    h1 { font-size: 17px; margin: 0 0 8px; }
    .status { font-size: 13px; color: #5f6f65; }
    .stats {
      display: grid;
      grid-template-columns: repeat(4, minmax(70px, 1fr));
      gap: 8px;
      margin-top: 10px;
    }
    .stat {
      border: 1px solid #d8dfd2;
      background: rgba(255,255,255,.62);
      padding: 7px;
    }
    .stat b { display: block; font-size: 15px; }
    .stat span { font-size: 11px; color: #5f6f65; }
    label, .row {
      display: grid;
      gap: 5px;
      font-size: 13px;
      margin: 7px 0;
    }
    select, input { font: inherit; }
    input[type="range"] { width: 100%; accent-color: #1e7d46; }
    .legend {
      position: absolute;
      z-index: 1000;
      left: 14px;
      bottom: 18px;
      width: min(440px, calc(100vw - 28px));
      padding: 12px;
      font-size: 13px;
      line-height: 1.45;
    }
    .err {
      display: none;
      position: absolute;
      z-index: 2000;
      top: 14px;
      left: 14px;
      right: 14px;
      background: #fff7ed;
      border: 1px solid #ea580c;
      color: #7c2d12;
      padding: 12px;
    }
    .popup { min-width: 220px; font-size: 13px; }
    .popup b { display: block; margin-bottom: 5px; }
    @media(max-width: 760px) {
      .top { display: block; }
      .controls { margin: 10px 0 0; }
      .legend { display: none; }
      .stats { grid-template-columns: repeat(2, 1fr); }
    }
  </style>
</head>
<body>
  <div id="map"></div>
  <div id="err" class="err"></div>
  <div class="top">
    <section class="panel summary">
      <h1>Result Presentation Map</h1>
      <div id="status" class="status">Ładowanie manifestu...</div>
      <div class="stats">
        <div class="stat"><b id="total">0</b><span>działek w DB</span></div>
        <div class="stat"><b id="visible">0</b><span>w widoku</span></div>
        <div class="stat"><b id="drawn">0</b><span>rysowane</span></div>
        <div class="stat"><b id="mode">-</b><span>tryb</span></div>
      </div>
    </section>
    <section class="panel controls">
      <label>widok mapy
        <select id="base">
          <option value="street">ulice</option>
          <option value="satellite">satelita</option>
        </select>
      </label>
      <label>kolor poligonów
        <select id="color">
          <option value="commune">gmina</option>
          <option value="county">powiat</option>
          <option value="voivodeship">województwo</option>
          <option value="precinct">obręb</option>
        </select>
      </label>
      <label>limit poligonów <output id="limitOut">500</output>
        <input id="limit" type="range" min="5" max="8000" step="5" value="500">
      </label>
      <label>gęste miejsca
        <select id="jump"><option value="">wybierz...</option></select>
      </label>
    </section>
  </div>
  <aside class="panel legend">
    Mapa czyta poligony z <code>canon.sqlite</code> przez lokalny serwer HTTP.
    Przełącznik „widok mapy” pozwala wybrać ulice albo satelitę.
    Link jest świeży, a serwer sam kończy działanie po TTL.
  </aside>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const PALETTE = ["#1e7d46","#c25a33","#2662a6","#9b6b16","#7a4fb2","#0f766e","#b42359","#5f7f24","#bd6b00","#3b5b92"];
    let manifest = null;
    let drawnParcels = [];
    let renderToken = 0;

    const map = L.map("map", { preferCanvas: true });
    const parcelLayer = L.layerGroup().addTo(map);
    const baseLayers = {
      street: L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 20,
        attribution: "© OpenStreetMap"
      }),
      satellite: L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
        maxZoom: 20,
        attribution: "Tiles © Esri"
      })
    };
    let activeBase = baseLayers.street.addTo(map);
    L.control.layers({"Ulice": baseLayers.street, "Satelita": baseLayers.satellite}, {}, {position: "bottomright"}).addTo(map);

    const $ = id => document.getElementById(id);
    const fmt = n => new Intl.NumberFormat("pl-PL").format(n || 0);

    function areaLabel(m2) {
      if (!m2) return "brak area_m2";
      if (m2 >= 10000) return `${(m2 / 10000).toLocaleString("pl-PL", {maximumFractionDigits: 2})} ha`;
      return `${Math.round(m2).toLocaleString("pl-PL")} m²`;
    }

    function showError(message) {
      $("err").textContent = message;
      $("err").style.display = "block";
    }

    function boundsFromBbox(bbox) {
      return L.latLngBounds([bbox[0], bbox[1]], [bbox[2], bbox[3]]);
    }

    function currentBbox() {
      const b = map.getBounds();
      const sw = b.getSouthWest();
      const ne = b.getNorthEast();
      return [sw.lat, sw.lng, ne.lat, ne.lng].join(",");
    }

    function hashText(text) {
      let hash = 2166136261;
      for (let i = 0; i < text.length; i++) {
        hash ^= text.charCodeAt(i);
        hash = Math.imul(hash, 16777619);
      }
      return hash >>> 0;
    }

    function colorKey(parcel) {
      return parcel[$("color").value] || "unknown";
    }

    function colorFor(parcel) {
      return PALETTE[hashText(colorKey(parcel)) % PALETTE.length];
    }

    function maxLimit() {
      return Number(manifest?.max_limit || 8000);
    }

    function objectLimit() {
      const raw = parseInt($("limit").value || manifest?.default_limit || 500, 10);
      return Math.max(5, Math.min(maxLimit(), raw));
    }

    function setBaseLayer(name) {
      const layer = baseLayers[name] || baseLayers.street;
      if (activeBase !== layer) {
        map.removeLayer(activeBase);
        activeBase = layer.addTo(map);
      }
      $("base").value = name;
      redrawCurrentParcels();
    }

    function drawParcel(parcel) {
      if (!parcel.geometry?.length) return false;
      const color = colorFor(parcel);
      const fillOpacity = $("base").value === "satellite" ? 0.34 : 0.24;
      for (const rings of parcel.geometry) {
        L.polygon(rings, {
          color,
          weight: 2,
          opacity: 0.98,
          fillColor: color,
          fillOpacity
        }).bindPopup(
          `<div class="popup"><b>${parcel.number || parcel.id}</b>` +
          `${parcel.commune || ""} / ${parcel.precinct || "brak obrębu"}<br>` +
          `${areaLabel(parcel.area_m2)}<br>` +
          `Kolor: ${colorKey(parcel)}<br>` +
          `<small>${parcel.id}</small></div>`
        ).addTo(parcelLayer);
      }
      return true;
    }

    function redrawCurrentParcels() {
      parcelLayer.clearLayers();
      let rendered = 0;
      for (const parcel of drawnParcels) {
        if (drawParcel(parcel)) rendered += 1;
      }
      $("drawn").textContent = fmt(rendered);
    }

    async function render() {
      if (!manifest) return;
      const token = ++renderToken;
      const limit = objectLimit();
      $("limitOut").textContent = fmt(limit);
      $("mode").textContent = "ładowanie";
      $("status").textContent = "Ładowanie poligonów...";

      try {
        const response = await fetch(`/api/parcels?bbox=${encodeURIComponent(currentBbox())}&limit=${limit}`, {cache: "no-store"});
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        if (token !== renderToken) return;

        drawnParcels = payload.parcels || [];
        redrawCurrentParcels();
        $("visible").textContent = fmt(payload.matched);
        $("mode").textContent = $("base").value === "satellite" ? "satelita" : "ulice";
        $("status").textContent = payload.matched > drawnParcels.length
          ? `${fmt(payload.matched)} działek przecina widok. Rysuję ${fmt(drawnParcels.length)} najbliżej środka mapy.`
          : `${fmt(payload.matched)} działek przecina widok.`;
      } catch (error) {
        showError(`Nie udało się załadować poligonów: ${error.message}`);
      }
    }

    function debounce(fn, delay = 140) {
      let timer = 0;
      return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), delay);
      };
    }

    function bboxAreaKm2(bbox) {
      const mid = (bbox[0] + bbox[2]) / 2;
      const height = Math.abs(bbox[2] - bbox[0]) * 111.32;
      const width = Math.abs(bbox[3] - bbox[1]) * 111.32 * Math.cos(mid * Math.PI / 180);
      return Math.max(0.000001, width * height);
    }

    function populateJumps() {
      const groups = (manifest.groups?.precinct || manifest.groups?.commune || [])
        .filter(item => item.count >= 2 && item.bbox)
        .map(item => ({ item, density: item.count / bboxAreaKm2(item.bbox) }))
        .sort((a, b) => b.density - a.density)
        .slice(0, 20);

      for (const {item, density} of groups) {
        const option = document.createElement("option");
        option.value = JSON.stringify(item.bbox);
        option.textContent = `${item.label} · ${fmt(item.count)} dz. · ${density.toFixed(1)}/km²`;
        $("jump").appendChild(option);
      }
    }

    async function init() {
      try {
        const response = await fetch("/api/manifest", {cache: "no-store"});
        if (!response.ok) throw new Error(`manifest HTTP ${response.status}`);
        manifest = await response.json();

        $("total").textContent = fmt(manifest.parcel_count);
        $("limit").max = String(maxLimit());
        $("limit").value = manifest.default_limit || 500;
        $("limitOut").textContent = fmt(objectLimit());
        populateJumps();

        if (manifest.bounds) map.fitBounds(boundsFromBbox(manifest.bounds), {padding: [24, 24]});
        else map.setView([52, 19], 6);

        map.on("moveend zoomend", debounce(render));
        map.on("baselayerchange", event => {
          if (event.layer === baseLayers.satellite) $("base").value = "satellite";
          else $("base").value = "street";
          redrawCurrentParcels();
        });
        $("limit").addEventListener("input", debounce(render, 90));
        $("color").addEventListener("change", redrawCurrentParcels);
        $("base").addEventListener("change", () => {
          setBaseLayer($("base").value);
        });
        $("jump").addEventListener("change", () => {
          if ($("jump").value) map.fitBounds(boundsFromBbox(JSON.parse($("jump").value)), {padding: [48, 48]});
        });

        await render();
      } catch (error) {
        showError(`Nie udało się uruchomić mapy: ${error.message}`);
      }
    }

    init();
  </script>
</body>
</html>"""


def con(path: str | Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(path), timeout=30)
    c.row_factory = sqlite3.Row
    return c


def send(h: BaseHTTPRequestHandler, payload: dict[str, Any], status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    h.send_response(status)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-store")
    h.send_header("Access-Control-Allow-Origin", "*")
    h.end_headers()
    h.wfile.write(body)


def exists(c: sqlite3.Connection, table: str) -> bool:
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def manifest() -> dict[str, Any]:
    c = con(CFG["canon_db"])
    try:
        if not exists(c, "canon_parcels"):
            return {"parcel_count": 0, "bounds": None, "groups": {}, "warnings": ["missing canon_parcels"]}

        summary = c.execute(
            """
            SELECT COUNT(*) AS n,
                   MIN(bbox_min_lat) AS min_lat,
                   MIN(bbox_min_lon) AS min_lon,
                   MAX(bbox_max_lat) AS max_lat,
                   MAX(bbox_max_lon) AS max_lon
            FROM canon_parcels
            WHERE bbox_min_lat IS NOT NULL AND bbox_min_lon IS NOT NULL
            """
        ).fetchone()

        groups: dict[str, list[dict[str, Any]]] = {}
        for field in ("precinct", "commune", "county", "voivodeship"):
            rows = c.execute(
                f"""
                SELECT COALESCE({field}, 'brak') AS label,
                       COUNT(*) AS count,
                       MIN(bbox_min_lat) AS min_lat,
                       MIN(bbox_min_lon) AS min_lon,
                       MAX(bbox_max_lat) AS max_lat,
                       MAX(bbox_max_lon) AS max_lon
                FROM canon_parcels
                GROUP BY COALESCE({field}, 'brak')
                HAVING min_lat IS NOT NULL
                ORDER BY count DESC, label
                LIMIT 250
                """
            ).fetchall()
            groups[field] = [
                {
                    "key": str(row["label"]),
                    "label": str(row["label"]),
                    "count": int(row["count"]),
                    "bbox": [row["min_lat"], row["min_lon"], row["max_lat"], row["max_lon"]],
                }
                for row in rows
            ]

        rcn_count = (
            int(c.execute("SELECT COUNT(*) AS n FROM canon_rcn_price_observations").fetchone()["n"])
            if exists(c, "canon_rcn_price_observations")
            else 0
        )

        return {
            "run_id": CFG["run_id"],
            "parcel_count": int(summary["n"] or 0),
            "rcn_count": rcn_count,
            "bounds": [summary["min_lat"], summary["min_lon"], summary["max_lat"], summary["max_lon"]]
            if summary["n"]
            else None,
            "groups": groups,
            "default_limit": int(CFG["default_limit"]),
            "max_limit": MAP_LIMIT_MAX,
            "expires_at_epoch": CFG["expires_at_epoch"],
        }
    finally:
        c.close()


def parse_bbox(q: dict[str, list[str]]) -> tuple[float, float, float, float] | None:
    try:
        values = [float(x) for x in q.get("bbox", [""])[0].split(",")]
        return tuple(values) if len(values) == 4 and values[0] <= values[2] and values[1] <= values[3] else None
    except ValueError:
        return None


def parse_limit(q: dict[str, list[str]]) -> int:
    try:
        value = int(q.get("limit", [CFG["default_limit"]])[0])
    except ValueError:
        value = int(CFG["default_limit"])
    return max(5, min(MAP_LIMIT_MAX, value))


def group_points(rows: list[sqlite3.Row]) -> dict[str, list[list[list[list[float]]]]]:
    out: dict[str, OrderedDict[int, OrderedDict[int, list[list[float]]]]] = {}
    for row in rows:
        out.setdefault(row["parcel_id"], OrderedDict()).setdefault(
            int(row["polygon_index"]), OrderedDict()
        ).setdefault(int(row["ring_index"]), []).append([float(row["lat"]), float(row["lon"])])
    return {
        parcel_id: [[points for _, points in rings.items() if len(points) >= 3] for _, rings in polygons.items()]
        for parcel_id, polygons in out.items()
    }


def parcels(q: dict[str, list[str]]) -> dict[str, Any]:
    c = con(CFG["canon_db"])
    try:
        bbox = parse_bbox(q) or tuple(manifest()["bounds"] or [])
        if len(bbox) != 4:
            return {"parcels": [], "matched": 0, "rendered": 0, "warnings": ["no bounds"]}

        min_lat, min_lon, max_lat, max_lon = bbox
        center = ((min_lat + max_lat) / 2, (min_lon + max_lon) / 2)
        where = "bbox_max_lat >= ? AND bbox_min_lat <= ? AND bbox_max_lon >= ? AND bbox_min_lon <= ?"
        params = (min_lat, max_lat, min_lon, max_lon)

        matched = int(c.execute(f"SELECT COUNT(*) AS n FROM canon_parcels WHERE {where}", params).fetchone()["n"])
        rows = c.execute(
            f"""
            SELECT parcel_id, parcel_number, voivodeship, county, commune, precinct, area_m2,
                   centroid_lat, centroid_lon, bbox_min_lat, bbox_min_lon, bbox_max_lat, bbox_max_lon
            FROM canon_parcels
            WHERE {where}
            ORDER BY ((COALESCE(centroid_lat, 0) - ?) * (COALESCE(centroid_lat, 0) - ?)
                    + (COALESCE(centroid_lon, 0) - ?) * (COALESCE(centroid_lon, 0) - ?))
            LIMIT ?
            """,
            (*params, center[0], center[0], center[1], center[1], parse_limit(q)),
        ).fetchall()

        ids = [row["parcel_id"] for row in rows]
        geom: dict[str, list[list[list[list[float]]]]] = {}
        if ids and exists(c, "canon_parcel_polygon_points"):
            placeholders = ",".join("?" for _ in ids)
            geom = group_points(
                c.execute(
                    f"""
                    SELECT parcel_id, polygon_index, ring_index, point_index, lat, lon
                    FROM canon_parcel_polygon_points
                    WHERE parcel_id IN ({placeholders})
                    ORDER BY parcel_id, polygon_index, ring_index, point_index
                    """,
                    ids,
                ).fetchall()
            )

        items = [
            {
                "id": row["parcel_id"],
                "number": row["parcel_number"],
                "voivodeship": row["voivodeship"],
                "county": row["county"],
                "commune": row["commune"],
                "precinct": row["precinct"],
                "area_m2": row["area_m2"],
                "center": [row["centroid_lat"], row["centroid_lon"]],
                "bbox": [row["bbox_min_lat"], row["bbox_min_lon"], row["bbox_max_lat"], row["bbox_max_lon"]],
                "geometry": geom.get(row["parcel_id"], []),
            }
            for row in rows
        ]

        return {
            "bbox": [min_lat, min_lon, max_lat, max_lon],
            "matched": matched,
            "rendered": len(items),
            "limit": parse_limit(q),
            "parcels": items,
            "warnings": [],
        }
    finally:
        c.close()


def artifacts() -> dict[str, Any]:
    db = Path(CFG["bus_db"])
    if not db.exists():
        return {"count": 0, "artifacts": []}
    c = con(db)
    try:
        if not exists(c, "bus_artifacts"):
            return {"count": 0, "artifacts": []}
        rows = c.execute(
            """
            SELECT id, producer_skill, artifact_type, artifact_key, created_at, updated_at
            FROM bus_artifacts
            WHERE run_id = ?
            ORDER BY id
            """,
            (CFG["run_id"],),
        ).fetchall()
        return {"count": len(rows), "artifacts": [dict(row) for row in rows]}
    finally:
        c.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)

        try:
            if parsed.path in {"/", "/map.html"}:
                body = HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/api/health":
                send(
                    self,
                    {
                        "status": "ok",
                        "run_id": CFG["run_id"],
                        "started_at_epoch": STARTED_AT,
                        "expires_at_epoch": CFG["expires_at_epoch"],
                        "ttl_seconds": CFG["ttl_seconds"],
                    },
                )
            elif parsed.path == "/api/manifest":
                send(self, manifest())
            elif parsed.path == "/api/parcels":
                send(self, parcels(query))
            elif parsed.path == "/api/artifacts":
                send(self, artifacts())
            else:
                send(self, {"status": "error", "message": "not found"}, 404)
        except Exception as exc:  # noqa: BLE001
            send(self, {"status": "error", "message": str(exc)}, 500)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canon-db", required=True)
    parser.add_argument("--bus-db", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--ttl-seconds", type=int, default=300)
    parser.add_argument("--default-limit", type=int, default=500)
    args = parser.parse_args()

    ttl = max(1, int(args.ttl_seconds))
    CFG.update(
        {
            "canon_db": str(Path(args.canon_db).resolve()),
            "bus_db": str(Path(args.bus_db).resolve()),
            "run_id": args.run_id,
            "ttl_seconds": ttl,
            "default_limit": max(5, min(MAP_LIMIT_MAX, int(args.default_limit))),
            "expires_at_epoch": time.time() + ttl,
        }
    )

    server = ThreadingHTTPServer((args.host, int(args.port)), Handler)
    timer = threading.Timer(CFG["ttl_seconds"], server.shutdown)
    timer.daemon = True
    timer.start()

    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
