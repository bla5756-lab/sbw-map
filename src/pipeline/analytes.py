"""Analyte catalogue: loading, name matching, and unit conversion.

Low-level helpers (load, match, convert) that build_site composes. Nothing here guesses: a value
whose unit cannot be converted to the catalogue unit is returned as None by `convert`.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

MASS_UNITS = {  # factor to ppb (ug/L)
    "ppt": 1e-3, "ng/l": 1e-3, "ppb": 1.0, "ug/l": 1.0, "µg/l": 1.0, "μg/l": 1.0, "mcg/l": 1.0,
    "ppm": 1e3, "mg/l": 1e3,
}
ALIAS_UNITS = {
    "micromhos/cm": "us/cm", "micromho/cm": "us/cm", "microsiemens/cm": "us/cm", "mmhos/cm": "us/cm",
    "umhos/cm": "us/cm", "µmhos/cm": "us/cm", "μmhos/cm": "us/cm", "umho/cm": "us/cm", "µs/cm": "us/cm",
    "μs/cm": "us/cm", "us/cm": "us/cm", "pci/l": "pci/l", "ntu": "ntu", "mfl": "mfl", "%": "%",
    "percent": "%", "ph": "ph", "ph units": "ph", "units": "units", "color units": "units", "cu": "units",
    "acu": "units", "pcu": "units", "apha": "units", "ton": "ton",
    "tons": "ton", "cfu/ml": "cfu/ml", "count": "count", "c": "c", "°c": "c", "deg c": "c",
    "us": "us/cm", "µmho/cm": "us/cm", "t.o.n.": "ton", "t.o.n": "ton", "color unit": "units", "color units": "units",
    "f": "f", "ºf": "f", "°f": "f", "deg f": "f", "degrees f": "f", "grains/gal": "gpg", "grains/gallon": "gpg", "gpg": "gpg", "gr/gal": "gpg",
}
GPG_TO_PPM = 17.1
NO3_TO_N = 4.43


def load_catalogue(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        cat = yaml.safe_load(fh)
    cat["by_id"] = {a["id"]: a for a in cat["analytes"]}
    index = {}
    for a in cat["analytes"]:
        for alias in [a["name"], a["id"]] + list(a.get("aliases", [])):
            index[_key(alias)] = a["id"]
    cat["alias_index"] = index
    return cat


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def match_analyte(cat: dict, raw_name: str | None) -> str | None:
    """Return the canonical id for a printed analyte name, or None."""
    if not raw_name:
        return None
    k = _key(raw_name)
    if k in cat["alias_index"]:
        return cat["alias_index"][k]
    # strip footnote markers and parentheticals, then retry
    stripped = re.sub(r"\(.*?\)|\[.*?\]|\*+|\d+$", "", raw_name).strip()
    k2 = _key(stripped)
    return cat["alias_index"].get(k2)


def normalize_unit(unit_raw: str | None, analyte_id: str | None = None) -> str | None:
    """Map a printed unit to a canonical token; None when the unit is unknown.

    Dimensionless analytes (pH) printed without a unit are accepted as their catalogue unit.
    """
    if unit_raw is None or not str(unit_raw).strip():
        return "ph" if analyte_id == "ph" else None
    u = str(unit_raw).strip().lower().replace("μ", "µ")
    u = re.sub(r"\s+", " ", u)
    if "no3" in u:
        return "ppm as no3" if u.startswith(("ppm", "mg")) else u
    if u in MASS_UNITS:
        return u
    if u in ALIAS_UNITS:
        return ALIAS_UNITS[u]
    if re.search(r"mho|µs/cm|us/cm|µs|microsiemens", u):
        return "us/cm"
    if u in ("cu", "acu", "pcu", "pt-co", "pt/co", "color unit", "color units", "apparent color units", "units (color)"):
        return "units"
    if u.startswith("ph") or u in ("unit", "std units", "s.u.", "su"):
        return "ph"
    if u in ("pci/l", "pc/l", "pci/liter"):
        return "pci/l"
    if u in ("ntu", "ntus"):
        return "ntu"
    if u.startswith("%") or u.startswith("percent"):
        return "%"
    if "grain" in u or u == "gpg":
        return "gpg"
    if u in ("ug/l", "µg/l", "mcg/l"):
        return "ppb"
    if u in ("mg/l",):
        return "ppm"
    if u in ("ng/l",):
        return "ppt"
    return u


def convert(value: float | None, unit_raw: str | None, target: str, analyte_id: str | None = None) -> float | None:
    """Convert `value` from the printed unit to the catalogue unit; None when not convertible."""
    if value is None:
        return None
    u = normalize_unit(unit_raw, analyte_id)
    t = target.lower()
    generic = u is None or u in ("unit", "units", "su", "std units", "standard units", "none")
    if generic and t in ("ph", "units", "ton", "ntu", "count"):
        return float(value)  # these are only ever printed in their own unit, or with none
    if u is None:
        return None
    if "%" in u and t == "%":
        return float(value)
    if u == t or ALIAS_UNITS.get(u) == t:
        return float(value)
    if u == "ppm as no3" and t == "ppm":
        return float(value) / NO3_TO_N
    if u in MASS_UNITS and t in MASS_UNITS:
        return float(value) * MASS_UNITS[u] / MASS_UNITS[t]
    if u == "gpg" and t == "ppm":
        return float(value) * GPG_TO_PPM
    if u == "f" and t == "c":
        return (float(value) - 32.0) * 5.0 / 9.0
    return None


def ratio_to_standard(analyte: dict, value: float | None, relative_max: float | None = None) -> float | None:
    """Fraction of the standard a value represents (1.0 = at the standard).

    RANGE standards (pH) return 0 inside the range and the distance outside it in range-widths.
    Analytes with no standard return value / relative_max when that is known.
    """
    if value is None:
        return None
    ttype = analyte.get("threshold_type")
    thr = analyte.get("threshold")
    if ttype == "RANGE":
        low, high = analyte.get("threshold_low"), thr
        width = (high - low) or 1.0
        if value < low:
            return (low - value) / width
        if value > high:
            return (value - high) / width
        return 0.0
    if ttype == "NONE" or thr in (None, 0):
        if ttype != "NONE" and thr == 0:
            return 1.0 if value > 0 else 0.0  # e.g. E. coli: any positive is at the standard
        if relative_max:
            return value / relative_max
        return None
    return value / thr
