from pathlib import Path

import pytest

from src.pipeline.analytes import convert, load_catalogue, match_analyte, normalize_unit, ratio_to_standard

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def cat():
    return load_catalogue(ROOT / "config" / "analytes.yaml")


def test_catalogue_ids_unique_and_groups_known(cat):
    ids = [a["id"] for a in cat["analytes"]]
    assert len(ids) == len(set(ids))
    for a in cat["analytes"]:
        assert a["group"] in cat["groups"], a["id"]
        assert a["threshold_type"] in {"MCL", "AL", "MRDL", "SMCL", "TT", "NL", "RANGE", "NONE"}, a["id"]
        if a["threshold_type"] == "RANGE":
            assert a["threshold_low"] < a["threshold"]


@pytest.mark.parametrize("raw,expected", [
    ("Arsenic", "arsenic"), ("ARSENIC", "arsenic"), ("Nitrate (as N)", "nitrate_n"), ("Nitrate as nitrogen", "nitrate_n"),
    ("Hexavalent Chromium", "chromium_6"), ("Chromium (VI)", "chromium_6"), ("TTHM", "tthm"),
    ("Total Trihalomethanes", "tthm"), ("Haloacetic Acids (five)", "haa5"), ("Lead (90th percentile)", "lead"),
    ("pH", "ph"), ("Total Dissolved Solids (TDS)", "tds"), ("1,2,3-TCP", "tcp_123"), ("PFOS", "pfos"),
    ("Hardness (as CaCO3)", "hardness"), ("Arsenic*", "arsenic"), ("Fluoride (a)", "fluoride"), ("Unobtainium", None), (None, None),
])
def test_match_analyte(cat, raw, expected):
    assert match_analyte(cat, raw) == expected


@pytest.mark.parametrize("value,unit,target,expected", [
    (5, "ppb", "ppb", 5.0), (5, "ug/L", "ppb", 5.0), (0.005, "ppm", "ppb", 5.0), (0.005, "mg/L", "ppb", 5.0),
    (4.6, "ppt", "ppb", 0.0046), (0.0046, "ppb", "ppt", 4.6), (12, "ng/L", "ppt", 12.0),
    (44.3, "ppm as NO3", "ppm", 10.0), (44.3, "mg/L as NO3", "ppm", 10.0),
    (10, "grains/gal", "ppm", 171.0), (7.8, None, "pH", 7.8), (7.8, "units", "pH", 7.8), (7.8, "pH units", "pH", 7.8),
    (900, "umhos/cm", "uS/cm", 900.0), (900, "micromhos/cm", "uS/cm", 900.0), (900, "µS/cm", "uS/cm", 900.0),
    (3, "CU", "units", 3.0), (2.5, "% pos / month", "%", 2.5), (5, "pCi/L", "pCi/L", 5.0),
    (5, "furlongs", "ppb", None), (None, "ppb", "ppb", None), (5, "ppb", "pCi/L", None),
])
def test_convert(value, unit, target, expected):
    out = convert(value, unit, target)
    if expected is None:
        assert out is None
    else:
        assert out == pytest.approx(expected, rel=1e-3)


def test_normalize_unit_handles_micro_variants():
    assert normalize_unit("µg/L") in ("µg/l", "ppb")
    assert normalize_unit("μmhos/cm") == "us/cm"


def test_ratio_mcl_and_none_and_range():
    arsenic = {"threshold_type": "MCL", "threshold": 10}
    assert ratio_to_standard(arsenic, 5) == 0.5
    assert ratio_to_standard(arsenic, None) is None
    ph = {"threshold_type": "RANGE", "threshold_low": 6.5, "threshold": 8.5}
    assert ratio_to_standard(ph, 7.2) == 0
    assert ratio_to_standard(ph, 9.5) == pytest.approx(0.5)
    assert ratio_to_standard(ph, 5.5) == pytest.approx(0.5)
    hardness = {"threshold_type": "NONE", "threshold": None}
    assert ratio_to_standard(hardness, 50, relative_max=200) == 0.25
    assert ratio_to_standard(hardness, 50) is None
    ecoli = {"threshold_type": "MCL", "threshold": 0}
    assert ratio_to_standard(ecoli, 1) == 1.0 and ratio_to_standard(ecoli, 0) == 0.0
