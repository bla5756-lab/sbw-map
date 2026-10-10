"""Tests for src/pipeline/sdwis_extract.py on a tiny synthetic SDWIS .tab inside a zip."""
from __future__ import annotations

import datetime as dt
import gzip
import json
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from pipeline import sdwis_extract as sx  # noqa: E402

HEADER = (
    "Regulating Agency\tWater System Number\tWater System Name\tSystem Status\t"
    "Federal Water System Classification\tState Water System Classification\tPrincipal County Served\t"
    "Population Served\tService Connections\tPS Code\tSampling Point Name\tFacility Type\tFacility Status\t"
    "Sample Type\tSample Date\tSample Time\tAnalysis Date\tELAP Cert#\tLab Name\tAnalyte Code\tAnalyte Name\t"
    "Result\tCounting Error\tUnits of Measure\tLess Than Reporting Level\tReporting Level\tDLR\tMCL\tMethod"
)


def row(pwsid, ps, point, ftype, date, code, name, result, unit, flag, dlr, mcl="", name_pad=True):
    """One data line in the real files' shape: padded PWSID / analyte name / unit, CRLF added later."""
    pad = (name + " " * 20) if name_pad else name
    return "\t".join([
        "DISTRICT 17 - SANTA CLARA", pwsid + "   ", "TEST SYSTEM " + pwsid[-1], "A", "C   ", "C   ", "SANTA CLARA",
        "100", "20", ps, point, ftype, "A", "RT", date, "10:00:00", date, "1234", "SOME LAB", code, pad,
        result, "", unit + "     ", flag, "1.000000000", dlr, mcl, "EPA 200.8",
    ])


ROWS_A = [
    # CA4300001: arsenic at two wells and one distribution point; one plain detect, one Y flag,
    # one N-flagged value below the DLR (the latest sample) -> latest_value must be null.
    row("CA4300001", "CA4300001_001_001", "WELL 01", "WL", "01-15-2024", "1005", "ARSENIC", "3.5", "UG/L", "N", "2", "10"),
    row("CA4300001", "CA4300001_002_002", "WELL 02", "WL", "03-10-2025", "1005", "ARSENIC", "", "UG/L", "Y", "2", "10"),
    row("CA4300001", "CA4300001_DST_801", "DBPR Sample", "DS", "06-01-2025", "1005", "ARSENIC", "1.0", "UG/L", "N", "2", "10"),
    # nitrate as N -> nitrate_n, unit kept as MG/L
    row("CA4300001", "CA4300001_001_001", "WELL 01", "WL", "01-15-2024", "1040", "NITRATE", "4.2", "MG/L", "N", ".4", "10"),
    # unmapped analyte with a '<' result -> non-detect, key is the raw name
    row("CA4300001", "CA4300001_001_001", "WELL 01", "WL", "01-15-2024", "2942", "BROMOFORM", "<0.5", "UG/L", "N", "1"),
    # laboratory pH and field pH both fold into ph
    row("CA4300001", "CA4300001_001_001", "WELL 01", "WL", "02-01-2024", "1925", "PH", "7.8", "PH", "N", ""),
    row("CA4300001", "CA4300001_001_001", "WELL 01", "WL", "02-02-2024", "C253", "PH, FIELD", "7.9", "PH", "N", ""),
    # a system that is not a target
    row("CA9999999", "CA9999999_001_001", "WELL 01", "WL", "01-15-2024", "1005", "ARSENIC", "9", "UG/L", "N", "2", "10"),
]
ROWS_B = [
    # second file: lead for CA4300002 (padding on the PWSID is stripped for matching)
    row("CA4300002", "CA4300002_DST_801", "LCR Tap Sample", "DS", "02-20-2026", "1030", "LEAD", "6", "UG/L", "N", "5", ""),
    # a malformed line: an extra tab inside the sampling point name -> written, counted, not aggregated
    row("CA4300002", "CA4300002_DST_801", "LCR\tTap Sample", "DS", "02-21-2026", "1030", "LEAD", "7", "UG/L", "N", "5", ""),
]

