# State laboratory results layer (SDWIS)

The second data layer of the map: every laboratory result the State Water Board holds for the
120 Santa Clara County water systems, 2023-01-03 to 2026-07-14. Produced by
`src/pipeline/sdwis_extract.py`; consumed by `src/pipeline/build_site.py` (`attach_sdwis`).

## What the data is

The Division of Drinking Water (DDW) publishes its database of record, SDWIS, as flat files on the
"EDT library" page: https://www.waterboards.ca.gov/drinking_water/certlic/drinkingwater/EDTlibrary.html

| File | Covers | Used here |
|---|---|---|
| `SDWIS4.zip` -> `SDWIS4.tab` | 2023-01-01 to 2025-12-31 | yes: 6,280,099 rows, 1.92 GB uncompressed |
| `SDWIS5.zip` -> `SDWIS5.tab` | 2026-01-01 to present | yes: 1,175,152 rows, 357 MB uncompressed |
| `data_dictionary.pdf` | field definitions, revision 12/2021 | read with `pdftotext -layout` |

Results reach SDWIS from certified laboratories through the CLIP portal (since 2021-09-01); the
page states that "data that does not meet DDW data quality objectives is not included". The rows
are individual analyses of individual samples: raw groundwater at wells, surface-water intakes,
treatment-plant effluent and distribution-system samples (including Lead and Copper Rule tap
samples and disinfection-byproduct samples). They are not compliance determinations.

Downloaded 2026-10-10 into `~/southbay-water-raw/` (outside the repo). Both `.tab` members are
stamped 2026-07-23 05:10 inside the zips and the newest sample in SDWIS5 is 2026-07-14, although
the page says SDWIS5 was "last update September 15, 2026" - the copy in hand is the July build;
re-download before the next refresh. SHA-256 of each zip is recorded in the results JSON.

## Running it

```
python3 -I src/pipeline/sdwis_extract.py ~/southbay-water-raw/SDWIS4.zip ~/southbay-water-raw/SDWIS5.zip
```
(`-I` because the archives are untrusted data; defaults point at `data/boundaries.geojson`,
`config/analytes.yaml`, `src/pipeline/sdwis_analytes.yaml` and `data/sdwis/`.) Runtime about
3 minutes; the `.tab` members are streamed line by line out of the zips, never unpacked or held in
memory. Tests: `python3 -m pytest tests/pipeline_tests/test_sdwis_extract.py -q` (on zgx the
ROS `launch_testing` pytest plugin on `PYTHONPATH` breaks collection; run with `env -u PYTHONPATH`).

Outputs
- `data/sdwis/santa_clara_rows.tsv.gz` - the 128,024 kept rows plus header, all 29 original
  columns and their padding untouched, LF line endings, UTF-8 (the source is cp1252), gzip with a
  zero mtime so re-runs are byte-identical.
- `data/sdwis/santa_clara_results.json` - `generated`, `source` (URLs, file sizes, SHA-256,
  member timestamps, row counts, kept date ranges, the non-detect rule, result-kind counts, the
  120 targets and which lack rows, analyte codes not listed in the map), and `systems`: one entry
  per target PWSID (also the three with no rows) with `name`, `n_rows`, `latest_sample`,
  `sampling_points`, `point_types` and `analytes`, keyed by canonical id when mapped, otherwise by
  the raw analyte name. Each analyte entry: `id`, `raw_name`, `storet` (Analyte Code), `unit` (as
  in the data), `n`, `n_detected`, `n_below_dlr`, `latest_date`, `latest_value`, `max`,
  `mean_detected`, `sampling_points`, `point_types`, `dlr`, `mcl`, plus `other_storets` when two
  codes fold into one id and `units_seen` when a key carries more than one unit (none do today).
  `latest_value` is the highest detected value among the rows dated `latest_date`, or null when
  the latest sample was a non-detect.

## Columns

The files are tab-delimited with CRLF line endings, no quoting, cp1252 bytes (`§`, en dash,
no-break space), and several fields right-padded with spaces (`CA4310011   `, `UG/L     `, the
analyte name). Every value is `.strip()`ed before use. 16 lines in the full files (none in Santa
Clara) have a stray tab inside Sampling Point Name and 30 fields; such rows are written to the TSV
and counted in `rows_malformed` but not aggregated.

