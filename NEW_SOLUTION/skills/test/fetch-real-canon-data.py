#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANON_DB = ROOT / "data" / "canon.sqlite"
RUN_ID = "real_canon_smoke"
WORKSPACE = ROOT / "runs" / RUN_ID

BBOX_WARSAW_CENTER = "20.9900,52.2200,21.0300,52.2500"

QUERIES = [
    ("amenity", "cafe", "node"),
    ("amenity", "restaurant", "node"),
    ("amenity", "school", "node"),
    ("shop", "supermarket", "node"),
]


def run_cmd(cmd: list[str]) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)

    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    print("\n$ " + " ".join(cmd))
    if proc.stderr.strip():
        print(proc.stderr.strip())

    stdout = proc.stdout.strip()
    if stdout:
        print(stdout)

    try:
        payload = json.loads(stdout.splitlines()[-1])
    except Exception as exc:
        raise RuntimeError(f"stdout is not final JSON: {exc}\nSTDOUT={stdout}\nSTDERR={proc.stderr}")

    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed: rc={proc.returncode}, "
            f"status={payload.get('status')}, code={payload.get('code')}"
        )

    return payload


def count_table(table: str) -> int:
    con = sqlite3.connect(CANON_DB)
    try:
        return int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        con.close()


def show_examples() -> None:
    con = sqlite3.connect(CANON_DB)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT osm_type, osm_id, tags_json, center_lat, center_lon
            FROM canon_osm_features
            ORDER BY fetched_at DESC
            LIMIT 10
            """
        ).fetchall()
    finally:
        con.close()

    print("\nEXAMPLES canon_osm_features:")
    for row in rows:
        tags = json.loads(row["tags_json"] or "{}")
        name = tags.get("name") or tags.get("amenity") or tags.get("shop") or "-"
        print(
            f"- {row['osm_type']} {row['osm_id']} | "
            f"{name} | lat={row['center_lat']} lon={row['center_lon']}"
        )


def main() -> int:
    print("REAL CANON DATA TEST")
    print(f"root={ROOT}")
    print(f"canon_db={CANON_DB}")
    print(f"run_id={RUN_ID}")
    print(f"bbox={BBOX_WARSAW_CENTER}")

    before = count_table("canon_osm_features")
    print(f"\nBEFORE canon_osm_features={before}")

    for key, value, element_types in QUERIES:
        run_cmd(
            [
                sys.executable,
                "skills/osm-overpass-fetch/scripts/run.py",
                "--run-id",
                RUN_ID,
                "--workspace",
                str(WORKSPACE),
                "--canon-db",
                str(CANON_DB),
                "--profile",
                "quick",
                "--bbox",
                BBOX_WARSAW_CENTER,
                "--key",
                key,
                "--value",
                value,
                "--element-types",
                element_types,
            ]
        )

    run_cmd(
        [
            sys.executable,
            "skills/osm_hotspot_grid/scripts/run.py",
            "--run-id",
            RUN_ID,
            "--workspace",
            str(WORKSPACE),
            "--canon-db",
            str(CANON_DB),
            "--profile",
            "quick",
            "--bbox",
            BBOX_WARSAW_CENTER,
            "--types",
            "amenity=cafe,amenity=restaurant,amenity=school,shop=supermarket",
        ]
    )

    after = count_table("canon_osm_features")
    print(f"\nAFTER canon_osm_features={after}")
    print(f"INSERTED_OR_UPDATED={after - before}")

    show_examples()

    con = sqlite3.connect(ROOT / "data" / "project_bus.sqlite")
    try:
        bus_count = con.execute(
            """
            SELECT COUNT(*)
            FROM bus_artifacts
            WHERE run_id = ?
            """,
            (RUN_ID,),
        ).fetchone()[0]
    finally:
        con.close()

    print(f"\nBUS_ARTIFACTS for {RUN_ID}={bus_count}")
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())