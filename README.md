# southbay-water

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
