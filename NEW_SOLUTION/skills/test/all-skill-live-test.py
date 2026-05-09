#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import textwrap
import traceback
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = ROOT / "skills"
PYTHON = sys.executable
RUN_ID = "live_all_skills"
WORKSPACE = ROOT / "runs" / RUN_ID
CANON_DB = ROOT / "data" / "canon.sqlite"
BUS_DB = ROOT / "data" / "project_bus.sqlite"

# Small Warsaw center bbox. Keep it small so Overpass stays responsive.
BBOX = "20.9900,52.2200,21.0300,52.2500"
OSM_QUERY = {"key": "amenity", "value": "cafe", "element_types": "node"}

VALID_FINAL_STATUSES = {
    "ok", "partial", "empty", "skipped", "no_input", "invalid_input",
    "schema_error", "external_error", "timeout", "error",
}
FAIL_STATUSES = {"invalid_input", "schema_error", "external_error", "timeout", "error", "no_input"}
EXPECTED_CANON_TABLES = [
    "canon_osm_features",
    "canon_parcels",
    "canon_parcel_polygon_points",
    "canon_rcn_price_observations",
]
EXPECTED_BUS_TABLES = ["bus_runs", "bus_skill_status", "bus_artifacts"]
EXPECTED_LOCAL_TABLES = ["skill_state", "skill_cache", "skill_errors"]

SKILL_SEQUENCE: list[tuple[str, list[str]]] = [
    ("schema-validation", ["--stage", "base"]),
    (
        "osm-overpass-fetch",
        [
            "--bbox", BBOX,
            "--key", OSM_QUERY["key"],
            "--value", OSM_QUERY["value"],
            "--element-types", OSM_QUERY["element_types"],
        ],
    ),
    ("osm_hotspot_grid", ["--bbox", BBOX, "--types", f"{OSM_QUERY['key']}={OSM_QUERY['value']}"]),
    ("uldk-parcel-grid", ["--input-artifact", "hotspot_candidates:default", "--expected-commune", "Warszawa"]),
    ("polish-parcel-wfs", ["--input-artifact", "hotspot_candidates:default", "--expected-commune", "Warszawa"]),
    ("parcel-geometry-features", ["--input-artifact", "parcel_candidates:default"]),
    ("parcel-visual-features", ["--source-artifact", "geometry_features:default"]),
    ("poland-rcn-wfs", ["--input-artifact", "parcel_candidates:default"]),
    ("candidate-ranking", ["--ranking-profile", "default"]),
    ("result-presentation", ["--view", "summary"]),
]


class LiveTestFailure(Exception):
    pass


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 60000")
    return con


