import json
from pathlib import Path

from src.pipeline.build_static import build_static

REPO = Path(__file__).resolve().parents[2]


def test_static_bundle_is_self_contained(tmp_path):
    (tmp_path / "src" / "web" / "vendor").mkdir(parents=True)
    (tmp_path / "data").mkdir(); (tmp_path / "reports" / "CA4300001").mkdir(parents=True)
    (tmp_path / "src" / "web" / "index.html").write_text('<script src="static/app.js"></script><link href="static/styles.css">')
    (tmp_path / "src" / "web" / "app.js").write_text("fetch('data/site.json')")
    (tmp_path / "src" / "web" / "styles.css").write_text("body{}")
    (tmp_path / "src" / "web" / "vendor" / "lib.js").write_text("//")
    (tmp_path / "data" / "site.json").write_text(json.dumps({"counts": {"systems": 1}}))
    (tmp_path / "data" / "site.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": []}))
    (tmp_path / "reports" / "CA4300001" / "ccr-2025.pdf").write_bytes(b"%PDF")
    out = build_static(tmp_path, tmp_path / "site")
    html = (out / "index.html").read_text()
    assert "static/app.js?v=" in html and "static/styles.css?v=" in html and '"/static/' not in html
    for rel in ("static/app.js", "static/styles.css", "static/vendor/lib.js", "data/site.json", "data/site.geojson", "reports/CA4300001/ccr-2025.pdf", "robots.txt", ".nojekyll"):
        assert (out / rel).is_file(), rel
    assert "Disallow: /" in (out / "robots.txt").read_text()


def test_real_web_app_has_no_absolute_or_api_paths():
    app = (REPO / "src" / "web" / "app.js").read_text()
    html = (REPO / "src" / "web" / "index.html").read_text()
    assert "/api/" not in app and "fetch('/" not in app and 'href="/${' not in app
    assert '"/static/' not in html and 'noindex' in html
