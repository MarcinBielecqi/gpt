import importlib.util
import sqlite3
from pathlib import Path


def load_migration_module():
    path = Path("skills/schema-validation/scripts/migrate_table_prefixes.py")
    spec = importlib.util.spec_from_file_location("migrate_table_prefixes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_cleanup_module():
    path = Path("skills/schema-validation/scripts/cleanup_helper_tables.py")
    spec = importlib.util.spec_from_file_location("cleanup_helper_tables", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_validate_module():
    path = Path("skills/schema-validation/scripts/validate_schema.py")
    spec = importlib.util.spec_from_file_location("validate_schema", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_table_prefix_migration_renames_and_clears_only_helpers(tmp_path):
    migrate = load_migration_module()
    db_path = tmp_path / "workspace.sqlite"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE osm_features (
            osm_type TEXT NOT NULL,
            osm_id INTEGER NOT NULL,
            fetched_at TEXT NOT NULL,
            source TEXT NOT NULL,
            bbox_query TEXT NOT NULL,
            tags_json TEXT NOT NULL,
            UNIQUE(osm_type, osm_id)
        );
        CREATE TABLE parcels (parcel_id TEXT PRIMARY KEY);
        CREATE TABLE osm_hotspot_mesh_cells (run_id TEXT NOT NULL);
        CREATE TABLE layer2_run_parcels (
            run_id TEXT NOT NULL,
            parcel_id TEXT NOT NULL,
            candidate_index INTEGER NOT NULL,
            source_bbox TEXT NOT NULL,
            PRIMARY KEY (run_id, parcel_id)
        );
        INSERT INTO osm_features VALUES ('node', 1, 't', 'test', 'bbox', '{}');
        INSERT INTO parcels VALUES ('P1');
        INSERT INTO osm_hotspot_mesh_cells VALUES ('run');
        INSERT INTO layer2_run_parcels VALUES ('run', 'P1', 1, 'bbox');
        """
    )
    connection.commit()
    connection.close()

    summary = migrate.migrate(db_path, clear_helpers=True)

    connection = sqlite3.connect(db_path)
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"canon_osm_features", "canon_parcels", "helper_osm_hotspot_mesh_cells", "helper_layer2_run_parcels"} <= tables
    assert {"osm_features", "parcels", "osm_hotspot_mesh_cells", "layer2_run_parcels"}.isdisjoint(tables)
    assert connection.execute("SELECT COUNT(*) FROM canon_osm_features").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM canon_parcels").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM helper_osm_hotspot_mesh_cells").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM helper_layer2_run_parcels").fetchone()[0] == 0
    connection.close()
    assert summary["helper_before"] == {"helper_osm_hotspot_mesh_cells": 1, "helper_layer2_run_parcels": 1}


def test_helper_cleanup_never_touches_canonical_tables(tmp_path):
    cleanup = load_cleanup_module()
    db_path = tmp_path / "workspace.sqlite"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE canon_parcels (parcel_id TEXT PRIMARY KEY);
        CREATE TABLE helper_layer2_run_parcels (
            run_id TEXT NOT NULL,
            parcel_id TEXT NOT NULL,
            candidate_index INTEGER NOT NULL,
            source_bbox TEXT NOT NULL,
            PRIMARY KEY (run_id, parcel_id)
        );
        INSERT INTO canon_parcels VALUES ('P1');
        INSERT INTO helper_layer2_run_parcels VALUES ('run', 'P1', 1, 'bbox');
        """
    )
    connection.commit()
    connection.close()

    cleanup.cleanup(db_path)

    connection = sqlite3.connect(db_path)
    assert connection.execute("SELECT COUNT(*) FROM canon_parcels").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM helper_layer2_run_parcels").fetchone()[0] == 0
    connection.close()


def test_validator_rejects_legacy_unprefixed_tables(tmp_path):
    validate = load_validate_module()
    db_path = tmp_path / "workspace.sqlite"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE parcels (parcel_id TEXT PRIMARY KEY)")
    connection.commit()
    connection.close()

    errors = validate.validate(db_path)

    assert "legacy unprefixed table remains: parcels" in errors
