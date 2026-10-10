"""State laboratory results (SDWIS / CLIP) for the Santa Clara County water systems.

Source: the State Water Board Division of Drinking Water "EDT library" files SDWIS4.zip
(2023-01-01 to 2025-12-31) and SDWIS5.zip (2026-01-01 to present), each holding one tab-delimited
.tab file of every laboratory result reported for every California public water system.

High level: `extract(zips, boundaries, catalogue, analyte_map, out_dir)` streams the .tab members
line by line (never loading a file into memory), keeps the rows whose Water System Number is one
of the PWSIDs in data/boundaries.geojson, and writes
  data/sdwis/santa_clara_rows.tsv.gz   every kept row, original columns, gzip
  data/sdwis/santa_clara_results.json  per-system, per-analyte aggregates (see `Aggregate`)
Mid level: `read_tab`, `classify_result`, `Aggregate`. The analyte-code mapping lives in
src/pipeline/sdwis_analytes.yaml; the catalogue in config/analytes.yaml.

Values and units are copied as the data gives them; nothing is converted (build_site converts).
Run against the downloaded files with an isolated interpreter:
  python3 -I src/pipeline/sdwis_extract.py raw/SDWIS4.zip raw/SDWIS5.zip
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import io
import json
import math
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Iterator, NamedTuple

import yaml

try:  # package use: from pipeline import sdwis_extract
    from .analytes import load_catalogue, match_analyte
except ImportError:  # script use: python3 -I src/pipeline/sdwis_extract.py ...
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from analytes import load_catalogue, match_analyte  # type: ignore

REPO_ROOT = Path(__file__).resolve().parents[2]
PAGE_URL = "https://www.waterboards.ca.gov/drinking_water/certlic/drinkingwater/EDTlibrary.html"
FILE_URL = "https://www.waterboards.ca.gov/drinking_water/certlic/drinkingwater/documents/edtlibrary/{name}"
DICTIONARY_URL = FILE_URL.format(name="data_dictionary.pdf")

# The files are cp1252: the only non-ASCII bytes seen are 0xA7 (section sign), 0x96 (en dash)
# and 0xA0 (no-break space), none of which decode as UTF-8.
ENCODING = "cp1252"

# Column names exactly as in the header row of SDWIS4.tab / SDWIS5.tab.
COL_PWSID = "Water System Number"
COL_SYSTEM_NAME = "Water System Name"
COL_PS_CODE = "PS Code"                      # <PWSID>_<facility id>_<sampling point>
COL_POINT_NAME = "Sampling Point Name"
COL_FACILITY_TYPE = "Facility Type"          # WL well, DS distribution, TP treatment plant, IN intake ...
COL_SAMPLE_TYPE = "Sample Type"
COL_SAMPLE_DATE = "Sample Date"              # MM-DD-YYYY in the files (the dictionary says YYYY-MM-DD)
COL_ANALYTE_CODE = "Analyte Code"
COL_ANALYTE_NAME = "Analyte Name"
COL_RESULT = "Result"
COL_UNIT = "Units of Measure"
COL_LESS_THAN = "Less Than Reporting Level"  # Y = non-detect, N = detection
COL_REPORTING_LEVEL = "Reporting Level"      # the laboratory's reporting level
COL_DLR = "DLR"                              # state detection limit for purposes of reporting
COL_MCL = "MCL"
# Sampling-point classes for the per-analyte "by_class" split. "distribution" is water as served or
# as it enters the system: DS distribution system (DBPR and LCR tap samples, distribution points),
# TP treatment-plant sampling points (plant effluents / entry points in these rows), CC consecutive
# connections (treated water purchased from a wholesaler, sampled at the turnout) and SS sampling
# stations. Everything else is "source": WL wells, IN intakes, SP springs, CH common headers (raw
# combined well water), OT other, and any code not listed here.
DISTRIBUTION_FACILITY_TYPES = frozenset({"DS", "TP", "CC", "SS"})
CLASS_DISTRIBUTION = "distribution"
CLASS_SOURCE = "source"

REQUIRED_COLUMNS = (
    COL_PWSID, COL_SYSTEM_NAME, COL_PS_CODE, COL_FACILITY_TYPE, COL_SAMPLE_DATE, COL_ANALYTE_CODE,
    COL_ANALYTE_NAME, COL_RESULT, COL_UNIT, COL_LESS_THAN, COL_DLR,
)

NON_DETECT_RULE = (
    "A row is a non-detect when any of: 'Less Than Reporting Level' is Y; Result is blank or not a "
    "number; Result starts with '<'; Result is <= 0; or DLR is numeric and Result < DLR (counted in "
    "n_below_dlr). Everything else is a detection. Values are as reported, never converted."
)

_DATE_MDY = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")
_DATE_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


# ----------------------------------------------------------------------------- low level

def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def parse_sample_date(raw: str) -> str | None:
    """'MM-DD-YYYY' (as in the files) or 'YYYY-MM-DD' (as in the dictionary) -> ISO date, else None."""
    s = raw.strip()
    m = _DATE_MDY.match(s)
    if m:
        mm, dd, yyyy = m.groups()
    else:
        m = _DATE_ISO.match(s)
        if not m:
            return None
        yyyy, mm, dd = m.groups()
    try:
        return dt.date(int(yyyy), int(mm), int(dd)).isoformat()
    except ValueError:
        return None


def parse_number(raw: str) -> float | None:
    s = raw.strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def nearest_rank_percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile: the value at rank ceil(pct/100 * n) of the sorted values; None if empty."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


class Classified(NamedTuple):
    value: float | None   # the numeric result when detected, else None
    detected: bool
    kind: str             # detected | flag | blank | lt_prefix | nonnumeric | nonpositive | below_dlr


def classify_result(result: str, less_than_flag: str, dlr: str) -> Classified:
    """Apply NON_DETECT_RULE to one row's Result / Less Than Reporting Level / DLR strings."""
    if less_than_flag.strip().upper() == "Y":
        return Classified(None, False, "flag")
    s = result.strip()
    if not s:
        return Classified(None, False, "blank")
    if s.startswith("<"):
        return Classified(None, False, "lt_prefix")
    value = parse_number(s)
    if value is None:
        return Classified(None, False, "nonnumeric")
    if value <= 0:
        return Classified(None, False, "nonpositive")
    dlr_value = parse_number(dlr)
    if dlr_value is not None and value < dlr_value:
        return Classified(None, False, "below_dlr")
    return Classified(value, True, "detected")


