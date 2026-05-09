# Parcel Geometry Features

Use this skill to compute derived geometric features for parcels already stored in SQLite.

Run:

```powershell
python skills/parcel-geometry-features/scripts/compute_parcel_geometry_features.py --db-path data/analysis_workspace.sqlite --run-id "<run_id>"
```

For all stored parcels:

```powershell
python skills/parcel-geometry-features/scripts/compute_parcel_geometry_features.py --db-path data/analysis_workspace.sqlite --all
```

## Rules

- Source of truth is `canon_parcel_polygon_points`.
- Write only to derived table `deriv_parcel_geometry_features`.
- The table can be deleted and rebuilt.
- Do not print polygon vertices.
- Read only compact progress and final summary.
- Use the computed `area_m2` and centroidal/principal moments for shape analysis; do not recompute them in ad hoc notebook-style loops.