| Column | Meaning (data dictionary) | Used |
|---|---|---|
| Regulating Agency | DDW district or LPA with primacy | - |
| Water System Number | PWSID `CAxxxxxxx` | filter key |
| Water System Name | | system `name` |
| System Status | A active / I inactive / P proposed | - |
| Federal / State Water System Classification | C, NC, NTNC, NP | - |
| Principal County Served, Population Served, Service Connections | | - |
| PS Code | `<PWSID>_<facility>_<sampling point>`; distribution points use `DST` | `sampling_points` (distinct codes) |
| Sampling Point Name | e.g. `WELL 02`, `LCR Tap Sample`, `DBPR Sample` | - |
| Facility Type | WL well, DS distribution, IN intake, TP treatment plant, SP spring, CC consecutive connection, OT other, SS sampling station, CH common header | `point_types` |
| Facility Status | | - |
| Sample Type | not in the 12/2021 dictionary; every Santa Clara row is `RT` (routine); `SP` and `A` occur elsewhere | counted in `source.sample_types` |
| Sample Date | `MM-DD-YYYY` in the files (the dictionary says `YYYY-MM-DD`; both are accepted) | dates |
| Sample Time, Analysis Date, ELAP Cert#, Lab Name | | - |
| Analyte Code | four-character SDWIS code, some alphabetic (`C253`, `C080`) | mapping key, `storet` |
| Analyte Name | | `raw_name`, mapping fallback |
| Result | numeric or blank; never `ND`/`<` in these files | values |
| Counting Error | radionuclides only (1,202 rows) | - |
| Units of Measure | UG/L, NG/L, MG/L, PCI/L, UMHO/CM, PH, NTU, UNITS, TON, MFL, C, AGGR, LANG | `unit`, never converted |
| Less Than Reporting Level | Y non-detect, N detection | non-detect rule |
| Reporting Level | the laboratory's reporting level | - |
| DLR | state detection limit for purposes of reporting (22 CCR 64400.34); blank for half the rows | non-detect rule, `dlr` |
| MCL | the standard the state attaches to the code; blank for unregulated analytes | `mcl` |
| Method | analytical method (CLIP submissions only) | - |

## Non-detects

In these files the flag and the result are perfectly aligned: every `Y` row has a blank Result
(102,098 rows) and every `N` row has a number (25,926 rows). The rule applied, in order:

1. `Less Than Reporting Level` = `Y` -> non-detect (102,098 rows).
2. Result blank, non-numeric, or starting with `<` -> non-detect (0 rows here; defensive).
3. Result <= 0 -> non-detect (98 rows, all Langelier index, an unmapped analyte for which a
   negative value is a legitimate reading - harmless, but do not map it without changing this).
4. DLR numeric and Result < DLR -> non-detect, counted in `n_below_dlr` (976 rows, e.g. free
   copper 12 UG/L against a DLR of 50; chiefly copper, barium, gross alpha, radium-228, nitrate,
   THM species). The state itself does not treat findings below the DLR as reportable, so the
   layer follows that; the numbers survive in the TSV if the lead prefers to use them.
5. Everything else is a detection (24,852 rows). Values are used as reported.

`max` and `mean_detected` are over detections only; `n` counts every row.

## Analyte mapping (`src/pipeline/sdwis_analytes.yaml`)

292 distinct analyte codes occur in the Santa Clara rows (each code has exactly one name and one
unit). 84 codes map to 82 catalogue ids; 208 keep `id: null` and are keyed by their raw name
(individual THM and HAA species, 120-odd pesticides and VOCs, PFAS beyond the catalogue nine,
Aroclors, orthophosphate, Langelier/aggressive indices, nitrate+nitrite, carbonate alkalinity...).
Decisions worth knowing:
- Nitrate (1040) is reported **as N**: the file carries MCL 10 and DLR 0.4 for it (as NO3 would be
  45 and 2), so it maps to `nitrate_n` with unit `MG/L` and no conversion label. The yaml has a
  `unit_label` field (and the extractor honours it) for the case where a future code is as NO3;
  `build_site.normalize_unit` understands `MG/L as NO3`. `NITRATE-NITRITE` (1038) is left unmapped.
- `PH` (1925) and `PH, FIELD` (C253) fold into `ph`; `COMBINED RADIUM` (4010) and the 4-row
  `TOTAL RADIUM FOR NTNC PER §64442(B)(3)` (C080) fold into `radium_combined`.
