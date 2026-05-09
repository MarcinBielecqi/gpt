import importlib.util
import math
import sqlite3
from pathlib import Path

from PIL import Image


def load_visual_module():
    path = Path("skills/parcel-visual-features/scripts/compute_parcel_visual_features.py")
    spec = importlib.util.spec_from_file_location("compute_parcel_visual_features", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_layer2_module():
    path = Path("skills/uldk-parcel-grid/scripts/probe_uldk_parcels.py")
    spec = importlib.util.spec_from_file_location("probe_uldk_parcels", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def insert_rectangle(connection, parcel_id="P1", width_m=50.0, height_m=50.0, lon=16.0, lat=50.0):
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


def green_tile_fetcher(_template, _z, _x, _y, _timeout):
    return Image.new("RGB", (256, 256), (40, 150, 40))


def gray_tile_fetcher(_template, _z, _x, _y, _timeout):
    return Image.new("RGB", (256, 256), (90, 90, 90))


def test_visual_feature_table_upserts_without_area_column():
    visual = load_visual_module()
    layer2 = load_layer2_module()
    connection = sqlite3.connect(":memory:")
    layer2.ensure_layer2_tables(connection)
    insert_rectangle(connection, "P1")
    layer2.link_layer2_run_parcel(connection, "demo", "P1", 1, "16.0,50.0,16.1,50.1")

    ids = visual.parcel_ids(connection, "demo", False, None)
    summary = visual.compute_to_db(connection, ids, zoom=18, progress_every=0, tile_fetcher=green_tile_fetcher)
    summary_again = visual.compute_to_db(connection, ids, zoom=18, progress_every=0, tile_fetcher=gray_tile_fetcher)

    count, green_ratio, brightness = connection.execute(
        "SELECT COUNT(*), ROUND(green_pixel_ratio, 3), ROUND(brightness_mean, 1) FROM deriv_parcel_visual_features"
    ).fetchone()
    columns = {row[1] for row in connection.execute("PRAGMA table_info(deriv_parcel_visual_features)").fetchall()}

    assert ids == ["P1"]
    assert summary["computed"] == 1
    assert summary_again["computed"] == 1
    assert count == 1
    assert "area_m2" not in columns
    assert green_ratio == 0.0
    assert brightness == 90.0


def test_visual_feature_stats_are_polygon_masked():
    visual = load_visual_module()
    layer2 = load_layer2_module()
    connection = sqlite3.connect(":memory:")
    layer2.ensure_layer2_tables(connection)
    insert_rectangle(connection, "P1")

    polygons = visual.load_polygon_rings(connection, "P1")
    feature = visual.compute_features("P1", polygons, zoom=18, tile_template="synthetic://tile", timeout=1, tile_fetcher=green_tile_fetcher)

    assert feature["masked_pixel_count"] > 0
    assert feature["tile_count"] >= 1
    assert feature["green_pixel_ratio"] == 1.0
    assert feature["dark_pixel_ratio"] == 0.0
    assert math.isclose(feature["brightness_mean"], (40 + 150 + 40) / 3.0)
