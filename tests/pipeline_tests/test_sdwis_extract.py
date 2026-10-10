"""Tests for src/pipeline/sdwis_extract.py on a tiny synthetic SDWIS*.tab inside a zip."""
import gzip
import json
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src" / "pipeline"))

import sdwis_extract as se  # noqa: E402

HEADER = [
    "Regulating Agency", "Water System Number", "Water System Name", "System Status",
    "Federal Water System Classification", "State Water System Classification", "Principal County Served",
    "Population Served", "Service Connections", "PS Code", "Sampling Point Name", "Facility Type",
    "Facility Status", "Sample Type", "Sample Date", "Sample Time", "Analysis Date", "ELAP Cert#", "Lab Name",
    "Analyte Code", "Analyte Name", "Result", "Counting Error", "Units of Measure",
    "Less Than Reporting Level", "Reporting Level", "DLR", "MCL", "Method",
]
assert len(HEADER) == 29


def row(**kw) -> str:
    """One data line; defaults mimic the real file, including the trailing-space padding."""
    base = {
        "Regulating Agency": "DISTRICT 17 - SANTA CLARA", "Water System Number": "CA4300001   ",
        "Water System Name": "TEST MUTUAL", "System Status": "A", "Federal Water System Classification": "C   ",
        "State Water System Classification": "C   ", "Principal County Served": "SANTA CLARA",
        "Population Served": "100", "Service Connections": "30", "PS Code": "CA4300001_001_001",
        "Sampling Point Name": "WELL 01", "Facility Type": "WL", "Facility Status": "A", "Sample Type": "RT",
        "Sample Date": "01-15-2024", "Sample Time": "10:00:00", "Analysis Date": "01-20-2024",
        "ELAP Cert#": "1180", "Lab Name": "SOME LAB", "Analyte Code": "1005", "Analyte Name": "ARSENIC     ",
        "Result": "3.5             ", "Counting Error": "", "Units of Measure": "UG/L     ",
        "Less Than Reporting Level": "N", "Reporting Level": "2.000000000", "DLR": "2", "MCL": "10",
        "Method": "EPA 200.8                     ",
    }
    base.update(kw)
    return "\t".join(base[c] for c in HEADER)


ROWS_A = [
    # CA4300001 arsenic: detected at well 1, ND (flag Y, blank result) at well 2, below-DLR at a DS point
    row(),
    row(**{"PS Code": "CA4300001_002_002", "Sampling Point Name": "WELL 02", "Sample Date": "03-10-2025",
           "Result": "", "Less Than Reporting Level": "Y"}),
    row(**{"PS Code": "CA4300001_DST_801", "Sampling Point Name": "DBPR Sample", "Facility Type": "DS",
           "Sample Date": "06-01-2025", "Result": "1.0"}),
    # nitrate (as N), detected
    row(**{"Analyte Code": "1040", "Analyte Name": "NITRATE", "Result": "4.2", "Units of Measure": "MG/L     ",
           "DLR": ".4", "MCL": "10", "Sample Date": "02-02-2024"}),
    # unmapped analyte with a '<'-prefixed result (never seen in the real data, rule is defensive)
    row(**{"Analyte Code": "2942", "Analyte Name": "BROMOFORM", "Result": "<0.5", "DLR": "1", "MCL": "",
           "Sample Date": "02-02-2024"}),
    # lab pH and field pH -> both canonical ph
    row(**{"Analyte Code": "1925", "Analyte Name": "PH", "Result": "7.8", "Units of Measure": "PH",
           "DLR": "", "MCL": "", "Sample Date": "02-02-2024"}),
    row(**{"Analyte Code": "C253", "Analyte Name": "PH, FIELD", "Result": "7.9", "Units of Measure": "PH",
           "DLR": "", "MCL": "", "Sample Date": "02-03-2024"}),
    # a system that is not in the boundaries file: must be dropped
    row(**{"Water System Number": "CA9999999   ", "Water System Name": "ELSEWHERE", "Result": "9"}),
]
ROWS_B = [
    # CA4300002 lead at a tap, detected; and a malformed line (extra tab inside Sampling Point Name)
    row(**{"Water System Number": "CA4300002", "Water System Name": "SECOND WATER CO", "Analyte Code": "1030",
           "Analyte Name": "LEAD", "Result": "6", "DLR": "5", "MCL": "", "Facility Type": "DS",
           "PS Code": "CA4300002_DST_901", "Sample Date": "01-05-2026"}),
    row(**{"Water System Number": "CA4300002", "Water System Name": "SECOND WATER CO",
           "Sampling Point Name": "WELL\tBROKEN", "Sample Date": "02-05-2026"}),
]

