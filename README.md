# svc-quiet-corners

Census-driven pipeline identifying remote, stable, low-sprawl US towns.

## Purpose

Identify and score remote, stable, low-sprawl US towns using Census Bureau data. Produces ranked JSON output for the Quiet Corners dashboard on headlessmode.com.

## Owner

Back Road Creative

## Status

Active — restored 2026-07-30 alongside the dashboard's return to CI.
Scoring and export run end-to-end and all five sprawl-risk inputs are live:
building permits and metro growth were re-sourced from keyless www2.census.gov
bulk CSVs after their API endpoints were retired (see Data Sources). A county
missing an input is scored over the factors it has, with the remaining weights
renormalized. Exports feed the live dashboard at
https://headlessmode.com/quiet-corners/, regenerated monthly by cron
(`0 6 1 * *` running `bin/quiet-corners-refresh.sh --publish`; see
Headlessmode integration below). The
underlying Census releases are annual or slower, so most monthly runs change
no data.

Archived 2026-04-06 alongside the dashboard.

## Headlessmode integration

Quiet Corners is a feature of Headlessmode, not a separate product: its only
user-facing surface is the dashboard at https://headlessmode.com/quiet-corners/.
This repository is the source of the data that dashboard reads.

- **Destination.** The export (`python -m src.main --all <site>/static/data/quiet-corners --force`)
  is written into the Headlessmode site repository at `static/data/quiet-corners/`.
- **Refresh.** An operator script, `bin/quiet-corners-refresh.sh --publish`, runs
  monthly from cron (`0 6 1 * *`). It regenerates the export, refuses a
  regression against the published `summary.json` (`src/export/regression_guard.py`)
  and opens a dated pull request on the Headlessmode site; the data goes live only
  when that pull request merges. The script and crontab live with the operator,
  outside this repository.
- **Contract.** The Headlessmode site's `scripts/test_data_contract.py` checks
  every field the dashboard script reads against the committed export; this
  repository's `tests/test_export_contract.py` and `tests/test_source_lineage.py`
  pin the export side.

## Exit Condition

Archive when the Quiet Corners dashboard is deprecated from headlessmode.com, or when Census data sources are no longer maintained.

## Setup

Requires `CENSUS_API_KEY` — `api.census.gov` refuses keyless queries (it
answers with `missing_key.html` as HTTP 200, which is why a keyless run
used to look like it worked). A key is free:
https://api.census.gov/data/key_signup.html

```bash
export CENSUS_API_KEY=<key>
python -m src.main --fetch                                          # download census data
python -m src.main --score                                          # compute scores
python -m src.main --export /path/to/hugo/static/data/quiet-corners/
python -m src.main --all /path/to/hugo/static/data/quiet-corners/   # fetch + score + export
```

The export is built in a staging directory beside the target and promoted only
after the contract validator passes on it, so a failed export exits non-zero and
leaves the previously published files serving.

`summary.json` records a `sources` block — size, UTC mtime and sha256 (plus the
pinned download `url`) for every raw input file the run actually opened, not
every download sitting in `data/raw` — so a published export names the bytes it
came from rather than only when it was written. `api_lineage` does the same for
the Census API responses behind the affordability, migration and (when the API
answers) county-population figures: the sanitized request (endpoint, dataset,
vintage, variables, geography), whether it came from the cache or a live call,
and a sha256 of the response. No API key or token is ever written to it.
`freshness` keeps three things apart: `source_vintage` (which data release),
`run_time` (when this export was generated, equal to `generated_at`) and
`refresh_schedule` (the declared monthly cron, not proof a run happened).
Its `total_states` counts the distinct
states of the exported rural counties; `states.json` is no longer produced.

Without a key the run refuses to start. `--allow-degraded` overrides that
and exports without affordability and state migration — the two sources
gated on `CENSUS_API_KEY` (see `KEYED_SOURCES` in `src/main.py`). Building
permits and metro growth are separate, keyless bulk-CSV sources, unaffected
by `--allow-degraded` either way (see Data Sources for their current
status). Once their in-flight re-source lands, sprawl risk in a degraded
run comes from 4 of its 5 factors — state migration is the only one gated
on the key — renormalized so scores stay on the same 0-100 scale. Use
`--allow-degraded` for plumbing checks, not for anything that will be
published.

### Flags

- `--fetch` / `--score` / `--export <dir>` / `--all <dir>` — pipeline stages (see Setup)
- `--data-dir <dir>` — raw data directory (default `data/raw`)
- `--force` — re-download `--fetch` data files even if still within their TTL
- `--allow-degraded` — run without `CENSUS_API_KEY`, accepting the missing dimensions above
- `--estimate-rucc` — opt in to estimating missing RUCC codes from population + remoteness.
  A county without a RUCC code is filtered out of every exported file, so *any* gap stops the
  run: fix the source, or pass this to classify the gap deliberately.
- `--acs-year <year>` — ACS vintage for affordability/migration fetches (default 2023)
- `--version` — print the pipeline version and exit

