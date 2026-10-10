"""Merge boundaries, CCR extracts and the state lab layer into data/site.json.

High-level behaviour: build(repo_root) -> dict, and a CLI that writes the file. Mid-level steps
are `load_systems`, `merge_ccr`, `attach_sdwis`; low-level helpers live in analytes.py.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from .analytes import convert, load_catalogue, match_analyte, normalize_unit, ratio_to_standard

COMMUNITY = "COMMUNITY"


def load_systems(boundaries_path: Path) -> tuple[dict, dict]:
    """Return (systems dict keyed by PWSID, slim GeoJSON FeatureCollection with fid + pwsid)."""
    with open(boundaries_path, encoding="utf-8") as fh:
        gj = json.load(fh)
    systems, features = {}, []
    for i, f in enumerate(sorted(gj["features"], key=lambda f: f["properties"]["WATER_SYSTEM_NUMBER"])):
        p = f["properties"]
        pwsid = p["WATER_SYSTEM_NUMBER"]
        systems[pwsid] = {
            "pwsid": pwsid,
            "fid": i,
            "name": p.get("WATER_SYSTEM_NAME"),
            "classification": p.get("FEDERAL_CLASSIFICATION"),
            "state_classification": p.get("STATE_CLASSIFICATION"),
            "owner_type": p.get("OWNER_TYPE_CODE"),
            "population": p.get("POPULATION"),
            "connections": p.get("SERVICE_CONNECTIONS"),
            "city": p.get("ADDRESS_CITY_NAME"),
            "county": p.get("COUNTY"),
            "regulating_agency": p.get("REGULATING_AGENCY"),
            "boundary_verified": p.get("VERIFIED_STATUS"),
            "report": {"status": "not_found" if p.get("FEDERAL_CLASSIFICATION") == COMMUNITY else "not_required"},
            "values": {},
            "other_rows": [],
        }
        features.append({"type": "Feature", "id": i, "properties": {"fid": i, "pwsid": pwsid}, "geometry": f["geometry"]})
    return systems, {"type": "FeatureCollection", "features": features}


def _effective(e: dict) -> tuple[float | None, str | None]:
    """The printed number an entry contributes: value, else the range high when a range shows a detection."""
    if e.get("detected") is False:
        return None, None
    if e.get("value") is not None:
        return e["value"], e.get("value_basis")
    if e.get("range_high") not in (None, 0):
        return e["range_high"], "range_high"
    return None, None


def _entry_value(cat_analyte: dict, e: dict) -> float | None:
    v, _ = _effective(e)
    if v is None:
        return None
    return convert(v, e.get("unit_raw"), cat_analyte["unit"])

TIER1_WORDS = ("90th", "average", "raa", "running annual", "monthly", "percent", "level detected", "level_detected",
               "highest site", "highest location", "highest annual", "count", "number of", "mean")
TIER2_WORDS = ("range", "single", "max", "highest", "result", "detected", "level")


def basis_tier(e: dict) -> int:
    """1 = the report's representative figure (average, 90th percentile, running annual average,
    monthly percentage, or the plain 'level detected'); 2 = a worst single result or range end."""
    b = (e.get("value_basis_effective") or e.get("value_basis") or "").lower()
    if any(w in b for w in TIER1_WORDS):
        return 1
    if any(w in b for w in TIER2_WORDS):
        return 2
    return 1 if e.get("value") is not None else 2


def select_value(a: dict, entries: list, pwsid: str, warnings: list) -> dict:
    """Pick the entry that represents the system for analyte `a`.

    Tier-1 entries win over tier-2 ones; within a tier the highest converted value wins (the worst
    source column). Lead and copper never fall back to a single tap result when a 90th-percentile
    entry exists, because the action level applies to the 90th percentile.
    """
    any_detected, unconverted = False, 0
    for e in entries:
        v = _entry_value(a, e)
        e["value_converted"] = v
        raw, basis = _effective(e)
        if basis == "range_high":
            e["value_basis_effective"] = "range_high"
        e["basis_tier"] = basis_tier(e)
        if raw is not None:
            any_detected = True
            if v is None:
                unconverted += 1
                warnings.append(f"{pwsid} {a['id']}: unit '{e.get('unit_raw')}' not convertible to {a['unit']}")
    tiers = sorted({e["basis_tier"] for e in entries})
    if a["group"] == "lead_copper" and any("90th" in (e.get("value_basis") or "").lower() for e in entries):
        tiers = [1]
    best, best_val = None, None
    for tier in tiers:
        for e in entries:
            if e["basis_tier"] != tier:
                continue
            v = e["value_converted"]
            if v is not None and (best_val is None or v > best_val):
                best, best_val = e, v
        if best is not None:
            break
    if best_val is not None:
        status = "value"
    elif any_detected and unconverted and (tiers == [1] or best is None):
        status = "unconverted" if not (a["group"] == "lead_copper" and tiers == [1]) else "nd"
    else:
        status = "nd"
    if a["group"] == "lead_copper" and tiers == [1] and best is None:
        status = "nd"  # printed 90th percentile is ND / "<x": at the standard's own metric nothing was found
    return {
        "status": status,
        "value": best_val,
        "unit": a["unit"],
        "basis": (best.get("value_basis_effective") or best.get("value_basis")) if best else None,
        "basis_tier": best.get("basis_tier") if best else None,
        "source_group": best.get("source_group") if best else None,
        "entries": entries,
    }


def merge_ccr(systems: dict, cat: dict, ccr_dir: Path) -> list[str]:
    """Fold every data/ccr/<PWSID>.json into its system. Returns a list of warnings."""
    warnings = []
    for path in sorted(ccr_dir.glob("*.json")):
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        pwsid = doc.get("pwsid") or path.stem
        sysrec = systems.get(pwsid)
        if sysrec is None:
            warnings.append(f"{path.name}: PWSID not in boundaries, skipped")
            continue
        status = doc.get("status")
        rep = {"status": status, "search_log": doc.get("search_log") or []}
        if status == "found" and doc.get("report"):
            r = doc["report"]
            rep.update({k: r.get(k) for k in ("data_year", "title", "url", "file", "format", "sha256", "pages", "retrieved")})
            rep["sources_described"] = doc.get("sources_described")
            rep["violations"] = doc.get("violations") or []
            rep["extraction_notes"] = doc.get("extraction_notes")
        sysrec["report"] = rep
        if status != "found":
            continue
        grouped: dict[str, list] = {}
        for e in doc.get("analytes") or []:
            aid = e.get("id") or match_analyte(cat, e.get("raw_name"))
            if aid not in cat["by_id"]:
                sysrec["other_rows"].append(e)
                continue
            grouped.setdefault(aid, []).append(e)
        for aid, entries in grouped.items():
            a = cat["by_id"][aid]
            sysrec["values"][aid] = select_value(a, entries, pwsid, warnings)
    return warnings


def attach_sdwis(systems: dict, cat: dict, sdwis_path: Path) -> dict | None:
    """Attach per-system state lab aggregates when data/sdwis/santa_clara_results.json exists."""
    if not sdwis_path.exists():
        return None
    with open(sdwis_path, encoding="utf-8") as fh:
        doc = json.load(fh)
    for pwsid, rec in (doc.get("systems") or {}).items():
        sysrec = systems.get(pwsid)
        if sysrec is None:
            continue
        lab = {}
        for key, agg in (rec.get("analytes") or {}).items():
            aid = agg.get("id") or match_analyte(cat, agg.get("raw_name"))
            if aid not in cat["by_id"]:
                continue
            a = cat["by_id"][aid]
            unit = agg.get("unit")
            latest = convert(agg.get("latest_value"), unit, a["unit"], aid)
            mx = convert(agg.get("max"), unit, a["unit"], aid)
            prev = lab.get(aid)
            if prev and (mx is None or (prev.get("max") or 0) >= (mx or 0)):
                continue
            p90 = convert(agg.get("p90"), unit, a["unit"], aid)
            basis_val, basis = (p90, "p90") if (a["group"] == "lead_copper" and p90 is not None) else (mx, "max")
            lab[aid] = {
                "status": "value" if (latest is not None or mx is not None) else "nd",
                "latest": latest, "max": mx, "p90": p90, "p90_n": agg.get("p90_n"), "basis_value": basis_val, "basis": basis,
                "unit": a["unit"], "n": agg.get("n"), "n_detected": agg.get("n_detected"), "latest_date": agg.get("latest_date"),
                "sampling_points": agg.get("sampling_points"), "raw_name": agg.get("raw_name"),
            }
        sysrec["lab"] = lab
        sysrec["lab_meta"] = {"n_rows": rec.get("n_rows"), "latest_sample": rec.get("latest_sample")}
    return doc.get("source")


def compute_ratios(systems: dict, cat: dict) -> None:
    for a in cat["analytes"]:
        aid = a["id"]
        rel_max = max((s["values"][aid]["value"] for s in systems.values()
                       if aid in s["values"] and s["values"][aid]["value"] is not None), default=None)
        rel_max_lab = max((s.get("lab", {}).get(aid, {}).get("max") or 0 for s in systems.values()), default=None) or None
        for s in systems.values():
            v = s["values"].get(aid)
            if v:
                v["ratio"] = ratio_to_standard(a, v["value"], rel_max) if v["status"] == "value" else (0.0 if v["status"] == "nd" else None)
            lv = s.get("lab", {}).get(aid)
            if lv:
                basis = lv.get("basis_value") if lv.get("basis_value") is not None else (lv["max"] if lv["max"] is not None else lv["latest"])
                lv["ratio"] = ratio_to_standard(a, basis, rel_max_lab) if basis is not None else 0.0


def build(repo_root: Path) -> tuple[dict, dict, list[str]]:
    cat = load_catalogue(repo_root / "config" / "analytes.yaml")
    systems, geo = load_systems(repo_root / "data" / "boundaries.geojson")
    warnings = merge_ccr(systems, cat, repo_root / "data" / "ccr")
    sdwis_source = attach_sdwis(systems, cat, repo_root / "data" / "sdwis" / "santa_clara_results.json")
    compute_ratios(systems, cat)
    analytes_out = []
    for a in cat["analytes"]:
        if a.get("hidden"):
            continue
        aid = a["id"]
        n_val = sum(1 for s in systems.values() if aid in s["values"])
        n_lab = sum(1 for s in systems.values() if aid in s.get("lab", {}))
        analytes_out.append({k: a.get(k) for k in ("id", "name", "group", "unit", "threshold", "threshold_type", "threshold_low", "phg", "notes")}
                            | {"n_ccr": n_val, "n_lab": n_lab})
    counts = {
        "systems": len(systems),
        "community": sum(1 for s in systems.values() if s["classification"] == COMMUNITY),
        "reports_found": sum(1 for s in systems.values() if s["report"]["status"] == "found"),
        "reports_not_found": sum(1 for s in systems.values() if s["report"]["status"] == "not_found"),
        "reports_not_required": sum(1 for s in systems.values() if s["report"]["status"] == "not_required"),
        "lab_systems": sum(1 for s in systems.values() if s.get("lab")),
    }
    site = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "region": "Santa Clara County, California",
        "groups": cat["groups"],
        "analytes": analytes_out,
        "systems": systems,
        "counts": counts,
        "sources": {
            "boundaries": "California Drinking Water System Area Boundaries (State Water Resources Control Board, Division of Drinking Water)",
            "ccr": "Each system's published Consumer Confidence Report (see per-system report link)",
            "lab": sdwis_source,
        },
        "warnings": warnings,
    }
    return site, geo, warnings


def main() -> None:
    ap = argparse.ArgumentParser(description="Build data/site.json and data/site.geojson")
    ap.add_argument("--repo", default=Path(__file__).resolve().parents[2], type=Path)
    args = ap.parse_args()
    site, geo, warnings = build(args.repo)
    out = args.repo / "data"
    with open(out / "site.json", "w", encoding="utf-8") as fh:
        json.dump(site, fh, ensure_ascii=False, separators=(",", ":"))
    with open(out / "site.geojson", "w", encoding="utf-8") as fh:
        json.dump(geo, fh, separators=(",", ":"))
    c = site["counts"]
    print(f"site.json: {c['systems']} systems, {c['reports_found']} reports found, {c['reports_not_found']} not found, "
          f"{c['reports_not_required']} not required, lab layer on {c['lab_systems']} systems, {len(warnings)} warnings")
    for w in warnings[:30]:
        print("  warn:", w)


if __name__ == "__main__":
    main()
