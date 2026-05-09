#!/usr/bin/env python3
from __future__ import annotations

import argparse
import heapq
import json
import math
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path


OUT = Path(__file__).resolve().parent / "trail_bbox_overlay.json"
OVERPASS = "https://lz4.overpass-api.de/api/interpreter"


def hav(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    radius = 6_371_000
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    x = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(x))


def fetch_easy_ways(bbox: str) -> list[dict]:
    query = f"""
    [out:json][timeout:90];
    (
      way["highway"~"^(path|footway|track|pedestrian)$"]({bbox});
      way["route"="hiking"]({bbox});
    );
    out tags geom;
    """
    request = urllib.request.Request(
        OVERPASS,
        data=urllib.parse.urlencode({"data": query}).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "parcel-easy-trail-bbox-overlay"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    ways = []
    for element in payload.get("elements", []):
        tags = element.get("tags", {})
        sac = tags.get("sac_scale", "")
        if sac and sac not in {"hiking", "T1"}:
            continue
        geometry = element.get("geometry") or []
        if len(geometry) < 2:
            continue
        ways.append(
            {
                "id": element.get("id"),
                "name": tags.get("name") or tags.get("ref") or tags.get("osmc:symbol") or f"way/{element.get('id')}",
                "geometry": [(round(float(point["lat"]), 6), round(float(point["lon"]), 6)) for point in geometry],
            }
        )
    return ways


def build_graph(ways: list[dict]) -> dict[tuple[float, float], list[tuple[float, tuple[float, float]]]]:
    graph: dict[tuple[float, float], list[tuple[float, tuple[float, float]]]] = defaultdict(list)
    for way in ways:
        for a, b in zip(way["geometry"], way["geometry"][1:]):
            distance = hav(a, b)
            if distance <= 0:
                continue
            graph[a].append((distance, b))
            graph[b].append((distance, a))
    return graph


def connected_components(graph: dict) -> list[list[tuple[float, float]]]:
    seen = set()
    components = []
    for node in graph:
        if node in seen:
            continue
        stack = [node]
        seen.add(node)
        component = []
        while stack:
            current = stack.pop()
            component.append(current)
            for _distance, neighbor in graph[current]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        components.append(component)
    return components


def dijkstra(graph: dict, start: tuple[float, float], allowed: set[tuple[float, float]]) -> tuple[tuple[float, float], dict, dict]:
    distances = {start: 0.0}
    previous = {}
    queue = [(0.0, start)]
    farthest = start
    while queue:
        distance, node = heapq.heappop(queue)
        if distance != distances[node]:
            continue
        if distance > distances[farthest]:
            farthest = node
        for edge, neighbor in graph[node]:
            if neighbor not in allowed:
                continue
            candidate = distance + edge
            if candidate < distances.get(neighbor, float("inf")):
                distances[neighbor] = candidate
                previous[neighbor] = node
                heapq.heappush(queue, (candidate, neighbor))
    return farthest, distances, previous


def longest_path(graph: dict) -> list[tuple[float, float]]:
    components = connected_components(graph)
    component = max(components, key=len)
    allowed = set(component)
    start = component[0]
    a, _distances, _previous = dijkstra(graph, start, allowed)
    b, distances, previous = dijkstra(graph, a, allowed)
    path = [b]
    while path[-1] != a:
        path.append(previous[path[-1]])
    path.reverse()
    return path


def cumulative(path: list[tuple[float, float]]) -> list[float]:
    values = [0.0]
    for a, b in zip(path, path[1:]):
        values.append(values[-1] + hav(a, b))
    return values


def point_at(path: list[tuple[float, float]], values: list[float], meter: float) -> list[float]:
    meter = max(0.0, min(meter, values[-1]))
    for index in range(1, len(values)):
        if values[index] >= meter:
            span = max(1e-9, values[index] - values[index - 1])
            ratio = (meter - values[index - 1]) / span
            lat = path[index - 1][0] + (path[index][0] - path[index - 1][0]) * ratio
            lon = path[index - 1][1] + (path[index][1] - path[index - 1][1]) * ratio
            return [round(lat, 7), round(lon, 7)]
    return [round(path[-1][0], 7), round(path[-1][1], 7)]


def bbox_for(center: list[float], size_km: float) -> list[float]:
    lat, lon = center
    half_m = size_km * 500
    dlat = half_m / 111_320
    dlon = half_m / (111_320 * max(0.2, math.cos(math.radians(lat))))
    return [round(lat - dlat, 7), round(lon - dlon, 7), round(lat + dlat, 7), round(lon + dlon, 7)]


def parse_bbox(raw: str) -> tuple[float, float, float, float]:
    south, west, north, east = [float(part.strip()) for part in raw.split(",")]
    return south, west, north, east


def inside_margin(point: tuple[float, float], bbox: tuple[float, float, float, float], margin_km: float) -> bool:
    if margin_km <= 0:
        return True
    lat, lon = point
    south, west, north, east = bbox
    margin_lat = (margin_km * 1000) / 111_320
    margin_lon = (margin_km * 1000) / (111_320 * max(0.2, math.cos(math.radians(lat))))
    return (south + margin_lat) <= lat <= (north - margin_lat) and (west + margin_lon) <= lon <= (east - margin_lon)


def filter_graph_by_margin(graph: dict, bbox: tuple[float, float, float, float], margin_km: float) -> dict:
    allowed = {node for node in graph if inside_margin(node, bbox, margin_km)}
    filtered: dict[tuple[float, float], list[tuple[float, tuple[float, float]]]] = defaultdict(list)
    for node in allowed:
        for distance, neighbor in graph[node]:
            if neighbor in allowed:
                filtered[node].append((distance, neighbor))
    return filtered


def main() -> int:
    parser = argparse.ArgumentParser(description="Build overlapping 20 km bbox overlay along easy OSM trails.")
    parser.add_argument("--bbox", default="50.55,15.55,51.15,16.65", help="Overpass bbox: south,west,north,east")
    parser.add_argument("--edge-margin-km", type=float, default=10.0, help="Keep route nodes at least this far from the sampled bbox edge.")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--box-size-km", type=float, default=20.0)
    parser.add_argument("--step-km", type=float, default=7.0)
    parser.add_argument("--output", default=str(OUT))
    args = parser.parse_args()

    ways = fetch_easy_ways(args.bbox)
    graph = build_graph(ways)
    graph = filter_graph_by_margin(graph, parse_bbox(args.bbox), args.edge_margin_km)
    if not graph:
        raise SystemExit("No graph nodes left after edge margin filter.")
    path = longest_path(graph)
    values = cumulative(path)
    route_length = values[-1]
    requested_step_m = args.step_km * 1000
    step_m = min(requested_step_m, route_length / max(1, args.count - 1))
    required = (args.count - 1) * step_m
    start = max(0.0, (route_length - required) / 2)
    boxes = []
    for index in range(args.count):
        center = point_at(path, values, start + index * step_m)
        boxes.append(
            {
                "index": index + 1,
                "center": center,
                "bbox": bbox_for(center, args.box_size_km),
                "size_km": args.box_size_km,
                "step_km": round(step_m / 1000, 3),
            }
        )

    sample_count = min(700, max(2, int(route_length // 250)))
    route = [point_at(path, values, route_length * i / (sample_count - 1)) for i in range(sample_count)]
    result = {
        "name": "Easy OSM trail bbox overlay",
        "source": "OSM Overpass: highway=path/footway/track/pedestrian and route=hiking; sac_scale empty, hiking, or T1",
        "route_length_m": round(route_length, 1),
        "bbox_size_km": args.box_size_km,
        "bbox_step_km": round(step_m / 1000, 3),
        "boxes": boxes,
        "route": route,
    }
    output = Path(args.output)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "ways": len(ways), "nodes": len(graph), "route_length_m": round(route_length, 1), "boxes": len(boxes)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
