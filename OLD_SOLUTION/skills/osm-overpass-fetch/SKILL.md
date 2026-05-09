---
name: osm-overpass-fetch
description: Bounded OSM smoke fetch only. Persistent hotspot runs should use osm_hotspot_grid so data is persisted to SQLite.
---

# OSM Overpass Fetch

Use only for a small smoke check when explicitly needed. Persistent hotspot runs must use `skills/osm_hotspot_grid`.

Rules:

- Do not use raw Overpass JSON as canonical data.
- Do not print raw responses or preview row dumps.
- Keep output to status, count, and paths.
- Prefer writing diagnostics under `results/**`.
