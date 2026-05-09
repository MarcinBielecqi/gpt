import importlib.util
import sqlite3
from pathlib import Path


def load_module():
    path = Path("skills/osm_hotspot_grid/scripts/build_hotspot_grid.py")
    spec = importlib.util.spec_from_file_location("hotspot_grid", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample_elements(name="A", lat=50.01):
    return [
        {
            "type": "node",
            "id": 100,
            "lat": lat,
            "lon": 16.01,
            "tags": {"amenity": "restaurant", "name": name},
        }
    ]


def test_osm_feature_upsert_deduplicates_same_object():
    m = load_module()
    connection = sqlite3.connect(":memory:")
    m.ensure_layer1_tables(connection)

    m.upsert_canon_osm_features(connection, sample_elements(), "t1", "test", "50,16,51,17")
    m.upsert_canon_osm_features(connection, sample_elements(), "t2", "test", "50,16,51,17")

    count = connection.execute("SELECT COUNT(*) FROM canon_osm_features").fetchone()[0]
    assert count == 1


def test_osm_feature_refetch_updates_existing_row():
    m = load_module()
    connection = sqlite3.connect(":memory:")
    m.ensure_layer1_tables(connection)

    m.upsert_canon_osm_features(connection, sample_elements("Old", 50.01), "t1", "test", "50,16,51,17")
    m.upsert_canon_osm_features(connection, sample_elements("New", 50.02), "t2", "test", "50,16,51,17")

    row = connection.execute("SELECT fetched_at, tags_json, center_lat FROM canon_osm_features").fetchone()
    assert row[0] == "t2"
    assert '"New"' in row[1]
    assert row[2] == 50.02


def test_hotspot_mesh_generation_reads_from_canon_osm_features_not_raw_file(tmp_path):
    m = load_module()
    connection = sqlite3.connect(":memory:")
    m.ensure_layer1_tables(connection)
    elements = [
        {"type": "node", "id": idx, "lat": 50.0 + idx * 0.001, "lon": 16.0 + idx * 0.001, "tags": {"amenity": "restaurant"}}
        for idx in range(1, 5)
    ]
    m.upsert_canon_osm_features(connection, elements, "t1", "test", "50,16,51,17")
    (tmp_path / "raw_generated_file.json").write_text("[]", encoding="utf-8")

    points, cells, _, _ = m.build_mesh_cells_from_db(
        connection,
        (50.0, 16.0, 50.01, 16.01),
        [("amenity", "restaurant", "amenity_restaurant")],
        4,
        450.0,
        0.0,
    )

    assert len(points) == 4
    assert cells
    assert all("coords" in cell for cell in cells)


def test_hotspot_mesh_cells_can_be_regenerated_from_canon_osm_features():
    m = load_module()
    connection = sqlite3.connect(":memory:")
    m.ensure_layer1_tables(connection)
    elements = [
        {"type": "node", "id": idx, "lat": 50.0 + idx * 0.001, "lon": 16.0 + idx * 0.001, "tags": {"amenity": "restaurant"}}
        for idx in range(1, 5)
    ]
    m.upsert_canon_osm_features(connection, elements, "t1", "test", "50,16,51,17")
    _, cells, _, _ = m.build_mesh_cells_from_db(
        connection,
        (50.0, 16.0, 50.01, 16.01),
        [("amenity", "restaurant", "amenity_restaurant")],
        4,
        450.0,
        0.0,
    )

    first = m.write_mesh_cells_sqlite(connection, "run-a", "t1", "50,16,51,17", 4, 450.0, 0.0, cells)
    connection.execute("DELETE FROM helper_osm_hotspot_mesh_cells WHERE run_id = ?", ("run-a",))
    second = m.write_mesh_cells_sqlite(connection, "run-a", "t2", "50,16,51,17", 4, 450.0, 0.0, cells)

    stored = tuple(connection.execute("SELECT COUNT(*), COUNT(DISTINCT run_id) FROM helper_osm_hotspot_mesh_cells").fetchone())
    assert first == second
    assert stored == (second, 1)


def test_layer1_tables_do_not_require_parcel_tables():
    m = load_module()
    connection = sqlite3.connect(":memory:")
    m.ensure_layer1_tables(connection)

    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"canon_osm_features", "helper_osm_hotspot_mesh_cells"} <= tables
    assert "canon_parcels" not in tables
