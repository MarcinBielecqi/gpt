#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from skills.shared.parcel_db import DEFAULT_ANALYSIS_DB_PATH, DEFAULT_CANON_DB_PATH, connect_workspace

VISUAL_SCRIPT = Path(__file__).resolve().with_name("compute_parcel_visual_features.py")
spec = importlib.util.spec_from_file_location("compute_parcel_visual_features", VISUAL_SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Cannot load {VISUAL_SCRIPT}")
visual = importlib.util.module_from_spec(spec)
spec.loader.exec_module(visual)


def missing_parcel_ids(connection: sqlite3.Connection, run_id: str | None, limit: int) -> list[str]:
    visual.ensure_visual_feature_table(connection)
    params: list[object] = []
    run_join = ""
    run_where = ""
    if run_id:
        run_join = "JOIN helper_layer2_run_parcels rp ON rp.parcel_id = p.parcel_id"
        run_where = "AND rp.run_id = ?"
        params.append(run_id)
    limit_clause = ""
    if limit > 0:
        limit_clause = "LIMIT ?"
        params.append(limit)
    rows = connection.execute(
        f"""
        SELECT DISTINCT p.parcel_id
        FROM canon_parcels p
        {run_join}
        LEFT JOIN deriv_parcel_visual_features vf ON vf.parcel_id = p.parcel_id
        WHERE vf.parcel_id IS NULL
        {run_where}
        ORDER BY p.parcel_id
        {limit_clause}
        """,
        tuple(params),
    ).fetchall()
    return [row[0] for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description="Fill missing deriv_parcel_visual_features rows with resumable progress.")
    parser.add_argument("--db-path", default=str(DEFAULT_ANALYSIS_DB_PATH))
    parser.add_argument("--canon-db-path", default=str(DEFAULT_CANON_DB_PATH))
    parser.add_argument("--run-id", help="Optional: fill only parcels linked to this Layer 2 run.")
    parser.add_argument("--limit", type=int, default=0, help="Optional batch size. 0 means all missing.")
    parser.add_argument("--zoom", type=int, default=18)
    parser.add_argument("--tile-template", default=visual.DEFAULT_TILE_TEMPLATE)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--summary-output", default="results/visual_features_fill_missing_summary.json")
    args = parser.parse_args()

    connection = connect_workspace(args.db_path, args.canon_db_path)
    try:
        ids = missing_parcel_ids(connection, args.run_id, args.limit)
        before_missing = len(ids)
        summary = visual.compute_to_db(
            connection,
            ids,
            zoom=args.zoom,
            tile_template=args.tile_template,
            timeout=args.timeout,
            progress_every=args.progress_every,
        )
    finally:
        connection.close()

    summary.update(
        {
            "mode": "fill_missing",
            "run_id": args.run_id,
            "missing_selected_before_run": before_missing,
            "limit": args.limit,
            "table": "deriv_parcel_visual_features",
            "zoom": args.zoom,
            "image_source": args.tile_template,
        }
    )
    output = Path(args.summary_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), **summary}, ensure_ascii=True, sort_keys=True))
    return 1 if summary["errors"] and not summary["computed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
