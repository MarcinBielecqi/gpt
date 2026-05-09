from __future__ import annotations

import argparse
import math

from skills.shared import bus
from skills.shared.db import connect

DESCRIPTION = "Compute geometry features from canonical parcel geometry and publish them to project_bus.sqlite."


def add_domain_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--parcel-id")


def _polygon_metrics(points: list[tuple[float, float]]) -> dict:
    if len(points) < 3:
        return {"area_m2": 0, "perimeter_m": 0, "compactness": 0, "elongation_ratio": 0}
    # Equirectangular approximation around polygon centroid.
    lat0 = sum(lat for _, lat in points) / len(points)
    scale_x = 111_320.0 * math.cos(math.radians(lat0))
    scale_y = 111_320.0
    xy = [(lon * scale_x, lat * scale_y) for lon, lat in points]
    area = 0.0
    perimeter = 0.0
    for i, (x1, y1) in enumerate(xy):
        x2, y2 = xy[(i + 1) % len(xy)]
        area += x1 * y2 - x2 * y1
        perimeter += math.hypot(x2 - x1, y2 - y1)
    area = abs(area) / 2.0
    compactness = 4 * math.pi * area / (perimeter * perimeter) if perimeter else 0
    xs = [x for x, _ in xy]
    ys = [y for _, y in xy]
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)
    small = max(1e-9, min(width, height))
    elongation = max(width, height) / small
    return {"area_m2": area, "perimeter_m": perimeter, "compactness": compactness, "elongation_ratio": elongation}


def _parcel_ids(args, ctx) -> list[str]:
    if args.parcel_id:
        return [args.parcel_id]
    selector_type, selector_key = bus.parse_selector(args.input_artifact, "parcel_candidates")
    artifact = bus.get_latest_artifact(ctx.bus_db, run_id=ctx.run_id, artifact_type=selector_type, artifact_key=selector_key)
    if not artifact:
        return []
    return [item["parcel_id"] for item in artifact["payload"].get("items", []) if item.get("parcel_id")]


def run(args: argparse.Namespace, ctx) -> dict:
    ids = _parcel_ids(args, ctx)
    if args.dry_run:
        return {"status": "ok", "counts": {"parcel_ids": len(ids)}, "artifacts": [], "warnings": []}
    if not ids:
        return {"status": "no_input", "code": "NO_PARCELS", "message": "No parcel ids found.", "counts": {}, "artifacts": [], "warnings": []}

    con = connect(ctx.canon_db)
    items = []
    for parcel_id in ids[: int(ctx.profile["limit"] )]:
        parcel = con.execute("SELECT * FROM canon_parcels WHERE parcel_id = ?", (parcel_id,)).fetchone()
        if parcel is None:
            continue
        rows = con.execute(
            """
            SELECT lon, lat FROM canon_parcel_polygon_points
            WHERE parcel_id = ? AND polygon_index = 0 AND ring_index = 0
            ORDER BY point_index
            """,
            (parcel_id,),
        ).fetchall()
        if rows:
            metrics = _polygon_metrics([(row["lon"], row["lat"]) for row in rows])
        else:
            metrics = {"area_m2": parcel["area_m2"] or 0, "perimeter_m": None, "compactness": None, "elongation_ratio": None}
        items.append({
            "parcel_id": parcel_id,
            "area_m2": metrics["area_m2"],
            "perimeter_m": metrics["perimeter_m"],
            "compactness": metrics["compactness"],
            "elongation_ratio": metrics["elongation_ratio"],
            "centroid_lat": parcel["centroid_lat"],
            "centroid_lon": parcel["centroid_lon"],
        })
    con.close()
    artifact = bus.publish_artifact(ctx.bus_db, run_id=ctx.run_id, producer_skill=ctx.skill, artifact_type="geometry_features", artifact_key="default", payload={"items": items, "count": len(items)})
    return {"status": "ok" if items else "empty", "counts": {"published": len(items)}, "artifacts": [{"type": artifact["type"], "key": artifact["key"]}], "warnings": []}
