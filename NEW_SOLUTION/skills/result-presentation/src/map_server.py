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

CFG: dict[str, Any] = {}
STARTED_AT = time.time()

HTML = r"""<!doctype html><html lang="pl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Result Presentation Map</title><link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
html,body,#map{height:100%;margin:0}body{font-family:Segoe UI,Tahoma,sans-serif;background:#f7f6ef;color:#18201b;overflow:hidden}#map{background:#e9ecdf}
.panel{background:rgba(255,255,250,.94);border:1px solid rgba(88,111,92,.35);box-shadow:0 14px 40px rgba(34,46,35,.18);backdrop-filter:blur(10px)}
.top{position:absolute;z-index:1000;top:14px;left:14px;right:14px;display:flex;gap:10px;pointer-events:none}.top>*{pointer-events:auto}
.summary{width:min(520px,calc(100vw - 28px));padding:12px 14px}.controls{margin-left:auto;min-width:230px;padding:10px}
h1{font-size:17px;margin:0 0 8px}.status{font-size:13px;color:#5f6f65}.stats{display:grid;grid-template-columns:repeat(4,minmax(70px,1fr));gap:8px;margin-top:10px}.stat{border:1px solid #d8dfd2;background:rgba(255,255,255,.62);padding:7px}.stat b{display:block;font-size:15px}.stat span{font-size:11px;color:#5f6f65}
label,.row{display:grid;gap:5px;font-size:13px;margin:7px 0}select,input{font:inherit}.legend{position:absolute;z-index:1000;left:14px;bottom:18px;width:min(420px,calc(100vw - 28px));padding:12px;font-size:13px;line-height:1.45}.err{display:none;position:absolute;z-index:2000;top:14px;left:14px;right:14px;background:#fff7ed;border:1px solid #ea580c;color:#7c2d12;padding:12px}
.popup{min-width:220px;font-size:13px}.popup b{display:block;margin-bottom:5px}
@media(max-width:760px){.top{display:block}.controls{margin:10px 0 0}.legend{display:none}.stats{grid-template-columns:repeat(2,1fr)}}
</style></head><body><div id="map"></div><div id="err" class="err"></div><div class="top"><section class="panel summary"><h1>Result Presentation Map</h1><div id="status" class="status">Ładowanie manifestu...</div><div class="stats"><div class="stat"><b id="total">0</b><span>działek w DB</span></div><div class="stat"><b id="visible">0</b><span>w widoku</span></div><div class="stat"><b id="drawn">0</b><span>rysowane</span></div><div class="stat"><b id="mode">-</b><span>tryb</span></div></div></section><section class="panel controls"><label>podkład<select id="base"><option value="street">ulice</option><option value="sat">satelita</option></select></label><label>kolor<select id="color"><option value="commune">gmina</option><option value="county">powiat</option><option value="voivodeship">województwo</option><option value="precinct">obręb</option></select></label><label>limit <output id="limitOut">500</output><input id="limit" type="range" min="5" max="2500" step="5" value="500"></label><label>gęste miejsca<select id="jump"><option value="">wybierz...</option></select></label></section></div><aside class="panel legend">Mapa czyta poligony z <code>canon.sqlite</code> przez lokalny serwer HTTP. Link jest świeży, a serwer sam kończy działanie po TTL.</aside>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>
const pal=["#1e7d46","#c25a33","#2662a6","#9b6b16","#7a4fb2","#0f766e","#b42359","#5f7f24","#bd6b00","#3b5b92"];let man=null,drawn=[],tok=0;
const map=L.map("map",{preferCanvas:true}), layer=L.layerGroup().addTo(map), base={street:L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:20,attribution:"© OpenStreetMap"}),sat:L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",{maxZoom:20,attribution:"Tiles © Esri"})};let active=base.street.addTo(map);
const $=id=>document.getElementById(id), fmt=n=>new Intl.NumberFormat("pl-PL").format(n||0), area=m=>!m?"brak area_m2":m>=10000?`${(m/10000).toLocaleString("pl-PL",{maximumFractionDigits:2})} ha`:`${Math.round(m).toLocaleString("pl-PL")} m²`;
function err(m){$("err").textContent=m;$("err").style.display="block"}function b2b(b){return L.latLngBounds([b[0],b[1]],[b[2],b[3]])}function mb(){let b=map.getBounds(),s=b.getSouthWest(),n=b.getNorthEast();return [s.lat,s.lng,n.lat,n.lng].join(",")}function hash(t){let h=2166136261;for(let i=0;i<t.length;i++){h^=t.charCodeAt(i);h=Math.imul(h,16777619)}return h>>>0}function key(p){return p[$("color").value]||"unknown"}function col(p){return pal[hash(key(p))%pal.length]}function limit(){return Math.max(5,Math.min(2500,parseInt($("limit").value||man.default_limit||500,10)))}
function draw(){layer.clearLayers();let c=0;for(const p of drawn){if(!p.geometry?.length)continue;let color=col(p);for(const rings of p.geometry){L.polygon(rings,{color,weight:2,opacity:.98,fillColor:color,fillOpacity:$("base").value==="sat"?.34:.24}).bindPopup(`<div class="popup"><b>${p.number||p.id}</b>${p.commune||""} / ${p.precinct||"brak obrębu"}<br>${area(p.area_m2)}<br>Kolor: ${key(p)}<br><small>${p.id}</small></div>`).addTo(layer)}c++}$("drawn").textContent=fmt(c)}
async function render(){if(!man)return;let t=++tok,l=limit();$("limitOut").textContent=fmt(l);$("mode").textContent="ładowanie";$("status").textContent="Ładowanie poligonów...";try{let r=await fetch(`/api/parcels?bbox=${encodeURIComponent(mb())}&limit=${l}`,{cache:"no-store"});if(!r.ok)throw Error("HTTP "+r.status);let p=await r.json();if(t!==tok)return;drawn=p.parcels||[];draw();$("visible").textContent=fmt(p.matched);$("mode").textContent="poligony";$("status").textContent=p.matched>drawn.length?`${fmt(p.matched)} działek przecina widok. Rysuję ${fmt(drawn.length)} najbliżej środka mapy.`:`${fmt(p.matched)} działek przecina widok.`}catch(e){err("Nie udało się załadować poligonów: "+e.message)}}
function deb(f,d=140){let x=0;return(...a)=>{clearTimeout(x);x=setTimeout(()=>f(...a),d)}}function dens(b){let mid=(b[0]+b[2])/2,h=Math.abs(b[2]-b[0])*111.32,w=Math.abs(b[3]-b[1])*111.32*Math.cos(mid*Math.PI/180);return Math.max(.000001,w*h)}
async function init(){try{let r=await fetch("/api/manifest",{cache:"no-store"});if(!r.ok)throw Error("manifest HTTP "+r.status);man=await r.json();$("total").textContent=fmt(man.parcel_count);$("limit").value=man.default_limit||500;$("limitOut").textContent=fmt(limit());for(const g of ((man.groups?.precinct||man.groups?.commune||[]).filter(x=>x.count>=2&&x.bbox).map(x=>({x,d:x.count/dens(x.bbox)})).sort((a,b)=>b.d-a.d).slice(0,20))){let o=document.createElement("option");o.value=JSON.stringify(g.x.bbox);o.textContent=`${g.x.label} · ${fmt(g.x.count)} dz. · ${g.d.toFixed(1)}/km²`;$("jump").appendChild(o)}if(man.bounds)map.fitBounds(b2b(man.bounds),{padding:[24,24]});else map.setView([52,19],6);map.on("moveend zoomend",deb(render));$("limit").addEventListener("input",deb(render,90));$("color").addEventListener("change",draw);$("base").addEventListener("change",()=>{map.removeLayer(active);active=base[$("base").value].addTo(map);draw()});$("jump").addEventListener("change",()=>{$("jump").value&&map.fitBounds(b2b(JSON.parse($("jump").value)),{padding:[48,48]})});await render()}catch(e){err("Nie udało się uruchomić mapy: "+e.message)}}init();
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
        s = c.execute("""SELECT COUNT(*) n, MIN(bbox_min_lat) a, MIN(bbox_min_lon) b, MAX(bbox_max_lat) c, MAX(bbox_max_lon) d
                         FROM canon_parcels WHERE bbox_min_lat IS NOT NULL AND bbox_min_lon IS NOT NULL""").fetchone()
        groups = {}
        for f in ("precinct", "commune", "county", "voivodeship"):
            rows = c.execute(f"""SELECT COALESCE({f},'brak') label, COUNT(*) count, MIN(bbox_min_lat) a, MIN(bbox_min_lon) b, MAX(bbox_max_lat) c, MAX(bbox_max_lon) d
                                 FROM canon_parcels GROUP BY COALESCE({f},'brak') HAVING a IS NOT NULL ORDER BY count DESC, label LIMIT 250""").fetchall()
            groups[f] = [{"key": str(r["label"]), "label": str(r["label"]), "count": int(r["count"]), "bbox": [r["a"], r["b"], r["c"], r["d"]]} for r in rows]
        rcn = int(c.execute("SELECT COUNT(*) n FROM canon_rcn_price_observations").fetchone()["n"]) if exists(c, "canon_rcn_price_observations") else 0
        return {"run_id": CFG["run_id"], "parcel_count": int(s["n"] or 0), "rcn_count": rcn,
                "bounds": [s["a"], s["b"], s["c"], s["d"]] if s["n"] else None, "groups": groups,
                "default_limit": int(CFG["default_limit"]), "expires_at_epoch": CFG["expires_at_epoch"]}
    finally:
        c.close()


def parse_bbox(q: dict[str, list[str]]) -> tuple[float, float, float, float] | None:
    try:
        p = [float(x) for x in q.get("bbox", [""])[0].split(",")]
        return tuple(p) if len(p) == 4 and p[0] <= p[2] and p[1] <= p[3] else None
    except ValueError:
        return None


def parse_limit(q: dict[str, list[str]]) -> int:
    try:
        v = int(q.get("limit", [CFG["default_limit"]])[0])
    except ValueError:
        v = int(CFG["default_limit"])
    return max(5, min(2500, v))


def group_points(rows: list[sqlite3.Row]) -> dict[str, list[list[list[list[float]]]]]:
    out: dict[str, OrderedDict[int, OrderedDict[int, list[list[float]]]]] = {}
    for r in rows:
        out.setdefault(r["parcel_id"], OrderedDict()).setdefault(int(r["polygon_index"]), OrderedDict()).setdefault(int(r["ring_index"]), []).append([float(r["lat"]), float(r["lon"])])
    return {pid: [[pts for _, pts in rings.items() if len(pts) >= 3] for _, rings in polys.items()] for pid, polys in out.items()}


def parcels(q: dict[str, list[str]]) -> dict[str, Any]:
    c = con(CFG["canon_db"])
    try:
        b = parse_bbox(q) or tuple(manifest()["bounds"] or [])
        if len(b) != 4:
            return {"parcels": [], "matched": 0, "rendered": 0, "warnings": ["no bounds"]}
        mnla, mnlo, mxla, mxlo = b
        center = ((mnla + mxla) / 2, (mnlo + mxlo) / 2)
        where = "bbox_max_lat>=? AND bbox_min_lat<=? AND bbox_max_lon>=? AND bbox_min_lon<=?"
        params = (mnla, mxla, mnlo, mxlo)
        matched = int(c.execute(f"SELECT COUNT(*) n FROM canon_parcels WHERE {where}", params).fetchone()["n"])
        rows = c.execute(f"""SELECT parcel_id, parcel_number, voivodeship, county, commune, precinct, area_m2, centroid_lat, centroid_lon,
                                    bbox_min_lat, bbox_min_lon, bbox_max_lat, bbox_max_lon
                             FROM canon_parcels WHERE {where}
                             ORDER BY ((COALESCE(centroid_lat,0)-?)*(COALESCE(centroid_lat,0)-?) + (COALESCE(centroid_lon,0)-?)*(COALESCE(centroid_lon,0)-?))
                             LIMIT ?""", (*params, center[0], center[0], center[1], center[1], parse_limit(q))).fetchall()
        ids = [r["parcel_id"] for r in rows]
        geom: dict[str, list[list[list[list[float]]]]] = {}
        if ids and exists(c, "canon_parcel_polygon_points"):
            ph = ",".join("?" for _ in ids)
            geom = group_points(c.execute(f"SELECT parcel_id, polygon_index, ring_index, point_index, lat, lon FROM canon_parcel_polygon_points WHERE parcel_id IN ({ph}) ORDER BY parcel_id, polygon_index, ring_index, point_index", ids).fetchall())
        items = [{"id": r["parcel_id"], "number": r["parcel_number"], "voivodeship": r["voivodeship"], "county": r["county"],
                  "commune": r["commune"], "precinct": r["precinct"], "area_m2": r["area_m2"], "center": [r["centroid_lat"], r["centroid_lon"]],
                  "bbox": [r["bbox_min_lat"], r["bbox_min_lon"], r["bbox_max_lat"], r["bbox_max_lon"]], "geometry": geom.get(r["parcel_id"], [])} for r in rows]
        return {"bbox": [mnla, mnlo, mxla, mxlo], "matched": matched, "rendered": len(items), "limit": parse_limit(q), "parcels": items, "warnings": []}
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
        rows = c.execute("""SELECT id, producer_skill, artifact_type, artifact_key, created_at, updated_at FROM bus_artifacts WHERE run_id=? ORDER BY id""", (CFG["run_id"],)).fetchall()
        return {"count": len(rows), "artifacts": [dict(r) for r in rows]}
    finally:
        c.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        p = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(p.query)
        try:
            if p.path in {"/", "/map.html"}:
                body = HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            elif p.path == "/api/health":
                send(self, {"status": "ok", "run_id": CFG["run_id"], "started_at_epoch": STARTED_AT, "expires_at_epoch": CFG["expires_at_epoch"], "ttl_seconds": CFG["ttl_seconds"]})
            elif p.path == "/api/manifest":
                send(self, manifest())
            elif p.path == "/api/parcels":
                send(self, parcels(q))
            elif p.path == "/api/artifacts":
                send(self, artifacts())
            else:
                send(self, {"status": "error", "message": "not found"}, 404)
        except Exception as exc:  # noqa: BLE001
            send(self, {"status": "error", "message": str(exc)}, 500)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--canon-db", required=True)
    ap.add_argument("--bus-db", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--ttl-seconds", type=int, default=300)
    ap.add_argument("--default-limit", type=int, default=500)
    args = ap.parse_args()
    CFG.update({"canon_db": str(Path(args.canon_db).resolve()), "bus_db": str(Path(args.bus_db).resolve()), "run_id": args.run_id,
                "ttl_seconds": max(1, int(args.ttl_seconds)), "default_limit": max(5, int(args.default_limit)),
                "expires_at_epoch": time.time() + max(1, int(args.ttl_seconds))})
    srv = ThreadingHTTPServer((args.host, int(args.port)), Handler)
    timer = threading.Timer(CFG["ttl_seconds"], srv.shutdown)
    timer.daemon = True
    timer.start()
    try:
        srv.serve_forever(poll_interval=0.25)
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
