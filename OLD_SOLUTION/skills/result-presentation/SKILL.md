# Result Presentation

Use this skill to render analysis results and export compact SQL summaries from SQLite.

The map renderer uses one stable template:

- layout: `skills/result-presentation/templates/analysis_map.html`
- generated shell: `analysis_map.html`
- generated data payload: `analysis_data.js`
- local runtime assets: `leaflet.css`, `leaflet.js`

Keep the HTML generic. Put analysis-specific mesh cells, OSM points, parcel geometry, counts, run IDs, and categories in `analysis_data.js`.

Render one HTML map:

```powershell
python skills/result-presentation/scripts/render_analysis_map.py --run-id "<run_id>" --db-path data/analysis_workspace.sqlite --analysis-id "<analysis_id>" --output-dir results/analysis_<analysis_id>
```

Render a filtered candidate map from a ranking JSON:

```powershell
python skills/result-presentation/scripts/render_analysis_map.py --run-id "<run_id>" --db-path data/analysis_workspace.sqlite --analysis-id "<analysis_id>" --selected-parcels-json results/analysis_<analysis_id>/ranked_candidates_<run_id>.json --output-dir results/analysis_<analysis_id>
```

Export compact JSON summaries:

```powershell
python skills/result-presentation/scripts/export_presentation_json.py --query-name run_summary --run-id "<run_id>" --db-path data/analysis_workspace.sqlite --output results/analysis_<analysis_id>/run_summary_<run_id>.json
```

## Rules

- Use SQLite as source of truth.
- Visually smoke-test changed map rendering before treating the skill as stable.
- Do not read large generated JSON or GeoJSON.
- Do not create ad hoc SQL for Researcher mode.
- Produce one result folder and one `analysis_map.html` for one user-facing analysis.
- Generated maps must work from `file:///` with local `leaflet.css`, `leaflet.js`, and same-folder `analysis_data.js`.
- Generated maps must include a street/satellite basemap selector.
- The map must always include a visible legend with checkboxes and a parcel checkbox.
- When parcels are present, show them by default with strong green outlines/fill and numbered center markers.
- Numbered parcel markers must share the parcel popup and hide/show with the parcel checkbox.
- When parcels are present, include a clickable parcel navigation list that zooms to a parcel and opens the shared popup.
- Final answers should cite compact counts and artifact paths only.
