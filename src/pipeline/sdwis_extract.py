#!/usr/bin/env python3
"""Extract State Water Board (DDW) laboratory results for the southbay-water systems.

Input: the SDWIS*.zip files from the State Water Board EDT library
(https://www.waterboards.ca.gov/drinking_water/certlic/drinkingwater/EDTlibrary.html), each
holding one tab-delimited SDWIS*.tab member, plus the project boundaries file (target PWSIDs),
the canonical analyte catalogue and the SDWIS analyte-code mapping.

Output (in --out-dir):
  santa_clara_rows.tsv.gz     every row whose Water System Number is one of the target systems,
                              original columns, cp1252 decoded and written as UTF-8 with LF ends
  santa_clara_results.json    per-system, per-analyte aggregates (see docs/sdwis-layer.md)

The .tab files are untrusted data: nothing in them is executed, they are streamed line by line,
and the recommended invocation is with isolated mode:

  python3 -I src/pipeline/sdwis_extract.py raw/SDWIS4.zip raw/SDWIS5.zip

Defaults for --boundaries, --catalogue, --analyte-map and --out-dir are relative to the repo root.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_PAGE = "https://www.waterboards.ca.gov/drinking_water/certlic/drinkingwater/EDTlibrary.html"
SOURCE_DIR = "https://www.waterboards.ca.gov/drinking_water/certlic/drinkingwater/documents/edtlibrary/"
ENCODING = "cp1252"  # the files are not UTF-8: section sign, en dash and NBSP appear as single bytes

# Column names exactly as in the header line of SDWIS4.tab / SDWIS5.tab.
COL_PWSID = "Water System Number"
COL_SYSTEM_NAME = "Water System Name"
COL_PS_CODE = "PS Code"
COL_FACILITY_TYPE = "Facility Type"
COL_SAMPLE_DATE = "Sample Date"
COL_CODE = "Analyte Code"
COL_NAME = "Analyte Name"
COL_RESULT = "Result"
COL_UNIT = "Units of Measure"
COL_LT = "Less Than Reporting Level"
COL_DLR = "DLR"
COL_MCL = "MCL"
REQUIRED_COLUMNS = (
    COL_PWSID, COL_SYSTEM_NAME, COL_PS_CODE, COL_FACILITY_TYPE, COL_SAMPLE_DATE, COL_CODE,
    COL_NAME, COL_RESULT, COL_UNIT, COL_LT, COL_DLR, COL_MCL,
)

NON_DETECT_RULE = (
    "A row counts as not detected when any of these holds: 'Less Than Reporting Level' is Y; "
    "Result is blank, non-numeric or starts with '<'; Result equals 0; or Result is numerically "
    "below the DLR column when a DLR is given (those rows are also counted in n_below_dlr). "
    "Every other row is a detection."
)

_NUMBER_RE = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")
_DATE_MDY_RE = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")
_DATE_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


# ----------------------------------------------------------------------------- small parsers
def parse_number(text: str) -> float | None:
    """Float from a Result/DLR cell, or None when blank or not a plain number."""
    text = text.strip()
    if not text or not _NUMBER_RE.match(text):
        return None
    return float(text)


def parse_sample_date(text: str) -> str | None:
    """'MM-DD-YYYY' (what the files contain) or 'YYYY-MM-DD' (what the dictionary says) -> ISO."""
    text = text.strip()
    m = _DATE_MDY_RE.match(text)
    if m:
        month, day, year = m.groups()
    else:
        m = _DATE_ISO_RE.match(text)
        if not m:
            return None
        year, month, day = m.groups()
    try:
        return dt.date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


def classify_result(result: str, less_than_flag: str, dlr: str) -> tuple[float | None, bool, bool]:
    """Apply NON_DETECT_RULE. Returns (value, detected, below_dlr).

    value is the parsed Result (None when blank/non-numeric/'<'-prefixed); below_dlr is True only
    for a numeric, unflagged result that is below the DLR column.
    """
    text = result.strip()
    if text.startswith("<"):
        return None, False, False
    value = parse_number(text)
    if less_than_flag.strip().upper() == "Y" or value is None or value == 0:
        return value, False, False
    dlr_value = parse_number(dlr)
    if dlr_value is not None and value < dlr_value:
        return value, False, True
    return value, True, False


# ----------------------------------------------------------------------------- config loading
def load_target_pwsids(boundaries_path: Path) -> dict[str, str]:
    """PWSID -> system name from the boundaries GeoJSON."""
    with open(boundaries_path, encoding="utf-8") as fh:
        geo = json.load(fh)
    out: dict[str, str] = {}
    for feature in geo["features"]:
        props = feature.get("properties") or {}
        pwsid = str(props.get("WATER_SYSTEM_NUMBER", "")).strip()
        if pwsid:
            out[pwsid] = str(props.get("WATER_SYSTEM_NAME", "")).strip()
    if not out:
        raise SystemExit(f"no WATER_SYSTEM_NUMBER properties found in {boundaries_path}")
    return out


def load_catalogue(catalogue_path: Path) -> dict[str, dict]:
    with open(catalogue_path, encoding="utf-8") as fh:
        cat = yaml.safe_load(fh)
    return {entry["id"]: entry for entry in cat["analytes"]}


def load_analyte_map(map_path: Path, catalogue: dict[str, dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    """Returns (by_storet, by_name). Every non-null id must exist in the catalogue."""
    with open(map_path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    by_storet: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for entry in doc["analytes"]:
        cid = entry.get("id")
        if cid is not None and cid not in catalogue:
            raise SystemExit(f"{map_path}: id {cid!r} (storet {entry.get('storet')}) is not in the catalogue")
        storet = str(entry["storet"]).strip()
        if storet in by_storet:
            raise SystemExit(f"{map_path}: storet {storet} listed twice")
        by_storet[storet] = entry
        name = str(entry.get("raw_name", "")).strip().upper()
        if name:
            by_name.setdefault(name, entry)
    return by_storet, by_name


# ----------------------------------------------------------------------------- aggregation
class AnalyteAgg:
    """Per (system, analyte key) accumulator."""

    __slots__ = ("n", "n_detected", "n_below_dlr", "detected_sum", "max", "latest_date", "latest_value",
                 "points", "point_types", "codes", "units", "dlrs", "mcls")

    def __init__(self) -> None:
        self.n = 0
        self.n_detected = 0
        self.n_below_dlr = 0
        self.detected_sum = 0.0
        self.max: float | None = None
        self.latest_date: str | None = None
        self.latest_value: float | None = None
        self.points: set[str] = set()
        self.point_types: collections.Counter = collections.Counter()
        self.codes: collections.Counter = collections.Counter()  # (storet, raw_name) -> rows
        self.units: collections.Counter = collections.Counter()
        self.dlrs: collections.Counter = collections.Counter()
        self.mcls: collections.Counter = collections.Counter()

    def add(self, *, date: str | None, value: float | None, detected: bool, below_dlr: bool, point: str,
            point_type: str, storet: str, raw_name: str, unit: str, dlr: str, mcl: str) -> None:
        self.n += 1
        self.codes[(storet, raw_name)] += 1
        self.units[unit] += 1
        if dlr:
            self.dlrs[dlr] += 1
        if mcl:
            self.mcls[mcl] += 1
        self.points.add(point)
        self.point_types[point_type or "?"] += 1
        if below_dlr:
            self.n_below_dlr += 1
        if detected and value is not None:
            self.n_detected += 1
            self.detected_sum += value
            self.max = value if self.max is None else max(self.max, value)
        if date is not None:
            if self.latest_date is None or date > self.latest_date:
                self.latest_date = date
                self.latest_value = value if detected else None
            elif date == self.latest_date and detected and value is not None:
                self.latest_value = value if self.latest_value is None else max(self.latest_value, value)

    def to_json(self, canonical_id: str | None) -> dict:
        (storet, raw_name), _ = self.codes.most_common(1)[0]
        unit, _ = self.units.most_common(1)[0]
        out = {
            "id": canonical_id,
            "raw_name": raw_name,
            "storet": storet,
            "unit": unit,
            "n": self.n,
            "n_detected": self.n_detected,
            "n_below_dlr": self.n_below_dlr,
            "latest_date": self.latest_date,
            "latest_value": self.latest_value,
            "max": self.max,
            "mean_detected": round(self.detected_sum / self.n_detected, 6) if self.n_detected else None,
            "sampling_points": len(self.points),
            "point_types": dict(sorted(self.point_types.items())),
            "dlr": self.dlrs.most_common(1)[0][0] if self.dlrs else None,
            "mcl": self.mcls.most_common(1)[0][0] if self.mcls else None,
        }
        if len(self.codes) > 1:
            out["other_storets"] = sorted(code for (code, _name) in self.codes if code != storet)
        if len(self.units) > 1:
            out["units_seen"] = sorted(self.units)
        return out


class SystemAgg:
    __slots__ = ("name", "n_rows", "latest_sample", "points", "point_types", "analytes", "key_codes")

    def __init__(self, name: str) -> None:
        self.name = name
        self.n_rows = 0
        self.latest_sample: str | None = None
        self.points: set[str] = set()
        self.point_types: collections.Counter = collections.Counter()
        self.analytes: dict[str, AnalyteAgg] = {}
        self.key_codes: dict[str, str] = {}  # raw-name key -> storet, to keep unmapped keys unambiguous


def analyte_key(storet: str, raw_name: str, entry: dict | None, system: SystemAgg) -> tuple[str, str | None]:
    """(key in the analytes dict, canonical id or None)."""
    cid = entry.get("id") if entry else None
    if cid:
        return cid, cid
    key = raw_name or storet
    owner = system.key_codes.setdefault(key, storet)
    if owner != storet:  # two different codes spelled the same: disambiguate the second one
        key = f"{raw_name} [{storet}]"
        system.key_codes.setdefault(key, storet)
    return key, None


# ----------------------------------------------------------------------------- streaming
def _zip_member(zf: zipfile.ZipFile) -> zipfile.ZipInfo:
    members = [zi for zi in zf.infolist() if not zi.is_dir()]
    tabs = [zi for zi in members if zi.filename.lower().endswith(".tab")]
    if len(tabs) != 1:
        raise SystemExit(f"{zf.filename}: expected exactly one .tab member, found {[m.filename for m in members]}")
    return tabs[0]


def sha256_of(path: Path) -> str:
    with open(path, "rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def extract(zip_paths: list[Path], boundaries: Path, catalogue_path: Path, analyte_map_path: Path,
            out_dir: Path, generated: str | None = None) -> dict:
    targets = load_target_pwsids(boundaries)
    catalogue = load_catalogue(catalogue_path)
    map_by_storet, map_by_name = load_analyte_map(analyte_map_path, catalogue)
    target_bytes = {p.encode("ascii") for p in targets}

    out_dir.mkdir(parents=True, exist_ok=True)
    rows_path = out_dir / "santa_clara_rows.tsv.gz"
    json_path = out_dir / "santa_clara_results.json"

    systems: dict[str, SystemAgg] = {p: SystemAgg(name) for p, name in targets.items()}
    header: list[str] | None = None
    idx: dict[str, int] = {}
    file_summaries: list[dict] = []
    total_rows = total_kept = total_malformed_kept = 0
    all_min = all_max = None
    skipped_bad_pwsid = 0

    with gzip.open(rows_path, "wt", encoding="utf-8", newline="\n") as rows_out:
        for zip_path in zip_paths:
            with zipfile.ZipFile(zip_path) as zf:
                member = _zip_member(zf)
                n_rows = n_kept = n_malformed = n_malformed_kept = 0
                f_min = f_max = None
                with zf.open(member) as raw:
                    first = raw.readline()
                    cols = first.decode(ENCODING).rstrip("\r\n").split("\t")
                    if header is None:
                        header = cols
                        missing = [c for c in REQUIRED_COLUMNS if c not in header]
                        if missing:
                            raise SystemExit(f"{zip_path}: header lacks columns {missing}")
                        idx = {c: i for i, c in enumerate(header)}
                        rows_out.write("\t".join(header) + "\n")
                    elif cols != header:
                        raise SystemExit(f"{zip_path}: header differs from {zip_paths[0]}")
                    i_pwsid = idx[COL_PWSID]
                    ncols = len(header)
                    for line in raw:
                        n_rows += 1
                        parts_b = line.split(b"\t", i_pwsid + 1)
                        if len(parts_b) <= i_pwsid or parts_b[i_pwsid].strip() not in target_bytes:
                            continue
                        text = line.decode(ENCODING).rstrip("\r\n")
                        fields = text.split("\t")
                        rows_out.write(text + "\n")
                        n_kept += 1
                        if len(fields) != ncols:
                            n_malformed += 1
                            n_malformed_kept += 1
                            print(f"warning: {member.filename} row {n_rows}: {len(fields)} fields, expected {ncols}; "
                                  f"kept in the TSV, excluded from aggregates", file=sys.stderr)
                            continue
                        pwsid = fields[i_pwsid].strip()
                        system = systems.get(pwsid)
                        if system is None:  # cannot happen after the bytes check, kept for safety
                            skipped_bad_pwsid += 1
                            continue
                        date = parse_sample_date(fields[idx[COL_SAMPLE_DATE]])
                        value, detected, below_dlr = classify_result(
                            fields[idx[COL_RESULT]], fields[idx[COL_LT]], fields[idx[COL_DLR]])
                        storet = fields[idx[COL_CODE]].strip()
                        raw_name = fields[idx[COL_NAME]].strip()
                        entry = map_by_storet.get(storet) or map_by_name.get(raw_name.upper())
                        unit = fields[idx[COL_UNIT]].strip()
                        if entry and entry.get("unit_label"):
                            unit = str(entry["unit_label"])
                        point = fields[idx[COL_PS_CODE]].strip()
                        point_type = fields[idx[COL_FACILITY_TYPE]].strip()
                        key, cid = analyte_key(storet, raw_name, entry, system)
                        agg = system.analytes.get(key)
                        if agg is None:
                            agg = system.analytes[key] = AnalyteAgg()
                        agg.add(date=date, value=value, detected=detected, below_dlr=below_dlr, point=point,
                                point_type=point_type, storet=storet, raw_name=raw_name, unit=unit,
                                dlr=fields[idx[COL_DLR]].strip(), mcl=fields[idx[COL_MCL]].strip())
                        system.n_rows += 1
                        system.points.add(point)
                        system.point_types[point_type or "?"] += 1
                        if date is not None:
                            if system.latest_sample is None or date > system.latest_sample:
                                system.latest_sample = date
                            f_min = date if f_min is None or date < f_min else f_min
                            f_max = date if f_max is None or date > f_max else f_max
                zip_bytes = zip_path.stat().st_size
                file_summaries.append({
                    "zip": zip_path.name,
                    "url": SOURCE_DIR + zip_path.name,
                    "zip_bytes": zip_bytes,
                    "zip_sha256": sha256_of(zip_path),
                    "member": member.filename,
                    "member_bytes": member.file_size,
                    "member_modified": dt.datetime(*member.date_time).isoformat(),
                    "columns": len(header),
                    "rows_total": n_rows,
                    "rows_kept": n_kept,
                    "rows_kept_malformed": n_malformed_kept,
                    "kept_sample_date_range": [f_min, f_max],
                })
                total_rows += n_rows
                total_kept += n_kept
                total_malformed_kept += n_malformed_kept
                if f_min is not None:
                    all_min = f_min if all_min is None or f_min < all_min else all_min
                    all_max = f_max if all_max is None or f_max > all_max else all_max

    with_rows = sorted(p for p, s in systems.items() if s.n_rows)
    without_rows = sorted(p for p, s in systems.items() if not s.n_rows)
    systems_json = {}
    for pwsid in sorted(systems):
        s = systems[pwsid]
        analytes_json = {}
        for key in sorted(s.analytes):
            cid = key if key in catalogue else None
            analytes_json[key] = s.analytes[key].to_json(cid)
        systems_json[pwsid] = {
            "name": s.name,
            "n_rows": s.n_rows,
            "latest_sample": s.latest_sample,
            "sampling_points": len(s.points),
            "point_types": dict(sorted(s.point_types.items())),
            "analytes": analytes_json,
        }

    result = {
        "generated": generated or dt.date.today().isoformat(),
        "source": {
            "page": SOURCE_PAGE,
            "data_dictionary": SOURCE_DIR + "data_dictionary.pdf",
            "encoding": f"{ENCODING} in the files; written as UTF-8",
            "columns": header,
            "files": file_summaries,
            "rows_total": total_rows,
            "rows_kept": total_kept,
            "rows_kept_malformed": total_malformed_kept,
            "kept_sample_date_range": [all_min, all_max],
            "pwsids_targeted": len(targets),
            "pwsids_with_rows": len(with_rows),
            "pwsids_without_rows": without_rows,
            "non_detect_rule": NON_DETECT_RULE,
            "catalogue": _rel(catalogue_path),
            "analyte_map": _rel(analyte_map_path),
            "boundaries": _rel(boundaries),
        },
        "systems": systems_json,
    }
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    return result


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


# ----------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("zips", nargs="+", type=Path, help="SDWIS*.zip files, oldest first")
    ap.add_argument("--boundaries", type=Path, default=REPO_ROOT / "data" / "boundaries.geojson")
    ap.add_argument("--catalogue", type=Path, default=REPO_ROOT / "config" / "analytes.yaml")
    ap.add_argument("--analyte-map", type=Path, default=REPO_ROOT / "src" / "pipeline" / "sdwis_analytes.yaml")
    ap.add_argument("--out-dir", type=Path, default=REPO_ROOT / "data" / "sdwis")
    ap.add_argument("--generated", default=None, help="override the ISO date written as 'generated'")
    args = ap.parse_args(argv)

    result = extract(args.zips, args.boundaries, args.catalogue, args.analyte_map, args.out_dir, args.generated)
    src = result["source"]
    for f in src["files"]:
        print(f"{f['zip']}: {f['rows_total']} rows, {f['rows_kept']} kept, dates {f['kept_sample_date_range']}")
    print(f"kept {src['rows_kept']} of {src['rows_total']} rows; "
          f"{src['pwsids_with_rows']} of {src['pwsids_targeted']} systems have rows; "
          f"without rows: {src['pwsids_without_rows']}")
    top = sorted(result["systems"].items(), key=lambda kv: -kv[1]["n_rows"])[:10]
    for pwsid, s in top:
        print(f"  {pwsid} {s['n_rows']:>7}  {s['name']}")
    print(f"wrote {args.out_dir / 'santa_clara_rows.tsv.gz'} and {args.out_dir / 'santa_clara_results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
