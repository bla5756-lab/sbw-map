import json
from pathlib import Path

import pytest

from src.pipeline.build_site import build

ROOT = Path(__file__).resolve().parents[2]

SQUARE = {"type": "Polygon", "coordinates": [[[-122, 37], [-121.9, 37], [-121.9, 37.1], [-122, 37.1], [-122, 37]]]}


def feature(pwsid, name, classification="COMMUNITY", pop=1000):
    return {"type": "Feature", "geometry": SQUARE, "properties": {
        "WATER_SYSTEM_NUMBER": pwsid, "WATER_SYSTEM_NAME": name, "FEDERAL_CLASSIFICATION": classification,
        "POPULATION": pop, "SERVICE_CONNECTIONS": 10, "ADDRESS_CITY_NAME": "X", "COUNTY": "SANTA CLARA"}}


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "config").mkdir(); (tmp_path / "data" / "ccr").mkdir(parents=True); (tmp_path / "data" / "sdwis").mkdir()
    (tmp_path / "config" / "analytes.yaml").write_text((ROOT / "config" / "analytes.yaml").read_text())
    gj = {"type": "FeatureCollection", "features": [
        feature("CA4300001", "FOUND SYSTEM"), feature("CA4300002", "MISSING SYSTEM"),
        feature("CA4300003", "PARK", classification="TRANSIENT NON-COMMUNITY"), feature("CA4300004", "ND SYSTEM")]}
    (tmp_path / "data" / "boundaries.geojson").write_text(json.dumps(gj))
    found = {"pwsid": "CA4300001", "system_name": "FOUND SYSTEM", "status": "found",
             "report": {"data_year": 2025, "title": "T", "url": "http://x", "file": "reports/CA4300001/ccr-2025.pdf", "format": "pdf", "sha256": "0", "pages": 2, "retrieved": "2026-10-10"},
             "sources_described": "wells", "search_log": [], "violations": ["late sample"],
             "analytes": [
                 {"id": "arsenic", "raw_name": "Arsenic", "unit_raw": "ppb", "value": 2.0, "value_basis": "average", "average": 2.0, "range_low": 1, "range_high": 3, "detected": True, "mcl_printed": "10", "phg_printed": "0.004", "source_group": "Groundwater", "sample_year": 2025, "page": 1, "notes": None},
                 {"id": "arsenic", "raw_name": "Arsenic", "unit_raw": "ppb", "value": 6.0, "value_basis": "average", "average": 6.0, "range_low": None, "range_high": None, "detected": True, "mcl_printed": "10", "phg_printed": "0.004", "source_group": "Surface", "sample_year": 2025, "page": 1, "notes": None},
                 {"id": None, "raw_name": "Nitrate (as NO3)", "unit_raw": "ppm as NO3", "value": 44.3, "value_basis": "average", "average": 44.3, "range_low": None, "range_high": None, "detected": True, "mcl_printed": "45", "phg_printed": "45", "source_group": None, "sample_year": 2025, "page": 1, "notes": None},
                 {"id": "pfos", "raw_name": "PFOS", "unit_raw": "ppb", "value": 0.0046, "value_basis": "average", "average": 0.0046, "range_low": None, "range_high": None, "detected": True, "mcl_printed": ".0040", "phg_printed": None, "source_group": None, "sample_year": 2024, "page": 2, "notes": None},
                 {"id": "ph", "raw_name": "pH", "unit_raw": None, "value": 9.5, "value_basis": "average", "average": 9.5, "range_low": None, "range_high": None, "detected": True, "mcl_printed": None, "phg_printed": None, "source_group": None, "sample_year": 2025, "page": 2, "notes": None},
                 {"id": "hardness", "raw_name": "Hardness", "unit_raw": "grains/gal", "value": 10, "value_basis": "average", "average": 10, "range_low": None, "range_high": None, "detected": True, "mcl_printed": None, "phg_printed": None, "source_group": None, "sample_year": 2025, "page": 2, "notes": None},
                 {"id": "selenium", "raw_name": "Selenium", "unit_raw": "furlongs", "value": 1, "value_basis": "average", "average": 1, "range_low": None, "range_high": None, "detected": True, "mcl_printed": None, "phg_printed": None, "source_group": None, "sample_year": 2025, "page": 2, "notes": None},
                 {"id": None, "raw_name": "Mystery compound", "unit_raw": "ppb", "value": 1, "value_basis": "average", "average": 1, "range_low": None, "range_high": None, "detected": True, "mcl_printed": None, "phg_printed": None, "source_group": None, "sample_year": 2025, "page": 2, "notes": None},
             ], "extraction_notes": None}
    nd = {"pwsid": "CA4300004", "system_name": "ND SYSTEM", "status": "found",
          "report": {"data_year": 2024, "title": "T", "url": "http://y", "file": "reports/CA4300004/ccr-2024.pdf", "format": "pdf", "sha256": "0", "pages": 1, "retrieved": "2026-10-10"},
          "sources_described": None, "search_log": [], "violations": [],
          "analytes": [{"id": "arsenic", "raw_name": "Arsenic", "unit_raw": "ppb", "value": None, "value_basis": None, "average": None, "range_low": None, "range_high": None, "detected": False, "mcl_printed": "10", "phg_printed": "0.004", "source_group": None, "sample_year": 2024, "page": 1, "notes": None}],
          "extraction_notes": None}
    missing = {"pwsid": "CA4300002", "system_name": "MISSING SYSTEM", "status": "not_found", "report": None, "search_log": ["searched"], "analytes": []}
    for d in (found, nd, missing):
        (tmp_path / "data" / "ccr" / f"{d['pwsid']}.json").write_text(json.dumps(d))
    return tmp_path


