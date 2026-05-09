from __future__ import annotations

import argparse

from skills.shared import bus

DESCRIPTION = "Rank parcel candidates using artifacts stored in project_bus.sqlite."


def add_domain_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ranking-profile", default="default")


def _items_by_id(artifact):
    if not artifact:
        return {}
    return {item.get("parcel_id"): item for item in artifact["payload"].get("items", []) if item.get("parcel_id")}


def run(args: argparse.Namespace, ctx) -> dict:
    parcels = bus.get_latest_artifact(ctx.bus_db, run_id=ctx.run_id, artifact_type="parcel_candidates", artifact_key="default")
    geometry = bus.get_latest_artifact(ctx.bus_db, run_id=ctx.run_id, artifact_type="geometry_features", artifact_key="default")
    rcn = bus.get_latest_artifact(ctx.bus_db, run_id=ctx.run_id, artifact_type="rcn_summary", artifact_key="default")
    if args.dry_run:
        return {"status": "ok", "counts": {"has_parcels": int(bool(parcels)), "has_geometry": int(bool(geometry)), "has_rcn": int(bool(rcn))}, "artifacts": [], "warnings": []}
    if not parcels:
        return {"status": "no_input", "code": "NO_PARCEL_CANDIDATES", "message": "No parcel candidates found.", "counts": {}, "artifacts": [], "warnings": []}

    geometry_by_id = _items_by_id(geometry)
    ranked = []
    for item in parcels["payload"].get("items", []):
        parcel_id = item.get("parcel_id")
        g = geometry_by_id.get(parcel_id, {})
        area = item.get("area_m2") or g.get("area_m2") or 0
        compactness = g.get("compactness") or 0
        elongation = g.get("elongation_ratio") or 0
        score = 0.0
        if area:
            score += min(area, 10000) / 10000
        score += compactness or 0
        if elongation:
            score += max(0, 1 - min(elongation, 10) / 10)
        ranked.append({"parcel_id": parcel_id, "score": round(score, 6), "parcel": item, "geometry": g})
    ranked.sort(key=lambda x: x["score"], reverse=True)
    ranked = ranked[: int(ctx.profile["limit"])]
    out = bus.publish_artifact(ctx.bus_db, run_id=ctx.run_id, producer_skill=ctx.skill, artifact_type="ranked_candidates", artifact_key=args.ranking_profile, payload={"items": ranked, "count": len(ranked), "ranking_profile": args.ranking_profile})
    return {"status": "ok" if ranked else "empty", "counts": {"ranked": len(ranked)}, "artifacts": [{"type": out["type"], "key": out["key"]}], "warnings": []}
