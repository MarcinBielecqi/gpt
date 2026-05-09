#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import BinaryIO, Iterable

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
ULDK_SCRIPT_DIR = ROOT_DIR / "skills" / "uldk-parcel-grid" / "scripts"
if str(ULDK_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(ULDK_SCRIPT_DIR))

import probe_uldk_parcels as layer2
from skills.shared.parcel_db import DEFAULT_ANALYSIS_DB_PATH, DEFAULT_CANON_DB_PATH, connect_workspace


DEFAULT_BASE_URL = "https://mapy.geoportal.gov.pl/wss/ext/PowiatoweBazyEwidencjiGruntow"
DEFAULT_SOURCE = "polish_parcel_wfs"
NS = {
    "gml": "http://www.opengis.net/gml/3.2",
    "gml31": "http://www.opengis.net/gml",
    "ms": "http://mapserver.gis.umn.edu/mapserver",
    "ows": "http://www.opengis.net/ows/1.1",
    "wfs": "http://www.opengis.net/wfs/2.0",
    "wfs11": "http://www.opengis.net/wfs",
    "xsd": "http://www.w3.org/2001/XMLSchema",
}


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def typename_local(typename: str) -> str:
    return typename.rsplit(":", 1)[-1]


def epsg2180_to_lonlat(x: float, y: float) -> tuple[float, float]:
    a = 6378137.0
    inv_f = 298.257222101
    f = 1.0 / inv_f
    e2 = 2 * f - f * f
    ep2 = e2 / (1 - e2)
    lon0 = math.radians(19.0)
    k0 = 0.9993
    x0 = -5300000.0
    y0 = 500000.0

    m = (x - x0) / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    lat1 = (
        mu
        + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
        + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
        + (151 * e1**3 / 96) * math.sin(6 * mu)
        + (1097 * e1**4 / 512) * math.sin(8 * mu)
    )
    n1 = a / math.sqrt(1 - e2 * math.sin(lat1) ** 2)
    r1 = a * (1 - e2) / (1 - e2 * math.sin(lat1) ** 2) ** 1.5
    t1 = math.tan(lat1) ** 2
    c1 = ep2 * math.cos(lat1) ** 2
    d = (y - y0) / (n1 * k0)

    lat = lat1 - (n1 * math.tan(lat1) / r1) * (
        d**2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * ep2) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * ep2 - 3 * c1**2) * d**6 / 720
    )
    lon = lon0 + (
        d
        - (1 + 2 * t1 + c1) * d**3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * ep2 + 24 * t1**2) * d**5 / 120
    ) / math.cos(lat1)
    return math.degrees(lon), math.degrees(lat)


def lonlat_to_epsg2180(lon: float, lat: float) -> tuple[float, float]:
    a = 6378137.0
    inv_f = 298.257222101
    f = 1.0 / inv_f
    e2 = 2 * f - f * f
    ep2 = e2 / (1 - e2)
    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    lon0 = math.radians(19.0)
    k0 = 0.9993
    x0 = -5300000.0
    y0 = 500000.0

    n = a / math.sqrt(1 - e2 * math.sin(lat_rad) ** 2)
    t = math.tan(lat_rad) ** 2
    c = ep2 * math.cos(lat_rad) ** 2
    aa = (lon_rad - lon0) * math.cos(lat_rad)
    m = a * (
        (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256) * lat_rad
        - (3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024) * math.sin(2 * lat_rad)
        + (15 * e2**2 / 256 + 45 * e2**3 / 1024) * math.sin(4 * lat_rad)
        - (35 * e2**3 / 3072) * math.sin(6 * lat_rad)
    )
    northing_x = x0 + k0 * (
        m
        + n
        * math.tan(lat_rad)
        * (
            aa**2 / 2
            + (5 - t + 9 * c + 4 * c**2) * aa**4 / 24
            + (61 - 58 * t + t**2 + 600 * c - 330 * ep2) * aa**6 / 720
        )
    )
    easting_y = y0 + k0 * n * (
        aa
        + (1 - t + c) * aa**3 / 6
        + (5 - 18 * t + t**2 + 72 * c - 58 * ep2) * aa**5 / 120
    )
    return northing_x, easting_y


