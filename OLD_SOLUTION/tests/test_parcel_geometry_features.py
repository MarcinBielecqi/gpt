import importlib.util
import math
import sqlite3
from pathlib import Path


def load_geometry_module():
    path = Path("skills/parcel-geometry-features/scripts/compute_parcel_geometry_features.py")
    spec = importlib.util.spec_from_file_location("compute_parcel_geometry_features", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_layer2_module():
    path = Path("skills/uldk-parcel-grid/scripts/probe_uldk_parcels.py")
    spec = importlib.util.spec_from_file_location("probe_uldk_parcels", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def insert_rectangle(connection, parcel_id="P1", width_m=20.0, height_m=10.0, lon=16.0, lat=50.0):
    meters_per_lon = 111_320.0 * math.cos(math.radians(lat))
    dlon = width_m / meters_per_lon
    dlat = height_m / 111_320.0
    points = [
        (lon, lat),
        (lon + dlon, lat),
        (lon + dlon, lat + dlat),
        (lon, lat + dlat),
        (lon, lat),
    ]
    connection.execute(
        """
        INSERT INTO canon_parcels (
            parcel_id, parcel_number, bbox_min_lat, bbox_min_lon, bbox_max_lat, bbox_max_lon, geometry_hash
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (parcel_id, "1", lat, lon, lat + dlat, lon + dlon, "hash"),
    )
    for index, (point_lon, point_lat) in enumerate(points):
        connection.execute(
            """
            INSERT INTO canon_parcel_polygon_points (
                parcel_id, polygon_index, ring_index, point_index, lon, lat
            )
            VALUES (?, 0, 0, ?, ?, ?)
            """,
            (parcel_id, index, point_lon, point_lat),
        )
    connection.commit()


def test_geometry_features_match_rectangle_moments():
    geometry = load_geometry_module()
    layer2 = load_layer2_module()
    connection = sqlite3.connect(":memory:")
    layer2.ensure_layer2_tables(connection)
    insert_rectangle(connection, width_m=20.0, height_m=10.0)

    polygons = geometry.load_polygon_rings(connection, "P1")
    feature = geometry.compute_features("P1", polygons)

    assert math.isclose(feature["area_m2"], 200.0, rel_tol=0.02)
    assert math.isclose(feature["perimeter_m"], 60.0, rel_tol=0.02)
    assert math.isclose(feature["centroidal_ixx_m4"], 20.0 * 10.0**3 / 12.0, rel_tol=0.05)
    assert math.isclose(feature["centroidal_iyy_m4"], 10.0 * 20.0**3 / 12.0, rel_tol=0.05)
    assert feature["principal_moment_min_m4"] <= feature["principal_moment_max_m4"]
    assert feature["elongation_ratio"] > 1.0
    assert 0.0 < feature["compactness"] <= 1.0


def test_geometry_feature_table_upserts_from_run_id():
    geometry = load_geometry_module()
    layer2 = load_layer2_module()
    connection = sqlite3.connect(":memory:")
    layer2.ensure_layer2_tables(connection)
    insert_rectangle(connection, "P1")
    layer2.link_layer2_run_parcel(connection, "demo", "P1", 1, "16.0,50.0,16.1,50.1")

    ids = geometry.parcel_ids(connection, "demo", False, None)
    summary = geometry.compute_to_db(connection, ids, progress_every=0)
    summary_again = geometry.compute_to_db(connection, ids, progress_every=0)

    row = connection.execute("SELECT COUNT(*), ROUND(area_m2, 1) FROM deriv_parcel_geometry_features").fetchone()
    assert ids == ["P1"]
    assert summary["computed"] == 1
    assert summary_again["computed"] == 1
    assert row[0] == 1
    assert row[1] > 0
