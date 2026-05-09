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
GEOM_ID_CHUNK = 800
CFG: dict[str, Any] = {}
STARTED_AT = time.time()

HTML = r"""<!doctype html><html lang="pl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Result Presentation Map</title><link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"><style>
html,body,#map{height:100%;margin:0}body{font-family:Segoe UI,Tahoma,sans-serif;background:#f7f6ef;color:#18201b;overflow:hidden}#map{background:#e9ecdf}.panel{background:rgba(255,255,250,.94);border:1px solid rgba(88,111,92,.35);box-shadow:0 14px 40px rgba(34,46,35,.18);backdrop-filter:blur(10px)}.top{position:absolute;z-index:1000;top:14px;left:14px;right:14px;display:flex;gap:10px;pointer-events:none}.top>*{pointer-events:auto}.summary{width:min(520px,calc(100vw - 28px));padding:12px 14px}.controls{margin-left:auto;min-width:260px;padding:10px}h1{font-size:17px;margin:0 0 8px}.status{font-size:13px;color:#5f6f65}.stats{display:grid;grid-template-columns:repeat(4,minmax(70px,1fr));gap:8px;margin-top:10px}.stat{border:1px solid #d8dfd2;background:rgba(255,255,255,.62);padding:7px}.stat b{display:block;font-size:15px}.stat span{font-size:11px;color:#5f6f65}label{display:grid;gap:5px;font-size:13px;margin:7px 0}select,input{font:inherit}input[type=range]{width:100%;accent-color:#1e7d46}.area-values{display:flex;align-items:center;justify-content:space-between;gap:6px;font-size:12px;color:#5f6f65}.area-slider{position:relative;height:28px}.area-slider input[type=range]{position:absolute;left:0;top:0;width:100%;pointer-events:none;background:transparent}.area-slider input[type=range]::-webkit-slider-thumb{pointer-events:auto}.area-slider input[type=range]::-moz-range-thumb{pointer-events:auto}.legend{position:absolute;z-index:1000;left:14px;bottom:18px;width:min(460px,calc(100vw - 28px));padding:12px;font-size:13px;line-height:1.45}.err{display:none;position:absolute;z-index:2000;top:14px;left:14px;right:14px;background:#fff7ed;border:1px solid #ea580c;color:#7c2d12;padding:12px}.popup{min-width:220px;font-size:13px}.popup b{display:block;margin-bottom:5px}@media(max-width:760px){.top{display:block}.controls{margin:10px 0 0}.legend{display:none}.stats{grid-template-columns:repeat(2,1fr)}}
</style></head><body><div id="map"></div><div id="err" class="err"></div><div class="top"><section class="panel summary"><h1>Result Presentation Map</h1><div id="status" class="status">Ładowanie manifestu...</div><div class="stats"><div class="stat"><b id="total">0</b><span>działek w DB</span></div><div class="stat"><b id="visible">0</b><span>w widoku+filtrze</span></div><div class="stat"><b id="drawn">0</b><span>rysowane</span></div><div class="stat"><b id="mode">-</b><span>tryb</span></div></div></section><section class="panel controls"><label>widok mapy<select id="base"><option value="street">ulice</option><option value="satellite">satelita</option></select></label><label>kolor poligonów<select id="color"><option value="commune">gmina</option><option value="county">powiat</option><option value="voivodeship">województwo</option><option value="precinct">obręb</option></select></label><label>limit poligonów <output id="limitOut">500</output><input id="limit" type="range" min="5" max="8000" step="5" value="500"></label><label>powierzchnia działki<div class="area-values"><output id="areaMinOut">0 m²</output><span>–</span><output id="areaMaxOut">∞</output></div><div class="area-slider"><input id="areaMin" type="range" min="0" max="1000" step="1" value="0"><input id="areaMax" type="range" min="0" max="1000" step="1" value="1000"></div></label><label>gęste miejsca<select id="jump"><option value="">wybierz...</option></select></label></section></div><aside class="panel legend">Mapa czyta poligony z <code>canon.sqlite</code> przez lokalny serwer HTTP. Filtr powierzchni działa po stronie serwera, więc nie pobiera geometrii odrzuconych działek.</aside>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>
const PAL=["#1e7d46","#c25a33","#2662a6","#9b6b16","#7a4fb2","#0f766e","#b42359","#5f7f24","#bd6b00","#3b5b92"];let man=null,drawn=[],tok=0,areaRender=null;
const $=id=>document.getElementById(id),fmt=n=>new Intl.NumberFormat("pl-PL").format(n||0);
const map=L.map("map",{preferCanvas:true}),parcels=L.layerGroup().addTo(map),base={street:L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:20,attribution:"© OpenStreetMap"}),satellite:L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",{maxZoom:20,attribution:"Tiles © Esri"})};let active=base.street.addTo(map);L.control.layers({"Ulice":base.street,"Satelita":base.satellite},{},{position:"bottomright"}).addTo(map);
function err(m){$("err").textContent=m;$("err").style.display="block"}function b2b(b){return L.latLngBounds([b[0],b[1]],[b[2],b[3]])}function curBbox(){const b=map.getBounds(),s=b.getSouthWest(),n=b.getNorthEast();return [s.lat,s.lng,n.lat,n.lng].join(",")}function areaText(m){if(!Number.isFinite(m))return"∞";if(m>=10000)return`${(m/10000).toLocaleString("pl-PL",{maximumFractionDigits:2})} ha`;return`${Math.round(m).toLocaleString("pl-PL")} m²`}function parcelArea(m){return m?areaText(m):"brak area_m2"}function hash(t){let h=2166136261;for(let i=0;i<t.length;i++){h^=t.charCodeAt(i);h=Math.imul(h,16777619)}return h>>>0}function ckey(p){return p[$("color").value]||"unknown"}function color(p){return PAL[hash(ckey(p))%PAL.length]}function maxLimit(){return Number(man?.max_limit||8000)}function limit(){return Math.max(5,Math.min(maxLimit(),parseInt($("limit").value||man?.default_limit||500,10)))}
function maxArea(){return Math.max(1,Number(man?.area_max_m2||1))}function sliderArea(v){let p=Math.max(0,Math.min(1000,parseInt(v||"0",10)));return Math.max(0,Math.round(Math.pow(10,(p/1000)*Math.log10(maxArea()+1))-1))}function normArea(changed){let a=parseInt($("areaMin").value||"0",10),b=parseInt($("areaMax").value||"1000",10);if(a>b){if(changed==="min")b=a;else a=b}$("areaMin").value=String(a);$("areaMax").value=String(b);return[a,b]}function areaFilter(changed){const [a,b]=normArea(changed);const f={min:sliderArea(a),max:sliderArea(b)};$("areaMinOut").textContent=areaText(f.min);$("areaMaxOut").textContent=areaText(f.max);return f}
function setBase(n){const l=base[n]||base.street;if(active!==l){map.removeLayer(active);active=l.addTo(map)}$("base").value=n;redraw()}function drawParcel(p){if(!p.geometry?.length)return false;const co=color(p),fo=$("base").value==="satellite"?.34:.24;for(const rings of p.geometry)L.polygon(rings,{color:co,weight:2,opacity:.98,fillColor:co,fillOpacity:fo}).bindPopup(`<div class="popup"><b>${p.number||p.id}</b>${p.commune||""} / ${p.precinct||"brak obrębu"}<br>${parcelArea(p.area_m2)}<br>Kolor: ${ckey(p)}<br><small>${p.id}</small></div>`).addTo(parcels);return true}
function redraw(){parcels.clearLayers();let n=0;for(const p of drawn)if(drawParcel(p))n++;$("drawn").textContent=fmt(n)}function debounce(fn,d=140){let t=0;return(...a)=>{clearTimeout(t);t=setTimeout(()=>fn(...a),d)}}const debRender=debounce(()=>render(),120);
async function render(){if(!man)return;const t=++tok,l=limit(),af=areaFilter();$("limitOut").textContent=fmt(l);$("mode").textContent="ładowanie";$("status").textContent="Ładowanie poligonów...";try{const q=new URLSearchParams({bbox:curBbox(),limit:String(l),min_area:String(af.min),max_area:String(af.max)});const r=await fetch(`/api/parcels?${q.toString()}`,{cache:"no-store"});if(!r.ok)throw Error(`HTTP ${r.status}`);const p=await r.json();if(t!==tok)return;drawn=p.parcels||[];redraw();$("visible").textContent=fmt(p.matched);$("mode").textContent=$("base").value==="satellite"?"satelita":"ulice";const range=`${areaText(af.min)}–${areaText(af.max)}`;$("status").textContent=p.matched>drawn.length?`${fmt(p.matched)} działek przecina widok i filtr ${range}. Rysuję ${fmt(drawn.length)} najbliżej środka mapy.`:`${fmt(p.matched)} działek przecina widok i filtr ${range}.`}catch(e){err(`Nie udało się załadować poligonów: ${e.message}`)}}
function dens(b){const mid=(b[0]+b[2])/2,h=Math.abs(b[2]-b[0])*111.32,w=Math.abs(b[3]-b[1])*111.32*Math.cos(mid*Math.PI/180);return Math.max(.000001,w*h)}function jumps(){const gs=(man.groups?.precinct||man.groups?.commune||[]).filter(x=>x.count>=2&&x.bbox).map(x=>({x,d:x.count/dens(x.bbox)})).sort((a,b)=>b.d-a.d).slice(0,20);for(const g of gs){const o=document.createElement("option");o.value=JSON.stringify(g.x.bbox);o.textContent=`${g.x.label} · ${fmt(g.x.count)} dz. · ${g.d.toFixed(1)}/km²`;$("jump").appendChild(o)}}
async function init(){try{const r=await fetch("/api/manifest",{cache:"no-store"});if(!r.ok)throw Error(`manifest HTTP ${r.status}`);man=await r.json();$("total").textContent=fmt(man.parcel_count);$("limit").max=String(maxLimit());$("limit").value=man.default_limit||500;$("limitOut").textContent=fmt(limit());$("areaMin").value="0";$("areaMax").value="1000";areaFilter();jumps();if(man.bounds)map.fitBounds(b2b(man.bounds),{padding:[24,24]});else map.setView([52,19],6);map.on("moveend zoomend",debounce(render));map.on("baselayerchange",e=>{$("base").value=e.layer===base.satellite?"satellite":"street";redraw()});$("limit").addEventListener("input",debounce(render,90));$("areaMin").addEventListener("input",()=>{areaFilter("min");debRender()});$("areaMax").addEventListener("input",()=>{areaFilter("max");debRender()});$("color").addEventListener("change",redraw);$("base").addEventListener("change",()=>setBase($("base").value));$("jump").addEventListener("change",()=>{$("jump").value&&map.fitBounds(b2b(JSON.parse($("jump").value)),{padding:[48,48]})});await render()}catch(e){err(`Nie udało się uruchomić mapy: ${e.message}`)}}init();
</script></body></html>"""


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
        s = c.execute(
            """
            SELECT COUNT(*) n, MIN(bbox_min_lat) min_lat, MIN(bbox_min_lon) min_lon,
                   MAX(bbox_max_lat) max_lat, MAX(bbox_max_lon) max_lon,
                   MIN(area_m2) min_area, MAX(area_m2) max_area
            FROM canon_parcels
            WHERE bbox_min_lat IS NOT NULL AND bbox_min_lon IS NOT NULL
            """
        ).fetchone()
        groups = {}
        for f in ("precinct", "commune", "county", "voivodeship"):
            rows = c.execute(
                f"""
                SELECT COALESCE({f}, 'brak') label, COUNT(*) count,
                       MIN(bbox_min_lat) min_lat, MIN(bbox_min_lon) min_lon,
                       MAX(bbox_max_lat) max_lat, MAX(bbox_max_lon) max_lon
                FROM canon_parcels
                GROUP BY COALESCE({f}, 'brak')
                HAVING min_lat IS NOT NULL
                ORDER BY count DESC, label
                LIMIT 250
                """
            ).fetchall()
            groups[f] = [
                {"key": str(r["label"]), "label": str(r["label"]), "count": int(r["count"]),
                 "bbox": [r["min_lat"], r["min_lon"], r["max_lat"], r["max_lon"]]}
                for r in rows
            ]
        rcn_count = int(c.execute("SELECT COUNT(*) n FROM canon_rcn_price_observations").fetchone()["n"]) if exists(c, "canon_rcn_price_observations") else 0
        return {
            "run_id": CFG["run_id"],
            "parcel_count": int(s["n"] or 0),
            "rcn_count": rcn_count,
            "bounds": [s["min_lat"], s["min_lon"], s["max_lat"], s["max_lon"]] if s["n"] else None,
            "groups": groups,
            "default_limit": int(CFG["default_limit"]),
            "max_limit": MAP_LIMIT_MAX,
            "area_min_m2": float(s["min_area"] or 0),
            "area_max_m2": float(s["max_area"] or 0),
            "expires_at_epoch": CFG["expires_at_epoch"],
        }
    finally:
        c.close()