CATALOGUE = {
    "groups": {"inorganic": "Inorganic"},
    "analytes": [
        {"id": "arsenic", "name": "Arsenic", "group": "inorganic", "unit": "ppb", "threshold": 10, "threshold_type": "MCL", "aliases": ["Arsenic"]},
        {"id": "nitrate_n", "name": "Nitrate (as N)", "group": "inorganic", "unit": "ppm", "threshold": 10, "threshold_type": "MCL", "aliases": ["Nitrate as N"]},
        {"id": "ph", "name": "pH", "group": "inorganic", "unit": "pH", "threshold": 8.5, "threshold_low": 6.5, "threshold_type": "RANGE", "aliases": ["pH"]},
        {"id": "lead", "name": "Lead", "group": "inorganic", "unit": "ppb", "threshold": 15, "threshold_type": "AL", "aliases": ["Lead"]},
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
        for p, n in [("CA4300001", "ONE"), ("CA4300002", "TWO"), ("CA4300003", "THREE (no rows)")]
    ],
}


def make_zip(path: Path, member: str, rows: list[str]) -> Path:
    text = "\r\n".join([HEADER, *rows]) + "\r\n"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(zipfile.ZipInfo(member, date_time=(2026, 7, 23, 5, 10, 16)), text.encode("cp1252"))
    return path


@pytest.fixture()
def inputs(tmp_path: Path) -> dict:
    raw = tmp_path / "raw"
    raw.mkdir()
    zips = [make_zip(raw / "SDWIS4.zip", "SDWIS4.tab", ROWS_A), make_zip(raw / "SDWIS5.zip", "SDWIS5.tab", ROWS_B)]
    cat = tmp_path / "analytes.yaml"
    cat.write_text(yaml.safe_dump(CATALOGUE), encoding="utf-8")
    amap = tmp_path / "sdwis_analytes.yaml"
    amap.write_text(yaml.safe_dump(ANALYTE_MAP), encoding="utf-8")
    bnd = tmp_path / "boundaries.geojson"
    bnd.write_text(json.dumps(BOUNDARIES), encoding="utf-8")
    return {"zips": zips, "cat": cat, "amap": amap, "bnd": bnd, "out": tmp_path / "out"}


def run(inputs: dict, amap: Path | None = None) -> dict:
    return sx.extract(inputs["zips"], inputs["bnd"], inputs["cat"], amap or inputs["amap"], inputs["out"],
                      today=dt.date(2026, 10, 10))


# ----------------------------------------------------------------------------- unit-level

@pytest.mark.parametrize("result, flag, dlr, detected, kind", [
    ("3.5", "N", "2", True, "detected"),
    ("", "Y", "2", False, "flag"),
    ("5", "Y", "2", False, "flag"),          # the flag wins even when a number is present
    ("", "N", "2", False, "blank"),
    ("<0.5", "N", "", False, "lt_prefix"),
    ("ND", "N", "", False, "nonnumeric"),
    ("0", "N", "", False, "nonpositive"),
    ("-0.2", "N", "1", False, "nonpositive"),
    ("1.0", "N", "2", False, "below_dlr"),
    ("2", "N", "2", True, "detected"),       # at the DLR counts as detected
    ("1.0", "N", "", True, "detected"),      # no DLR -> nothing to compare against
    ("1.0", "N", ".5", True, "detected"),
])
def test_classify_result(result, flag, dlr, detected, kind):
    c = sx.classify_result(result, flag, dlr)
    assert (c.detected, c.kind) == (detected, kind)
    assert (c.value is not None) == detected


def test_nearest_rank_percentile():
    assert sx.nearest_rank_percentile([], 90) is None
    assert sx.nearest_rank_percentile([5.0], 90) == 5.0
    assert sx.nearest_rank_percentile([2.0, 1.0], 90) == 2.0            # ceil(1.8) = rank 2
    assert sx.nearest_rank_percentile(list(range(1, 11)), 90) == 9       # ceil(9.0) = rank 9 of 10
    assert sx.nearest_rank_percentile([float(v) for v in range(20, 0, -1)], 90) == 18.0   # unsorted input
    assert sx.nearest_rank_percentile([1.0, 2.0, 3.0], 100) == 3.0


@pytest.mark.parametrize("ftype, cls", [
    ("DS", "distribution"), ("TP", "distribution"), ("SS", "distribution"),
    ("WL", "source"), ("IN", "source"), ("SP", "source"), ("CH", "source"), ("OT", "source"), ("CC", "source"),
    ("", "source"), ("ZZ", "source"), (" ds ", "distribution"),
])
def test_point_class(ftype, cls):
    assert sx.point_class(ftype) == cls


