# Poland RCN WFS

Use this skill for land price evidence from Polish RCN public WFS.

Run only the SQLite-backed wrapper:

```powershell
python skills/poland-rcn-wfs/scripts/fetch_rcn_wfs.py --run-id "<run_id>" --db-path data/analysis_workspace.sqlite --from-linked-parcels --limit 500 --page-size 100
```

Optional real-price variant:

```powershell
python skills/poland-rcn-wfs/scripts/fetch_rcn_wfs.py --run-id "<run_id>" --db-path data/analysis_workspace.sqlite --from-linked-parcels --limit 500 --page-size 100 --inflation-index-json "<cpi_index.json>" --inflation-reference-year "<year>"
```

For an automatic full parcel coverage scan, use the tiled scanner:

```powershell
python skills/poland-rcn-wfs/scripts/scan_all_parcels_rcn.py --run-id "<run_id>" --db-path data/analysis_workspace.sqlite --canon-db-path data/canon_workspace.sqlite --tile-size-km 2 --limit-per-tile 1000 --page-size 100 --continue-on-error
```

Small bbox test before a large run:

```powershell
python skills/poland-rcn-wfs/scripts/scan_all_parcels_rcn.py --run-id "<run_id>" --db-path data/analysis_workspace.sqlite --canon-db-path data/canon_workspace.sqlite --bbox-4326 "16.43,50.68,16.46,50.70" --limit-tiles 2 --limit-per-tile 50 --page-size 25 --continue-on-error
```

The inflation index is runtime input. Do not hardcode CPI/inflation assumptions in repo files.

For a multi-area analysis, run it once per `run_id` and pass the same presentation `analysis_id` later.

## Rules

- RCN is evidence of past transactions, not current availability.
- Store fetched RCN records only in SQLite table `canon_rcn_price_observations`.
- Do not scrape portals or interactive maps.
- Do not write raw XML/JSON as canonical data.
- Do not print raw records, personal details, geometry, or full XML.
- Read only compact progress and final summary.
- Prefer `--from-linked-parcels` so the query bbox comes from parcels linked to the run.
- Use `--bbox-4326` only for explicit diagnostic research.
- Report whether inflation-adjusted prices were computed and what reference year was used.
- The full scanner stores raw transaction evidence in `canon_rcn_price_observations` and per-parcel scan status/statistics in `canon_rcn_parcel_checks`, including zero-record checks and compact error status.

This skill does not create parcel scores. It only persists raw transaction evidence and exposes compact counts/median price signals.
