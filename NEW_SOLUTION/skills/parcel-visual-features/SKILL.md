# Skill: Parcel Visual Features

## Cel

Publikuje artefakt cech wizualnych zgodnie z nowym kontraktem.

## Publiczne wejścia CLI

| Parametr | Opis |
|---|---|
| `--run-id` | Identyfikator runu. |
| `--workspace` | Katalog roboczy runu, np. `runs/walbrzych_001`. |
| `--canon-db` | Ścieżka do bazy kanonicznej, np. `data/canon.sqlite`. |
| `--profile` | Profil wykonania: `quick`, `normal`, `deep`. |
| `--resume` | Opcjonalne wznowienie. |
| `--dry-run` | Sprawdza wejścia bez pełnego wykonania. |
| `--input-summary` | Opcjonalny selektor artefaktu `skill_summary` z `project_bus.sqlite`. |
| `--input-artifact` | Opcjonalny selektor artefaktu z `project_bus.sqlite`, np. `parcel_candidates:default`. |

## Wejścia domenowe

| Parametr | Opis |
|---|---|
| `--source-artifact` | Selektor źródłowego artefaktu, domyślnie `geometry_features:default`. |

## Dostęp do danych

Skill może czytać:

```text
data/canon.sqlite
data/project_bus.sqlite
runs/<run_id>/skills/parcel_visual_features/run.sqlite
```

Skill może pisać:

```text
data/canon.sqlite
data/project_bus.sqlite
runs/<run_id>/skills/parcel_visual_features/run.sqlite
```

Skill nie czyta lokalnych baz innych skillów.

## Artefakty publikowane

| `artifact_type` | Opis |
|---|---|
| `visual_features` | Cechy wizualne działek albo status ich braku. |
| `skill_summary` | Mały raport końcowy zapisany jako JSON string w `bus_artifacts.payload_json`. |

## Komunikacja

`stdout` zawiera dokładnie jedną końcową linię JSON.

`stderr` może zawierać tylko:

```text
PROGRESS {...}
ERROR {...}
```

## Zasady

Nie twórz luźnych plików JSON.

Publikuj wspólne JSON-y wyłącznie jako string w `project_bus.sqlite`.

Nie zakładaj kolejności pipeline’u ani istnienia innych skillów.