def point_class(facility_type: str) -> str:
    """'distribution' or 'source' for a Facility Type code (see DISTRIBUTION_FACILITY_TYPES)."""
    return CLASS_DISTRIBUTION if facility_type.strip().upper() in DISTRIBUTION_FACILITY_TYPES else CLASS_SOURCE


def read_tab(zip_path: Path) -> Iterator[tuple[zipfile.ZipInfo, str]]:
    """Yield (member, line) for every line of the single .tab member; line endings stripped.

    The archive is untrusted data: only the first member whose name ends in .tab is read, as text.
    """
    with zipfile.ZipFile(zip_path) as zf:
        members = [m for m in zf.infolist() if m.filename.lower().endswith(".tab") and not m.is_dir()]
        if not members:
            raise ValueError(f"{zip_path}: no .tab member")
        member = members[0]
        with zf.open(member) as raw:
            text = io.TextIOWrapper(raw, encoding=ENCODING, newline="")
            for line in text:
                yield member, line.rstrip("\r\n")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------------------- inputs

def load_target_pwsids(boundaries_path: Path) -> dict[str, str]:
    """PWSID -> system name for every feature of the boundaries GeoJSON."""
    with open(boundaries_path, encoding="utf-8") as fh:
        gj = json.load(fh)
    out = {}
    for f in gj["features"]:
        p = f["properties"]
        out[str(p["WATER_SYSTEM_NUMBER"]).strip()] = p.get("WATER_SYSTEM_NAME")
    return out


def load_analyte_map(path: Path, cat: dict) -> dict:
    """sdwis_analytes.yaml -> {'by_code': {storet: entry}, 'by_name': {key(raw_name): entry}}.

    Every non-null id must exist in the catalogue; otherwise ValueError.
    """
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    by_code, by_name, bad = {}, {}, []
    for e in doc.get("analytes") or []:
        if e.get("id") is not None and e["id"] not in cat["by_id"]:
            bad.append(f"{e.get('storet')} -> {e['id']}")
        code = str(e["storet"]).strip()
        by_code[code] = e
        if e.get("raw_name"):
            by_name[_key(str(e["raw_name"]))] = e
    if bad:
        raise ValueError(f"{path}: ids not in the catalogue: {', '.join(bad)}")
    return {"by_code": by_code, "by_name": by_name}


