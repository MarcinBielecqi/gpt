from __future__ import annotations

import argparse
import statistics

from skills.shared import bus
from skills.shared.db import connect

DESCRIPTION = "Summarize canonical RCN price observations for selected parcel candidates."


def add_domain_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bbox-4326", help="Optional direct bbox. Used only as metadata when no parcel candidates are supplied.")


def run(args: argparse.Namespace, ctx) -> dict:
    selector_type, selector_key = bus.parse_selector(args.input_artifact, "parcel_candidates")
    artifact = bus.get_latest_artifact(ctx.bus_db, run_id=ctx.run_id, artifact_type=selector_type, artifact_key=selector_key)
    parcel_ids = []
    if artifact:
        parcel_ids = [item["parcel_id"] for item in artifact["payload"].get("items", []) if item.get("parcel_id")]
    if args.dry_run:
        return {"status": "ok", "counts": {"parcel_ids": len(parcel_ids)}, "artifacts": [], "warnings": []}
    if not parcel_ids:
        return {"status": "no_input", "code": "NO_PARCEL_CANDIDATES", "message": "No parcel candidates found.", "counts": {}, "artifacts": [], "warnings": []}

    placeholders = ",".join("?" for _ in parcel_ids)
    con = connect(ctx.canon_db)
    rows = con.execute(
        f"""
        SELECT parcel_id, transaction_date, area_m2, price_pln, price_per_m2,
               inflation_adjusted_price_per_m2
        FROM canon_rcn_price_observations
        WHERE parcel_id IN ({placeholders})
        """,
        parcel_ids,
    ).fetchall()
    con.close()
    values = [row["inflation_adjusted_price_per_m2"] or row["price_per_m2"] for row in rows if (row["inflation_adjusted_price_per_m2"] or row["price_per_m2"]) is not None]
    summary = {
        "parcel_count": len(parcel_ids),
        "observation_count": len(rows),
        "price_per_m2": {
            "count": len(values),
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "avg": sum(values) / len(values) if values else None,
            "median": statistics.median(values) if values else None,
        },
    }
    artifact = bus.publish_artifact(ctx.bus_db, run_id=ctx.run_id, producer_skill=ctx.skill, artifact_type="rcn_summary", artifact_key="default", payload=summary)
    return {"status": "ok" if values else "empty", "counts": {"observations": len(rows), "priced": len(values)}, "artifacts": [{"type": artifact["type"], "key": artifact["key"]}], "warnings": []}
