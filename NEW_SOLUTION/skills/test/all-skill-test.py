#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import py_compile
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = ROOT / "skills"
DATA_DIR = ROOT / "data"
PYTHON = sys.executable

STANDARD_ARGS = [
    "--run-id",
    "--workspace",
    "--canon-db",
    "--profile",
    "--resume",
    "--dry-run",
    "--input-summary",
    "--input-artifact",
]

FORBIDDEN_PUBLIC_ARGS = [
    "--db-path",
    "--analysis-db-path",
    "--canon-db-path",
    "--summary-output",
    "--ai-summary",
    "--timeout-s",
    "--deadline-s",
    "--progress-every",
    "--retries",
    "--retry-sleep-s",
    "--page-size",
    "--max-pages",
    "--limit",
    "--quiet",
]

EXPECTED_ROOT_ENTRIES = {"data", "skills"}
EXPECTED_CANON_TABLES = {
    "canon_osm_features",
    "canon_parcels",
    "canon_parcel_polygon_points",
    "canon_rcn_price_observations",
}
EXPECTED_BUS_TABLES = {"bus_runs", "bus_skill_status", "bus_artifacts"}
EXPECTED_LOCAL_TABLES = {"skill_state", "skill_cache", "skill_errors"}
VALID_FINAL_STATUSES = {
    "ok", "partial", "empty", "skipped", "no_input", "invalid_input",
    "schema_error", "external_error", "timeout", "error",
}

DOMAIN_FIXTURES = {
    "candidate-ranking": ["--ranking-profile", "default"],
    "osm-overpass-fetch": ["--bbox", "16.20,50.70,16.35,50.85", "--key", "amenity", "--value", "school"],
    "osm_hotspot_grid": ["--bbox", "16.20,50.70,16.35,50.85", "--types", "amenity=school"],
    "parcel-geometry-features": [],
    "parcel-visual-features": ["--source-artifact", "geometry_features:default"],
    "poland-rcn-wfs": ["--bbox-4326", "16.20,50.70,16.35,50.85"],
    "polish-parcel-wfs": ["--bbox-4326", "16.20,50.70,16.35,50.85"],
    "result-presentation": ["--view", "summary"],
    "schema-validation": ["--stage", "base"],
    "uldk-parcel-grid": ["--bbox-4326", "16.20,50.70,16.35,50.85"],
}

BBOX_SKILLS = {
    "osm-overpass-fetch": ["--key", "amenity", "--value", "school"],
    "osm_hotspot_grid": ["--types", "amenity=school"],
    "polish-parcel-wfs": [],
    "uldk-parcel-grid": [],
}

NON_NETWORK_SMOKE_ORDER = [
    "schema-validation",
    "osm_hotspot_grid",
    "uldk-parcel-grid",
    "parcel-geometry-features",
    "parcel-visual-features",
    "poland-rcn-wfs",
    "candidate-ranking",
    "result-presentation",
]


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


class TestFailure(Exception):
    pass


def skill_dirs() -> list[Path]:
    items = []
    for path in sorted(SKILLS_DIR.iterdir()):
        if not path.is_dir() or path.name in {"shared", "test", "__pycache__"}:
            continue
        if (path / "scripts" / "run.py").exists():
            items.append(path)
    return items


