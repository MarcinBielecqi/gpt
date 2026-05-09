import importlib.util
import json
import sqlite3
from pathlib import Path


def load_hotspot_module():
    path = Path("skills/osm_hotspot_grid/scripts/build_hotspot_grid.py")
    spec = importlib.util.spec_from_file_location("hotspot_grid", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_renderer_module():
    path = Path("skills/result-presentation/scripts/render_analysis_map.py")
    spec = importlib.util.spec_from_file_location("render_layer1_hotspot_map", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_layer2_module():
    path = Path("skills/uldk-parcel-grid/scripts/probe_uldk_parcels.py")
    spec = importlib.util.spec_from_file_location("probe_uldk_parcels", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_layer1_renderer_builds_html_from_sqlite(tmp_path):
    hotspot = load_hotspot_module()
    renderer = load_renderer_module()
    db_path = tmp_path / "layer1.sqlite"
    connection = sqlite3.connect(db_path)
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
                "category": "amenity_restaurant",
                "tag_key": "amenity",
                "tag_value": "restaurant",
                "coords": [(50.0, 16.0), (50.01, 16.0), (50.01, 16.01)],
                "center": {"lat": 50.0066, "lon": 16.0033},
                "score": 10.0,
                "norm": 1.0,
                "source_point_count": 3,
            }
        ],
    )
    hotspot.upsert_canon_osm_features(
        connection,
        [
            {
                "type": "node",
                "id": 1,
                "lat": 50.005,
                "lon": 16.005,
                "tags": {"amenity": "restaurant", "name": "Test place"},
            }
        ],
        "2026-01-01T00:00:00+00:00",
        "test",
        "50.0,16.0,50.1,16.1",
    )
    rows = renderer.candidate_rows(connection, "demo", 10)
    points = renderer.point_rows(connection, rows)
    connection.close()

    html = renderer.build_html("demo", [renderer.feature_from_row(row) for row in rows], points)

    assert "Analysis layers" in html
    payload = renderer.build_payload("demo", [renderer.feature_from_row(row) for row in rows], points)

    assert "analysis_data.js" in html
    assert "Visible layers" in html
    assert "Map type" in html
    assert "satelliteLayer" in html
    assert "parcel-toggle" in html
    assert payload["analysis_id"] == "demo"
    assert payload["categories"] == ["amenity_restaurant"]
    assert payload["points"][0]["name"] == "Test place"


def test_renderer_limit_zero_reads_all_rows(tmp_path):
    hotspot = load_hotspot_module()
    renderer = load_renderer_module()
    connection = sqlite3.connect(tmp_path / "layer1.sqlite")
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
                "category": "amenity_restaurant",
                "tag_key": "amenity",
                "tag_value": "restaurant",
                "coords": [(50.0, 16.0), (50.01, 16.0), (50.01, 16.01)],
                "center": {"lat": 50.0066, "lon": 16.0033},
                "score": 10.0,
                "norm": 1.0,
                "source_point_count": 3,
            },
            {
                "category": "amenity_restaurant",
                "tag_key": "amenity",
                "tag_value": "restaurant",
                "coords": [(50.0, 16.0), (50.01, 16.01), (50.0, 16.01)],
                "center": {"lat": 50.0033, "lon": 16.0066},
                "score": 5.0,
                "norm": 0.5,
                "source_point_count": 3,
            },
        ],
    )

    assert len(renderer.candidate_rows(connection, "demo", 1)) == 1
    assert len(renderer.candidate_rows(connection, "demo", 0)) == 2
    connection.close()


def test_renderer_ignores_unlinked_layer2_parcels(tmp_path):
    hotspot = load_hotspot_module()
    layer2 = load_layer2_module()
    renderer = load_renderer_module()
    connection = sqlite3.connect(tmp_path / "analysis.sqlite")
    hotspot.ensure_layer1_tables(connection)
    layer2.ensure_layer2_tables(connection)
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
                "category": "tourism_attraction",
                "tag_key": "tourism",
                "tag_value": "attraction",
                "coords": [(50.0, 16.0), (50.01, 16.0), (50.01, 16.01)],
                "center": {"lat": 50.0066, "lon": 16.0033},
                "score": 10.0,
                "norm": 1.0,
                "source_point_count": 1,
            }
        ],
    )
    parcel = layer2.parse_uldk_response(
        "0\n"
        "021302_5.0001.1|1|Gmina Test|Powiat Test|Dolnoslaskie|src|"
        "POLYGON((16.0 50.0,16.01 50.0,16.01 50.01,16.0 50.0))"
    )
    layer2.upsert_parcel(connection, parcel)

    rows = renderer.candidate_rows(connection, "demo", 0)
    parcels = renderer.parcel_features(connection, rows)

    assert parcels == []
    connection.close()


