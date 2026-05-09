from pathlib import Path
import importlib.util


def load_module():
    p = Path("skills/osm_hotspot_grid/scripts/build_hotspot_grid.py")
    spec = importlib.util.spec_from_file_location("hotspot_grid", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_bbox():
    m = load_module()
    assert m.parse_bbox("50.0,16.0,51.0,17.0") == (50.0, 16.0, 51.0, 17.0)


def test_parse_osm_types():
    m = load_module()
    out = m.parse_osm_types("amenity=restaurant,tourism=attraction")
    assert out[0] == ("amenity", "restaurant", "amenity_restaurant")
    assert out[1] == ("tourism", "attraction", "tourism_attraction")


def test_bbox_area_positive():
    m = load_module()
    area = m.bbox_area_m2(50.0, 16.0, 50.01, 16.01)
    assert area > 0


def test_assign_points_to_cell():
    m = load_module()
    cell = m.Cell(50.0, 16.0, 50.1, 16.1, 0)
    pts = [{"lat": 50.05, "lon": 16.05}, {"lat": 51.0, "lon": 16.05}]
    out = m.assign_points_to_cell(pts, cell)
    assert len(out) == 1


def test_split_cell():
    m = load_module()
    children = m.split_cell(m.Cell(50.0, 16.0, 50.2, 16.2, 0))
    assert len(children) == 4
    assert all(c.depth == 1 for c in children)


def test_score_cell():
    m = load_module()
    s = m.score_cell(4, 20.0)
    assert s > 0


def test_dedupe_points():
    m = load_module()
    pts = [
        {"lat": 50.0, "lon": 16.0},
        {"lat": 50.00001, "lon": 16.00001},
        {"lat": 50.01, "lon": 16.01},
    ]
    out = m.dedupe_points(pts, 5.0, 50.0)
    assert len(out) <= len(pts)


def test_write_geojson(tmp_path):
    m = load_module()
    cells = [
        {
            "id": "x",
            "category": "amenity_restaurant",
            "tag_key": "amenity",
            "tag_value": "restaurant",
            "bbox": {"min_lat": 50.0, "min_lon": 16.0, "max_lat": 50.1, "max_lon": 16.1},
            "point_count": 3,
            "area_m2": 1000.0,
            "density_per_km2": 10.0,
            "score": 5.0,
            "depth": 2,
            "points": [{"name": "a", "osm_id": 1}],
        }
    ]
    out = tmp_path / "a.geojson"
    m.write_geojson(out, cells)
    assert out.exists()


def test_repair_text():
    m = load_module()
    assert m.overpass_core.repair_text("SrebrnogÃ³rski") == "Srebrnogórski"
    assert m.overpass_core.repair_text("DonÅ¼on") == "Donżon"
