"""Browser end-to-end checks against a throwaway server on :8199 (headless Chromium via Playwright)."""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PORT = 8198
URL = f"http://127.0.0.1:{PORT}/"
SHOTS = Path(__file__).parent / "screenshots"


def _wait_port(port, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


@pytest.fixture(scope="module")
def server():
    env = dict(os.environ, SOUTHBAY_WATER_NO_AUTOAPP="")
    env.pop("SOUTHBAY_WATER_NO_AUTOAPP")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "src.server.app:app", "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"], cwd=ROOT, env=env)
    assert _wait_port(PORT), "test server did not start"
    yield URL
    proc.terminate(); proc.wait(timeout=10)


@pytest.fixture(scope="module")
def page(server):
    from playwright.sync_api import sync_playwright
    SHOTS.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 1500, "height": 900})
        pg.errors = []
        pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: pg.errors.append(f"pageerror: {e}"))
        pg.goto(server, wait_until="networkidle")
        pg.wait_for_function("window.__sbw && window.__sbw.ready === true", timeout=40000)
        time.sleep(1.0)
        yield pg
        browser.close()


def state(pg):
    return pg.evaluate("({counts: window.__sbw.counts, enabled: [...window.__sbw.enabled], solo: window.__sbw.solo, source: window.__sbw.source, hash: location.hash, n: window.__sbw.geo.features.length, ready: window.__sbw.ready})")


def test_loads_all_systems_without_console_errors(page):
    s = state(page)
    assert s["n"] == 120 and s["ready"]
    assert not page.errors, page.errors
    c = s["counts"]
    assert c["value"] + c["gray"] + c["red"] + c["notRequired"] == 120
    assert c["notRequired"] == 48  # non-community systems are hatched, never counted as data
    page.screenshot(path=str(SHOTS / "e2e-01-default.png"))


def test_sidebar_lists_every_analyte_with_counts(page):
    rows = page.locator("#analytes .row")
    site = page.evaluate("window.__sbw.site")
    assert rows.count() == len(site["analytes"])
    assert page.locator("#analytes .row[data-id='arsenic'] input").is_checked()
    badge = page.locator("#analytes .row[data-id='arsenic'] .badge").inner_text()
    assert badge.strip() == str(site["analytes"][[a["id"] for a in site["analytes"]].index("arsenic")]["n_ccr"])


def test_toggle_changes_composite_and_hash(page):
    before = state(page)
    page.locator("#analytes .row[data-id='nitrate_n'] .switch").click()
    after = state(page)
    assert "nitrate_n" in before["enabled"] and "nitrate_n" not in after["enabled"]
    assert "nitrate_n" not in after["hash"]
    page.locator("#analytes .row[data-id='nitrate_n'] .switch").click()
    assert "nitrate_n" in state(page)["enabled"]


def test_solo_grays_out_systems_missing_the_analyte(page):
    page.click("#analytes .row[data-id='arsenic'] .name")
    s = state(page)
    assert s["solo"] == "arsenic" and "solo=arsenic" in s["hash"]
    assert "Soloing" in page.locator("#solo-banner").inner_text()
    found = page.evaluate("Object.values(window.__sbw.site.systems).filter(x => x.report.status === 'found')")
    with_ars = sum(1 for x in found if "arsenic" in x["values"])
    assert s["counts"]["value"] == with_ars and s["counts"]["gray"] == len(found) - with_ars
    page.screenshot(path=str(SHOTS / "e2e-02-solo.png"))
    page.click("#solo-exit")
    assert state(page)["solo"] is None


def test_clear_then_primary_buttons(page):
    page.click("#btn-none")
    s = state(page)
    assert s["enabled"] == [] and "composite" not in page.locator("#mode-chip").inner_text().lower() or "no analyte" in page.locator("#mode-chip").inner_text().lower()
    page.click("#btn-primary")
    s = state(page)
    assert len(s["enabled"]) > 5 and "composite" in page.locator("#mode-chip").inner_text().lower()


def test_noncommunity_toggle_hides_hatched_systems(page):
    page.locator("label:has(#toggle-noncommunity) .switch").click()
    s = state(page)
    assert s["counts"]["notRequired"] == 0 and s["counts"]["value"] + s["counts"]["gray"] + s["counts"]["red"] == 72
    page.locator("label:has(#toggle-noncommunity) .switch").click()
    assert state(page)["counts"]["notRequired"] == 48


def test_click_found_system_opens_detail_with_report_link(page):
    # pick a found system, project its label point to screen coordinates, click it
    target = page.evaluate("""() => {
      const S = window.__sbw;
      const sys = Object.values(S.site.systems).find(x => x.report.status === 'found');
      const f = S.labels.features.find(l => l.properties.fid === sys.fid);
      const p = S.map.project(f.geometry.coordinates);
      return {pwsid: sys.pwsid, name: sys.name, x: p.x, y: p.y, file: sys.report.file};
    }""")
    page.mouse.move(target["x"], target["y"]); time.sleep(0.4)
    assert not page.locator("#tooltip").is_hidden()
    page.mouse.click(target["x"], target["y"]); time.sleep(0.5)
    detail = page.locator("#detail")
    assert detail.is_visible()
    assert target["pwsid"] in detail.inner_text()
    link = page.locator("#detail a[href^='/reports/']").first
    href = link.get_attribute("href")
    assert href == "/" + target["file"]
    assert page.request.get(URL.rstrip("/") + href).status == 200
    page.screenshot(path=str(SHOTS / "e2e-03-detail.png"))
    page.keyboard.press("Escape")
    assert detail.is_hidden()


def test_search_filters_rows(page):
    page.fill("#search", "pfo")
    ids = page.evaluate("[...document.querySelectorAll('#analytes .row')].map(r => r.dataset.id)")
    assert set(ids) == {"pfoa", "pfos"}
    page.fill("#search", "")
    assert page.locator("#analytes .row").count() > 50


def test_theme_switch_rebuilds_layers(page):
    page.click("#btn-theme")
    page.wait_for_function("window.__sbw.ready === true", timeout=40000)
    time.sleep(1.0)
    assert page.evaluate("!!window.__sbw.map.getLayer('sys-fill') && !!window.__sbw.map.getLayer('sys-label-big')")
    assert page.evaluate("document.body.dataset.theme") == "light"
    page.screenshot(path=str(SHOTS / "e2e-04-light.png"))
    page.click("#btn-theme")
    page.wait_for_function("window.__sbw.ready === true", timeout=40000)
    assert not page.errors, page.errors


def test_lab_mode_prefers_distribution_samples_and_offers_raw_toggle(page):
    page.click(".seg-btn[data-source=lab]")
    page.wait_for_timeout(500)
    assert not page.locator("#lab-options").evaluate("e => e.classList.contains('hidden')")
    assert "distribution" in page.text_content("#legend-note")
    before = state(page)["counts"]
    page.click("label#lab-options .switch")
    page.wait_for_timeout(500)
    assert "all sampling points" in page.text_content("#legend-note")
    assert state(page)["counts"]["red"] == before["red"]
    page.click("label#lab-options .switch")
    page.click(".seg-btn[data-source=ccr]")
    page.wait_for_timeout(300)
    assert page.locator("#lab-options").evaluate("e => e.classList.contains('hidden')")
