---
name: polish-parcel-wfs
description: Discover, probe, and fetch Polish cadastral parcel polygons from WFS services into the existing parcel SQLite cache. Use for Geoportal Powiatowe Bazy Ewidencji Gruntow, EZIUDP/WebEWID county endpoints, or compatible Polish parcel WFS services when canonical parcel tables should be populated without changing table architecture.
---

# Polish Parcel WFS

Use this skill when a Polish cadastral WFS can return parcel polygons directly through `GetFeature`.

Main script:

`skills/polish-parcel-wfs/scripts/fetch_polish_parcel_wfs.py`

## Core Workflow

1. Run `--mode probe` on the endpoint first. It combines `GetCapabilities`, `DescribeFeatureType`, and a small parsed sample.
2. Adjust `--typename`, field mapping, CRS, bbox, and filters based on the probe output.
3. Run `--mode fetch` to upsert parcels into `canon_parcels` and `canon_parcel_polygon_points`.
4. Read compact `PROGRESS {...}` lines from stderr and the final JSON summary from stdout.

## Typical Geoportal County Fetch

```powershell
python skills/polish-parcel-wfs/scripts/fetch_polish_parcel_wfs.py --county-code 2216 --mode probe --count 10 --max-pages 1
```

```powershell
python skills/polish-parcel-wfs/scripts/fetch_polish_parcel_wfs.py --county-code 2216 --mode fetch --typename ms:dzialki --count 100 --srsname EPSG:2180 --db-path data/analysis_workspace.sqlite --canon-db-path data/canon_workspace.sqlite
```

The default Geoportal URL shape is:

```text
https://mapy.geoportal.gov.pl/wss/ext/PowiatoweBazyEwidencjiGruntow/<county-code>?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature&TYPENAMES=ms:dzialki&COUNT=100&STARTINDEX=0&SRSNAME=EPSG:2180
```

## External County Endpoint

```powershell
python skills/polish-parcel-wfs/scripts/fetch_polish_parcel_wfs.py --endpoint-url "https://walbrzyski-wms.webewid.pl/iip/ows" --county-code 0221 --mode probe --typename ms:dzialki --count 10
```

```powershell
python skills/polish-parcel-wfs/scripts/fetch_polish_parcel_wfs.py --endpoint-url "https://walbrzyski-wms.webewid.pl/iip/ows" --county-code 0221 --mode fetch --typename ms:dzialki --cql-filter "NAZWA_GMINY='Gluszyca'" --commune-filter "Gluszyca"
```

## Useful Modes

- `--mode capabilities`: prints WFS feature types, titles, CRS hints, and WGS84 boxes from `GetCapabilities`.
- `--mode schema`: prints fields from `DescribeFeatureType` for `--typename`.
- `--mode probe`: runs capabilities, schema, and a small parsed `GetFeature` sample.
- `--mode url`: prints the exact `GetFeature` URL without calling it.
- `--mode fetch`: writes parsed parcel polygons to canonical SQLite tables.

## API Levers The Script Exposes

- Endpoint selection: `--county-code`, `--base-url`, `--endpoint-url`.
- WFS request shape: `--version`, `--typename`, `--typename-param`, `--count`, `--startindex`, `--srsname`, `--output-format`, `--property-name`, `--sort-by`.
- Server-side narrowing: `--bbox`, `--bbox-4326`, `--bbox-srs`, `--cql-filter`, raw `--filter-xml`.
- Client-side safety: `--commune-filter`, `--limit`, `--max-pages`, `--stream`.
- Field mapping: `--parcel-id-field`, `--parcel-number-field`, `--commune-field`, `--precinct-field`, `--precinct-code-field`, `--county-field`, `--voivodeship-field`.
- Coordinate handling: `EPSG:2180`, `EPSG:4326`, and `CRS84` output geometries, with `--xy-order auto|xy|yx`.

## Storage Contract

- Write only to `canon_parcels` and `canon_parcel_polygon_points`.
- Do not change table definitions.
- Do not write raw XML/GML to canonical tables.
- Do not print raw geometry or vertex lists.
- Store polygon points, bbox, and centroid as `lon,lat` degrees.
- Convert `EPSG:2180` meter coordinates before writing points so downstream skills can share data with `uldk-parcel-grid`.

## Notes

- Default field names match Geoportal MapServer `ms:dzialki`.
- If a county uses different field names, run `--mode schema`, then pass field mapping flags rather than editing code.
- Prefer server-side `--bbox-4326` or `--cql-filter` before client-side filtering when the service supports it.
- Use `--stream` for very large single responses; use normal paged fetch when the endpoint reports `numberReturned` reliably.
