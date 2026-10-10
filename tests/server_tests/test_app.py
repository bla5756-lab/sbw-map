from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.server.app import create_app, load_config

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(load_config()))


def test_index_serves_app(client):
    r = client.get("/")
    assert r.status_code == 200 and "South Bay Water Quality" in r.text and "static/app.js" in r.text


def test_static_assets(client):
    for path in ("/static/app.js", "/static/styles.css", "/static/vendor/maplibre-gl.js", "/static/vendor/maplibre-gl.css"):
        assert client.get(path).status_code == 200, path


def test_health_and_site(client):
    h = client.get("/api/health").json()
    assert h["ok"] and h["counts"]["systems"] == 120
    site = client.get("/api/site").json()
    assert len(site["systems"]) == 120 and site["analytes"] and site["groups"]
    geo = client.get("/api/boundaries").json()
    assert geo["type"] == "FeatureCollection" and len(geo["features"]) == 120


def test_system_lookup(client):
    assert client.get("/api/system/ca4310004").json()["name"] == "CITY OF GILROY"
    assert client.get("/api/system/CA0000000").status_code == 404


def test_reports_are_served_and_traversal_blocked(client):
    found = [p for p in (ROOT / "reports").glob("*/ccr-*.pdf")]
    if found:
        p = found[0]
        r = client.get(f"/reports/{p.parent.name}/{p.name}")
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/pdf")
    assert client.get("/reports/../config/app.yaml").status_code in (404, 400)
    assert client.get("/reports/CA4310004/../../config/app.yaml").status_code in (404, 400)
    assert client.get("/reports/CA4310004/nope.pdf").status_code == 404


def test_missing_config_crashes(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUTHBAY_WATER_CONFIG", str(tmp_path / "nope.yaml"))
    with pytest.raises(SystemExit):
        load_config()
