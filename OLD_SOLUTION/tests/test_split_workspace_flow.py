import importlib.util
import sqlite3
from pathlib import Path

from skills.shared.parcel_db import connect_workspace


def load_module(relative_path, module_name):
    path = Path(relative_path)
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def table_counts(db_path):
    connection = sqlite3.connect(db_path)
    try:
        names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {name: connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in names}
    finally:
        connection.close()


def test_split_workspace_writes_canon_and_analysis_tables(tmp_path):
    hotspot = load_module("skills/osm_hotspot_grid/scripts/build_hotspot_grid.py", "hotspot_grid")
    layer2 = load_module("skills/uldk-parcel-grid/scripts/probe_uldk_parcels.py", "probe_uldk_parcels")
    geometry = load_module("skills/parcel-geometry-features/scripts/compute_parcel_geometry_features.py", "geometry_features")
    analysis_db = tmp_path / "analysis.sqlite"
    canon_db = tmp_path / "canon.sqlite"
    run_id = "split_smoke"

    connection = connect_workspace(analysis_db, canon_db)
    try:
        hotspot.ensure_layer1_tables(connection)
        layer2.ensure_layer2_tables(connection)
        geometry.ensure_geometry_feature_table(connection)

        elements = [
            {"type": "node", "id": idx, "lat": 50.700 + idx * 0.0001, "lon": 16.420 + idx * 0.0001, "tags": {"tourism": "viewpoint"}}
            for idx in range(1, 4)
        ]
        hotspot.upsert_canon_osm_features(connection, elements, "2026-05-06T00:00:00Z", "smoke", "50.699,16.419,50.703,16.424")
        _, cells, _, _ = hotspot.build_mesh_cells_from_db(
            connection,
            (50.699, 16.419, 50.703, 16.424),
            [("tourism", "viewpoint", "tourism_viewpoint")],
            4,
            450.0,
            0.0,
        )
        hotspot.write_mesh_cells_sqlite(connection, run_id, "2026-05-06T00:00:00Z", "50.699,16.419,50.703,16.424", 4, 450.0, 0.0, cells)

        parcel = layer2.parse_uldk_response(
            "0\n"
            "026101_1.0033.239/1|239/1|Gmina Smoke|Powiat Smoke|Dolnoslaskie|src|"
            "POLYGON((16.4200 50.7000,16.4210 50.7000,16.4210 50.7010,16.4200 50.7010,16.4200 50.7000))"
        )
        layer2.upsert_parcel(connection, parcel)
        layer2.link_layer2_run_parcel(connection, run_id, parcel["parcel_id"], 1, "16.4200,50.7000,16.4210,50.7010")
        geometry.compute_to_db(connection, geometry.parcel_ids(connection, run_id, False, None), progress_every=0)
        connection.commit()
    finally:
        connection.close()

    canon_counts = table_counts(canon_db)
    analysis_counts = table_counts(analysis_db)

    assert canon_counts["canon_osm_features"] == 3
    assert canon_counts["canon_parcels"] == 1
    assert canon_counts["canon_parcel_polygon_points"] == 5
    assert "helper_layer2_run_parcels" not in canon_counts
    assert analysis_counts["helper_osm_hotspot_mesh_cells"] > 0
    assert analysis_counts["helper_layer2_run_parcels"] == 1
    assert analysis_counts["deriv_parcel_geometry_features"] == 1
    assert "canon_parcels" not in analysis_counts