def test_build_statuses_and_values(repo):
    site, geo, warnings = build(repo)
    s = site["systems"]
    assert site["counts"] == {"systems": 4, "community": 3, "reports_found": 2, "reports_not_found": 1, "reports_not_required": 1, "lab_systems": 0}
    assert s["CA4300002"]["report"]["status"] == "not_found" and s["CA4300002"]["report"]["search_log"] == ["searched"]
    assert s["CA4300003"]["report"]["status"] == "not_required"
    found = s["CA4300001"]
    assert found["report"]["status"] == "found" and found["report"]["violations"] == ["late sample"]
    # multi-source: the highest column wins, both entries kept
    ars = found["values"]["arsenic"]
    assert ars["value"] == 6.0 and ars["source_group"] == "Surface" and len(ars["entries"]) == 2 and ars["ratio"] == pytest.approx(0.6)
    # nitrate as NO3 matched by alias-less raw name? it has id None but raw name maps nowhere -> other_rows
    assert any(r["raw_name"] == "Mystery compound" for r in found["other_rows"])
    # pfos ppb -> ppt ratio against the 4 ppt MCL
    assert found["values"]["pfos"]["value"] == pytest.approx(4.6) and found["values"]["pfos"]["ratio"] == pytest.approx(1.15)
    # pH with no unit printed still converts; 9.5 is half a range-width above 8.5
    assert found["values"]["ph"]["value"] == 9.5 and found["values"]["ph"]["ratio"] == pytest.approx(0.5)
    # hardness in grains/gal converts; NONE standard -> relative ratio of 1.0 (it is the max)
    assert found["values"]["hardness"]["value"] == pytest.approx(171) and found["values"]["hardness"]["ratio"] == 1.0
    # unconvertible unit is flagged, not guessed
    assert found["values"]["selenium"]["status"] == "unconverted" and found["values"]["selenium"]["value"] is None
    assert any("furlongs" in w for w in warnings)
    # ND is data with ratio 0
    assert s["CA4300004"]["values"]["arsenic"]["status"] == "nd" and s["CA4300004"]["values"]["arsenic"]["ratio"] == 0.0
    # geo carries fid + pwsid only, ids match
    assert [f["properties"]["pwsid"] for f in geo["features"]] == sorted(s)
    assert all(f["id"] == s[f["properties"]["pwsid"]]["fid"] for f in geo["features"])