def parse_bbox(q: dict[str, list[str]]) -> tuple[float, float, float, float] | None:
    try:
        v = [float(x) for x in q.get("bbox", [""])[0].split(",")]
        return tuple(v) if len(v) == 4 and v[0] <= v[2] and v[1] <= v[3] else None
    except ValueError:
        return None


def parse_limit(q: dict[str, list[str]]) -> int:
    try:
        value = int(q.get("limit", [CFG["default_limit"]])[0])
    except ValueError:
        value = int(CFG["default_limit"])
    return max(5, min(MAP_LIMIT_MAX, value))


def parse_area_filter(q: dict[str, list[str]]) -> tuple[float | None, float | None]:
    def one(name: str) -> float | None:
        raw = q.get(name, [None])[0]
        if raw in (None, ""):
            return None
        try:
            return max(0.0, float(raw))
        except ValueError:
            return None
    a, b = one("min_area"), one("max_area")
    if a is not None and b is not None and a > b:
        a, b = b, a
    return a, b


def group_points(rows: list[sqlite3.Row]) -> dict[str, list[list[list[list[float]]]]]:
    out: dict[str, OrderedDict[int, OrderedDict[int, list[list[float]]]]] = {}
    for r in rows:
        out.setdefault(r["parcel_id"], OrderedDict()).setdefault(int(r["polygon_index"]), OrderedDict()).setdefault(int(r["ring_index"]), []).append([float(r["lat"]), float(r["lon"])])
    return {pid: [[pts for _, pts in rings.items() if len(pts) >= 3] for _, rings in polys.items()] for pid, polys in out.items()}


