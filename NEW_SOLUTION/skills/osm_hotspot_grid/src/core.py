from __future__ import annotations

import argparse
import json
from collections import defaultdict

from skills.shared import bus
from skills.shared.ai_protocol import SkillError
from skills.shared.db import connect

DESCRIPTION = "Build hotspot candidates from canonical OSM features and publish them to project_bus.sqlite."


def add_domain_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bbox", required=True, help="min_lon,min_lat,max_lon,max_lat")
    parser.add_argument("--types", default="", help="Optional CSV of tag filters, e.g. amenity=school,shop=supermarket")


def _parse_bbox(raw: str) -> tuple[float, float, float, float]:
    parts = [float(x.strip()) for x in raw.split(",")]
    if len(parts) != 4:
        raise SkillError("INVALID_BBOX", "bbox must have four numbers", status="invalid_input", recoverable=True)
    min_lon, min_lat, max_lon, max_lat = parts
    if min_lon >= max_lon or min_lat >= max_lat:
        raise SkillError("INVALID_BBOX", "bbox bounds are invalid", status="invalid_input", recoverable=True)
    return min_lon, min_lat, max_lon, max_lat


def _parse_types(raw: str) -> list[tuple[str, str]]:
    items = []
    for token in [x.strip() for x in raw.split(",") if x.strip()]:
        if "=" not in token:
            raise SkillError("INVALID_TYPES", "types must use key=value tokens", status="invalid_input", recoverable=True)
        k, v = token.split("=", 1)
        items.append((k.strip(), v.strip()))
    return items


def _matches(tags_json: str, filters: list[tuple[str, str]]) -> bool:
    if not filters:
        return True
    try:
        tags = json.loads(tags_json or "{}")
    except Exception:
        return False
    return any(str(tags.get(k)) == v for k, v in filters)


def run(args: argparse.Namespace, ctx) -> dict:
    min_lon, min_lat, max_lon, max_lat = _parse_bbox(args.bbox)
    filters = _parse_types(args.types)
    if args.dry_run:
        return {"status": "ok", "counts": {"planned_queries": 1}, "artifacts": [], "warnings": []}

    con = connect(ctx.canon_db)
    rows = con.execute(
        """
        SELECT osm_type, osm_id, tags_json, center_lat, center_lon
        FROM canon_osm_features
        WHERE center_lat BETWEEN ? AND ? AND center_lon BETWEEN ? AND ?
        """,
        (min_lat, max_lat, min_lon, max_lon),
    ).fetchall()
    con.close()

    grid_n = int(ctx.profile["grid_n"])
    limit = int(ctx.profile["limit"])
    buckets = defaultdict(list)
    for row in rows:
        if row["center_lat"] is None or row["center_lon"] is None:
            continue
        if not _matches(row["tags_json"], filters):
            continue
        x = min(grid_n - 1, max(0, int((row["center_lon"] - min_lon) / (max_lon - min_lon) * grid_n)))
        y = min(grid_n - 1, max(0, int((row["center_lat"] - min_lat) / (max_lat - min_lat) * grid_n)))
        buckets[(x, y)].append(dict(row))

    candidates = []
    for (x, y), bucket in buckets.items():
        c_min_lon = min_lon + (max_lon - min_lon) * x / grid_n
        c_max_lon = min_lon + (max_lon - min_lon) * (x + 1) / grid_n
        c_min_lat = min_lat + (max_lat - min_lat) * y / grid_n
        c_max_lat = min_lat + (max_lat - min_lat) * (y + 1) / grid_n
        candidates.append({
            "candidate_id": f"osm_{x}_{y}",
            "bbox": [c_min_lon, c_min_lat, c_max_lon, c_max_lat],
            "center_lon": (c_min_lon + c_max_lon) / 2,
            "center_lat": (c_min_lat + c_max_lat) / 2,
            "score": len(bucket),
            "source_count": len(bucket),
        })
    candidates.sort(key=lambda item: item["score"], reverse=True)
    candidates = candidates[:limit]

    payload = {"bbox": args.bbox, "types": args.types, "items": candidates, "count": len(candidates)}
    artifact = bus.publish_artifact(ctx.bus_db, run_id=ctx.run_id, producer_skill=ctx.skill, artifact_type="hotspot_candidates", artifact_key="default", payload=payload)
    return {"status": "ok" if candidates else "empty", "counts": {"input_features": len(rows), "published": len(candidates)}, "artifacts": [{"type": artifact["type"], "key": artifact["key"]}], "warnings": []}
