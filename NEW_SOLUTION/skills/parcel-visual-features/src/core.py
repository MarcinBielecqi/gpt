from __future__ import annotations

import argparse

from skills.shared import bus

DESCRIPTION = "Publish visual feature placeholders or cached visual features through project_bus.sqlite."


def add_domain_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-artifact", default="geometry_features:default")


def run(args: argparse.Namespace, ctx) -> dict:
    selector_type, selector_key = bus.parse_selector(args.source_artifact, "geometry_features")
    artifact = bus.get_latest_artifact(ctx.bus_db, run_id=ctx.run_id, artifact_type=selector_type, artifact_key=selector_key)
    count = len(artifact["payload"].get("items", [])) if artifact else 0
    if args.dry_run:
        return {"status": "ok", "counts": {"source_items": count}, "artifacts": [], "warnings": []}
    if not artifact:
        return {"status": "no_input", "code": "NO_GEOMETRY_FEATURES", "message": "No geometry feature artifact found.", "counts": {}, "artifacts": [], "warnings": []}
    # The new contract keeps JSON in the bus. Tile fetching should be added here behind the same output contract.
    items = [{"parcel_id": item.get("parcel_id"), "visual_status": "not_computed"} for item in artifact["payload"].get("items", [])]
    out = bus.publish_artifact(ctx.bus_db, run_id=ctx.run_id, producer_skill=ctx.skill, artifact_type="visual_features", artifact_key="default", payload={"items": items, "count": len(items)})
    return {"status": "skipped", "code": "VISUAL_ENGINE_NOT_PORTED", "message": "Visual tile engine is not ported in this contract skeleton.", "counts": {"published": len(items)}, "artifacts": [{"type": out["type"], "key": out["key"]}], "warnings": ["visual tile engine not ported"]}
