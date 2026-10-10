"""Open the live site in headless Chromium, capture console errors and screenshots for review."""
import json, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8100/"
out = Path(__file__).parent / "screenshots"
out.mkdir(exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 950}, device_scale_factor=1)
    errors, warnings = [], []
    pg.on("console", lambda m: (errors if m.type == "error" else warnings if m.type == "warning" else []).append(m.text))
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    pg.goto(url, wait_until="networkidle")
    pg.wait_for_function("window.__sbw && window.__sbw.ready === true", timeout=30000)
    time.sleep(2.5)  # let tiles/labels settle
    pg.screenshot(path=str(out / "01-default.png"))
    state = pg.evaluate("({counts: window.__sbw.counts, enabled: [...window.__sbw.enabled], source: window.__sbw.source, n: window.__sbw.geo.features.length})")
    print("state", json.dumps(state))
    # hover over Gilroy area and screenshot tooltip
    pg.mouse.move(600, 820); time.sleep(0.6); pg.screenshot(path=str(out / "02-hover.png"))
    # click to open detail
    pg.mouse.click(600, 820); time.sleep(0.8); pg.screenshot(path=str(out / "03-detail.png"))
    # solo arsenic via sidebar name click
    pg.click("div.row[data-id='arsenic'] .name"); time.sleep(0.8); pg.screenshot(path=str(out / "04-solo-arsenic.png"))
    print("errors", json.dumps(errors, indent=1)[:3000])
    print("warnings", json.dumps(warnings[:10], indent=1)[:1500])
    b.close()
