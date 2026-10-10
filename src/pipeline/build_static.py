"""Emit the self-contained static site (what GitHub Pages serves) into <repo>/site.

Everything the browser needs is copied: the page, its assets, the merged data files and the
downloaded reports. No server code is involved; the page fetches `data/*.json` relatively.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import time
from pathlib import Path

ROBOTS = "User-agent: *\nDisallow: /\n"


def version_stamp(repo: Path) -> str:
    try:
        sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        return sha or str(int(time.time()))
    except Exception:  # not a git checkout
        return str(int(time.time()))


def build_static(repo: Path, out: Path | None = None) -> Path:
    out = out or repo / "site"
    web, data, reports = repo / "src" / "web", repo / "data", repo / "reports"
    for required in (data / "site.json", data / "site.geojson", web / "index.html"):
        if not required.exists():
            raise SystemExit(f"build_static: missing {required}; run build_site first")
    if out.exists():
        shutil.rmtree(out)
    (out / "static").mkdir(parents=True)
    (out / "data").mkdir()
    stamp = version_stamp(repo)
    html = (web / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "styles.css"):
        html = html.replace(f'static/{name}"', f'static/{name}?v={stamp}"')
        shutil.copy2(web / name, out / "static" / name)
    shutil.copytree(web / "vendor", out / "static" / "vendor")
    (out / "index.html").write_text(html, encoding="utf-8")
    for name in ("site.json", "site.geojson"):
        shutil.copy2(data / name, out / "data" / name)
    if reports.exists():
        shutil.copytree(reports, out / "reports")
    (out / "robots.txt").write_text(ROBOTS, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the static site folder")
    ap.add_argument("--repo", default=Path(__file__).resolve().parents[2], type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    out = build_static(args.repo, args.out)
    n_files = sum(1 for p in out.rglob("*") if p.is_file())
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"static site: {out} ({n_files} files, {size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
