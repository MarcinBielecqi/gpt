import importlib.util
import sqlite3
from pathlib import Path


def load_rcn_module():
    path = Path("skills/poland-rcn-wfs/scripts/fetch_rcn_wfs.py")
    spec = importlib.util.spec_from_file_location("fetch_rcn_wfs", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_layer2_module():
    path = Path("skills/uldk-parcel-grid/scripts/probe_uldk_parcels.py")
    spec = importlib.util.spec_from_file_location("probe_uldk_parcels", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample_xml(price="120000", area="0.1200", gml_id="dzialki.1"):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0"
                       xmlns:ms="http://mapserver.gis.umn.edu/mapserver"
                       xmlns:gml="http://www.opengis.net/gml/3.2"
                       numberReturned="1">
  <wfs:member>
    <ms:dzialki gml:id="{gml_id}">
      <ms:teryt>0213</ms:teryt>
      <ms:tran_rodzaj_trans>wolnyRynek</ms:tran_rodzaj_trans>
      <ms:tran_rodzaj_rynku>prywatny</ms:tran_rodzaj_rynku>
      <ms:tran_sprzedajacy>osobaFizyczna</ms:tran_sprzedajacy>
      <ms:tran_kupujacy>osobaFizyczna</ms:tran_kupujacy>
      <ms:dok_data>2025-01-15</ms:dok_data>
      <ms:nier_rodzaj>nieruchomoscGruntowaNiezabudowana</ms:nier_rodzaj>
      <ms:nier_prawo>wlasnoscNieruchomosciGruntowej</ms:nier_prawo>
      <ms:dzi_id_dzialki>021302_5.0001.1</ms:dzi_id_dzialki>
      <ms:dzi_nr_dzialki>1</ms:dzi_nr_dzialki>
      <ms:dzi_pow_ewid>{area}</ms:dzi_pow_ewid>
      <ms:dzi_cena_brutto>{price}</ms:dzi_cena_brutto>
      <ms:dzi_adres>Gmina Test</ms:dzi_adres>
    </ms:dzialki>
  </wfs:member>
</wfs:FeatureCollection>"""


def test_layer3_table_and_upsert_deduplicate_per_run():
    rcn = load_rcn_module()
    connection = sqlite3.connect(":memory:")
    rcn.ensure_layer3_tables(connection)
    records, returned = rcn.parse_records(sample_xml())

    assert returned == 1
    assert len(records) == 1
    changed = rcn.upsert_records(connection, records, "demo", "16.0,50.0,16.1,50.1", {"q": 1}, "2026-01-01T00:00:00+00:00")
    changed_again = rcn.upsert_records(connection, records, "demo", "16.0,50.0,16.1,50.1", {"q": 1}, "2026-01-02T00:00:00+00:00")

    row = connection.execute("SELECT COUNT(*), ROUND(AVG(price_per_m2), 2), MAX(fetched_at) FROM canon_rcn_price_observations").fetchone()
    assert changed == 1
    assert changed_again == 1
    assert row == (1, 100.0, "2026-01-02T00:00:00+00:00")


def test_layer3_same_source_record_can_be_used_for_multiple_runs():
    rcn = load_rcn_module()
    connection = sqlite3.connect(":memory:")
    records, _ = rcn.parse_records(sample_xml())

    rcn.upsert_records(connection, records, "run_a", None, {}, "2026-01-01T00:00:00+00:00")
    rcn.upsert_records(connection, records, "run_b", None, {}, "2026-01-01T00:00:00+00:00")

    assert connection.execute("SELECT COUNT(*) FROM canon_rcn_price_observations").fetchone()[0] == 2


def test_layer3_from_layer2_bbox_uses_linked_parcels():
    rcn = load_rcn_module()
    layer2 = load_layer2_module()
    connection = sqlite3.connect(":memory:")
    layer2.ensure_layer2_tables(connection)
    parcel = layer2.parse_uldk_response(
        "0\n"
        "021302_5.0001.1|1|Gmina Test|Powiat Test|Dolnoslaskie|src|"
        "POLYGON((16.0 50.0,16.01 50.0,16.01 50.01,16.0 50.0))"
    )
    layer2.upsert_parcel(connection, parcel)
    layer2.link_layer2_run_parcel(connection, "demo", parcel["parcel_id"], 1, "16.0,50.0,16.01,50.01")

    bbox = rcn.layer2_bbox_4326(connection, "demo", 0)

    assert bbox == "16.0,50.0,16.01,50.01"


def test_layer3_summary_reports_price_statistics():
    rcn = load_rcn_module()
    connection = sqlite3.connect(":memory:")
    records_a, _ = rcn.parse_records(sample_xml(price="120000", area="0.1200", gml_id="a"))
    records_b, _ = rcn.parse_records(sample_xml(price="240000", area="0.1200", gml_id="b"))
    rcn.upsert_records(connection, records_a + records_b, "demo", None, {}, "2026-01-01T00:00:00+00:00")

    summary = rcn.run_summary(connection, "demo")

    assert summary["rcn_records"] == 2
    assert summary["priced_records"] == 2
    assert summary["median_price_per_m2"] == 150.0


def test_layer3_can_store_inflation_adjusted_price_variant():
    rcn = load_rcn_module()
    connection = sqlite3.connect(":memory:")
    records, _ = rcn.parse_records(sample_xml(price="120000", area="0.1200", gml_id="a"))
    rcn.upsert_records(
        connection,
        records,
        "demo",
        None,
        {},
        "2026-01-01T00:00:00+00:00",
        {"2025": 100.0, "2026": 110.0},
        "2026",
    )

    row = connection.execute(
        """
        SELECT inflation_reference_year, ROUND(inflation_factor, 2),
               ROUND(inflation_adjusted_price_pln, 2),
               ROUND(inflation_adjusted_price_per_m2, 2)
        FROM canon_rcn_price_observations
        """
    ).fetchone()
    summary = rcn.run_summary(connection, "demo")

    assert row == ("2026", 1.1, 132000.0, 110.0)
    assert summary["median_inflation_adjusted_price_per_m2"] == 110.0
    assert summary["inflation_adjusted_priced_records"] == 1
