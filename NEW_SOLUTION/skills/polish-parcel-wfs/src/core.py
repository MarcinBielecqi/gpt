from __future__ import annotations

import argparse

from skills.shared import bus
from skills.shared.ai_protocol import SkillError
from skills.shared.db import connect

DESCRIPTION = "Select parcel candidates from canonical parcels and publish them to project_bus.sqlite."
DEFAULT_INPUT_TYPE = "hotspot_candidates"


def add_domain_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bbox-4326", help="Optional direct bbox: min_lon,min_lat,max_lon,max_lat")
    parser.add_argument("--expected-commune")
    parser.add_argument("--strict-commune", action="store_true")


def _parse_bbox(raw: str) -> tuple[float, float, float, float]:
    parts = [float(x.strip()) for x in raw.split(",")]
    if len(parts) != 4:
        raise SkillError("INVALID_BBOX", "bbox must have four numbers", status="invalid_input", recoverable=True)
    min_lon, min_lat, max_lon, max_lat = parts
    if min_lon >= max_lon or min_lat >= max_lat:
        raise SkillError("INVALID_BBOX", "bbox bounds are invalid", status="invalid_input", recoverable=True)
    return min_lon, min_lat, max_lon, max_lat


def _candidate_bboxes(args, ctx) -> list[list[float]]:
    if args.bbox_4326:
        return [list(_parse_bbox(args.bbox_4326))]
    selector_type, selector_key = bus.parse_selector(args.input_artifact, DEFAULT_INPUT_TYPE)
    artifact = bus.get_latest_artifact(ctx.bus_db, run_id=ctx.run_id, artifact_type=selector_type, artifact_key=selector_key)
    if artifact is None:
        return []
    return [item.get("bbox") for item in artifact["payload"].get("items", []) if item.get("bbox")]


def run(args: argparse.Namespace, ctx) -> dict:
    bboxes = _candidate_bboxes(args, ctx)
    if args.dry_run:
        return {"status": "ok", "counts": {"input_bboxes": len(bboxes)}, "artifacts": [], "warnings": []}
    if not bboxes:
        return {"status": "no_input", "code": "NO_CANDIDATE_BBOXES", "message": "No bbox input found.", "counts": {}, "artifacts": [], "warnings": []}

    con = connect(ctx.canon_db)
    seen = set()
    items = []
    for bbox in bboxes:
        min_lon, min_lat, max_lon, max_lat = bbox
        rows = con.execute(
            """
            SELECT parcel_id, parcel_number, commune, area_m2, centroid_lat, centroid_lon,
                   bbox_min_lat, bbox_min_lon, bbox_max_lat, bbox_max_lon
            FROM canon_parcels
            WHERE centroid_lat BETWEEN ? AND ? AND centroid_lon BETWEEN ? AND ?
            """,
            (min_lat, max_lat, min_lon, max_lon),
        ).fetchall()
        for row in rows:
            if row["parcel_id"] in seen:
                continue
            if args.expected_commune and args.strict_commune and row["commune"] != args.expected_commune:
                continue
            seen.add(row["parcel_id"])
            items.append({
                "parcel_id": row["parcel_id"],
                "parcel_number": row["parcel_number"],
                "commune": row["commune"],
                "area_m2": row["area_m2"],
                "centroid_lat": row["centroid_lat"],
                "centroid_lon": row["centroid_lon"],
                "bbox": [row["bbox_min_lon"], row["bbox_min_lat"], row["bbox_max_lon"], row["bbox_max_lat"]],
            })
            if len(items) >= int(ctx.profile["limit"]):
                break
    con.close()
    payload = {"items": items, "count": len(items), "source_bboxes": len(bboxes), "expected_commune": args.expected_commune}
    artifact = bus.publish_artifact(ctx.bus_db, run_id=ctx.run_id, producer_skill=ctx.skill, artifact_type="parcel_candidates", artifact_key="default", payload=payload)
    return {"status": "ok" if items else "empty", "counts": {"input_bboxes": len(bboxes), "published": len(items)}, "artifacts": [{"type": artifact["type"], "key": artifact["key"]}], "warnings": []}