### navigation-places.json

The map labelled candidate towns and nothing else, so a national view showed 47
unfamiliar small towns and no city to locate them against. This is the
orientation layer, built by `src/export/navigation_places.py` from the place
gazetteer and `sub-est2024.csv`. `candidates.json` is untouched: widening it
would change its ranking contract, and the site reserves `t`/`pt` for it.

Fields are `geoid`, `name`, `display_name`, `state`, `tier`, `lat`, `lon`
(`NAVIGATION_PLACE_REQUIRED_FIELDS` in `src/export/contract.py`). `name` is the
Census legal name, kept for details panels; `display_name` drops the legal
suffix and any "(balance)", case-sensitively, so "Bullhead City city" stays
"Bullhead City". `tier` is `major` (population >= 250,000, the national view)
or `regional` (>= 100,000) — the plan asked for county seats regionally, but
nothing here loads a county-seat list. Records run largest first, so the site
thins the layer from the tail.

Against the 2024 sources that is 342 places over the floor plus the largest
place in each of the five states with nothing over it (DE, ME, VT, WV, WY):
**347 records, 46KB minified, every state covered.** A 25K floor would give
1,606 — a second copy of `candidates.json`. The file is staged and promoted
with the rest but is not in `ARRAY_FILE_CONTRACTS`, because a registered file
is a required one and `export_all`'s callers supply none; `tests/test_navigation_places.py` is its contract test.

### places-index.json and places-<ST>.json

`navigation-places.json` stops at 100K people, so a real town such as Franklin, NC
(GEOID 3724640, 4,362) appears in no file the map can label. These are the complete
tier: **every** gazetteer place with coordinates, one shard per state, built by
`build_all_places` in `src/export/navigation_places.py` (gazetteer rows left-joined to
`sub-est2024.csv` population by GEOID, so a CDP the estimates omit is kept with a null
population). Ranking is population descending (null last), then land area descending;
`navigation-places.json` is the head of that same ranked list, with its floors and state
anchors unchanged, so orientation and local labels cannot diverge.

`places-index.json` is `{ST: {bbox: [west, south, east, north], count}}`; each
`places-<ST>.json` is `{state, fields, places}` with `fields` =
`geoid, display_name, lat, lon, population` and each place a list in that order
(`PLACE_SHARD_FIELDS` in `src/export/contract.py`). They are flat files in the export
directory because `do_export` promotes each staged file with `os.replace`, which cannot
move a directory; promotion also deletes a published `places-*.json` the new export does
not carry. The contract requires the index, a well-formed shard per listed state, each
shard at most 150KB and the set at most 2MB, and `do_export` additionally checks that the
shard total equals the gazetteer rows with coordinates (32,333 at 2024), so no population
window can drop a town. `candidates.json` is untouched.

### places-national.json

The head of that same ranking — the `PLACE_NATIONAL_HEAD` (1,500) largest places that
have an estimate — as one fetch, `{fields, places}` with `fields` = the shard fields plus
`state`. The map requests a state's shard only once the view is zoomed in on it, so
below that zoom its town ladder was `candidates.json` alone (scored 1K-3K towns), and
zooming out *added* small towns instead of shedding them. With the head loaded from the
first label zoom, a national or multi-state view and a one-state view rank from one
list; the per-row `state` lets the client drop a head row once that state's shard is in.
The contract requires the file, at most 1,500 rows in rank order, every row present in
its state's shard with the same record, and the file under the per-shard 150KB (~85KB
at 2024).

## Data Sources

- Census Bureau PEP API (county populations; the state-population vintages are retired)
- Census Gazetteer (place coordinates)
- USDA Rural-Urban Continuum Codes
- USDA County Typology Codes, 2025 edition (recreation/retirement; parser prefers its long format)
- Census ACS 1-Year B07001 (state migration acceleration)
- BPS county bulk CSV (county-level permit trends) — the API timeseries endpoint is retired; permits now come from the keyless annual county files at www2.census.gov/econ/bps/County/
- PEP subcounty bulk CSV (fast-growing metro detection, a sprawl-risk input) — the place API endpoint is retired; metro growth now comes from the keyless sub-est2024.csv
- Census Subcounty Population Estimates (candidate towns; SUMLEV 157 place-part rows
  give the containing county — a split place is assigned to the county holding most
  of it, and only places with no part row fall back to nearest-centroid matching)

## Lint

`ruff.toml` extends `pyproject.toml`'s `[tool.ruff.lint]` select (E F W I S) and
turns on any additional rule family that is already at zero findings, so that
family can never regress silently. A family is added to `extend-select` only
after a separate change brings its findings to zero — never alongside the code
fix that gets it there.

## Architecture

- `src/core/` — config (FIPS, thresholds), dataclass models
- `src/ingest/` — Census API client, population fetchers, data downloader
- `src/scoring/` — stability (CV+slope), remoteness (haversine), sprawl risk, composite
- `src/export/` — Hugo JSON export
