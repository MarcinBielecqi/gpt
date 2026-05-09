# Parcel Visual Features

Use this skill to compute lightweight visual features for parcels already stored in SQLite.

Run:

```powershell
python skills/parcel-visual-features/scripts/compute_parcel_visual_features.py --db-path data/analysis_workspace.sqlite --run-id "<run_id>"
```

For all stored parcels:

```powershell
python skills/parcel-visual-features/scripts/compute_parcel_visual_features.py --db-path data/analysis_workspace.sqlite --all --progress-every 10
```

## Rules

- Source of truth for parcel shape is `canon_parcel_polygon_points`.
- Write only to derived table `deriv_parcel_visual_features`.
- The table can be deleted and rebuilt.
- Do not store screenshots, tile dumps, or raw image blobs in SQLite.
- Do not print pixels, polygon vertices, or large JSON.
- Read only compact `PROGRESS {...}` liveness lines and the final summary.
- Use `deriv_parcel_geometry_features.area_m2` for area; visual features must not duplicate `area_m2`.