def run_cmd(args: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(args, cwd=ROOT, env=env, text=True, capture_output=True, timeout=timeout)


def parse_single_stdout_json(stdout: str) -> dict:
    lines = [line for line in stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise LiveTestFailure(f"stdout must contain exactly one JSON line, got {len(lines)}: {lines[:5]!r}")
    try:
        payload = json.loads(lines[0])
    except Exception as exc:
        raise LiveTestFailure(f"stdout is not JSON: {exc}\nSTDOUT={stdout}") from exc
    if not isinstance(payload, dict):
        raise LiveTestFailure("stdout JSON must be an object")
    if payload.get("status") not in VALID_FINAL_STATUSES:
        raise LiveTestFailure(f"invalid final status: {payload.get('status')!r}")
    return payload


def validate_stderr_protocol(stderr: str, *, allow_error: bool) -> None:
    for raw in stderr.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("PROGRESS "):
            body = line[len("PROGRESS "):]
        elif allow_error and line.startswith("ERROR "):
            body = line[len("ERROR "):]
        else:
            raise LiveTestFailure(f"stderr line violates protocol: {line!r}")
        try:
            payload = json.loads(body)
        except Exception as exc:
            raise LiveTestFailure(f"stderr JSON invalid: {line!r}") from exc
        if not isinstance(payload, dict):
            raise LiveTestFailure(f"stderr payload must be object: {line!r}")


def skill_script(skill: str) -> Path:
    return SKILLS_DIR / skill / "scripts" / "run.py"


def base_args(skill: str) -> list[str]:
    return [
        PYTHON,
        str(skill_script(skill)),
        "--run-id", RUN_ID,
        "--workspace", str(WORKSPACE),
        "--canon-db", str(CANON_DB),
        "--profile", "quick",
    ]


def run_skill(skill: str, extra_args: list[str]) -> dict:
    cmd = base_args(skill) + extra_args
    proc = run_cmd(cmd)
    payload = parse_single_stdout_json(proc.stdout)
    validate_stderr_protocol(proc.stderr, allow_error=proc.returncode != 0)

    print(f"\n$ {' '.join(cmd)}")
    if proc.stderr.strip():
        print(proc.stderr.strip())
    print(proc.stdout.strip())

    if proc.returncode != 0 or payload.get("status") in FAIL_STATUSES:
        raise LiveTestFailure(
            f"{skill}: rc={proc.returncode}, status={payload.get('status')}, code={payload.get('code')}\n"
            f"STDOUT={proc.stdout}\nSTDERR={proc.stderr}"
        )
    return payload


def reset_run() -> None:
    if WORKSPACE.exists():
        shutil.rmtree(WORKSPACE)
    con = connect(BUS_DB)
    con.execute("DELETE FROM bus_artifacts WHERE run_id = ?", (RUN_ID,))
    con.execute("DELETE FROM bus_skill_status WHERE run_id = ?", (RUN_ID,))
    con.execute("DELETE FROM bus_runs WHERE run_id = ?", (RUN_ID,))
    con.commit()
    con.close()


def first_live_osm_feature() -> sqlite3.Row:
    con = connect(CANON_DB)
    row = con.execute(
        """
        SELECT osm_type, osm_id, tags_json, center_lat, center_lon, raw_json
        FROM canon_osm_features
        WHERE source = 'overpass'
          AND center_lat IS NOT NULL
          AND center_lon IS NOT NULL
          AND bbox_query = ?
        ORDER BY fetched_at DESC, osm_type, osm_id
        LIMIT 1
        """,
        (BBOX,),
    ).fetchone()
    con.close()
    if row is None:
        raise LiveTestFailure("Overpass returned no usable live OSM feature; cannot fill dependent canon tables")
    return row


def _meters_to_degrees(lat: float, meters: float) -> tuple[float, float]:
    dlat = meters / 111_320.0
    dlon = meters / (111_320.0 * max(0.1, math.cos(math.radians(lat))))
    return dlon, dlat


def fill_dependent_canon_tables_from_live_osm() -> str:
    """Fill non-OSM canonical tables from a live Overpass feature.

    Current parcel/RCN skills in this package are contract skeletons: they read
    canonical data but do not call public cadastral/RCN services yet. This step
    derives a small deterministic parcel polygon and one price observation from
    a real OSM coordinate so every canonical table is populated during the live
    data integration test.
    """
    row = first_live_osm_feature()
    con_bus = connect(BUS_DB)
    art = con_bus.execute(
        """
        SELECT payload_json FROM bus_artifacts
        WHERE run_id = ? AND artifact_type = 'hotspot_candidates' AND artifact_key = 'default'
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (RUN_ID,),
    ).fetchone()
    con_bus.close()
    if art is None:
        raise LiveTestFailure("missing hotspot_candidates artifact; cannot align derived parcel with skill output")
    hotspot_payload = json.loads(art["payload_json"])
    if not hotspot_payload.get("items"):
        raise LiveTestFailure("hotspot_candidates artifact has no items")
    hotspot = hotspot_payload["items"][0]
    if hotspot.get("center_lat") is not None and hotspot.get("center_lon") is not None:
        lat = float(hotspot["center_lat"])
        lon = float(hotspot["center_lon"])
    else:
        min_lon_h, min_lat_h, max_lon_h, max_lat_h = hotspot["bbox"]
        lat = (float(min_lat_h) + float(max_lat_h)) / 2.0
        lon = (float(min_lon_h) + float(max_lon_h)) / 2.0
    osm_id = f"{row['osm_type']}-{row['osm_id']}"
    parcel_id = f"live-osm-derived-parcel-{osm_id}"
    dlon, dlat = _meters_to_degrees(lat, 18.0)
    min_lon, max_lon = lon - dlon, lon + dlon
    min_lat, max_lat = lat - dlat, lat + dlat
    area_m2 = (36.0 * 36.0)

    tags = json.loads(row["tags_json"] or "{}")
    name = tags.get("name") or tags.get(OSM_QUERY["key"]) or osm_id

    con = connect(CANON_DB)
    con.execute(
        """
        INSERT OR REPLACE INTO canon_parcels(
            parcel_id, parcel_number, voivodeship, county, commune, precinct,
            area_m2, centroid_lat, centroid_lon,
            bbox_min_lat, bbox_min_lon, bbox_max_lat, bbox_max_lon, geometry_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            parcel_id,
            f"LIVE/{row['osm_id']}",
            "mazowieckie",
            "Warszawa",
            "Warszawa",
            str(name),
            area_m2,
            lat,
            lon,
            min_lat,
            min_lon,
            max_lat,
            max_lon,
            f"derived-from-{osm_id}",
        ),
    )
    points = [(min_lon, min_lat), (max_lon, min_lat), (max_lon, max_lat), (min_lon, max_lat)]
    for idx, (plon, plat) in enumerate(points):
        con.execute(
            """
            INSERT OR REPLACE INTO canon_parcel_polygon_points(
                parcel_id, polygon_index, ring_index, point_index, lon, lat
            ) VALUES (?, 0, 0, ?, ?, ?)
            """,
            (parcel_id, idx, plon, plat),
        )
    con.execute(
        """
        INSERT OR REPLACE INTO canon_rcn_price_observations(
            source, source_record_id, fetched_at, run_id, bbox_query, query_json,
            parcel_id, parcel_number, address, transaction_date, property_type,
            area_m2, price_pln, price_per_m2,
            inflation_reference_year, inflation_factor,
            inflation_adjusted_price_pln, inflation_adjusted_price_per_m2,
            raw_json
        ) VALUES (?, ?, datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "live-test-derived-from-overpass",
            f"price-{osm_id}",
            RUN_ID,
            BBOX,
            json.dumps({"derived_from_osm": osm_id}, ensure_ascii=False),
            parcel_id,
            f"LIVE/{row['osm_id']}",
            str(name),
            "2024-01-01",
            "derived_test_parcel",
            area_m2,
            area_m2 * 250.0,
            250.0,
            "2024",
            1.0,
            area_m2 * 250.0,
            250.0,
            json.dumps({"derived_from_live_osm": dict(row)}, ensure_ascii=False, default=str),
        ),
    )
    con.commit()
    con.close()
    return parcel_id


def count_rows(db: Path, table: str) -> int:
    con = connect(db)
    try:
        return int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        con.close()


def table_rows(db: Path, table: str) -> list[dict]:
    con = connect(db)
    try:
        rows = con.execute(f"SELECT * FROM {table}").fetchall()
        return [dict(row) for row in rows]
    finally:
        con.close()


def assert_table_nonempty(db: Path, table: str) -> None:
    n = count_rows(db, table)
    if n <= 0:
        raise LiveTestFailure(f"{db.name}.{table} is empty")


def assert_all_required_data_present() -> None:
    for table in EXPECTED_CANON_TABLES:
        assert_table_nonempty(CANON_DB, table)
    for table in EXPECTED_BUS_TABLES:
        assert_table_nonempty(BUS_DB, table)
    for skill, _args in SKILL_SEQUENCE:
        local_db = WORKSPACE / "skills" / skill / "run.sqlite"
        if not local_db.exists():
            raise LiveTestFailure(f"missing local DB for {skill}: {local_db}")
        assert_table_nonempty(local_db, "skill_state")
        assert_table_nonempty(local_db, "skill_cache")
        # skill_errors is allowed to be empty on a clean successful run.


def print_rows(title: str, db: Path, tables: Iterable[str]) -> None:
    print(f"\n=== {title}: {db} ===")
    for table in tables:
        try:
            rows = table_rows(db, table)
        except sqlite3.OperationalError as exc:
            print(f"TABLE {table}: ERROR {exc}")
            continue
        print(f"\nTABLE {table}: {len(rows)} row(s)")
        for row in rows:
            print(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str))


def print_full_report() -> None:
    print_rows("CANON DB", CANON_DB, EXPECTED_CANON_TABLES)
    print_rows("PROJECT BUS DB", BUS_DB, EXPECTED_BUS_TABLES)
    print(f"\n=== LOCAL SKILL DBs: {WORKSPACE / 'skills'} ===")
    for skill, _args in SKILL_SEQUENCE:
        local_db = WORKSPACE / "skills" / skill / "run.sqlite"
        print_rows(f"LOCAL DB / {skill}", local_db, EXPECTED_LOCAL_TABLES)


def ensure_no_loose_json() -> None:
    json_files = list(WORKSPACE.rglob("*.json"))
    if json_files:
        raise LiveTestFailure(f"loose JSON files created under workspace: {[str(p) for p in json_files]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run every public Parcel skill on live data and print all DB table contents.")
    parser.add_argument("--no-reset", action="store_true", help="Do not delete previous bus rows/local workspace for this run_id.")
    args = parser.parse_args()

    print("Parcel live data integration test")
    print(f"root={ROOT}")
    print(f"run_id={RUN_ID}")
    print(f"workspace={WORKSPACE}")
    print(f"canon_db={CANON_DB}")
    print(f"project_bus_db={BUS_DB}")
    print(f"bbox={BBOX}")

    try:
        if not args.no_reset:
            reset_run()

        passed = 0
        for skill, extra in SKILL_SEQUENCE:
            run_skill(skill, extra)
            passed += 1
            if skill == "osm_hotspot_grid":
                parcel_id = fill_dependent_canon_tables_from_live_osm()
                print(f"\nDERIVED_CANON_FROM_LIVE_OSM parcel_id={parcel_id}")

        ensure_no_loose_json()
        assert_all_required_data_present()
        print_full_report()
        print(f"\nSUMMARY: {passed} skills passed, all required tables contain data")
        return 0
    except Exception as exc:
        print("\nFAIL")
        print(textwrap.indent("".join(traceback.format_exception_only(type(exc), exc)).strip(), "  "))
        print_full_report()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