def resolve_analyte(amap: dict, cat: dict, code: str, raw_name: str) -> tuple[str | None, str | None, bool]:
    """-> (canonical id or None, unit_label override or None, listed-in-yaml?)."""
    e = amap["by_code"].get(code) or amap["by_name"].get(_key(raw_name))
    if e is not None:
        return e.get("id"), e.get("unit_label"), True
    return match_analyte(cat, raw_name), None, False


# ----------------------------------------------------------------------------- aggregation

class Stats:
    """Result statistics for a set of rows: the fields shared by an analyte and its class split."""

    __slots__ = ("n", "n_detected", "sum", "max", "detected_values", "latest_date", "latest_values", "points")

    def __init__(self) -> None:
        self.n = self.n_detected = 0
        self.sum = 0.0
        self.max: float | None = None
        self.detected_values: list[float] = []  # every detected result, for the 90th percentile
        self.latest_date: str | None = None
        self.latest_values: list[float] = []    # detected results dated latest_date
        self.points: set[str] = set()

    def add(self, c: Classified, date: str | None, ps_code: str) -> None:
        self.n += 1
        self.points.add(ps_code)
        if c.detected:
            self.n_detected += 1
            self.sum += c.value
            self.max = c.value if self.max is None else max(self.max, c.value)
            self.detected_values.append(c.value)
        if date is not None:
            if self.latest_date is None or date > self.latest_date:
                self.latest_date = date
                self.latest_values = [c.value] if c.detected else []
            elif date == self.latest_date and c.detected:
                self.latest_values.append(c.value)

    def to_json(self) -> dict:
        return {
            "n": self.n,
            "n_detected": self.n_detected,
            "latest_date": self.latest_date,
            "latest_value": max(self.latest_values) if self.latest_values else None,
            "max": self.max,
            "mean_detected": round(self.sum / self.n_detected, 6) if self.n_detected else None,
            # nearest-rank 90th percentile of the detected results (the statistic the lead/copper
            # action level applies to, though over all points and the whole period here)
            "p90": nearest_rank_percentile(self.detected_values, 90),
            "p90_n": len(self.detected_values),
            "sampling_points": len(self.points),
        }


class Aggregate:
    """One (system, analyte key) accumulator: overall Stats, a per-class split, and row metadata."""

    __slots__ = ("id", "total", "by_class", "n_below_dlr", "point_types", "codes", "names", "units", "dlrs", "mcls")

    def __init__(self, analyte_id: str | None) -> None:
        self.id = analyte_id
        self.total = Stats()
        self.by_class: dict[str, Stats] = {}
        self.n_below_dlr = 0
        self.point_types: Counter = Counter()
        self.codes: Counter = Counter()
        self.names: dict[str, str] = {}
        self.units: Counter = Counter()
        self.dlrs: Counter = Counter()
        self.mcls: Counter = Counter()

    def add(self, c: Classified, date: str | None, ps_code: str, facility_type: str, code: str,
            raw_name: str, unit: str, dlr: str, mcl: str) -> None:
        self.total.add(c, date, ps_code)
        cls = point_class(facility_type)
        sub = self.by_class.get(cls)
        if sub is None:
            sub = self.by_class[cls] = Stats()
        sub.add(c, date, ps_code)
        self.codes[code] += 1
        self.names.setdefault(code, raw_name)
        self.units[unit] += 1
        if dlr:
            self.dlrs[dlr] += 1
        if mcl:
            self.mcls[mcl] += 1
        self.point_types[facility_type or "?"] += 1
        if c.kind == "below_dlr":
            self.n_below_dlr += 1

    def to_json(self) -> dict:
        main_code = self.codes.most_common(1)[0][0]
        stats = self.total.to_json()
        out = {
            "id": self.id,
            "raw_name": self.names[main_code],
            "storet": main_code,
            "unit": self.units.most_common(1)[0][0],
            "n": stats["n"],
            "n_detected": stats["n_detected"],
            "n_below_dlr": self.n_below_dlr,
            "latest_date": stats["latest_date"],
            "latest_value": stats["latest_value"],
            "max": stats["max"],
            "mean_detected": stats["mean_detected"],
            "p90": stats["p90"],
            "p90_n": stats["p90_n"],
            "sampling_points": stats["sampling_points"],
            "point_types": dict(sorted(self.point_types.items())),
            "dlr": self.dlrs.most_common(1)[0][0] if self.dlrs else None,
            "mcl": self.mcls.most_common(1)[0][0] if self.mcls else None,
            # the same statistics split by sampling-point class; only classes with rows appear
            "by_class": {cls: self.by_class[cls].to_json() for cls in sorted(self.by_class)},
        }
        if len(self.codes) > 1:
            out["other_storets"] = {c: self.names[c] for c in sorted(self.codes) if c != main_code}
        if len(self.units) > 1:
            out["units_seen"] = dict(sorted(self.units.items()))
        return out