def test_renderer_adds_only_run_linked_layer2_parcels(tmp_path):
    hotspot = load_hotspot_module()
    layer2 = load_layer2_module()
    renderer = load_renderer_module()
    connection = sqlite3.connect(tmp_path / "analysis.sqlite")
    hotspot.ensure_layer1_tables(connection)
    layer2.ensure_layer2_tables(connection)
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
                "category": "tourism_attraction",
                "tag_key": "tourism",
                "tag_value": "attraction",
                "coords": [(50.0, 16.0), (50.01, 16.0), (50.01, 16.01)],
                "center": {"lat": 50.0066, "lon": 16.0033},
                "score": 10.0,
                "norm": 1.0,
                "source_point_count": 1,
            }
        ],
    )
    parcel = layer2.parse_uldk_response(
        "0\n"
        "021302_5.0001.1|1|Gmina Test|Powiat Test|Dolnoslaskie|src|"
        "POLYGON((16.0 50.0,16.01 50.0,16.01 50.01,16.0 50.0))"
    )
    layer2.upsert_parcel(connection, parcel)
    layer2.link_layer2_run_parcel(connection, "demo", parcel["parcel_id"], 1, "16.0,50.0,16.01,50.01", "Gmina Test")

    rows = renderer.candidate_rows(connection, "demo", 0)
    parcels = renderer.parcel_features(connection, rows)
    html = renderer.build_html("demo", [renderer.feature_from_row(row) for row in rows], [], parcels)

    assert len(parcels) == 1
    assert "Layer 2 parcels" in html
    assert "analysis_data.js" in html
    assert "parcelMarkerLayer" in html
    assert "parcelLabelIcon" in html
    assert "parcel-nav" in html
    assert "focusParcel" in html
    assert "Google Maps" in html
    connection.close()


def test_renderer_can_filter_to_selected_ranked_parcels(tmp_path):
    hotspot = load_hotspot_module()
    layer2 = load_layer2_module()
    renderer = load_renderer_module()
    connection = sqlite3.connect(tmp_path / "analysis.sqlite")
    hotspot.ensure_layer1_tables(connection)
    layer2.ensure_layer2_tables(connection)
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
                "category": "tourism_attraction",
                "tag_key": "tourism",
                "tag_value": "attraction",
                "coords": [(50.0, 16.0), (50.01, 16.0), (50.01, 16.01)],
                "center": {"lat": 50.0066, "lon": 16.0033},
                "score": 10.0,
                "norm": 1.0,
                "source_point_count": 1,
            }
        ],
    )
    parcel_a = layer2.parse_uldk_response(
        "0\n"
        "021302_5.0001.1|1|Gmina Test|Powiat Test|Dolnoslaskie|src|"
        "POLYGON((16.0 50.0,16.01 50.0,16.01 50.01,16.0 50.0))"
    )
    parcel_b = layer2.parse_uldk_response(
        "0\n"
        "021302_5.0001.2|2|Gmina Test|Powiat Test|Dolnoslaskie|src|"
        "POLYGON((16.02 50.0,16.03 50.0,16.03 50.01,16.02 50.0))"
    )
    for index, parcel in enumerate([parcel_a, parcel_b], start=1):
        layer2.upsert_parcel(connection, parcel)
        layer2.link_layer2_run_parcel(connection, "demo", parcel["parcel_id"], index, "16.0,50.0,16.03,50.01", "Gmina Test")

    selected_json = tmp_path / "ranked.json"
    selected_json.write_text(json.dumps({"top": [{"parcel_id": "021302_5.0001.2"}]}), encoding="utf-8")

    rows = renderer.candidate_rows(connection, "demo", 0)
    selected = renderer.load_selected_parcel_ids(str(selected_json))
    parcels = renderer.parcel_features(connection, rows, selected)

    assert selected == {"021302_5.0001.2"}
    assert len(parcels) == 1
    assert parcels[0]["properties"]["parcel_id"] == "021302_5.0001.2"
    connection.close()


def test_renderer_combines_multiple_runs_into_one_html(tmp_path):
    hotspot = load_hotspot_module()
    renderer = load_renderer_module()
    connection = sqlite3.connect(tmp_path / "analysis.sqlite")
    hotspot.ensure_layer1_tables(connection)
    for run_id, lon_offset in [("demo_a", 0.0), ("demo_b", 0.02)]:
        hotspot.write_mesh_cells_sqlite(
            connection,
            run_id,
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
                    "coords": [(50.0, 16.0 + lon_offset), (50.01, 16.0 + lon_offset), (50.01, 16.01 + lon_offset)],
                    "center": {"lat": 50.0066, "lon": 16.0033 + lon_offset},
                    "score": 10.0,
                    "norm": 1.0,
                    "source_point_count": 1,
                }
            ],
        )

    rows = renderer.candidate_rows(connection, ["demo_a", "demo_b"], 0)
    html = renderer.build_html("combined_demo", [renderer.feature_from_row(row) for row in rows], [], [], ["demo_a", "demo_b"])
    payload = renderer.build_payload("combined_demo", [renderer.feature_from_row(row) for row in rows], [], [], ["demo_a", "demo_b"])

    assert len(rows) == 2
    assert "analysis_data.js" in html
    assert payload["analysis_id"] == "combined_demo"
    assert payload["run_ids"] == ["demo_a", "demo_b"]
    assert payload["counts"]["runs"] == 2
    connection.close()


def test_renderer_documents_parcel_markers_in_html_even_without_parcels():
    renderer = load_renderer_module()

    html = renderer.build_html("empty", [], [], [])

    assert "parcelMarkerLayer" in html
    assert "parcelLabelIcon" in html
    assert "parcel-toggle" in html


def test_renderer_payload_keeps_data_outside_html():
    renderer = load_renderer_module()

    html = renderer.build_html("demo", [], [], [])
    payload = renderer.build_payload("demo", [], [], [])

    assert "window.PARCEL_ANALYSIS_DATA = " not in html
    assert "analysis_data.js" in html
    assert payload["data"] == {"type": "FeatureCollection", "features": []}