CATALOGUE = {
    "groups": {"inorganic": "Inorganic", "secondary": "Secondary", "lead_copper": "Lead and copper"},
    "analytes": [
        {"id": "arsenic", "name": "Arsenic", "group": "inorganic", "unit": "ppb", "aliases": ["Arsenic"]},
        {"id": "nitrate_n", "name": "Nitrate (as N)", "group": "inorganic", "unit": "ppm", "aliases": []},
        {"id": "ph", "name": "pH", "group": "secondary", "unit": "pH", "aliases": ["pH"]},
        {"id": "lead", "name": "Lead", "group": "lead_copper", "unit": "ppb", "aliases": ["Lead"]},
    ],
}
ANALYTE_MAP = {
    "analytes": [
        {"storet": "1005", "raw_name": "ARSENIC", "unit": "UG/L", "id": "arsenic"},
        {"storet": "1040", "raw_name": "NITRATE", "unit": "MG/L", "id": "nitrate_n"},
        {"storet": "2942", "raw_name": "BROMOFORM", "unit": "UG/L", "id": None},
        {"storet": "1925", "raw_name": "PH", "unit": "PH", "id": "ph"},
        {"storet": "C253", "raw_name": "PH, FIELD", "unit": "PH", "id": "ph"},
        {"storet": "1030", "raw_name": "LEAD", "unit": "UG/L", "id": "lead"},
    ]
}
BOUNDARIES = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"WATER_SYSTEM_NUMBER": p, "WATER_SYSTEM_NAME": n}, "geometry": None}
        for p, n in [("CA4300001", "TEST MUTUAL"), ("CA4300002", "SECOND WATER CO"), ("CA4300003", "NO DATA WATER")]
    ],
}


def make_zip(path: Path, member: str, lines: list[str]) -> Path:
    body = "\r\n".join(["\t".join(HEADER)] + lines) + "\r\n"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(member, body.encode("cp1252"))
    return path


