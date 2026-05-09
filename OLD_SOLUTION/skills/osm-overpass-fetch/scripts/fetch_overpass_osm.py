#!/usr/bin/env python3
"""Run Overpass OSM queries for a bbox and save raw/normalized outputs."""

from __future__ import annotations

import argparse
import json

from overpass_core import DEFAULT_ENDPOINT, build_query, parse_element_types, post_overpass, preview_rows, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bbox", required=True, help="min_lat,min_lon,max_lat,max_lon in EPSG:4326 for Overpass QL.")
    parser.add_argument("--key", required=True, help="OSM tag key, for example tourism.")
    parser.add_argument("--value", required=True, help="OSM tag value, for example attraction.")
    parser.add_argument("--element-types", default="node", help="node|way|relation|nwr")
    parser.add_argument("--out-mode", default="center", help="Overpass out mode, for example ids|tags|body|center")
    parser.add_argument("--limit", type=int, default=50, help="Maximum returned objects per out clause.")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="Overpass interpreter endpoint URL.")
    parser.add_argument("--timeout", type=int, default=60, help="HTTP timeout in seconds.")
    parser.add_argument("--out-raw-json", help="Path for full Overpass JSON response.")
    parser.add_argument("--out-preview-json", help="Path for compact preview rows.")
    args = parser.parse_args()

    element_types = parse_element_types(args.element_types)
    query = build_query(args.bbox, args.key, args.value, element_types, args.out_mode, args.limit)
    response = post_overpass(args.endpoint, query, args.timeout)

    write_json(args.out_raw_json, response)
    write_json(args.out_preview_json, preview_rows(response))

    elements = response.get("elements", [])
    result = {
        "endpoint": args.endpoint,
        "query": query,
        "element_count": len(elements),
        "out_raw_json": args.out_raw_json,
        "out_preview_json": args.out_preview_json,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