def test_nitrate_as_no3_converts_when_id_given(repo):
    path = repo / "data" / "ccr" / "CA4300001.json"
    d = json.loads(path.read_text())
    for e in d["analytes"]:
        if e["raw_name"].startswith("Nitrate"):
            e["id"] = "nitrate_n"
    path.write_text(json.dumps(d))
    site, _, _ = build(repo)
    n = site["systems"]["CA4300001"]["values"]["nitrate_n"]
    assert n["value"] == pytest.approx(10.0, rel=1e-3) and n["ratio"] == pytest.approx(1.0, rel=1e-3)


def test_real_data_builds_without_unknown_pwsids():
    site, geo, warnings = build(ROOT)
    assert site["counts"]["systems"] == 120 and len(geo["features"]) == 120
    assert not [w for w in warnings if "not in boundaries" in w]
    for pwsid, s in site["systems"].items():
        assert s["report"]["status"] in {"found", "not_found", "not_required"}, pwsid
        if s["classification"] != "COMMUNITY":
            assert s["report"]["status"] in {"not_required", "found"}
        for aid, v in s["values"].items():
            assert v["status"] in {"value", "nd", "unconverted"}
            if v["status"] == "value":
                assert v["value"] is not None and v["value"] >= 0, (pwsid, aid)


REPO = Path(__file__).resolve().parents[2]


def _entry(aid, raw, value, basis, unit="ppb", **kw):
    e = {"id": aid, "raw_name": raw, "unit_raw": unit, "value": value, "value_basis": basis, "average": None,
         "range_low": None, "range_high": None, "detected": value is not None, "mcl_printed": None, "phg_printed": None,
         "source_group": None, "sample_year": 2025, "page": 1, "notes": None}
    e.update(kw)
    return e


def test_average_beats_worst_single_result():
    from src.pipeline.analytes import load_catalogue
    from src.pipeline.build_site import select_value
    cat = load_catalogue(REPO / "config" / "analytes.yaml")
    a = cat["by_id"]["pfos"]
    entries = [_entry("pfos", "PFOS", 3.2, "average", "ppt"), _entry("pfos", "PFOS", 6.2, "range_high", "ppt")]
    out = select_value(a, entries, "X", [])
    assert out["value"] == 3.2 and out["basis_tier"] == 1 and out["status"] == "value"


def test_range_high_used_only_when_no_representative_figure():
    from src.pipeline.analytes import load_catalogue
    from src.pipeline.build_site import select_value
    cat = load_catalogue(REPO / "config" / "analytes.yaml")
    a = cat["by_id"]["nitrate_n"]
    entries = [_entry("nitrate_n", "Nitrate", None, None, "ppm", detected=True, range_low=0, range_high=7.8)]
    out = select_value(a, entries, "X", [])
    assert out["value"] == 7.8 and out["basis"] == "range_high" and out["basis_tier"] == 2


def test_lead_never_colored_by_a_single_tap_when_90th_percentile_exists():
    from src.pipeline.analytes import load_catalogue
    from src.pipeline.build_site import select_value
    cat = load_catalogue(REPO / "config" / "analytes.yaml")
    a = cat["by_id"]["lead"]
    entries = [_entry("lead", "Lead", 2.1, "90th percentile"), _entry("lead", "Lead", 810, "highest single result")]
    out = select_value(a, entries, "X", [])
    assert out["value"] == 2.1
    nd = [_entry("lead", "Lead", None, "90th percentile (printed < 5)", detected=True, range_low=0, range_high=41)]
    out = select_value(a, nd, "X", [])
    assert out["status"] == "nd" and out["value"] is None
