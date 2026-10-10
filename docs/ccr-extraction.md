# CCR extraction contract

One JSON file per water system at `data/ccr/<PWSID>.json`, and the downloaded report(s) under
`reports/<PWSID>/`. Values are copied from the report as printed; nothing is estimated.

## Which report
The most recent Consumer Confidence Report (CCR, "annual water quality report") the system has
published. CCRs for calendar year 2025 were due 2026-07-01, so prefer the 2025 report; fall back to
2024, then 2023. Record the data year the report covers, not the year it was published.

## Where to look
- The system's own website (`/water-quality`, `/ccr`, "Consumer Confidence Report", "Annual Water
  Quality Report"); city utilities often host it under Public Works or Utilities.
- Web search for `"<system name>" "consumer confidence report" 2025` and the 2024 variant.
- For Cal Water (`CWSC`), Cal Am and Great Oaks: the company site has a per-district report page.
- City of San Jose Municipal Water publishes one report covering both of its systems
  (North San Jose/Alviso and Evergreen/Edenvale/Coyote); Cupertino's system is operated by San Jose
  Water under lease and has its own report.
- Small mutual water companies usually have no website. Try a search, the Santa Clara County
  Department of Environmental Health small-water-systems pages, and the State Water Board's
  Drinking Water Watch (`https://sdwis.waterboards.ca.gov/PDWW/`) for a report link. Spend at most
  about ten minutes per small system; if nothing is published, record `not_found` with the
  `search_log` of what was tried. A truthful `not_found` is the correct outcome and shows red on the
  map.

## Download rules
- `curl -L --fail -A "Mozilla/5.0" -o reports/<PWSID>/ccr-<data_year>.pdf <url>`; confirm with
  `file` that it is a PDF (an HTML page saved as .pdf is a failure). HTML-only reports are saved as
  `ccr-<data_year>.html`, with `format: html`.
- Record `sha256sum` of the saved file.
- Extract text with `pdftotext -layout <pdf> -` and read the tables from that; `pypdf` is available
  too. Run any Python that reads downloaded files with `python3 -I`.
- Never run git. Write only inside `reports/<PWSID>/` and `data/ccr/<PWSID>.json`.

## JSON schema
```json
{
  "pwsid": "CA4310011",
  "system_name": "SAN JOSE WATER",
  "status": "found",
  "report": {
    "data_year": 2025,
    "title": "2025 Annual Water Quality Report",
    "url": "https://example.org/ccr-2025.pdf",
    "file": "reports/CA4310011/ccr-2025.pdf",
    "format": "pdf",
    "sha256": "<hex>",
    "pages": 12,
    "retrieved": "2026-10-10"
  },
  "sources_described": "one or two sentences: where the water comes from, as the report states",
  "search_log": ["URL or query tried -> what happened"],
  "analytes": [
    {
      "id": "arsenic",
      "raw_name": "Arsenic",
      "unit_raw": "ppb",
      "value": 1.2,
      "value_basis": "average",
      "average": 1.2,
      "range_low": 0.5,
      "range_high": 3.0,
      "detected": true,
      "mcl_printed": "10",
      "phg_printed": "0.004",
      "source_group": "Groundwater",
      "sample_year": 2025,
      "page": 4,
      "notes": null
    }
  ],
  "violations": ["verbatim or closely paraphrased text of any violation or exceedance the report discloses"],
  "extraction_notes": "anything ambiguous about the tables"
}
```

Field rules
- `status`: `found` or `not_found`. For `not_found`, `report` is null, `analytes` is `[]`, and
  `search_log` is required.
- `id`: the canonical id from `config/analytes.yaml` whose `aliases` match `raw_name`; `null` when
  the analyte is not in the catalogue (keep `raw_name` so it still shows in the detail panel).
  Nitrate printed "as NO3" keeps `id: nitrate_n` with `unit_raw: "ppm as NO3"` - the pipeline converts.
- `unit_raw`: exactly as printed (`ppb`, `ppm`, `ug/L`, `mg/L`, `pCi/L`, `NTU`, `uS/cm`,
  `umhos/cm`, `grains/gal`, `ppt`, `ng/L`, `%`, `pH units`...). Do not convert.
- `value`: the one number the map uses for this entry. Use the average if the report gives one;
  else the 90th percentile (lead/copper); else the highest running annual average (TTHM/HAA5);
  else the highest single result or range high. `value_basis` names which. `null` when the
  analyte is reported as ND / not detected (`detected: false`).
- `average`, `range_low`, `range_high`: as printed; `null` when absent. A range printed as
  "ND - 3.0" has `range_low: 0`, `range_high: 3.0`.
- `source_group`: when the table has separate columns per water source (Groundwater, Surface
  water, Imported/SFPUC, Valley Water treated...), write one entry per analyte per column and name
  the column here. Otherwise `null`.
- `mcl_printed`, `phg_printed`: strings as printed (`"10"`, `"TT"`, `"n/a"`), `null` when absent.
- `page`: 1-based page of the PDF where the row appears.
- Include every row in the report's data tables: regulated primary contaminants, radiologicals,
  disinfection byproducts and residuals, lead and copper, microbial, secondary standards,
  unregulated contaminants (UCMR), PFAS, and general mineral rows (hardness, sodium, alkalinity,
  pH...). Rows the report lists as not detected still get an entry with `detected: false`.