def endpoint_url(args: argparse.Namespace) -> str:
    if args.endpoint_url:
        return args.endpoint_url
    if not args.county_code:
        raise ValueError("--county-code is required when --endpoint-url is not set")
    return f"{args.base_url.rstrip('/')}/{args.county_code}"


def request_text(url: str, timeout: int) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Codex Polish parcel WFS helper"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def open_wfs(url: str, timeout: int) -> BinaryIO:
    request = urllib.request.Request(url, headers={"User-Agent": "Codex Polish parcel WFS helper"})
    return urllib.request.urlopen(request, timeout=timeout)


def typename_parameter(version: str, requested: str) -> str:
    if requested != "auto":
        return requested
    return "TYPENAMES" if version.startswith("2.") else "TYPENAME"


def bbox_4326_to_srs(bbox_4326: str, target_srs: str) -> str:
    parts = [float(part.strip()) for part in bbox_4326.split(",")]
    if len(parts) != 4:
        raise ValueError("--bbox-4326 must be min_lon,min_lat,max_lon,max_lat")
    min_lon, min_lat, max_lon, max_lat = parts
    if min_lon >= max_lon or min_lat >= max_lat:
        raise ValueError("--bbox-4326 order must be min_lon,min_lat,max_lon,max_lat")
    if target_srs.upper().endswith("2180"):
        points = [
            lonlat_to_epsg2180(min_lon, min_lat),
            lonlat_to_epsg2180(min_lon, max_lat),
            lonlat_to_epsg2180(max_lon, min_lat),
            lonlat_to_epsg2180(max_lon, max_lat),
        ]
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        return f"{min(xs):.3f},{min(ys):.3f},{max(xs):.3f},{max(ys):.3f}"
    if target_srs.upper().endswith("4326") or target_srs.upper().endswith("CRS84"):
        return f"{min_lon},{min_lat},{max_lon},{max_lat}"
    raise ValueError(f"Cannot convert --bbox-4326 to {target_srs}")


def build_operation_url(endpoint: str, params: dict[str, str]) -> str:
    separator = "&" if "?" in endpoint else "?"
    return endpoint + separator + urllib.parse.urlencode(params)


def build_getfeature_url(args: argparse.Namespace, start_index: int) -> str:
    params = {
        "SERVICE": args.service,
        "VERSION": args.version,
        "REQUEST": "GetFeature",
        typename_parameter(args.version, args.typename_param): args.typename,
        "COUNT": str(args.count),
        "STARTINDEX": str(start_index),
        "SRSNAME": args.srsname,
    }
    if args.output_format:
        params["OUTPUTFORMAT"] = args.output_format
    if args.property_name:
        params["PROPERTYNAME"] = args.property_name
    if args.sort_by:
        params["SORTBY"] = args.sort_by
    if args.cql_filter:
        params["CQL_FILTER"] = args.cql_filter
    if args.filter_xml:
        params["FILTER"] = args.filter_xml
    bbox = args.bbox
    if args.bbox_4326:
        bbox = bbox_4326_to_srs(args.bbox_4326, args.bbox_srs or args.srsname)
    if bbox:
        srs = args.bbox_srs or args.srsname
        params["BBOX"] = f"{bbox},{srs}"
    return build_operation_url(endpoint_url(args), params)


def build_capabilities_url(args: argparse.Namespace) -> str:
    return build_operation_url(endpoint_url(args), {"SERVICE": args.service, "REQUEST": "GetCapabilities"})


def build_describe_url(args: argparse.Namespace) -> str:
    params = {
        "SERVICE": args.service,
        "VERSION": args.version,
        "REQUEST": "DescribeFeatureType",
        "TYPENAME": args.typename,
    }
    return build_operation_url(endpoint_url(args), params)


def text_of_first(node: ET.Element, paths: Iterable[str]) -> str | None:
    for path in paths:
        child = node.find(path, NS)
        if child is not None and child.text:
            return child.text.strip()
    return None


