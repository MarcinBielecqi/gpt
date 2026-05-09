import importlib.util
import io
import sqlite3
from pathlib import Path


def load_layer2_module():
    path = Path("skills/uldk-parcel-grid/scripts/probe_uldk_parcels.py")
    spec = importlib.util.spec_from_file_location("probe_uldk_parcels", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_layer2_runner_module():
    path = Path("skills/uldk-parcel-grid/scripts/run_uldk_from_candidates.py")
    spec = importlib.util.spec_from_file_location("run_uldk_from_candidates", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_hotspot_module():
    path = Path("skills/osm_hotspot_grid/scripts/build_hotspot_grid.py")
    spec = importlib.util.spec_from_file_location("hotspot_grid", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_layer2_tables_can_exist_without_area_tiles():
    m = load_layer2_module()
    connection = sqlite3.connect(":memory:")
    m.ensure_layer2_tables(connection)

    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"canon_parcels", "canon_parcel_polygon_points", "helper_layer2_run_parcels"} <= tables
    assert "area_tiles" not in tables


def test_layer2_parcel_upsert_deduplicates_and_replaces_points():
    m = load_layer2_module()
    connection = sqlite3.connect(":memory:")
    m.ensure_layer2_tables(connection)
    first = m.parse_uldk_response(
        "0\n"
        "021302_5.0001.1|1|Gmina Test|Powiat Test|Dolnoslaskie|src|"
        "POLYGON((16.0 50.0,16.01 50.0,16.01 50.01,16.0 50.0))"
    )
    second = m.parse_uldk_response(
        "0\n"
        "021302_5.0001.1|2|Gmina Test|Powiat Test|Dolnoslaskie|src|"
        "POLYGON((16.0 50.0,16.02 50.0,16.02 50.02,16.0 50.0))"
    )

    m.upsert_parcel(connection, first)
    m.upsert_parcel(connection, second)

    parcel_count = connection.execute("SELECT COUNT(*) FROM canon_parcels").fetchone()[0]
    point_count = connection.execute("SELECT COUNT(*) FROM canon_parcel_polygon_points").fetchone()[0]
    row = connection.execute("SELECT parcel_number, bbox_max_lon FROM canon_parcels").fetchone()
    assert parcel_count == 1
    assert point_count == 4
    assert row == ("2", 16.02)


def test_layer2_parcel_upsert_removes_stale_points():
    m = load_layer2_module()
    connection = sqlite3.connect(":memory:")
    m.ensure_layer2_tables(connection)
    first = m.parse_uldk_response(
        "0\n"
        "021302_5.0001.1|1|Gmina Test|Powiat Test|Dolnoslaskie|src|"
        "POLYGON((16.0 50.0,16.01 50.0,16.01 50.01,16.0 50.01,16.0 50.0))"
    )
    second = m.parse_uldk_response(
        "0\n"
        "021302_5.0001.1|1|Gmina Test|Powiat Test|Dolnoslaskie|src|"
        "POLYGON((16.0 50.0,16.01 50.0,16.0 50.0))"
    )

    m.upsert_parcel(connection, first)
    m.upsert_parcel(connection, second)

    point_count = connection.execute("SELECT COUNT(*) FROM canon_parcel_polygon_points").fetchone()[0]
    assert point_count == 3


def test_layer2_probe_skips_points_inside_existing_parcel(monkeypatch):
    m = load_layer2_module()
    connection = sqlite3.connect(":memory:")
    m.ensure_layer2_tables(connection)
    parcel = m.parse_uldk_response(
        "0\n"
        "021302_5.0001.1|1|Gmina Test|Powiat Test|Dolnoslaskie|src|"
        "POLYGON((16.0 50.0,16.1 50.0,16.1 50.1,16.0 50.1,16.0 50.0))"
    )
    m.upsert_parcel(connection, parcel)

    def fail_fetch(lon, lat):
        raise AssertionError("ULDK should not be called for a point already inside a known parcel")

    monkeypatch.setattr(m, "fetch_uldk_parcel", fail_fetch)
    summary = m.run_probe(connection, (16.01, 50.01, 16.02, 50.02), 1000, 10, False)

    assert summary["requests"] == 0
    assert summary["skipped_existing"] >= 1
    assert summary["found_parcel_ids"] == ["021302_5.0001.1"]


def test_layer2_probe_rejects_large_existing_parcel_and_skips_polygon(monkeypatch):
    m = load_layer2_module()
    connection = sqlite3.connect(":memory:")
    m.ensure_layer2_tables(connection)
    parcel = m.parse_uldk_response(
        "0\n"
        "021302_5.0001.1|1|Gmina Test|Powiat Test|Dolnoslaskie|src|"
        "POLYGON((16.0 50.0,16.2 50.0,16.2 50.2,16.0 50.2,16.0 50.0))"
    )
    m.upsert_parcel(connection, parcel)

    def fail_fetch(lon, lat):
        raise AssertionError("Rejected known polygon should prevent repeated ULDK calls")

    monkeypatch.setattr(m, "fetch_uldk_parcel", fail_fetch)
    summary = m.run_probe(
        connection,
        (16.01, 50.01, 16.19, 50.19),
        1000,
        10,
        False,
        max_parcel_area_m2=12_000,
    )

    assert summary["requests"] == 0
    assert summary["rejected"] >= 1
    assert summary["rejected_reasons"]["too_large"] >= 1
    assert summary["skipped_rejected_polygon"] >= 1
    assert summary["found_parcel_ids"] == []


def test_layer2_probe_uses_uldk_and_persists_new_parcel(monkeypatch):
    m = load_layer2_module()
    connection = sqlite3.connect(":memory:")

    def fake_fetch(lon, lat):
        return m.parse_uldk_response(
            "0\n"
            "021302_5.0001.7|7|Gmina Test|Powiat Test|Dolnoslaskie|src|"
            "POLYGON((16.0 50.0,16.01 50.0,16.01 50.01,16.0 50.0))"
        )

    monkeypatch.setattr(m, "fetch_uldk_parcel", fake_fetch)
    summary = m.run_probe(connection, (16.0, 50.0, 16.001, 50.001), 1000, 1, False)

    assert summary["requests"] == 1
    assert summary["inserted"] == 1
    assert connection.execute("SELECT COUNT(*) FROM canon_parcels").fetchone()[0] == 1


def test_layer2_parses_uldk_empty_response_as_empty():
    m = load_layer2_module()

    assert m.parse_uldk_response("-1 brak wynikÃ³w\nbÅ‚Ä™dny format odpowiedzi XML") is None


def test_layer2_expected_commune_filters_out_of_scope(monkeypatch):
    m = load_layer2_module()
    connection = sqlite3.connect(":memory:")

    def fake_fetch(lon, lat):
        return m.parse_uldk_response(
            "0\n"
            "021302_5.0001.7|7|Walim|Powiat Test|Dolnoslaskie|src|"
            "POLYGON((16.0 50.0,16.01 50.0,16.01 50.01,16.0 50.0))"
        )

    monkeypatch.setattr(m, "fetch_uldk_parcel", fake_fetch)
    summary = m.run_probe(connection, (16.0, 50.0, 16.001, 50.001), 1000, 1, False, "Gluszyca")

    assert summary["requests"] == 1
    assert summary["inserted"] == 0
    assert summary["out_of_scope"] == 1
    assert connection.execute("SELECT COUNT(*) FROM canon_parcels").fetchone()[0] == 0


def test_layer2_probe_emits_compact_progress(monkeypatch):
    m = load_layer2_module()
    connection = sqlite3.connect(":memory:")
    progress = io.StringIO()

    def fake_fetch(lon, lat):
        return None

    monkeypatch.setattr(m, "fetch_uldk_parcel", fake_fetch)
    summary = m.run_probe(
        connection,
        (16.0, 50.0, 16.001, 50.001),
        1000,
        1,
        False,
        progress_label="demo",
        progress_every=1,
        progress_stream=progress,
    )

    lines = [line for line in progress.getvalue().splitlines() if line.startswith("PROGRESS ")]
    assert summary["requests"] == 1
    assert summary["grid_points"] >= 1
    assert any('"event": "start"' in line for line in lines)
    assert any('"event": "done"' in line for line in lines)


def test_layer2_runner_does_not_filter_commune_by_default(monkeypatch):
    hotspot = load_hotspot_module()
    runner = load_layer2_runner_module()
    connection = sqlite3.connect(":memory:")
    hotspot.ensure_layer1_tables(connection)
    hotspot.write_mesh_cells_sqlite(
        connection,
        "demo",
        "2026-01-01T00:00:00+00:00",
        "50.0,16.0,50.1,16.1",
        2,
        450.0,
        0.0,
        [
            {
                "category": "tourism_viewpoint",
                "tag_key": "tourism",
                "tag_value": "viewpoint",
                "coords": [(50.0, 16.0), (50.01, 16.0), (50.01, 16.01)],
                "center": {"lat": 50.0066, "lon": 16.0033},
                "score": 10.0,
                "norm": 1.0,
                "source_point_count": 1,
            },
            {
                "category": "tourism_viewpoint",
                "tag_key": "tourism",
                "tag_value": "viewpoint",
                "coords": [(50.0, 16.02), (50.01, 16.02), (50.01, 16.03)],
                "center": {"lat": 50.0066, "lon": 16.0233},
                "score": 9.0,
                "norm": 0.9,
                "source_point_count": 1,
            },
        ],
    )
    calls = {"count": 0}

    def fake_fetch(lon, lat):
        calls["count"] += 1
        commune = "Other" if calls["count"] == 1 else "Gmina Test"
        return runner.l2.parse_uldk_response(
            "0\n"
            f"021302_5.0001.{calls['count']}|{calls['count']}|{commune}|Powiat Test|Dolnoslaskie|src|"
            "POLYGON((16.0 50.0,16.01 50.0,16.01 50.01,16.0 50.0))"
        )

    monkeypatch.setattr(runner.l2, "fetch_uldk_parcel", fake_fetch)
    summary = runner.run_layer2(connection, "demo", "Gmina Test", 2, 1000, 1, stop_after_first_hit=True)

    assert summary["tested_candidates"] == 1
    assert summary["linked_parcels"] == 1
    assert summary["out_of_scope_candidates"] == 0
    assert summary["strict_commune"] is False
    assert connection.execute("SELECT COUNT(*) FROM helper_layer2_run_parcels WHERE run_id = 'demo'").fetchone()[0] == 1
    row = connection.execute("SELECT expected_commune FROM helper_layer2_run_parcels WHERE run_id = 'demo'").fetchone()
    assert row["expected_commune"] == "Gmina Test"


def test_layer2_runner_strict_commune_filters_when_requested(monkeypatch):
    hotspot = load_hotspot_module()
    runner = load_layer2_runner_module()
    connection = sqlite3.connect(":memory:")
    hotspot.ensure_layer1_tables(connection)
    hotspot.write_mesh_cells_sqlite(
        connection,
        "demo",
        "2026-01-01T00:00:00+00:00",
        "50.0,16.0,50.1,16.1",
        2,
        450.0,
        0.0,
        [
            {
                "category": "tourism_viewpoint",
                "tag_key": "tourism",
                "tag_value": "viewpoint",
                "coords": [(50.0, 16.0), (50.01, 16.0), (50.01, 16.01)],
                "center": {"lat": 50.0066, "lon": 16.0033},
                "score": 10.0,
                "norm": 1.0,
                "source_point_count": 1,
            },
            {
                "category": "tourism_viewpoint",
                "tag_key": "tourism",
                "tag_value": "viewpoint",
                "coords": [(50.0, 16.02), (50.01, 16.02), (50.01, 16.03)],
                "center": {"lat": 50.0066, "lon": 16.0233},
                "score": 9.0,
                "norm": 0.9,
                "source_point_count": 1,
            },
        ],
    )
    calls = {"count": 0}

    def fake_fetch(lon, lat):
        calls["count"] += 1
        commune = "Other" if calls["count"] == 1 else "Gmina Test"
        return runner.l2.parse_uldk_response(
            "0\n"
            f"021302_5.0001.{calls['count']}|{calls['count']}|{commune}|Powiat Test|Dolnoslaskie|src|"
            "POLYGON((16.0 50.0,16.01 50.0,16.01 50.01,16.0 50.0))"
        )

    monkeypatch.setattr(runner.l2, "fetch_uldk_parcel", fake_fetch)
    summary = runner.run_layer2(connection, "demo", "Gmina Test", 2, 1000, 1, strict_commune=True, stop_after_first_hit=True)

    assert summary["tested_candidates"] == 2
    assert summary["linked_parcels"] == 1
    assert summary["out_of_scope_candidates"] == 1
    assert summary["strict_commune"] is True
    assert summary["filtered_commune"] == "Gmina Test"
    assert connection.execute("SELECT COUNT(*) FROM helper_layer2_run_parcels WHERE run_id = 'demo'").fetchone()[0] == 1


def test_layer2_runner_harvests_multiple_candidates_by_default(monkeypatch):
    hotspot = load_hotspot_module()
    runner = load_layer2_runner_module()
    connection = sqlite3.connect(":memory:")
    hotspot.ensure_layer1_tables(connection)
    hotspot.write_mesh_cells_sqlite(
        connection,
        "demo",
        "2026-01-01T00:00:00+00:00",
        "50.0,16.0,50.1,16.1",
        2,
        450.0,
        0.0,
        [
            {
                "category": "tourism_viewpoint",
                "tag_key": "tourism",
                "tag_value": "viewpoint",
                "coords": [(50.0, 16.0), (50.01, 16.0), (50.01, 16.01)],
                "center": {"lat": 50.0066, "lon": 16.0033},
                "score": 10.0,
                "norm": 1.0,
                "source_point_count": 1,
            },
            {
                "category": "tourism_viewpoint",
                "tag_key": "tourism",
                "tag_value": "viewpoint",
                "coords": [(50.0, 16.02), (50.01, 16.02), (50.01, 16.03)],
                "center": {"lat": 50.0066, "lon": 16.0233},
                "score": 9.0,
                "norm": 0.9,
                "source_point_count": 1,
            },
        ],
    )
    calls = {"count": 0}

    def fake_fetch(lon, lat):
        calls["count"] += 1
        offset = calls["count"] / 100.0
        return runner.l2.parse_uldk_response(
            "0\n"
            f"021302_5.0001.{calls['count']}|{calls['count']}|Gmina Test|Powiat Test|Dolnoslaskie|src|"
            f"POLYGON(({16.0 + offset} 50.0,{16.005 + offset} 50.0,{16.005 + offset} 50.005,{16.0 + offset} 50.0))"
        )

    monkeypatch.setattr(runner.l2, "fetch_uldk_parcel", fake_fetch)
    summary = runner.run_layer2(connection, "demo", None, 2, 1000, 1)

    assert summary["tested_candidates"] == 2
    assert summary["linked_parcels"] == 2
    assert connection.execute("SELECT COUNT(*) FROM helper_layer2_run_parcels WHERE run_id = 'demo'").fetchone()[0] == 2
