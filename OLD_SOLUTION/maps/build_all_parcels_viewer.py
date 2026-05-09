#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANON_DB = ROOT / "data" / "canon_workspace.sqlite"
OUT_DIR = Path(__file__).resolve().parent
DATA_DIR = OUT_DIR / "all_parcels_data"


def clean_part(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    return text if text else fallback


def slug(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip())
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:80] or "unknown"


def bbox_union(items: list[dict]) -> list[float]:
    return [
        min(item["bbox"][0] for item in items),
        min(item["bbox"][1] for item in items),
        max(item["bbox"][2] for item in items),
        max(item["bbox"][3] for item in items),
    ]


def centroid(items: list[dict]) -> list[float]:
    area_sum = sum(max(0.0, float(item.get("area_m2") or 0.0)) for item in items)
    if area_sum:
        lat = sum(item["center"][0] * max(0.0, float(item.get("area_m2") or 0.0)) for item in items) / area_sum
        lon = sum(item["center"][1] * max(0.0, float(item.get("area_m2") or 0.0)) for item in items) / area_sum
    else:
        lat = sum(item["center"][0] for item in items) / len(items)
        lon = sum(item["center"][1] for item in items) / len(items)
    return [round(lat, 7), round(lon, 7)]


def load_parcels(connection: sqlite3.Connection) -> list[dict]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT parcel_id, parcel_number, voivodeship, county, commune, precinct,
               area_m2, centroid_lat, centroid_lon,
               bbox_min_lat, bbox_min_lon, bbox_max_lat, bbox_max_lon
        FROM canon_parcels
        WHERE bbox_min_lat IS NOT NULL
          AND bbox_min_lon IS NOT NULL
          AND bbox_max_lat IS NOT NULL
          AND bbox_max_lon IS NOT NULL
        ORDER BY voivodeship, county, commune, precinct, parcel_number, parcel_id
        """
    ).fetchall()
    parcels: list[dict] = []
    for row in rows:
        bbox = [
            round(float(row["bbox_min_lat"]), 7),
            round(float(row["bbox_min_lon"]), 7),
            round(float(row["bbox_max_lat"]), 7),
            round(float(row["bbox_max_lon"]), 7),
        ]
        center_lat = row["centroid_lat"] if row["centroid_lat"] is not None else (bbox[0] + bbox[2]) / 2.0
        center_lon = row["centroid_lon"] if row["centroid_lon"] is not None else (bbox[1] + bbox[3]) / 2.0
        parcels.append(
            {
                "id": row["parcel_id"],
                "number": row["parcel_number"] or row["parcel_id"],
                "voivodeship": clean_part(row["voivodeship"], "unknown"),
                "county": clean_part(row["county"], "unknown"),
                "commune": clean_part(row["commune"], "unknown"),
                "precinct": clean_part(row["precinct"], "unknown"),
                "area_m2": round(float(row["area_m2"] or 0.0), 2),
                "center": [round(float(center_lat), 7), round(float(center_lon), 7)],
                "bbox": bbox,
            }
        )
    return parcels


def load_geometry(connection: sqlite3.Connection, parcel_ids: list[str]) -> dict[str, list[list[list[list[float]]]]]:
    if not parcel_ids:
        return {}
    placeholders = ",".join("?" for _ in parcel_ids)
    rows = connection.execute(
        f"""
        SELECT parcel_id, polygon_index, ring_index, point_index, lon, lat
        FROM canon_parcel_polygon_points
        WHERE parcel_id IN ({placeholders})
        ORDER BY parcel_id, polygon_index, ring_index, point_index
        """,
        parcel_ids,
    ).fetchall()
    polygons: dict[str, dict[int, dict[int, list[list[float]]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for parcel_id, polygon_index, ring_index, _point_index, lon, lat in rows:
        polygons[parcel_id][int(polygon_index)][int(ring_index)].append([round(float(lat), 7), round(float(lon), 7)])
    result: dict[str, list[list[list[list[float]]]]] = {}
    for parcel_id, parcel_polygons in polygons.items():
        result[parcel_id] = [
            [parcel_polygons[polygon_index][ring_index] for ring_index in sorted(parcel_polygons[polygon_index])]
            for polygon_index in sorted(parcel_polygons)
        ]
    return result


def make_group(level: str, key: str, label: str, items: list[dict], chunk: str | None = None) -> dict:
    group = {
        "level": level,
        "key": key,
        "label": label,
        "count": len(items),
        "area_m2": round(sum(float(item.get("area_m2") or 0.0) for item in items), 2),
        "bbox": bbox_union(items),
        "center": centroid(items),
    }
    if chunk:
        group["chunk"] = chunk
    return group


def grouped(parcels: list[dict], fields: list[str]) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = defaultdict(list)
    for parcel in parcels:
        key = "|".join(parcel[field] for field in fields)
        result[key].append(parcel)
    return dict(result)


def write_viewer(parcels: list[dict], connection: sqlite3.Connection) -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    precinct_groups = grouped(parcels, ["voivodeship", "county", "commune", "precinct"])
    commune_groups = grouped(parcels, ["voivodeship", "county", "commune"])
    county_groups = grouped(parcels, ["voivodeship", "county"])
    chunk_files: list[str] = []

    precinct_manifest = []
    for index, (key, items) in enumerate(sorted(precinct_groups.items()), start=1):
        parts = key.split("|")
        chunk_name = f"precinct_{index:04d}_{slug('_'.join(parts[-2:]))}.json"
        geometry = load_geometry(connection, [item["id"] for item in items])
        payload = {
            "key": key,
            "level": "precinct",
            "count": len(items),
            "parcels": [
                {
                    **item,
                    "geometry": geometry.get(item["id"], []),
                }
                for item in items
            ],
        }
        (DATA_DIR / chunk_name).write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        chunk_files.append(chunk_name)
        precinct_manifest.append(make_group("precinct", key, " / ".join(parts[-2:]), items, chunk_name))

    commune_manifest = [
        make_group("commune", key, " / ".join(key.split("|")[-2:]), items)
        for key, items in sorted(commune_groups.items())
    ]
    county_manifest = [
        make_group("county", key, " / ".join(key.split("|")[-2:]), items)
        for key, items in sorted(county_groups.items())
    ]
    manifest = {
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "immutable_viewer": True,
        "source": str(DEFAULT_CANON_DB.relative_to(ROOT)),
        "parcel_count": len(parcels),
        "limits": {"max_rendered_polygons": 30},
        "bounds": bbox_union(parcels),
        "groups": {
            "county": county_manifest,
            "commune": commune_manifest,
            "precinct": precinct_manifest,
        },
    }
    (DATA_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"parcels": len(parcels), "chunks": len(chunk_files), "manifest": str(DATA_DIR / "manifest.json")}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build immutable static all-parcels viewer data.")
    parser.add_argument("--canon-db-path", default=str(DEFAULT_CANON_DB))
    args = parser.parse_args()
    connection = sqlite3.connect(args.canon_db_path)
    try:
        parcels = load_parcels(connection)
        if not parcels:
            raise SystemExit("No parcels found in canon_parcels.")
        summary = write_viewer(parcels, connection)
    finally:
        connection.close()
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
