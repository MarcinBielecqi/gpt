# Skill: Result Presentation

## Cel

Tworzy metadane prezentacji wyników oraz lokalny viewer mapy działek podobny do `OLD_SOLUTION/maps/all_parcels.html`.

Dla `--view map` skill nie generuje nowych skryptów ani HTML przy każdym uruchomieniu. Uruchamia stały serwer z folderu skilla:

```text
NEW_SOLUTION/skills/result-presentation/src/map_server.py
```

Serwer zwraca świeży link `localhost` i sam kończy działanie po TTL. Domyślny TTL to `300` sekund, czyli 5 minut.

## CLI

| Parametr | Opis |
|---|---|
| `--view` | `summary`, `map`, `candidates` albo `all`. |
| `--host` | Host lokalnego serwera mapy. Domyślnie `127.0.0.1`. |
| `--port` | Port lokalnego serwera mapy. `0` wybiera wolny port. |
| `--ttl-seconds` | Czas życia lokalnego serwera w sekundach. Domyślnie `300`. |
| `--map-limit` | Domyślny limit renderowanych działek. Domyślnie `500`. |
| `--serve-timeout-seconds` | Maksymalny czas health-checku serwera. Domyślnie `15`. |
| `--no-serve` | Publikuje metadane mapy bez uruchamiania localhost. |

Standardowe argumenty protokołu skilla pozostają bez zmian: `--run-id`, `--workspace`, `--canon-db`, `--profile`, `--resume`, `--dry-run`, `--input-summary`, `--input-artifact`.

## Lokalny viewer mapy

`--view map` uruchamia `src/map_server.py`, który serwuje:

```text
/map.html
/api/health
/api/manifest
/api/parcels?bbox=<minLat,minLon,maxLat,maxLon>&limit=<n>
/api/artifacts
```

Viewer czyta dane na bieżąco z `canon.sqlite`:

```text
canon_parcels
canon_parcel_polygon_points
canon_rcn_price_observations
```

## Artefakty

| `artifact_type` | Opis |
|---|---|
| `report_metadata` | Metadane widoku. |
| `local_map_server` | Świeży link `localhost`, TTL, port i health URL. |
| `html_report` | Prosty HTML tylko dla `summary`, `candidates`, `all`. |
| `skill_summary` | Końcowy JSON protokołu skilla. |

## Zasady

Nie twórz luźnych JSON-ów.

Nie generuj nowych skryptów ani HTML dla widoku mapy przy każdym uruchomieniu.

Publikuj wspólne JSON-y wyłącznie jako string w `project_bus.sqlite`.

Nie zakładaj kolejności pipeline’u ani istnienia innych skillów.
