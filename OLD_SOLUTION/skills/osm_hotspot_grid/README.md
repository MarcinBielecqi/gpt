# osm_hotspot_grid

OSM hotspot helper: fetch OSM data, persist it to SQLite, and build derived hotspot mesh rows.

Run:

```powershell
python skills\osm_hotspot_grid\scripts\build_hotspot_grid.py --bbox "50.54,16.60,50.60,16.72" --types "tourism=attraction,tourism=viewpoint,natural=peak,amenity=restaurant" --run-id osm_hotspot_grid_demo --db-path data\\analysis_workspace.sqlite --output-dir results\osm_hotspot_grid\osm_hotspot_grid_demo
```

Canonical tables:

- `canon_osm_features`
- `helper_osm_hotspot_mesh_cells`

Generated files are diagnostics only. Researcher mode should not open them.
