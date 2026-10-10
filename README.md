# southbay-water

**Live site:** https://bla5756-lab.github.io/sbw-map/ (static GitHub Pages build of this repo; no server).
Also self-hosted on zgx at http://100.68.6.10:8100 for the LAN.

Self-hosted map of drinking-water quality across the water systems of Santa Clara County
(the South Bay). Every service-area polygon is colored by the measured level of a chosen
analyte relative to its California drinking-water standard. Nothing is synthesized: values come
from each system's published Consumer Confidence Report (CCR) and from the State Water Board's
laboratory results database.

Color legend
- heat ramp: measured level as a fraction of the standard (MCL / action level / secondary MCL)
- **gray**: a report exists for the system, but it does not include the selected analyte
- **red**: no report could be found for the system

Layout
- `config/` analyte catalogue (standards, units, name aliases) and app settings
- `src/pipeline/` data acquisition and the merge that produces `data/site.json`
- `src/server/` the HTTP server (static site + JSON API)
- `src/web/` the browser app
- `data/` boundaries, per-system CCR extracts, state lab extracts, merged site data
- `reports/` the downloaded CCR documents, one folder per water system ID
- `tests/` mirrors `src/` (`*_tests`), plus browser end-to-end checks
- `deploy/` systemd unit and install script

How it is built
- `python -m src.pipeline.build_site` merges `data/boundaries.geojson`, `data/ccr/*.json` and
  `data/sdwis/santa_clara_results.json` into `data/site.json` + `data/site.geojson`.
- `python -m src.pipeline.build_static` copies the page, assets, data and reports into `site/`,
  which is what GitHub Pages serves (`.github/workflows/pages.yml` does both on every push to
  `dev` or `main`). The page fetches `data/site.json` with a relative path, so it works from any
  folder or from a plain file server.
- `src/server/app.py` is an optional local server for development (`deploy/` installs it as a
  user systemd unit on zgx).
- Tests: `tests/run.sh` (unit) and `tests/run.sh e2e` (adds the headless-Chromium suites, one
  against the dev server and one against the static bundle served by `python -m http.server`).

Discoverability: the page carries `noindex, nofollow` and the site ships a `robots.txt` that
disallows all crawlers; the repository has no topics or description on purpose.
