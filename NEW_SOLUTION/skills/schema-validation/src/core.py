from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from skills.shared import bus
from skills.shared.db import CANON_SQL, BUS_SQL, connect

DESCRIPTION = "Validate and initialize the canonical DB, project bus DB, and local skill DB."


def add_domain_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--stage", choices=["init", "base", "final"], default="base")


def _tables(path: Path) -> set[str]:
    con = connect(path)
    rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    con.close()
    return {row[0] for row in rows}


def run(args: argparse.Namespace, ctx) -> dict:
    if args.dry_run:
        return {"status": "ok", "counts": {"planned_checks": 3}, "artifacts": [], "warnings": []}

    con = connect(ctx.canon_db)
    con.executescript(CANON_SQL)
    con.commit()
    con.close()

    con = connect(ctx.bus_db)
    con.executescript(BUS_SQL)
    con.commit()
    con.close()

    canon_required = {"canon_osm_features", "canon_parcels", "canon_parcel_polygon_points", "canon_rcn_price_observations"}
    bus_required = {"bus_runs", "bus_skill_status", "bus_artifacts"}
    local_required = {"skill_state", "skill_cache", "skill_errors"}

    missing_canon = sorted(canon_required - _tables(ctx.canon_db))
    missing_bus = sorted(bus_required - _tables(ctx.bus_db))
    missing_local = sorted(local_required - _tables(ctx.local_db))

    payload = {
        "stage": args.stage,
        "canon_db": str(ctx.canon_db),
        "project_bus_db": str(ctx.bus_db),
        "local_db": str(ctx.local_db),
        "missing": {"canon": missing_canon, "bus": missing_bus, "local": missing_local},
    }
    artifact = bus.publish_artifact(
        ctx.bus_db,
        run_id=ctx.run_id,
        producer_skill=ctx.skill,
        artifact_type="validation_result",
        artifact_key=args.stage,
        payload=payload,
    )
    status = "ok" if not (missing_canon or missing_bus or missing_local) else "schema_error"
    return {
        "status": status,
        "counts": {"missing_tables": len(missing_canon) + len(missing_bus) + len(missing_local)},
        "artifacts": [{"type": artifact["type"], "key": artifact["key"]}],
        "warnings": [],
    }