def run_cmd(args: list[str], *, timeout: int = 20, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    cmd = list(args)
    # Use -S for child Python processes so local contract tests are not slowed or polluted
    # by user/global sitecustomize hooks. The package is self-contained and uses stdlib only.
    if cmd and Path(cmd[0]).name.startswith("python") and (len(cmd) == 1 or cmd[1] != "-S"):
        cmd.insert(1, "-S")
    return subprocess.run(cmd, cwd=str(cwd), env=env, text=True, capture_output=True, timeout=timeout)


def parse_single_stdout_json(stdout: str) -> dict:
    lines = [line for line in stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise TestFailure(f"stdout must contain exactly one JSON line, got {len(lines)} lines: {lines[:3]!r}")
    try:
        payload = json.loads(lines[0])
    except Exception as exc:
        raise TestFailure(f"stdout is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise TestFailure("stdout JSON must be an object")
    if payload.get("status") not in VALID_FINAL_STATUSES:
        raise TestFailure(f"invalid final status: {payload.get('status')!r}")
    return payload


def validate_stderr_protocol(stderr: str, *, allow_error: bool = False) -> None:
    for raw in stderr.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("PROGRESS "):
            body = line[len("PROGRESS "):]
        elif allow_error and line.startswith("ERROR "):
            body = line[len("ERROR "):]
        else:
            raise TestFailure(f"stderr line does not follow protocol: {line!r}")
        try:
            payload = json.loads(body)
        except Exception as exc:
            raise TestFailure(f"stderr protocol payload is not valid JSON: {line!r}") from exc
        if not isinstance(payload, dict):
            raise TestFailure(f"stderr protocol payload is not an object: {line!r}")


def sqlite_tables(path: Path) -> set[str]:
    con = sqlite3.connect(path)
    rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    con.close()
    return {row[0] for row in rows}


def sqlite_column_type(path: Path, table: str, column: str) -> str | None:
    con = sqlite3.connect(path)
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    con.close()
    for row in rows:
        if row[1] == column:
            return str(row[2]).upper()
    return None


def check(condition: bool, message: str) -> None:
    if not condition:
        raise TestFailure(message)


def test_root_shape() -> None:
    entries = {p.name for p in ROOT.iterdir() if p.name not in {"__pycache__"}}
    unexpected = sorted(entries - EXPECTED_ROOT_ENTRIES)
    missing = sorted(EXPECTED_ROOT_ENTRIES - entries)
    check(not unexpected, f"unexpected root entries: {unexpected}")
    check(not missing, f"missing root entries: {missing}")


def test_data_databases_and_schema_files() -> None:
    check((DATA_DIR / "canon.sqlite").exists(), "missing data/canon.sqlite")
    check((DATA_DIR / "project_bus.sqlite").exists(), "missing data/project_bus.sqlite")
    check((DATA_DIR / "schema" / "canon.sql").exists(), "missing data/schema/canon.sql")
    check((DATA_DIR / "schema" / "project_bus.sql").exists(), "missing data/schema/project_bus.sql")
    check(EXPECTED_CANON_TABLES <= sqlite_tables(DATA_DIR / "canon.sqlite"), "canon.sqlite missing required tables")
    check(EXPECTED_BUS_TABLES <= sqlite_tables(DATA_DIR / "project_bus.sqlite"), "project_bus.sqlite missing required tables")
    check(sqlite_column_type(DATA_DIR / "project_bus.sqlite", "bus_artifacts", "payload_json") == "TEXT", "bus_artifacts.payload_json must be TEXT")


def test_shared_imports() -> None:
    proc = run_cmd([PYTHON, "-c", "import skills.shared.ai_protocol, skills.shared.bus, skills.shared.skill_context, skills.shared.profiles, skills.shared.db"], timeout=10)
    check(proc.returncode == 0, proc.stderr or proc.stdout)


def test_python_compiles() -> None:
    py_files = sorted(SKILLS_DIR.rglob("*.py"))
    check(py_files, "no Python files found")
    for path in py_files:
        py_compile.compile(str(path), doraise=True)


def test_skill_layout() -> None:
    dirs = skill_dirs()
    check(dirs, "no public skill directories found")
    for skill_dir in dirs:
        for rel in ["SKILL.md", "skill.yaml", "schema.sql", "scripts/run.py", "src/core.py"]:
            check((skill_dir / rel).exists(), f"{skill_dir.name}: missing {rel}")
        schema = (skill_dir / "schema.sql").read_text(encoding="utf-8")
        for table in EXPECTED_LOCAL_TABLES:
            check(table in schema, f"{skill_dir.name}: schema.sql missing {table}")
        md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        check("project_bus.sqlite" in md, f"{skill_dir.name}: SKILL.md must mention project_bus.sqlite")
        check("canon.sqlite" in md, f"{skill_dir.name}: SKILL.md must mention canon.sqlite")


def test_skill_yaml_contract() -> None:
    for skill_dir in skill_dirs():
        text = (skill_dir / "skill.yaml").read_text(encoding="utf-8")
        check(f"name: {skill_dir.name}" in text, f"{skill_dir.name}: skill.yaml name mismatch")
        for field in ["profiles:", "public_args:", "artifacts:"]:
            check(field in text, f"{skill_dir.name}: skill.yaml missing {field}")
        for arg in [a[2:] for a in STANDARD_ARGS]:
            check(arg in text, f"{skill_dir.name}: skill.yaml missing public arg {arg}")


def test_help_contract() -> None:
    for skill_dir in skill_dirs():
        proc = run_cmd([PYTHON, str(skill_dir / "scripts" / "run.py"), "--help"], timeout=10)
        check(proc.returncode == 0, f"{skill_dir.name}: --help failed: {proc.stderr}")
        for arg in STANDARD_ARGS:
            check(arg in proc.stdout, f"{skill_dir.name}: --help missing {arg}")
        for arg in FORBIDDEN_PUBLIC_ARGS:
            check(arg not in proc.stdout, f"{skill_dir.name}: forbidden public arg present: {arg}")


def run_skill_dry(skill_dir: Path, temp_root: Path) -> dict:
    skill = skill_dir.name
    args = [
        PYTHON, str(skill_dir / "scripts" / "run.py"),
        "--run-id", "contract_test",
        "--workspace", str(temp_root / "runs" / "contract_test"),
        "--canon-db", str(temp_root / "data" / "canon.sqlite"),
        "--profile", "quick",
        "--dry-run",
    ] + DOMAIN_FIXTURES.get(skill, [])
    proc = run_cmd(args, timeout=20)
    check(proc.returncode == 0, f"{skill}: dry-run failed with {proc.returncode}\nSTDOUT={proc.stdout}\nSTDERR={proc.stderr}")
    payload = parse_single_stdout_json(proc.stdout)
    validate_stderr_protocol(proc.stderr)
    check(payload.get("skill") == skill, f"{skill}: stdout skill mismatch: {payload.get('skill')}")
    check(payload.get("run_id") == "contract_test", f"{skill}: stdout run_id mismatch")
    return payload


def test_dry_run_contract_all_skills() -> None:
    with tempfile.TemporaryDirectory(prefix="parcel_skill_contract_") as raw:
        temp_root = Path(raw)
        for skill_dir in skill_dirs():
            payload = run_skill_dry(skill_dir, temp_root)
            check("artifacts" in payload and isinstance(payload["artifacts"], list), f"{skill_dir.name}: missing artifacts list")
            local_db = temp_root / "runs" / "contract_test" / "skills" / skill_dir.name / "run.sqlite"
            check(local_db.exists(), f"{skill_dir.name}: missing local run.sqlite")
            check(EXPECTED_LOCAL_TABLES <= sqlite_tables(local_db), f"{skill_dir.name}: local DB missing required tables")
        json_files = list(temp_root.rglob("*.json"))
        check(not json_files, f"loose JSON files created during dry-run: {[str(p) for p in json_files[:10]]}")


def test_invalid_bbox_protocol() -> None:
    with tempfile.TemporaryDirectory(prefix="parcel_skill_invalid_") as raw:
        temp_root = Path(raw)
        for skill, extras in BBOX_SKILLS.items():
            skill_dir = SKILLS_DIR / skill
            bbox_arg = "--bbox" if skill in {"osm-overpass-fetch", "osm_hotspot_grid"} else "--bbox-4326"
            args = [
                PYTHON, str(skill_dir / "scripts" / "run.py"),
                "--run-id", "invalid_bbox_test",
                "--workspace", str(temp_root / "runs" / "invalid_bbox_test"),
                "--canon-db", str(temp_root / "data" / "canon.sqlite"),
                "--profile", "quick",
                "--dry-run",
                bbox_arg, "17,51,16,50",
            ] + extras
            proc = run_cmd(args, timeout=20)
            check(proc.returncode != 0, f"{skill}: invalid bbox should fail")
            payload = parse_single_stdout_json(proc.stdout)
            validate_stderr_protocol(proc.stderr, allow_error=True)
            check(payload.get("status") == "invalid_input", f"{skill}: expected invalid_input, got {payload.get('status')}")
            check(payload.get("code") == "INVALID_BBOX", f"{skill}: expected INVALID_BBOX, got {payload.get('code')}")


def seed_canon_db(canon_db: Path) -> None:
    con = sqlite3.connect(canon_db)
    con.execute(
        """
        INSERT OR REPLACE INTO canon_osm_features(
            osm_type, osm_id, fetched_at, source, bbox_query, tags_json, geometry_json,
            center_lat, center_lon, bbox_min_lat, bbox_min_lon, bbox_max_lat, bbox_max_lon,
            geometry_hash, raw_json
        ) VALUES (?, ?, datetime('now'), ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, NULL, ?)
        """,
        (
            "node", 1, "test", "16.20,50.70,16.35,50.85",
            json.dumps({"amenity": "school"}), 50.75, 16.25,
            50.75, 16.25, 50.75, 16.25,
            json.dumps({"type": "node", "id": 1}),
        ),
    )
    con.execute(
        """
        INSERT OR REPLACE INTO canon_parcels(
            parcel_id, parcel_number, voivodeship, county, commune, precinct, area_m2,
            centroid_lat, centroid_lon, bbox_min_lat, bbox_min_lon, bbox_max_lat, bbox_max_lon,
            geometry_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "parcel-1", "1/1", "dolnoslaskie", "test", "Test Commune", "test", 1200.0,
            50.75, 16.25, 50.749, 16.249, 50.751, 16.251, "geom-1",
        ),
    )
    points = [(16.249, 50.749), (16.251, 50.749), (16.251, 50.751), (16.249, 50.751)]
    for idx, (lon, lat) in enumerate(points):
        con.execute(
            """
            INSERT OR REPLACE INTO canon_parcel_polygon_points(parcel_id, polygon_index, ring_index, point_index, lon, lat)
            VALUES (?, 0, 0, ?, ?, ?)
            """,
            ("parcel-1", idx, lon, lat),
        )
    con.execute(
        """
        INSERT OR REPLACE INTO canon_rcn_price_observations(
            source, source_record_id, fetched_at, run_id, bbox_query, query_json, parcel_id,
            transaction_date, area_m2, price_pln, price_per_m2, inflation_adjusted_price_per_m2, raw_json
        ) VALUES (?, ?, datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "test", "rcn-1", "pipeline_smoke", "16.20,50.70,16.35,50.85", "{}", "parcel-1",
            "2024-01-01", 1200.0, 240000.0, 200.0, 220.0, "{}",
        ),
    )
    con.commit()
    con.close()


def run_skill_once(skill: str, temp_root: Path, *, extra_args: list[str] | None = None, expect_returncode: int = 0) -> dict:
    skill_dir = SKILLS_DIR / skill
    args = [
        PYTHON, str(skill_dir / "scripts" / "run.py"),
        "--run-id", "pipeline_smoke",
        "--workspace", str(temp_root / "runs" / "pipeline_smoke"),
        "--canon-db", str(temp_root / "data" / "canon.sqlite"),
        "--profile", "quick",
    ] + (extra_args if extra_args is not None else DOMAIN_FIXTURES.get(skill, []))
    proc = run_cmd(args, timeout=20)
    check(proc.returncode == expect_returncode, f"{skill}: expected rc {expect_returncode}, got {proc.returncode}\nSTDOUT={proc.stdout}\nSTDERR={proc.stderr}")
    payload = parse_single_stdout_json(proc.stdout)
    validate_stderr_protocol(proc.stderr)
    check(payload.get("skill") == skill, f"{skill}: stdout skill mismatch")
    return payload


def artifact_count(bus_db: Path, run_id: str, artifact_type: str) -> int:
    con = sqlite3.connect(bus_db)
    value = con.execute(
        "SELECT COUNT(*) FROM bus_artifacts WHERE run_id = ? AND artifact_type = ?",
        (run_id, artifact_type),
    ).fetchone()[0]
    con.close()
    return int(value)


def test_non_network_pipeline_smoke() -> None:
    with tempfile.TemporaryDirectory(prefix="parcel_pipeline_smoke_") as raw:
        temp_root = Path(raw)
        run_skill_once("schema-validation", temp_root, extra_args=["--stage", "base"])
        seed_canon_db(temp_root / "data" / "canon.sqlite")

        run_skill_once("osm_hotspot_grid", temp_root, extra_args=["--bbox", "16.20,50.70,16.35,50.85", "--types", "amenity=school"])
        run_skill_once("uldk-parcel-grid", temp_root, extra_args=["--input-artifact", "hotspot_candidates:default", "--expected-commune", "Test Commune"])
        run_skill_once("parcel-geometry-features", temp_root, extra_args=["--input-artifact", "parcel_candidates:default"])
        visual = run_skill_once("parcel-visual-features", temp_root, extra_args=["--source-artifact", "geometry_features:default"])
        check(visual.get("status") == "skipped", "parcel-visual-features should currently report skipped")
        run_skill_once("poland-rcn-wfs", temp_root, extra_args=["--input-artifact", "parcel_candidates:default"])
        run_skill_once("candidate-ranking", temp_root, extra_args=["--ranking-profile", "default"])
        run_skill_once("result-presentation", temp_root, extra_args=["--view", "summary"])

        bus_db = temp_root / "data" / "project_bus.sqlite"
        for artifact_type in [
            "validation_result",
            "hotspot_candidates",
            "parcel_candidates",
            "geometry_features",
            "visual_features",
            "rcn_summary",
            "ranked_candidates",
            "report_metadata",
            "skill_summary",
        ]:
            check(artifact_count(bus_db, "pipeline_smoke", artifact_type) > 0, f"missing bus artifact: {artifact_type}")
        json_files = list(temp_root.rglob("*.json"))
        check(not json_files, f"loose JSON files created during smoke: {[str(p) for p in json_files[:10]]}")


def test_bus_payload_json_is_valid_string() -> None:
    with tempfile.TemporaryDirectory(prefix="parcel_bus_roundtrip_") as raw:
        temp_root = Path(raw)
        run_skill_once("schema-validation", temp_root, extra_args=["--stage", "base"])
        bus_db = temp_root / "data" / "project_bus.sqlite"
        con = sqlite3.connect(bus_db)
        rows = con.execute("SELECT payload_json FROM bus_artifacts").fetchall()
        con.close()
        check(rows, "no bus artifacts found after schema-validation")
        for (payload_json,) in rows:
            check(isinstance(payload_json, str), "payload_json is not a Python string")
            json.loads(payload_json)


TESTS: list[tuple[str, Callable[[], None]]] = [
    ("root contains only data/ and skills/", test_root_shape),
    ("data databases and schemas", test_data_databases_and_schema_files),
    ("shared modules import", test_shared_imports),
    ("all Python files compile", test_python_compiles),
    ("skill directory layout", test_skill_layout),
    ("skill.yaml contract", test_skill_yaml_contract),
    ("CLI help contract", test_help_contract),
    ("dry-run contract for every skill", test_dry_run_contract_all_skills),
    ("invalid bbox error protocol", test_invalid_bbox_protocol),
    ("non-network pipeline smoke", test_non_network_pipeline_smoke),
    ("bus payload_json is valid JSON string", test_bus_payload_json_is_valid_string),
]


def main() -> int:
    print("Parcel skill contract test")
    print(f"root: {ROOT}")
    print(f"skills: {len(skill_dirs())}")
    print("")

    checks: list[Check] = []
    for name, func in TESTS:
        try:
            func()
            checks.append(Check(name=name, ok=True))
            print(f"PASS  {name}")
        except Exception as exc:
            detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            checks.append(Check(name=name, ok=False, detail=detail))
            print(f"FAIL  {name}")
            print(textwrap.indent(detail, "      "))

    passed = sum(1 for item in checks if item.ok)
    failed = len(checks) - passed
    print("")
    print(f"SUMMARY: {passed} passed, {failed} failed, {len(checks)} total")
    if failed:
        print("")
        print("FAILED CHECKS:")
        for item in checks:
            if not item.ok:
                print(f"- {item.name}: {item.detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
