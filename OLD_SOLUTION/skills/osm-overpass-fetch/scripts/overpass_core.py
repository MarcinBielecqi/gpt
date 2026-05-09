from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_ENDPOINT = "https://lz4.overpass-api.de/api/interpreter"


def build_query(bbox: str, key: str, value: str, element_types: list[str], out_mode: str, limit: int) -> str:
    selectors = [f'{element_type}["{key}"="{value}"]({bbox});' for element_type in element_types]
    return f"[out:json][timeout:25];({''.join(selectors)});out {out_mode} {limit};"


def post_overpass(endpoint: str, query: str, timeout: int) -> dict:
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "parcel-osm-overpass-skill",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        text = response.read().decode("utf-8", errors="replace")
    return json.loads(text)


def parse_element_types(raw: str) -> list[str]:
    mapping = {
        "node": ["node"],
        "way": ["way"],
        "relation": ["relation"],
        "nwr": ["node", "way", "relation"],
    }
    if raw not in mapping:
        raise ValueError("--element-types must be one of: node, way, relation, nwr")
    return mapping[raw]


def representative_lat_lon(element: dict) -> tuple[float | str, float | str]:
    return (
        element.get("lat", element.get("center", {}).get("lat", "")),
        element.get("lon", element.get("center", {}).get("lon", "")),
    )


def preview_rows(payload: dict) -> list[dict]:
    rows = []
    for element in payload.get("elements", []):
        tags = repair_text_values(element.get("tags", {}))
        lat, lon = representative_lat_lon(element)
        rows.append(
            {
                "type": element.get("type", ""),
                "id": element.get("id", ""),
                "lat": lat,
                "lon": lon,
                "name": repair_text(tags.get("name", "")),
                "tag_value": tags,
            }
        )
    return rows


def write_json(path: str | Path | None, payload: dict | list) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def repair_text(value: str) -> str:
    if not isinstance(value, str) or not any(token in value for token in ("Ã", "Å", "Ä", "Â")):
        return value
    try:
        return value.encode("latin1").decode("utf-8")
    except UnicodeError:
        return value


def repair_text_values(value):
    if isinstance(value, dict):
        return {repair_text(k): repair_text_values(v) for k, v in value.items()}
    if isinstance(value, list):
        return [repair_text_values(v) for v in value]
    if isinstance(value, str):
        return repair_text(value)
    return value