def parcels(q: dict[str, list[str]]) -> dict[str, Any]:
    c = con(CFG["canon_db"])
    try:
        bbox = parse_bbox(q) or tuple(manifest()["bounds"] or [])
        if len(bbox) != 4:
            return {"parcels": [], "matched": 0, "rendered": 0, "warnings": ["no bounds"]}
        min_lat, min_lon, max_lat, max_lon = bbox
        center = ((min_lat + max_lat) / 2, (min_lon + max_lon) / 2)
        where = ["bbox_max_lat >= ? AND bbox_min_lat <= ? AND bbox_max_lon >= ? AND bbox_min_lon <= ?"]
        params: list[Any] = [min_lat, max_lat, min_lon, max_lon]
        min_area, max_area = parse_area_filter(q)
        if min_area is not None:
            where.append("area_m2 >= ?")
            params.append(min_area)
        if max_area is not None:
            where.append("area_m2 <= ?")
            params.append(max_area)
        where_sql = " AND ".join(where)
        matched = int(c.execute(f"SELECT COUNT(*) n FROM canon_parcels WHERE {where_sql}", params).fetchone()["n"])
        rows = c.execute(
            f"""
            SELECT parcel_id, parcel_number, voivodeship, county, commune, precinct, area_m2,
                   centroid_lat, centroid_lon, bbox_min_lat, bbox_min_lon, bbox_max_lat, bbox_max_lon
            FROM canon_parcels
            WHERE {where_sql}
            ORDER BY ((COALESCE(centroid_lat,0)-?)*(COALESCE(centroid_lat,0)-?)
                    + (COALESCE(centroid_lon,0)-?)*(COALESCE(centroid_lon,0)-?))
            LIMIT ?
            """,
            (*params, center[0], center[0], center[1], center[1], parse_limit(q)),
        ).fetchall()
        ids = [r["parcel_id"] for r in rows]
        geom: dict[str, list[list[list[list[float]]]]] = {}
        if ids and exists(c, "canon_parcel_polygon_points"):
            geom_rows: list[sqlite3.Row] = []
            for i in range(0, len(ids), GEOM_ID_CHUNK):
                chunk = ids[i:i + GEOM_ID_CHUNK]
                ph = ",".join("?" for _ in chunk)
                geom_rows.extend(c.execute(
                    f"""
                    SELECT parcel_id, polygon_index, ring_index, point_index, lat, lon
                    FROM canon_parcel_polygon_points
                    WHERE parcel_id IN ({ph})
                    ORDER BY parcel_id, polygon_index, ring_index, point_index
                    """,
                    chunk,
                ).fetchall())
            geom = group_points(geom_rows)
        items = [
            {"id": r["parcel_id"], "number": r["parcel_number"], "voivodeship": r["voivodeship"],
             "county": r["county"], "commune": r["commune"], "precinct": r["precinct"],
             "area_m2": r["area_m2"], "center": [r["centroid_lat"], r["centroid_lon"]],
             "bbox": [r["bbox_min_lat"], r["bbox_min_lon"], r["bbox_max_lat"], r["bbox_max_lon"]],
             "geometry": geom.get(r["parcel_id"], [])}
            for r in rows
        ]
        return {
            "bbox": [min_lat, min_lon, max_lat, max_lon],
            "matched": matched,
            "rendered": len(items),
            "limit": parse_limit(q),
            "area_filter": {"min_area_m2": min_area, "max_area_m2": max_area},
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
            "SELECT id, producer_skill, artifact_type, artifact_key, created_at, updated_at FROM bus_artifacts WHERE run_id=? ORDER BY id",
            (CFG["run_id"],),
        ).fetchall()
        return {"count": len(rows), "artifacts": [dict(r) for r in rows]}
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
                send(self, {"status": "ok", "run_id": CFG["run_id"], "started_at_epoch": STARTED_AT, "expires_at_epoch": CFG["expires_at_epoch"], "ttl_seconds": CFG["ttl_seconds"]})
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
    CFG.update({
        "canon_db": str(Path(args.canon_db).resolve()),
        "bus_db": str(Path(args.bus_db).resolve()),
        "run_id": args.run_id,
        "ttl_seconds": ttl,
        "default_limit": max(5, min(MAP_LIMIT_MAX, int(args.default_limit))),
        "expires_at_epoch": time.time() + ttl,
    })
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