@pytest.fixture
def inputs(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    zips = [make_zip(raw / "SDWIS4.zip", "SDWIS4.tab", ROWS_A), make_zip(raw / "SDWIS5.zip", "SDWIS5.tab", ROWS_B)]
    boundaries = tmp_path / "boundaries.geojson"
    boundaries.write_text(json.dumps(BOUNDARIES), encoding="utf-8")
    catalogue = tmp_path / "analytes.yaml"
    catalogue.write_text(yaml.safe_dump(CATALOGUE), encoding="utf-8")
    amap = tmp_path / "sdwis_analytes.yaml"
    amap.write_text(yaml.safe_dump(ANALYTE_MAP), encoding="utf-8")
    return {"zips": zips, "boundaries": boundaries, "catalogue": catalogue, "amap": amap, "out": tmp_path / "out"}


def run(inputs, amap=None):
    return se.extract(inputs["zips"], inputs["boundaries"], inputs["catalogue"], amap or inputs["amap"],
                      inputs["out"], generated="2026-10-10")


# ----------------------------------------------------------------------------- unit rules
def test_parse_sample_date_accepts_file_and_dictionary_formats():
    assert se.parse_sample_date("09-03-2024") == "2024-09-03"
    assert se.parse_sample_date("2024-09-03") == "2024-09-03"
    assert se.parse_sample_date("") is None
    assert se.parse_sample_date("13-40-2024") is None


@pytest.mark.parametrize(
    "result, flag, dlr, expected",
    [
        ("3.5", "N", "2", (3.5, True, False)),       # plain detection
        ("", "Y", "2", (None, False, False)),        # the file's way of saying non-detect
        ("1.5", "Y", "", (1.5, False, False)),       # flagged Y even with a number: not detected
        ("<0.5", "N", "", (None, False, False)),     # '<' prefix
        ("0", "N", "", (0.0, False, False)),         # zero result
        ("1.0", "N", "2", (1.0, False, True)),       # numeric but below the DLR
        ("2", "N", "2", (2.0, True, False)),         # at the DLR counts as detected
        ("7.8", "N", "", (7.8, True, False)),        # no DLR column (pH)
        ("abc", "N", "", (None, False, False)),      # unparseable
    ],
)
def test_classify_result(result, flag, dlr, expected):
    assert se.classify_result(result, flag, dlr) == expected


# ----------------------------------------------------------------------------- end to end
def test_filter_and_tsv(inputs):
    res = run(inputs)
    with gzip.open(inputs["out"] / "santa_clara_rows.tsv.gz", "rt", encoding="utf-8", newline="") as fh:
        lines = fh.read().split("\n")
    assert lines[-1] == ""
    lines = lines[:-1]
    assert lines[0] == "\t".join(HEADER)
    assert len(lines) == 1 + 7 + 2  # header + kept rows from A (8 minus the foreign system) + 2 from B
    assert not any("CA9999999" in ln for ln in lines)
    assert lines[1] == ROWS_A[0]  # original columns and padding preserved verbatim
    assert all(ln.count("\t") == 28 for ln in lines[:-1])  # the malformed line is last and has 29 tabs
    assert lines[-1].count("\t") == 29

    src = res["source"]
    assert src["rows_total"] == len(ROWS_A) + len(ROWS_B)
    assert src["rows_kept"] == 9
    assert src["rows_kept_malformed"] == 1
    assert [f["rows_total"] for f in src["files"]] == [8, 2]
    assert [f["rows_kept"] for f in src["files"]] == [7, 2]
    assert src["files"][0]["member"] == "SDWIS4.tab"
    assert src["pwsids_targeted"] == 3
    assert src["pwsids_with_rows"] == 2
    assert src["pwsids_without_rows"] == ["CA4300003"]
    assert src["kept_sample_date_range"] == ["2024-01-15", "2026-01-05"]
    assert res["generated"] == "2026-10-10"
    # the json on disk matches what extract() returned
    on_disk = json.loads((inputs["out"] / "santa_clara_results.json").read_text(encoding="utf-8"))
    assert on_disk == res


def test_aggregation(inputs):
    res = run(inputs)
    systems = res["systems"]
    assert sorted(systems) == ["CA4300001", "CA4300002", "CA4300003"]
    assert systems["CA4300003"] == {"name": "NO DATA WATER", "n_rows": 0, "latest_sample": None,
                                    "sampling_points": 0, "point_types": {}, "analytes": {}}

    s1 = systems["CA4300001"]
    assert s1["n_rows"] == 7
    assert s1["latest_sample"] == "2025-06-01"
    assert s1["sampling_points"] == 3
    assert s1["point_types"] == {"DS": 1, "WL": 6}
    assert sorted(s1["analytes"]) == ["BROMOFORM", "arsenic", "nitrate_n", "ph"]

    ars = s1["analytes"]["arsenic"]
    assert ars["id"] == "arsenic" and ars["raw_name"] == "ARSENIC" and ars["storet"] == "1005"
    assert ars["unit"] == "UG/L"
    assert ars["n"] == 3 and ars["n_detected"] == 1 and ars["n_below_dlr"] == 1
    assert ars["max"] == 3.5 and ars["mean_detected"] == 3.5
    assert ars["latest_date"] == "2025-06-01" and ars["latest_value"] is None  # latest row was below the DLR
    assert ars["sampling_points"] == 3
    assert ars["point_types"] == {"DS": 1, "WL": 2}
    assert ars["dlr"] == "2" and ars["mcl"] == "10"

    nit = s1["analytes"]["nitrate_n"]
    assert nit["unit"] == "MG/L" and nit["latest_value"] == 4.2 and nit["n_detected"] == 1

    bro = s1["analytes"]["BROMOFORM"]
    assert bro["id"] is None and bro["raw_name"] == "BROMOFORM" and bro["storet"] == "2942"
    assert bro["n"] == 1 and bro["n_detected"] == 0 and bro["latest_value"] is None and bro["max"] is None
    assert bro["mean_detected"] is None and bro["mcl"] is None

    ph = s1["analytes"]["ph"]
    assert ph["n"] == 2 and ph["n_detected"] == 2
    assert ph["storet"] in {"1925", "C253"} and ph["other_storets"] == [c for c in ["1925", "C253"] if c != ph["storet"]]
    assert ph["latest_date"] == "2024-02-03" and ph["latest_value"] == 7.9 and ph["max"] == 7.9
    assert ph["mean_detected"] == 7.85

    s2 = systems["CA4300002"]
    assert s2["n_rows"] == 1  # the malformed row is excluded from aggregates
    assert s2["latest_sample"] == "2026-01-05"
    assert s2["analytes"]["lead"]["latest_value"] == 6.0 and s2["analytes"]["lead"]["point_types"] == {"DS": 1}


def test_unit_label_override(inputs, tmp_path):
    amap = dict(ANALYTE_MAP)
    amap["analytes"] = [dict(e) for e in ANALYTE_MAP["analytes"]]
    nitrate = next(e for e in amap["analytes"] if e["storet"] == "1040")
    nitrate["unit_label"] = "MG/L as NO3"
    p = tmp_path / "map_no3.yaml"
    p.write_text(yaml.safe_dump(amap), encoding="utf-8")
    res = run(inputs, amap=p)
    assert res["systems"]["CA4300001"]["analytes"]["nitrate_n"]["unit"] == "MG/L as NO3"


def test_name_fallback_when_storet_unlisted(inputs, tmp_path):
    amap = {"analytes": [{"storet": "9999", "raw_name": "ARSENIC", "unit": "UG/L", "id": "arsenic"}]}
    p = tmp_path / "map_name.yaml"
    p.write_text(yaml.safe_dump(amap), encoding="utf-8")
    res = run(inputs, amap=p)
    a = res["systems"]["CA4300001"]["analytes"]
    assert "arsenic" in a and a["arsenic"]["storet"] == "1005"
    assert "NITRATE" in a and a["NITRATE"]["id"] is None  # unlisted code keeps its raw name


def test_unknown_catalogue_id_is_rejected(inputs, tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump({"analytes": [{"storet": "1005", "raw_name": "ARSENIC", "id": "not_real"}]}),
                 encoding="utf-8")
    with pytest.raises(SystemExit):
        run(inputs, amap=p)


def test_repo_analyte_map_is_consistent_with_catalogue():
    """The committed mapping must only use ids that exist in config/analytes.yaml."""
    catalogue = se.load_catalogue(REPO / "config" / "analytes.yaml")
    by_storet, _ = se.load_analyte_map(REPO / "src" / "pipeline" / "sdwis_analytes.yaml", catalogue)
    assert by_storet["1040"]["id"] == "nitrate_n"
    assert by_storet["1041"]["id"] == "nitrite_n"
    assert by_storet["1080"]["id"] == "chromium_6"
    assert by_storet["2943"]["id"] is None  # bromodichloromethane is not in the catalogue
