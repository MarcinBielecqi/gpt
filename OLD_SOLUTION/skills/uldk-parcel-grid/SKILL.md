# ULDK Parcel Grid

This skill exposes two first-class entry points. They share the same canonical parcel cache, but they solve different entry problems:

- `probe_uldk_parcels.py`: direct low-level probe for one selected `bbox`
- `run_uldk_from_candidates.py`: higher-level wrapper that chooses candidate bboxes automatically and links results to one `run_id`

## Which Script To Use

Use `probe_uldk_parcels.py` when:

- you already have one concrete `bbox`
- you want to probe one area directly without iterating candidate cells through the wrapper
- you want to fill or refresh the canonical parcel cache for that exact area

Use `run_uldk_from_candidates.py` when:

- you want the wrapper to start from an existing `run_id`
- you want the script to rank candidate cells, test them in order, and stop when enough linked parcels were found
- you want output linked back to the run through the run membership helper table

## Shared Storage Model

Both scripts write canonical parcel data into:

- `canon_parcels`
- `canon_parcel_polygon_points`

Only the wrapper also writes run membership into the helper table that links `run_id` to canonical `parcel_id`.

That means direct probing grows the canonical parcel cache, while the wrapper additionally says which canonical parcels belong to one analysis run.

## Script 1: Direct BBox Probe

Script:

`skills/uldk-parcel-grid/scripts/probe_uldk_parcels.py`

What it does:

1. Builds a grid of sample points inside one `bbox`.
2. For each point, first checks whether an already cached parcel covers that point.
3. If not, queries ULDK for that point.
4. Normalizes and upserts returned parcels into canonical tables.
5. Applies cheap parcel rejection gates such as min/max area or bbox shape.
6. Returns one compact JSON summary plus optional `PROGRESS {...}` lines.

What it does not do:

- it does not read candidate cells from the hotspot table
- it does not populate run membership links
- it does not choose between multiple candidate bboxes for you

Typical command:

```powershell
python skills/uldk-parcel-grid/scripts/probe_uldk_parcels.py --bbox "<min_lon,min_lat,max_lon,max_lat>" --run-id "<label_only_run_id>" --db-path data/analysis_workspace.sqlite --grid-size-m 35 --max-requests 50 --progress-every 25
```

Useful flags:

- `--bbox`: required, one concrete search rectangle in `min_lon,min_lat,max_lon,max_lat`
- `--run-id`: required label for logs/output; canonical rows stay keyed by `parcel_id`
- `--grid-size-m`: probe density; smaller means more grid points
- `--max-requests`: upper bound on real ULDK calls
- `--refresh-existing`: ignore cached parcel hits and re-query ULDK points
- `--expected-commune`: keep only parcels whose ULDK commune matches
- `--min-parcel-area-m2`, `--max-parcel-area-m2`, `--max-bbox-area-m2`, `--max-bbox-aspect-ratio`: cheap rejection gates
- `--no-skip-rejected-polygons`: disables the optimization that skips future points inside already rejected parcel polygons

Output behavior:

- progress goes to stderr as compact `PROGRESS {...}` lines
- stdout prints `run_id=...`
- stdout then prints one compact JSON summary

Summary fields include:

- `grid_points`
- `requests`
- `inserted`
- `skipped_existing`
- `skipped_rejected_polygon`
- `empty`
- `errors`
- `out_of_scope`
- `rejected`
- `rejected_reasons`
- `found_parcel_ids`

## Script 2: Candidate Wrapper

Script:

`skills/uldk-parcel-grid/scripts/run_uldk_from_candidates.py`

What it does:

1. Reads candidates from `helper_osm_hotspot_mesh_cells` for one `run_id`.
2. Sorts them by score and removes duplicate bboxes.
3. Runs a one-request smoke probe per candidate.
4. Skips empty, out-of-scope, or clearly failing candidates.
5. Runs a fuller probe only for promising candidates.
6. Links found canonical parcels to the run through the run membership helper table.
7. Writes a JSON summary file under `results/analysis_<analysis_id>/...`.

What it adds on top of direct probing:

- automatic candidate selection from `helper_osm_hotspot_mesh_cells`
- per-candidate progress
- run-level status such as `ok`, `no_parcels_found`, `no_in_scope_parcels`, or `error`
- linkage between analysis `run_id` and canonical parcels

Typical command:

```powershell
python skills/uldk-parcel-grid/scripts/run_uldk_from_candidates.py --run-id "<run_id>" --db-path data/analysis_workspace.sqlite --max-candidates 80 --grid-size-m 45 --max-requests-per-candidate 120 --exclude-categories natural_wood --min-parcel-area-m2 1500 --max-parcel-area-m2 12000 --max-bbox-aspect-ratio 8 --target-linked-parcels 500 --progress-every 50
```

For a multi-area run set, add the same `--analysis-id "<analysis_id>"` to every command so summaries are written into one result folder.

Useful flags:

- `--run-id`: required run id used to read candidates and rebuild linked parcel membership
- `--max-candidates`: how many candidate cells to consider
- `--exclude-categories`: comma-separated candidate categories to skip before probing
- `--max-requests-per-candidate`: request budget for each candidate after the smoke test
- `--target-linked-parcels`: stop once enough parcels were linked
- `--stop-after-first-hit`: legacy narrow mode for smoke tests
- `--expected-commune --strict-commune`: turn commune into a hard candidate filter
- `--summary-output`: override the default summary file path

Output behavior:

- stderr prints compact run-level `PROGRESS {...}` lines and optional inner probe progress
- stdout prints one compact JSON object that includes the summary file path in `output`
- the wrapper clears existing parcel membership rows for the same `run_id` before rebuilding them

Summary fields include:

- `run_id`
- `status`
- `tested_candidates`
- `selected_candidate`
- `linked_parcels`
- `inserted_parcels`
- `requests`
- `rejected_parcels`
- `rejected_reasons`
- `skipped_rejected_polygon_points`
- `error_examples`

## Rules

- Do not inspect raw ULDK responses.
- Do not print parcel geometry or vertex lists.
- Do not add artificial sleeps or delays.
- Read only compact `PROGRESS {...}` lines and final JSON summaries unless code-level debugging is explicitly needed.
- Use progress only to confirm liveness.
- Do not filter by commune unless the user explicitly asks for strict administrative filtering. If strict filtering is requested in the wrapper, add `--expected-commune "<commune>" --strict-commune`.
- Keep cheap rejection in script flags. Do not write ad hoc parcel filters in side scripts.
- Do not loop over candidate bboxes manually when the wrapper already fits the task.

Choose the direct probe for one known bbox. Choose the wrapper when candidates already exist in the workspace and the run needs linked parcel membership.
