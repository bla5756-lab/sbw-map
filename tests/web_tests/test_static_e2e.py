"""The static bundle served by a dumb file server (what GitHub Pages does) must work on its own."""
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PORT = 8197
pytestmark = pytest.mark.e2e


def _wait_port(port, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


@pytest.fixture(scope="module")
def static_url():
    subprocess.run([sys.executable, "-m", "src.pipeline.build_site"], cwd=ROOT, check=True, capture_output=True)
    subprocess.run([sys.executable, "-m", "src.pipeline.build_static"], cwd=ROOT, check=True, capture_output=True)
    proc = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "--bind", "127.0.0.1", "--directory", str(ROOT / "site")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert _wait_port(PORT)
    yield f"http://127.0.0.1:{PORT}/"
    proc.terminate(); proc.wait(timeout=10)


@pytest.fixture(scope="module")
def page(static_url):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 1500, "height": 900})
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.goto(static_url, wait_until="domcontentloaded")
        pg.wait_for_function("window.__sbw && window.__sbw.ready === true", timeout=40000)
        time.sleep(1.0)
        yield pg
        browser.close()


def test_static_site_boots_from_relative_paths(page):
    assert page.errors == []
    s = page.evaluate("({n: window.__sbw.geo.features.length, c: window.__sbw.counts})")
    assert s["n"] == 120 and s["c"]["value"] + s["c"]["gray"] + s["c"]["red"] + (s["c"].get("notRequired") or 0) == 120


def test_static_site_report_links_resolve(page, static_url):
    found = page.evaluate("Object.values(window.__sbw.site.systems).find(s => s.report.status === 'found').pwsid")
    page.evaluate(f"window.__sbw.api.openDetail('{found}', window.__sbw.site.systems['{found}'].fid)")
    page.wait_for_timeout(300)
    href = page.get_attribute("#detail .report-actions .btn-primary", "href")
    assert href.startswith("reports/")
    assert page.request.get(static_url + href).status == 200
    assert page.request.get(static_url + "robots.txt").text().startswith("User-agent: *")


def test_static_reports_browser_lists_every_found_report(page):
    page.click("#btn-reports")
    page.wait_for_timeout(300)
    n_found = page.evaluate("Object.values(window.__sbw.site.systems).filter(s => s.report.status === 'found').length")
    assert page.locator("#reports-body a.btn-primary").count() == n_found