def test_parse_sample_date():
    assert sx.parse_sample_date("09-03-2024") == "2024-09-03"
    assert sx.parse_sample_date("2024-09-03") == "2024-09-03"
    assert sx.parse_sample_date("13-40-2024") is None
    assert sx.parse_sample_date("") is None


def test_analyte_map_rejects_unknown_id(inputs: dict, tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump({"analytes": [{"storet": "1005", "raw_name": "ARSENIC", "id": "arsenik"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="arsenik"):
        run(inputs, amap=bad)


# ----------------------------------------------------------------------------- end to end

def test_filter_writes_only_target_rows_with_original_columns(inputs: dict):
    run(inputs)
    with gzip.open(inputs["out"] / "santa_clara_rows.tsv.gz", "rt", encoding="utf-8", newline="\n") as fh:
        lines = fh.read().split("\n")
    assert lines[-1] == ""
    lines = lines[:-1]
    assert lines[0] == HEADER
    kept = lines[1:]
    assert len(kept) == 9                      # 7 target rows from file A + 2 from file B
    assert all(not line.startswith("DISTRICT 17 - SANTA CLARA\tCA9999999") for line in kept)
    assert kept[0] == ROWS_A[0]                # padding and column text untouched
    assert sum(1 for line in kept if line.count("\t") == HEADER.count("\t") + 1) == 1  # the malformed line is kept


def test_source_block(inputs: dict):
    doc = run(inputs)
    assert doc["generated"] == "2026-10-10"
    s = doc["source"]
    assert [f["rows_total"] for f in s["files"]] == [8, 2]
    assert [f["rows_kept"] for f in s["files"]] == [7, 2]
    assert [f["rows_malformed"] for f in s["files"]] == [0, 1]
    assert s["files"][0]["member"] == "SDWIS4.tab"
    assert s["files"][0]["member_modified"] == "2026-07-23T05:10:16"
    assert s["files"][0]["url"].endswith("/edtlibrary/SDWIS4.zip")
    assert s["files"][0]["kept_date_range"] == ["2024-01-15", "2025-06-01"]
    assert len(s["files"][0]["zip_sha256"]) == 64
    assert s["columns"] == HEADER.split("\t")
    assert s["rows_kept"] == 9
    assert s["targets"] == {"pwsids": 3, "with_rows": 2, "without_rows": ["CA4300003"]}
    assert s["result_kinds"] == {"below_dlr": 1, "detected": 5, "flag": 1, "lt_prefix": 1}
    assert s["unlisted_in_map"] == []
    assert s["sample_types"] == {"RT": 8}


def test_aggregation(inputs: dict):
    doc = run(inputs)
    with open(inputs["out"] / "santa_clara_results.json", encoding="utf-8") as fh:
        assert json.load(fh) == doc
    systems = doc["systems"]
    assert list(systems) == ["CA4300001", "CA4300002", "CA4300003"]

    one = systems["CA4300001"]
    assert one["n_rows"] == 7
    assert one["name"] == "TEST SYSTEM 1"
    assert one["latest_sample"] == "2025-06-01"
    assert one["sampling_points"] == 3
    assert one["point_types"] == {"DS": 1, "WL": 6}
    assert set(one["analytes"]) == {"arsenic", "nitrate_n", "ph", "BROMOFORM"}

    ars = one["analytes"]["arsenic"]
    assert ars["id"] == "arsenic" and ars["storet"] == "1005" and ars["raw_name"] == "ARSENIC"
    assert ars["unit"] == "UG/L"
    assert ars["n"] == 3 and ars["n_detected"] == 1 and ars["n_below_dlr"] == 1
    assert ars["max"] == 3.5 and ars["mean_detected"] == 3.5
    assert ars["p90"] == 3.5 and ars["p90_n"] == 1              # one detection -> that value
    assert ars["latest_date"] == "2025-06-01" and ars["latest_value"] is None   # latest row was below the DLR
    assert ars["sampling_points"] == 3
    assert ars["point_types"] == {"DS": 1, "WL": 2}
    assert ars["dlr"] == "2" and ars["mcl"] == "10"

    nit = one["analytes"]["nitrate_n"]
    assert nit["unit"] == "MG/L" and nit["latest_value"] == 4.2 and nit["n_detected"] == 1

    brm = one["analytes"]["BROMOFORM"]
    assert brm["id"] is None and brm["raw_name"] == "BROMOFORM" and brm["storet"] == "2942"
    assert brm["n"] == 1 and brm["n_detected"] == 0 and brm["max"] is None and brm["mean_detected"] is None
    assert brm["latest_value"] is None
    assert brm["p90"] is None and brm["p90_n"] == 0            # no detections -> null

    ph = one["analytes"]["ph"]
    assert ph["n"] == 2 and ph["n_detected"] == 2
    assert ph["latest_date"] == "2024-02-02" and ph["latest_value"] == 7.9 and ph["max"] == 7.9
    assert ph["mean_detected"] == 7.85
    assert ph["p90"] == 7.9 and ph["p90_n"] == 2               # rank ceil(0.9 * 2) = 2 -> the larger value
    assert ph["other_storets"] == {"C253": "PH, FIELD"}   # 1925 is the more frequent... tie -> first seen

    two = systems["CA4300002"]
    assert two["n_rows"] == 1                       # the malformed line is not aggregated
    assert two["analytes"]["lead"]["latest_value"] == 6.0
    assert two["analytes"]["lead"]["point_types"] == {"DS": 1}

    three = systems["CA4300003"]
    assert three == {"name": "THREE (no rows)", "n_rows": 0, "latest_sample": None, "sampling_points": 0,
                     "point_types": {}, "analytes": {}}


def test_by_class_split(inputs: dict):
    """Each analyte carries the same statistics split into distribution (DS/TP/SS) and source rows."""
    doc = run(inputs)
    one = doc["systems"]["CA4300001"]["analytes"]
    ars = one["arsenic"]["by_class"]
    assert set(ars) == {"source", "distribution"}
    assert ars["source"] == {"n": 2, "n_detected": 1, "latest_date": "2025-03-10", "latest_value": None,
                             "max": 3.5, "mean_detected": 3.5, "p90": 3.5, "p90_n": 1, "sampling_points": 2}
    assert ars["distribution"] == {"n": 1, "n_detected": 0, "latest_date": "2025-06-01", "latest_value": None,
                                   "max": None, "mean_detected": None, "p90": None, "p90_n": 0, "sampling_points": 1}
    # the split adds up to the top level and the top-level fields are unchanged
    assert ars["source"]["n"] + ars["distribution"]["n"] == one["arsenic"]["n"] == 3
    assert one["arsenic"]["max"] == 3.5 and one["arsenic"]["latest_date"] == "2025-06-01"
    assert set(one["nitrate_n"]["by_class"]) == {"source"}             # wells only
    lead = doc["systems"]["CA4300002"]["analytes"]["lead"]["by_class"]
    assert set(lead) == {"distribution"} and lead["distribution"]["latest_value"] == 6.0


def test_unit_label_override(inputs: dict, tmp_path: Path):
    """A nitrate code reported as NO3 would carry unit_label; the extract reports that label, not MG/L."""
    amap = dict(ANALYTE_MAP)
    amap["analytes"] = [dict(e, unit_label="MG/L as NO3") if e["storet"] == "1040" else e for e in ANALYTE_MAP["analytes"]]
    path = tmp_path / "map_no3.yaml"
    path.write_text(yaml.safe_dump(amap), encoding="utf-8")
    doc = run(inputs, amap=path)
    nit = doc["systems"]["CA4300001"]["analytes"]["nitrate_n"]
    assert nit["unit"] == "MG/L as NO3" and nit["latest_value"] == 4.2


def test_header_mismatch_raises(inputs: dict, tmp_path: Path):
    other = tmp_path / "raw" / "other.zip"
    with zipfile.ZipFile(other, "w") as zf:
        zf.writestr("other.tab", "Water System Number\tResult\r\nCA4300001\t1\r\n")
    with pytest.raises(ValueError, match="header"):
        sx.extract([inputs["zips"][0], other], inputs["bnd"], inputs["cat"], inputs["amap"], inputs["out"])