def capability_layers(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    layers = []
    for feature_type in root.findall(".//wfs:FeatureType", NS) + root.findall(".//wfs11:FeatureType", NS):
        name = text_of_first(feature_type, ["wfs:Name", "wfs11:Name"])
        if not name:
            continue
        bbox_node = feature_type.find("ows:WGS84BoundingBox", NS)
        bbox = None
        if bbox_node is not None:
            lower = text_of_first(bbox_node, ["ows:LowerCorner"])
            upper = text_of_first(bbox_node, ["ows:UpperCorner"])
            if lower and upper:
                bbox = {"lower": lower, "upper": upper}
        default_crs = text_of_first(feature_type, ["wfs:DefaultCRS", "wfs11:SRS"])
        other_crs = [
            child.text.strip()
            for child in feature_type.findall("wfs:OtherCRS", NS) + feature_type.findall("wfs11:OtherSRS", NS)
            if child.text and child.text.strip()
        ]
        layers.append(
            {
                "name": name,
                "title": text_of_first(feature_type, ["wfs:Title", "wfs11:Title"]),
                "default_crs": default_crs,
                "other_crs": other_crs,
                "wgs84_bbox": bbox,
            }
        )
    return layers


def describe_fields(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    fields = []
    for element in root.findall(".//xsd:element", NS):
        name = element.attrib.get("name")
        if not name:
            continue
        fields.append(
            {
                "name": name,
                "type": element.attrib.get("type"),
                "min_occurs": element.attrib.get("minOccurs"),
                "max_occurs": element.attrib.get("maxOccurs"),
            }
        )
    return fields


def child_text(node: ET.Element, field_name: str | None) -> str | None:
    if not field_name:
        return None
    for child in list(node):
        if local_name(child.tag) == field_name and child.text:
            value = child.text.strip()
            return value or None
    return None


def detect_feature_nodes(root: ET.Element, typename: str) -> list[ET.Element]:
    wanted = typename_local(typename)
    direct = [node for node in root.iter() if local_name(node.tag) == wanted]
    if direct:
        return direct
    members = [node for node in root.iter() if local_name(node.tag) in {"member", "featureMember"}]
    result = []
    for member in members:
        for child in list(member):
            if child.tag and not local_name(child.tag).startswith("boundedBy"):
                result.append(child)
    return result


def number_returned(root: ET.Element) -> int | None:
    for key in ("numberReturned", "numberOfFeatures"):
        raw = root.attrib.get(key)
        if raw is None:
            continue
        try:
            return int(raw)
        except ValueError:
            return None
    return None


def coordinate_order(srsname: str, xy_order: str) -> str:
    if xy_order != "auto":
        return xy_order
    upper = srsname.upper()
    if upper.endswith("4326") and "CRS84" not in upper:
        return "yx"
    return "xy"


def convert_coord(first: float, second: float, srsname: str, xy_order: str) -> tuple[float, float]:
    order = coordinate_order(srsname, xy_order)
    x, y = (first, second) if order == "xy" else (second, first)
    upper = srsname.upper()
    if upper.endswith("2180"):
        return epsg2180_to_lonlat(x, y)
    if upper.endswith("4326") or upper.endswith("CRS84"):
        return x, y
    raise ValueError(f"Unsupported SRSNAME for coordinate conversion: {srsname}")


def parse_poslist(text: str, srsname: str, xy_order: str, dimension: int = 2) -> list[list[float]]:
    values = [float(part) for part in text.split()]
    if len(values) % dimension:
        raise ValueError("gml:posList coordinate count is not divisible by srsDimension")
    ring = []
    for index in range(0, len(values), dimension):
        lon, lat = convert_coord(values[index], values[index + 1], srsname, xy_order)
        ring.append([lon, lat])
    return ring


def parse_coordinates(text: str, srsname: str, xy_order: str) -> list[list[float]]:
    ring = []
    for pair in text.split():
        first, second, *_rest = [float(part) for part in pair.split(",")]
        lon, lat = convert_coord(first, second, srsname, xy_order)
        ring.append([lon, lat])
    return ring


def ring_srs(ring_node: ET.Element, fallback: str) -> str:
    node: ET.Element | None = ring_node
    while node is not None:
        srs = node.attrib.get("srsName")
        if srs:
            return srs
        node = None
    return fallback


def polygon_rings(polygon: ET.Element, fallback_srs: str, xy_order: str) -> list[list[list[float]]]:
    srsname = polygon.attrib.get("srsName") or fallback_srs
    rings = []
    for ring_node in [node for node in polygon.iter() if local_name(node.tag) == "LinearRing"]:
        pos_list = next((child for child in ring_node if local_name(child.tag) == "posList"), None)
        if pos_list is not None and pos_list.text:
            dimension = int(pos_list.attrib.get("srsDimension") or polygon.attrib.get("srsDimension") or 2)
            rings.append(parse_poslist(pos_list.text, ring_srs(ring_node, srsname), xy_order, dimension))
            continue
        coords = next((child for child in ring_node if local_name(child.tag) == "coordinates"), None)
        if coords is not None and coords.text:
            rings.append(parse_coordinates(coords.text, ring_srs(ring_node, srsname), xy_order))
    return rings


def feature_geometry(feature: ET.Element, srsname: str, xy_order: str = "auto") -> dict:
    polygons = []
    for polygon in [node for node in feature.iter() if local_name(node.tag) == "Polygon"]:
        rings = polygon_rings(polygon, srsname, xy_order)
        if rings:
            polygons.append(rings)
    if not polygons:
        raise ValueError("feature has no supported GML Polygon geometry")
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def feature_to_parcel(feature: ET.Element, args: argparse.Namespace) -> dict | None:
    parcel_id = child_text(feature, args.parcel_id_field)
    if not parcel_id:
        return None
    commune = child_text(feature, args.commune_field)
    if args.commune_filter and commune != args.commune_filter:
        return None
    geometry = feature_geometry(feature, args.srsname, args.xy_order)
    stats = layer2.geometry_stats(geometry)
    return {
        "parcel_id": parcel_id,
        "parcel_number": child_text(feature, args.parcel_number_field),
        "voivodeship": child_text(feature, args.voivodeship_field),
        "county": child_text(feature, args.county_field),
        "commune": commune,
        "precinct": child_text(feature, args.precinct_field) or child_text(feature, args.precinct_code_field),
        "geometry": geometry,
        **stats,
    }


def parse_features(xml_text: str, srsname: str, commune_filter: str | None = None) -> tuple[list[dict], int | None]:
    args = argparse.Namespace(
        typename="ms:dzialki",
        parcel_id_field="ID_DZIALKI",
        parcel_number_field="NUMER_DZIALKI",
        voivodeship_field=None,
        county_field=None,
        commune_field="NAZWA_GMINY",
        precinct_field="NAZWA_OBREBU",
        precinct_code_field="NUMER_OBREBU",
        commune_filter=commune_filter,
        srsname=srsname,
        xy_order="auto",
    )
    return parse_features_with_args(xml_text, args)


def parse_features_with_args(xml_text: str, args: argparse.Namespace) -> tuple[list[dict], int | None]:
    root = ET.fromstring(xml_text)
    parcels = []
    for feature in detect_feature_nodes(root, args.typename):
        parcel = feature_to_parcel(feature, args)
        if parcel:
            parcels.append(parcel)
    return parcels, number_returned(root)


def stream_parcels(source: BinaryIO, args: argparse.Namespace):
    wanted = typename_local(args.typename)
    for _event, elem in ET.iterparse(source, events=("end",)):
        if local_name(elem.tag) != wanted:
            continue
        parcel = feature_to_parcel(elem, args)
        if parcel:
            yield parcel
        elem.clear()


def run_fetch(connection, args: argparse.Namespace) -> dict:
    layer2.ensure_layer2_tables(connection)
    start = args.startindex
    pages = fetched = inserted_or_updated = skipped = errors = 0
    error_examples: list[str] = []
    while True:
        if args.max_pages and pages >= args.max_pages:
            break
        if args.limit and fetched >= args.limit:
            break
        url = build_getfeature_url(args, start)
        try:
            xml_text = request_text(url, args.timeout)
            parcels, returned = parse_features_with_args(xml_text, args)
        except Exception as exc:
            errors += 1
            if len(error_examples) < 3:
                error_examples.append(f"startindex={start}: {exc}")
            break
        if args.limit:
            parcels = parcels[: max(0, args.limit - fetched)]
        for parcel in parcels:
            before = connection.total_changes
            layer2.upsert_parcel(connection, parcel)
            if connection.total_changes > before:
                inserted_or_updated += 1
            else:
                skipped += 1
        pages += 1
        fetched += len(parcels)
        print(
            "PROGRESS "
            + json.dumps(
                {
                    "stage": "polish_parcel_wfs_fetch",
                    "county_code": args.county_code,
                    "typename": args.typename,
                    "startindex": start,
                    "returned": len(parcels),
                    "number_returned": returned,
                    "fetched": fetched,
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
        if not parcels or returned == 0 or len(parcels) < args.count:
            break
        start += args.count
    return summary(args, pages, fetched, inserted_or_updated, skipped, errors, error_examples)


def run_stream_fetch(connection, args: argparse.Namespace) -> dict:
    layer2.ensure_layer2_tables(connection)
    fetched = inserted_or_updated = skipped = errors = 0
    error_examples: list[str] = []
    try:
        with open_wfs(build_getfeature_url(args, args.startindex), args.timeout) as response:
            for parcel in stream_parcels(response, args):
                if args.limit and fetched >= args.limit:
                    break
                before = connection.total_changes
                layer2.upsert_parcel(connection, parcel)
                if connection.total_changes > before:
                    inserted_or_updated += 1
                else:
                    skipped += 1
                fetched += 1
                if args.progress_every and fetched % args.progress_every == 0:
                    print(
                        "PROGRESS "
                        + json.dumps(
                            {
                                "stage": "polish_parcel_wfs_stream",
                                "county_code": args.county_code,
                                "typename": args.typename,
                                "fetched": fetched,
                                "inserted_or_updated": inserted_or_updated,
                            },
                            ensure_ascii=True,
                            sort_keys=True,
                        ),
                        file=sys.stderr,
                        flush=True,
                    )
    except Exception as exc:
        errors += 1
        error_examples.append(str(exc))
    return summary(args, 1 if fetched else 0, fetched, inserted_or_updated, skipped, errors, error_examples[:3], stream=True)


def summary(
    args: argparse.Namespace,
    pages: int,
    fetched: int,
    inserted_or_updated: int,
    skipped: int,
    errors: int,
    error_examples: list[str],
    stream: bool = False,
) -> dict:
    return {
        "source": args.source,
        "county_code": args.county_code,
        "endpoint_url": args.endpoint_url,
        "service": args.service,
        "version": args.version,
        "typename": args.typename,
        "srsname": args.srsname,
        "bbox": args.bbox,
        "bbox_4326": args.bbox_4326,
        "cql_filter": args.cql_filter,
        "commune_filter": args.commune_filter,
        "startindex": args.startindex,
        "count": args.count,
        "pages": pages,
        "stream": stream,
        "fetched_features": fetched,
        "inserted_or_updated": inserted_or_updated,
        "skipped_unchanged": skipped,
        "errors": errors,
        "error_examples": error_examples,
        "units_check": "WFS geometries are stored as lon/lat degrees; EPSG:2180 input coordinates are converted before canonical persistence.",
    }


def run_capabilities(args: argparse.Namespace) -> dict:
    url = build_capabilities_url(args)
    layers = capability_layers(request_text(url, args.timeout))
    return {"endpoint": endpoint_url(args), "operation": "GetCapabilities", "feature_types": layers}


def run_schema(args: argparse.Namespace) -> dict:
    url = build_describe_url(args)
    fields = describe_fields(request_text(url, args.timeout))
    return {"endpoint": endpoint_url(args), "operation": "DescribeFeatureType", "typename": args.typename, "fields": fields}


def run_probe(args: argparse.Namespace) -> dict:
    capabilities = run_capabilities(args)
    schema = run_schema(args)
    sample_url = build_getfeature_url(args, args.startindex)
    try:
        sample_xml = request_text(sample_url, args.timeout)
        parcels, returned = parse_features_with_args(sample_xml, args)
        sample = {
            "sample_returned": returned,
            "sample_parsed": len(parcels),
            "sample_parcel_ids": [parcel["parcel_id"] for parcel in parcels[:5]],
        }
    except Exception as exc:
        sample = {"sample_error": str(exc)}
    return {"capabilities": capabilities, "schema": schema, "sample": sample}


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mode", choices=["fetch", "capabilities", "schema", "probe", "url"], default="fetch")
    parser.add_argument("--county-code", help="Geoportal PowiatoweBazyEwidencjiGruntow path segment, e.g. 2216.")
    parser.add_argument("--endpoint-url", help="Full WFS endpoint URL. When set, --county-code is only recorded in output.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--service", default="WFS")
    parser.add_argument("--version", default="2.0.0")
    parser.add_argument("--typename", "--typenames", dest="typename", default="ms:dzialki")
    parser.add_argument("--typename-param", choices=["auto", "TYPENAME", "TYPENAMES"], default="auto")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--startindex", type=int, default=0)
    parser.add_argument("--srsname", default="EPSG:2180")
    parser.add_argument("--xy-order", choices=["auto", "xy", "yx"], default="auto")
    parser.add_argument("--bbox", help="Native WFS bbox in --bbox-srs coordinates, without trailing CRS.")
    parser.add_argument("--bbox-4326", help="Convenience bbox as min_lon,min_lat,max_lon,max_lat.")
    parser.add_argument("--bbox-srs", help="CRS for --bbox. Defaults to --srsname.")
    parser.add_argument("--cql-filter", help="Optional vendor CQL_FILTER.")
    parser.add_argument("--filter-xml", help="Optional raw OGC FILTER XML.")
    parser.add_argument("--property-name", help="Optional WFS PROPERTYNAME list.")
    parser.add_argument("--sort-by", help="Optional WFS SORTBY.")
    parser.add_argument("--output-format", help="Optional WFS OUTPUTFORMAT.")
    parser.add_argument("--parcel-id-field", default="ID_DZIALKI")
    parser.add_argument("--parcel-number-field", default="NUMER_DZIALKI")
    parser.add_argument("--voivodeship-field")
    parser.add_argument("--county-field")
    parser.add_argument("--commune-field", default="NAZWA_GMINY")
    parser.add_argument("--precinct-field", default="NAZWA_OBREBU")
    parser.add_argument("--precinct-code-field", default="NUMER_OBREBU")
    parser.add_argument("--commune-filter", help="Optional client-side exact filter on the configured commune field.")
    parser.add_argument("--db-path", default=str(DEFAULT_ANALYSIS_DB_PATH))
    parser.add_argument("--canon-db-path", default=str(DEFAULT_CANON_DB_PATH))
    parser.add_argument("--max-pages", type=int, default=0, help="Safety cap; 0 means no page cap.")
    parser.add_argument("--limit", type=int, default=0, help="Optional total feature cap; 0 means no cap.")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--stream", action="store_true", help="Stream one GetFeature response instead of page-by-page XML reads.")
    parser.add_argument("--progress-every", type=int, default=500)


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.service.upper() != "WFS":
        parser.error("--service must be WFS")
    if args.count <= 0:
        parser.error("--count must be positive")
    if args.mode in {"fetch", "probe", "url"} and not (
        args.srsname.upper().endswith("2180") or args.srsname.upper().endswith("4326") or args.srsname.upper().endswith("CRS84")
    ):
        parser.error("Only EPSG:2180, EPSG:4326, or CRS84 output geometries are currently supported.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover and fetch Polish cadastral parcel WFS features into canonical SQLite tables.")
    add_arguments(parser)
    args = parser.parse_args()
    validate_args(parser, args)

    if args.mode == "capabilities":
        result = run_capabilities(args)
    elif args.mode == "schema":
        result = run_schema(args)
    elif args.mode == "probe":
        result = run_probe(args)
    elif args.mode == "url":
        result = {"url": build_getfeature_url(args, args.startindex)}
    else:
        connection = connect_workspace(args.db_path, args.canon_db_path)
        try:
            result = run_stream_fetch(connection, args) if args.stream else run_fetch(connection, args)
        finally:
            connection.close()
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 1 if result.get("errors") and not result.get("fetched_features") else 0


if __name__ == "__main__":
    raise SystemExit(main())