- `COPPER, FREE` (1022) is the Lead and Copper Rule tap-sample copper code -> `copper`;
  `LEAD` (1030) -> `lead`. See the caveat below.
- `CARBON, TOTAL` (2920) is SDWIS's total organic carbon code -> `total_organic_carbon`.
- Units stay as reported: UG/L where the catalogue says ppm (barium, zinc, copper) or ppt
  (1,2,3-TCP, DBCP); NG/L for PFAS; UMHO/CM for conductance. `build_site` converts all of them
  (verified: every unit/target pair in the output converts).
- Catalogue ids with no rows at all: chlorine_residual, chloramines, total_coliform, e_coli,
  heterotrophic_bacteria, radon, molybdenum, ndma, nmor, pfas_total (microbial and residual
  disinfectant monitoring is not in these files).
- Mapped ids never detected in any system: antimony, atrazine, benzene, beryllium, bromate,
  carbon_tetrachloride, chlorite, cyanide, dbcp, dce_cis_12, dichloromethane, dioxane_14,
  gross_beta, lithium, mercury, mtbe, nitrite_n, radium_226, tce, tcp_123, thallium, xylenes.
- The catalogue spells one id `pfbA` (capital A); the map uses it as given.

## Coverage

- 128,024 rows kept: 101,474 from SDWIS4 (2023-01-03 to 2025-12-30) and 26,550 from SDWIS5
  (2026-01-05 to 2026-07-14). No duplicates across the two files, no unparseable dates.
- 117 of the 120 systems have rows; 96 have a 2026 sample, 20 a 2025 one, 1 (CA4300748) nothing
  after 2024. Without any rows: CA4300752 Maria Lane Mutual Water Company (12 connections),
  CA4300779 Lakeside SD - Lakeside School (NTNC), CA4301062 Lee's Orchard Owners Association
  (13 connections).
- Sampling points: 99,849 rows at wells, 20,639 distribution, 3,288 intakes, 2,472 treatment
  plants, 901 other, 588 springs, 265 consecutive connections, 22 SS/CH.
- Systems with the most rows: CA4310011 San Jose Water 48,043; CA4310001 CWSC Los Altos Suburban
  15,868; CA4310012 City of Santa Clara 7,216; CA4310004 City of Gilroy 6,331; CA4310020 City of
  San Jose - EVG/EDV/COY 5,380; CA4310022 Great Oaks WC 5,044; CA4310006 City of Morgan Hill 4,913;
  CA4310014 City of Sunnyvale 2,947; CA4310013 Stanford University 2,172; CA4310007 City of
  Mountain View 2,047.
- Most widely monitored canonical analytes: nitrate_n (103 systems), nitrite_n (101), copper
  (91), lead (90), fluoride (78), chromium_6 (77), perchlorate (75), tcp_123 (75), the Title 22
  inorganic metals (73), tthm/haa5 (71).

## Caveats

- **Lead and copper are individual tap samples, not the 90th percentile** that the action level
  applies to. San Jose Water's `max` lead is 810 UG/L (one tap, 2025-09-05) and `build_site`
  currently colours by `max`, which would show 54x the action level. Either compute a 90th
  percentile per monitoring period from the TSV or colour lead/copper by `latest_value`/`mean`.
- Raw-water results dominate (81% of rows are wells and intakes). A well's result is not what
  customers drink: sources may be treated, blended or unused. The per-analyte `point_types`
  tells how much of each aggregate is distribution (`DS`) versus source water.
- `max` over three and a half years and all points is a conservative, not a representative,
  number; `mean_detected` ignores non-detects and so overstates typical levels for rarely
  detected analytes. TTHM/HAA5 compliance is a locational running annual average, not any of these.
- The county's wholesaler, CA4310027 Santa Clara Valley Water District (25,021 rows, treated
  surface water delivered to most of the retailers), has no retail service area and is not among
  the 120 boundaries, so its results are not in this layer. Six other inactive or non-public
  CA43 systems in the data are likewise outside the boundaries (CA4310028 San Jose State University,
  CA4300976, CA4300939, CA4300971, CA4300989, CA4300546; 421 rows together).
- Detections below the state DLR (976 rows) are counted as non-detects; see above.
- Microbial results, residual disinfectant readings and the 2011-2022 files are not included.
- The analyte catalogue of the state (`STORET` supporting file) was not downloaded; code meanings
  above come from the names in the data and the dictionary.
