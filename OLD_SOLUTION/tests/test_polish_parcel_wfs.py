import importlib.util
from pathlib import Path

PARCEL_DIR = Path(__file__).resolve().parents[1]


def load_module(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, PARCEL_DIR / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_epsg2180_inverse_matches_existing_forward_conversion():
    wfs = load_module("skills/polish-parcel-wfs/scripts/fetch_polish_parcel_wfs.py", "polish_wfs")
    rcn = load_module("skills/poland-rcn-wfs/scripts/fetch_rcn_wfs.py", "rcn_wfs")

    for lon, lat in [(19.0, 52.0), (16.9, 54.0), (23.0, 50.0)]:
        x, y = rcn.lonlat_to_epsg2180(lon, lat)
        converted_lon, converted_lat = wfs.epsg2180_to_lonlat(x, y)

        assert abs(converted_lon - lon) < 0.000001
        assert abs(converted_lat - lat) < 0.000001


def test_parse_features_converts_wfs_meter_coordinates_to_existing_lonlat_units():
    wfs = load_module("skills/polish-parcel-wfs/scripts/fetch_polish_parcel_wfs.py", "polish_wfs_parse")
    rcn = load_module("skills/poland-rcn-wfs/scripts/fetch_rcn_wfs.py", "rcn_wfs_parse")
    lonlat_ring = [
        (19.0, 52.0),
        (19.001, 52.0),
        (19.001, 52.001),
        (19.0, 52.0),
    ]
    coords_2180 = []
    for lon, lat in lonlat_ring:
        x, y = rcn.lonlat_to_epsg2180(lon, lat)
        coords_2180.extend([f"{x:.6f}", f"{y:.6f}"])
    pos_list = " ".join(coords_2180)
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0"
    xmlns:gml="http://www.opengis.net/gml/3.2"
    xmlns:ms="http://mapserver.gis.umn.edu/mapserver"
    numberReturned="1">
  <wfs:member>
    <ms:dzialki>
      <ms:msGeometry>
        <gml:Polygon srsName="urn:ogc:def:crs:EPSG::2180">
          <gml:exterior>
            <gml:LinearRing>
              <gml:posList srsDimension="2">{pos_list}</gml:posList>
            </gml:LinearRing>
          </gml:exterior>
        </gml:Polygon>
      </ms:msGeometry>
      <ms:ID_DZIALKI>TEST.1</ms:ID_DZIALKI>
      <ms:NUMER_DZIALKI>1</ms:NUMER_DZIALKI>
      <ms:NAZWA_OBREBU>Obreb Test</ms:NAZWA_OBREBU>
      <ms:NAZWA_GMINY>Gmina Test</ms:NAZWA_GMINY>
    </ms:dzialki>
  </wfs:member>
</wfs:FeatureCollection>
"""

    parcels, returned = wfs.parse_features(xml, "EPSG:2180")

    assert returned == 1
    assert parcels[0]["parcel_id"] == "TEST.1"
    assert parcels[0]["parcel_number"] == "1"
    assert parcels[0]["commune"] == "Gmina Test"
    assert parcels[0]["precinct"] == "Obreb Test"
    ring = parcels[0]["geometry"]["coordinates"][0]
    assert abs(ring[0][0] - 19.0) < 0.000001
    assert abs(ring[0][1] - 52.0) < 0.000001
    assert 14.0 < parcels[0]["bbox_min_lon"] < 24.5
    assert 49.0 < parcels[0]["bbox_min_lat"] < 55.5
    assert parcels[0]["area_m2"] > 0
