from __future__ import annotations

import argparse
import hashlib
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from skills.shared import bus
from skills.shared.ai_protocol import SkillError, emit_progress
from skills.shared.db import connect

DESCRIPTION = "Fetch OSM objects from Overpass and append/upsert them into canon.sqlite."


def add_domain_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bbox", required=True, help="min_lon,min_lat,max_lon,max_lat")
    parser.add_argument("--key", required=True)
    parser.add_argument("--value", required=True)
    parser.add_argument("--element-types", default="nwr")


def _parse_bbox(raw: str) -> tuple[float, float, float, float]:
    parts = [float(x.strip()) for x in raw.split(",")]
    if len(parts) != 4:
        raise SkillError("INVALID_BBOX", "bbox must have four numbers", status="invalid_input", recoverable=True)
    min_lon, min_lat, max_lon, max_lat = parts
    if min_lon >= max_lon or min_lat >= max_lat:
        raise SkillError("INVALID_BBOX", "bbox bounds are invalid", status="invalid_input", recoverable=True)
    return min_lon, min_lat, max_lon, max_lat


def _center(element: dict):
    if "lat" in element and "lon" in element:
        return float(element["lat"]), float(element["lon"])
    c = element.get("center") or {}
    if "lat" in c and "lon" in c:
        return float(c["lat"]), float(c["lon"])
    b = element.get("bounds") or {}
    if all(k in b for k in ("minlat", "minlon", "maxlat", "maxlon")):
        return (float(b["minlat"]) + float(b["maxlat"])) / 2, (float(b["minlon"]) + float(b["maxlon"])) / 2
    return None, None


def _bbox(element: dict, lat, lon):
    b = element.get("bounds") or {}
    if all(k in b for k in ("minlat", "minlon", "maxlat", "maxlon")):
        return float(b["minlat"]), float(b["minlon"]), float(b["maxlat"]), float(b["maxlon"])
    if lat is not None and lon is not None:
        return lat, lon, lat, lon
    return None, None, None, None


def run(args: argparse.Namespace, ctx) -> dict:
    min_lon, min_lat, max_lon, max_lat = _parse_bbox(args.bbox)
    if args.dry_run:
        return {"status": "ok", "counts": {"planned_requests": 1}, "artifacts": [], "warnings": []}

    limit = int(ctx.profile["limit"])
    timeout = int(ctx.profile["timeout_s"])
    bbox_ql = f"{min_lat},{min_lon},{max_lat},{max_lon}"
    tag_filter = f"[{json.dumps(args.key)}={json.dumps(args.value)}]"

    element_map = {
        "n": "node",
        "w": "way",
        "r": "relation",
        "node": "node",
        "way": "way",
        "relation": "relation",
    }

    raw_types = (args.element_types or "nwr").strip()
    if "," in raw_types or " " in raw_types:
        tokens = [token.strip() for token in raw_types.replace(",", " ").split() if token.strip()]
    elif raw_types in {"n", "w", "r", "nw", "nr", "wr", "nwr"}:
        tokens = list(raw_types)
    else:
        tokens = [raw_types]

    statements = []
    for token in tokens:
        element_type = element_map.get(token)
        if element_type is None:
            raise SkillError(
                "INVALID_ELEMENT_TYPES",
                "element-types must contain node/way/relation or n/w/r",
                status="invalid_input",
                recoverable=True,
            )
        statements.append(f"{element_type}{tag_filter}({bbox_ql});")

    query = f"[out:json][timeout:{timeout}];(" + "".join(statements) + f");out center qt {limit};"

    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    request = urllib.request.Request(
        "https://overpass-api.de/api/interpreter",
        data=data,
        headers={
            "User-Agent": "parcel-skill-live-test/1.0",
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
    )

    emit_progress(ctx, stage="fetch", event="request")

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise SkillError("OVERPASS_ERROR", str(exc)[:200], status="external_error", recoverable=True)

    elements = payload.get("elements", [])
    now = datetime.now(timezone.utc).isoformat()

    con = connect(ctx.canon_db)
    inserted = 0

    for element in elements:
        lat, lon = _center(element)
        bmin_lat, bmin_lon, bmax_lat, bmax_lon = _bbox(element, lat, lon)

        geometry_json = (
            json.dumps(element.get("geometry"), ensure_ascii=False, sort_keys=True)
            if element.get("geometry")
            else None
        )
        geometry_hash = hashlib.sha256(geometry_json.encode("utf-8")).hexdigest() if geometry_json else None

        con.execute(
            """
            INSERT OR REPLACE INTO canon_osm_features(
                osm_type, osm_id, fetched_at, source, bbox_query, tags_json, geometry_json,
                center_lat, center_lon, bbox_min_lat, bbox_min_lon, bbox_max_lat, bbox_max_lon,
                geometry_hash, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                element.get("type", "unknown"),
                int(element.get("id", 0)),
                now,
                "overpass",
                args.bbox,
                json.dumps(element.get("tags", {}), ensure_ascii=False, sort_keys=True),
                geometry_json,
                lat,
                lon,
                bmin_lat,
                bmin_lon,
                bmax_lat,
                bmax_lon,
                geometry_hash,
                json.dumps(element, ensure_ascii=False, sort_keys=True),
            ),
        )
        inserted += 1

    con.commit()
    con.close()

    artifact = bus.publish_artifact(
        ctx.bus_db,
        run_id=ctx.run_id,
        producer_skill=ctx.skill,
        artifact_type="osm_features_import",
        artifact_key="default",
        payload={
            "bbox": args.bbox,
            "key": args.key,
            "value": args.value,
            "count": inserted,
        },
    )

    return {
        "status": "ok" if inserted else "empty",
        "counts": {"inserted": inserted},
        "artifacts": [{"type": artifact["type"], "key": artifact["key"]}],
        "warnings": [],
    }