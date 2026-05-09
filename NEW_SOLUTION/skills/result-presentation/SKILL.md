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
| `--map-limit` | Domyślny limit renderowanych działek. Domyślnie `500`; UI pozwala dojść do `8000`. |
| `--analysis-dir` | Folder z plikami JSON analiz. Domyślnie `<folder canon.sqlite>/analysis`, zwykle `data/analysis`. |
| `--serve-timeout-seconds` | Maksymalny czas health-checku serwera. Domyślnie `15`. |
| `--no-serve` | Publikuje metadane mapy bez uruchamiania localhost. |

Standardowe argumenty protokołu skilla pozostają bez zmian: `--run-id`, `--workspace`, `--canon-db`, `--profile`, `--resume`, `--dry-run`, `--input-summary`, `--input-artifact`.

## Lokalny viewer mapy

`--view map` uruchamia `src/map_server.py`, który serwuje:

```text
/map.html
/api/health
/api/manifest
/api/parcels?bbox=<minLat,minLon,maxLat,maxLon>&limit=<n>&min_area=<m2>&max_area=<m2>&analysis_key=<key>
/api/artifacts
/api/analysis-files
/api/analysis?file=<relative-json-path>
/api/analysis/upload
```

Viewer czyta dane na bieżąco z `canon.sqlite`:

```text
canon_parcels
canon_parcel_polygon_points
canon_rcn_price_observations
```

Filtry powierzchni i analizy działek są wykonywane po stronie lokalnego serwera przed pobraniem geometrii, żeby nie ładować odrzuconych poligonów.

## Analizy JSON

Domyślny folder analiz to:

```text
data/analysis
```

Mapa pokazuje pliki `.json` z tego folderu w wybierajce. Użytkownik może też wczytać plik JSON ręcznie przez input pliku w przeglądarce.

Preferowany format:

```json
{
  "title": "Nazwa analizy",
  "description": "Opis analizy widoczny w chowalnym panelu na mapie.",
  "parcel_ids": [
    "020810_5.0008.386/9",
    "020810_5.0008.386/11"
  ]
}
```

Obsługiwane są też warianty kompatybilne:

```json
{
  "analysis": {
    "title": "Nazwa analizy",
    "description": "Opis analizy"
  },
  "parcels": [
    {"id": "020810_5.0008.386/9"},
    {"parcel_id": "020810_5.0008.386/11"}
  ]
}
```

Zasady formatu:

- JSON musi być obiektem.
- Wymagana jest tablica `parcel_ids`, `parcels` albo `ids`.
- Identyfikatory muszą odpowiadać `canon_parcels.parcel_id`.
- Opis może być w `description`, `summary`, `analysis.description` albo `analysis.summary`.
- Duplikaty ID są usuwane przy ładowaniu.
- Tryb `analiza` filtruje mapę tylko do działek z wybranego JSON-a; tryb `ogólny` ignoruje analizę.

## Artefakty

| `artifact_type` | Opis |
|---|---|
| `report_metadata` | Metadane widoku. |
| `local_map_server` | Świeży link `localhost`, TTL, port, health URL i folder analiz. |
| `html_report` | Prosty HTML tylko dla `summary`, `candidates`, `all`. |
| `skill_summary` | Końcowy JSON protokołu skilla. |

## Zasady

Nie twórz luźnych JSON-ów.

Nie generuj nowych skryptów ani HTML dla widoku mapy przy każdym uruchomieniu.

Publikuj wspólne JSON-y wyłącznie jako string w `project_bus.sqlite`.

Nie zakładaj kolejności pipeline’u ani istnienia innych skillów.