class SystemAgg:
    __slots__ = ("names", "n_rows", "latest_sample", "points", "point_types", "analytes", "raw_keys")

    def __init__(self) -> None:
        self.names: Counter = Counter()
        self.n_rows = 0
        self.latest_sample: str | None = None
        self.points: set[str] = set()
        self.point_types: Counter = Counter()
        self.analytes: dict[str, Aggregate] = {}
        self.raw_keys: dict[str, str] = {}  # unmapped key -> storet, to keep raw-name keys unambiguous

    def key_for(self, analyte_id: str | None, code: str, raw_name: str) -> str:
        if analyte_id is not None:
            return analyte_id
        key = raw_name or code
        owner = self.raw_keys.setdefault(key, code)
        return key if owner == code else f"{key} [{code}]"

    def to_json(self, fallback_name: str | None) -> dict:
        return {
            "name": self.names.most_common(1)[0][0] if self.names else fallback_name,
            "n_rows": self.n_rows,
            "latest_sample": self.latest_sample,
            "sampling_points": len(self.points),
            "point_types": dict(sorted(self.point_types.items())),
            "analytes": {k: self.analytes[k].to_json() for k in sorted(self.analytes)},
        }


# ----------------------------------------------------------------------------- extraction

def _open_gzip_text(path: Path):
    # mtime=0 so that identical input yields byte-identical output
    gz = gzip.GzipFile(filename="", mode="wb", fileobj=open(path, "wb"), mtime=0)
    return io.TextIOWrapper(gz, encoding="utf-8", newline="\n")


