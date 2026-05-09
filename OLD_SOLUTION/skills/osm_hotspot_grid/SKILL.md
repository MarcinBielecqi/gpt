---
name: osm_hotspot_grid
description: Fetch runtime-selected OSM objects, persist them in SQLite, and build hotspot candidate cells from SQLite.
---

# OSM Hotspot Grid

Use this skill only through:

```powershell
python skills/osm_hotspot_grid/scripts/build_hotspot_grid.py --bbox "<min_lat,min_lon,max_lat,max_lon>" --types "<key=value,...>" --run-id "<run_id>" --db-path data/analysis_workspace.sqlite --output-dir results/osm_hotspot_grid/<run_id>
```

## Rules

- Runtime tags only; do not hardcode profiles.
- SQLite is canonical: `canon_osm_features`, then `helper_osm_hotspot_mesh_cells`.
- Generated files are diagnostics only.
- Do not open generated files in Researcher mode.
- Do not inspect individual OSM objects.
- For tourism attractiveness, do not use `natural=wood` as a primary tag.

Downstream parcel probing reads candidates from `helper_osm_hotspot_mesh_cells` by `run_id`; the agent should not build candidate loops manually.
