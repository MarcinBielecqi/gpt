import importlib.util
import sqlite3
from pathlib import Path


def load_hotspot_module():
    path = Path("skills/osm_hotspot_grid/scripts/build_hotspot_grid.py")
    spec = importlib.util.spec_from_file_location("hotspot_grid", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_export_module():
    path = Path("skills/result-presentation/scripts/export_presentation_json.py")
    spec = importlib.util.spec_from_file_location("export_presentation_json", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_rcn_module():
    path = Path("skills/poland-rcn-wfs/scripts/fetch_rcn_wfs.py")
    spec = importlib.util.spec_from_file_location("fetch_rcn_wfs", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_named_sql_export_returns_bounded_layer1_candidates():
    hotspot = load_hotspot_module()
    exporter = load_export_module()
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
            }
        ],
    )

    rows = exporter.rows_as_dicts(connection, "top_layer1_candidates", {"run_id": "demo", "limit": 1, "expected_commune": None})

    assert len(rows) == 1
    assert rows[0]["category"] == "tourism_viewpoint"


def test_run_summary_does_not_require_layer2_tables():
    hotspot = load_hotspot_module()
    exporter = load_export_module()
    connection = sqlite3.connect(":memory:")
    hotspot.ensure_layer1_tables(connection)

    rows = exporter.rows_as_dicts(connection, "run_summary", {"run_id": "demo", "limit": 10, "expected_commune": None})

    assert {"metric": "helper_layer2_run_parcels", "value": 0} in rows


def test_layer3_rcn_summary_export_is_bounded():
    exporter = load_export_module()
    rcn = load_rcn_module()
    connection = sqlite3.connect(":memory:")
    rcn.ensure_layer3_tables(connection)
    rcn.upsert_records(
        connection,
        [
            {
                "gml_id": "a",
                "dzi_id_dzialki": "021302_5.0001.1",
                "dzi_pow_ewid": "0.1000",
                "dzi_cena_brutto": "100000",
                "dok_data": "2025-01-01",
            },
            {
                "gml_id": "b",
                "dzi_id_dzialki": "021302_5.0001.2",
                "dzi_pow_ewid": "0.1000",
                "dzi_cena_brutto": "200000",
                "dok_data": "2025-01-01",
            },
        ],
        "demo",
        None,
        {},
        "2026-01-01T00:00:00+00:00",
        {"2025": 100.0, "2026": 110.0},
        "2026",
    )

    rows = exporter.rows_as_dicts(connection, "layer3_rcn_summary", {"run_id": "demo", "limit": 10, "expected_commune": None})

    assert rows == [
        {
            "rcn_records": 2,
            "priced_records": 2,
            "min_price_per_m2": 100.0,
            "avg_price_per_m2": 150.0,
            "max_price_per_m2": 200.0,
            "inflation_adjusted_priced_records": 2,
            "avg_inflation_adjusted_price_per_m2": 165.0,
            "inflation_reference_year": "2026",
        }
    ]