def extract(zips: list[Path], boundaries: Path, catalogue: Path, analyte_map: Path, out_dir: Path,
            today: dt.date | None = None) -> dict:
    """Run the extraction and write both outputs; returns the results document."""
    cat = load_catalogue(catalogue)
    amap = load_analyte_map(analyte_map, cat)
    targets = load_target_pwsids(boundaries)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_path = out_dir / "santa_clara_rows.tsv.gz"
    results_path = out_dir / "santa_clara_results.json"

    systems: dict[str, SystemAgg] = {}
    header: list[str] | None = None
    idx: dict[str, int] = {}
    files_meta, unlisted, sample_types, bad_dates, kinds = [], Counter(), Counter(), 0, Counter()

    with _open_gzip_text(rows_path) as out:
        for zip_path in zips:
            rows_total = rows_kept = rows_malformed = 0
            kept_min = kept_max = None
            member, at_header = None, True
            for member, line in read_tab(zip_path):
                if at_header:  # first line of every file is the column header
                    at_header = False
                    cols = line.split("\t")
                    if header is None:
                        missing = [c for c in REQUIRED_COLUMNS if c not in cols]
                        if missing:
                            raise ValueError(f"{zip_path}: header lacks columns {missing}")
                        header = cols
                        idx = {c: i for i, c in enumerate(cols)}
                        out.write("\t".join(header) + "\n")
                    elif cols != header:
                        raise ValueError(f"{zip_path}: header differs from {zips[0].name}")
                    continue
                rows_total += 1
                first = line.split("\t", idx[COL_PWSID] + 1)
                if len(first) <= idx[COL_PWSID]:
                    continue
                pwsid = first[idx[COL_PWSID]].strip()
                if pwsid not in targets:
                    continue
                rows_kept += 1
                out.write(line + "\n")
                fields = line.split("\t")
                if len(fields) != len(header):
                    rows_malformed += 1
                    continue
                r = {c: fields[i].strip() for c, i in idx.items()}
                date = parse_sample_date(r[COL_SAMPLE_DATE])
                if date is None:
                    bad_dates += 1
                else:
                    kept_min = date if kept_min is None or date < kept_min else kept_min
                    kept_max = date if kept_max is None or date > kept_max else kept_max
                sample_types[r.get(COL_SAMPLE_TYPE, "")] += 1
                c = classify_result(r[COL_RESULT], r[COL_LESS_THAN], r[COL_DLR])
                kinds[c.kind] += 1
                code, raw_name = r[COL_ANALYTE_CODE], r[COL_ANALYTE_NAME]
                analyte_id, unit_label, listed = resolve_analyte(amap, cat, code, raw_name)
                if not listed:
                    unlisted[(code, raw_name)] += 1
                sysagg = systems.setdefault(pwsid, SystemAgg())
                sysagg.n_rows += 1
                sysagg.names[r[COL_SYSTEM_NAME]] += 1
                sysagg.points.add(r[COL_PS_CODE])
                sysagg.point_types[r[COL_FACILITY_TYPE] or "?"] += 1
                if date is not None and (sysagg.latest_sample is None or date > sysagg.latest_sample):
                    sysagg.latest_sample = date
                key = sysagg.key_for(analyte_id, code, raw_name)
                agg = sysagg.analytes.get(key)
                if agg is None:
                    agg = sysagg.analytes[key] = Aggregate(analyte_id)
                agg.add(c, date, r[COL_PS_CODE], r[COL_FACILITY_TYPE], code, raw_name,
                        unit_label or r[COL_UNIT], r[COL_DLR], r.get(COL_MCL, ""))
            if member is None:
                raise ValueError(f"{zip_path}: empty .tab member")
            files_meta.append({
                "zip": zip_path.name,
                "url": FILE_URL.format(name=zip_path.name),
                "zip_bytes": zip_path.stat().st_size,
                "zip_sha256": sha256_of(zip_path),
                "member": member.filename,
                "member_bytes": member.file_size,
                "member_modified": dt.datetime(*member.date_time).isoformat(),
                "rows_total": rows_total,
                "rows_kept": rows_kept,
                "rows_malformed": rows_malformed,
                "kept_date_range": [kept_min, kept_max],
            })
            print(f"{zip_path.name}: {rows_total:,} rows, kept {rows_kept:,} ({rows_malformed} malformed), "
                  f"dates {kept_min}..{kept_max}", file=sys.stderr)

    without = sorted(p for p in targets if p not in systems)
    analyte_ids = {a.id for s in systems.values() for a in s.analytes.values() if a.id}
    raw_keys = {k for s in systems.values() for k, a in s.analytes.items() if a.id is None}
    doc = {
        "generated": (today or dt.date.today()).isoformat(),
        "source": {
            "title": "State Water Resources Control Board, Division of Drinking Water: SDWIS laboratory results (EDT library)",
            "page": PAGE_URL,
            "data_dictionary": DICTIONARY_URL,
            "files": files_meta,
            "columns": header,
            "non_detect_rule": NON_DETECT_RULE,
            "result_kinds": dict(sorted(kinds.items())),
            "sample_types": dict(sorted(sample_types.items())),
            "rows_kept": sum(f["rows_kept"] for f in files_meta),
            "rows_bad_date": bad_dates,
            "targets": {"pwsids": len(targets), "with_rows": len(systems), "without_rows": without},
            "analytes": {"canonical_ids": len(analyte_ids), "unmapped_names": len(raw_keys)},
            "unlisted_in_map": [{"storet": c, "raw_name": n, "n": k} for (c, n), k in sorted(unlisted.items())],
        },
        "systems": {},
    }
    for pwsid in sorted(targets):
        agg = systems.get(pwsid)
        doc["systems"][pwsid] = agg.to_json(targets[pwsid]) if agg else {
            "name": targets[pwsid], "n_rows": 0, "latest_sample": None, "sampling_points": 0,
            "point_types": {}, "analytes": {},
        }
    with open(results_path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    return doc


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Extract Santa Clara County rows from the SDWIS EDT-library files")
    ap.add_argument("zips", nargs="+", type=Path, help="SDWIS4.zip SDWIS5.zip (any order, any number)")
    ap.add_argument("--boundaries", type=Path, default=REPO_ROOT / "data" / "boundaries.geojson")
    ap.add_argument("--catalogue", type=Path, default=REPO_ROOT / "config" / "analytes.yaml")
    ap.add_argument("--analyte-map", type=Path, default=REPO_ROOT / "src" / "pipeline" / "sdwis_analytes.yaml")
    ap.add_argument("--out-dir", type=Path, default=REPO_ROOT / "data" / "sdwis")
    args = ap.parse_args(argv)
    doc = extract(args.zips, args.boundaries, args.catalogue, args.analyte_map, args.out_dir)
    s = doc["source"]
    print(f"kept {s['rows_kept']:,} rows for {s['targets']['with_rows']} of {s['targets']['pwsids']} systems; "
          f"{s['analytes']['canonical_ids']} canonical analytes, {s['analytes']['unmapped_names']} unmapped names; "
          f"{len(s['unlisted_in_map'])} analyte codes not listed in the map", file=sys.stderr)


if __name__ == "__main__":
    main()
