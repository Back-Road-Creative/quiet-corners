"""Frontend contract constants for exported JSON files.

Single source of truth for the fields the dashboard JS reads by name.
Both the runtime validator and the contract tests import from here, so
production installs never depend on the tests package.
Source: quiet-corners-dashboard.html filterCounties(), makePopupHtml(),
renderTable(), renderCandidateRow(), renderSparklines().
"""

from collections.abc import Mapping

from src.core.models import FACTOR_STATUSES, FACTOR_UNKNOWN

COUNTY_REQUIRED_FIELDS = set(
    "fips state name population rucc stability_score cv slope_pct nearest_metro "
    "distance_to_metro_mi sprawl_risk sprawl_factors affordability_score "
    "median_home_value median_rent quietness_score overall_score".split()
)

SPRAWL_FACTOR_FIELDS = set(
    "interstate permits migration metro_growth recreation".split()
)

# Additive evidence contract (WS-QC1). Version 1 adds, and removes nothing:
#   counties.json / candidates.json  sprawl_factor_status  {factor: status}
#   summary.json                     evidence_schema_version, and
#                                    sprawl_factor_availability {factor: {status: n}}
# The block is deliberately not in COUNTY_REQUIRED_FIELDS: published files that
# predate it must stay readable, and the validator only checks it where present.
# `sprawl_factors` scores are unchanged — an unavailable factor still reads 0.0
# there, so a reader that wants "was there data" must use the status block.
EVIDENCE_SCHEMA_VERSION = 1
SPRAWL_FACTOR_STATUS_KEY = "sprawl_factor_status"
SPRAWL_FACTOR_STATUS_VALUES = frozenset(FACTOR_STATUSES)

GEO_REQUIRED_FIELDS = set(
    "fips lat lon stability_score sprawl_risk overall_score".split()
)

SUMMARY_REQUIRED_FIELDS = set(
    "pipeline_version generated_at total_counties_scored total_rural_counties "
    "total_states coverage sprawl_factor_coverage data_sources sources "
    "remoteness_cap_mi".split()
)

CANDIDATE_REQUIRED_FIELDS = set(
    "fips name state county_fips population lat lon".split()
)

# navigation-places.json — the map's orientation labels. site-headlessmode's
# data contract reserves the identifiers `t` and `pt` for candidate records.
NAVIGATION_PLACE_REQUIRED_FIELDS = set(
    "geoid name display_name state tier lat lon".split()
)

# places-index.json + places-<ST>.json — EVERY gazetteer place, one shard per
# state, so the map can label a town no scored layer carries. Flat files: the
# promote step in main.do_export moves each staged file with os.replace and
# cannot move a directory. A shard is {state, fields, places}; each place is a
# list ordered by PLACE_SHARD_FIELDS (a record per place as an object would be
# ~3x the bytes). The index is {ST: {bbox: [w, s, e, n], count}}.
PLACE_INDEX_FILE = "places-index.json"
PLACE_SHARD_PREFIX = "places-"
PLACE_SHARD_FIELDS = ("geoid", "display_name", "lat", "lon", "population")
PLACE_SHARD_REQUIRED_FIELDS = {"state", "fields", "places"}
PLACE_INDEX_ENTRY_FIELDS = {"bbox", "count"}
# Measured against the 2024 sources: 32,333 places, well under both.
PLACE_SHARD_MAX_BYTES = 150_000
PLACE_SHARDS_MAX_TOTAL_BYTES = 2_000_000

# places-national.json — the head of the SAME ranked list, one fetch, so a
# national or multi-state view labels from the same ladder before any state's
# shard is requested. Without it the map's town ladder below the shard zoom
# was candidates.json alone (1K-3K scored towns), and zooming out ADDED small
# towns instead of shedding them (site place-labels.test.mjs, 2026-10-07).
# Each row carries its state so the client drops it once that shard loads.
# 1,500 rows is ~85KB at 2024 (under PLACE_SHARD_MAX_BYTES, so one fetch costs
# no more than one shard); every row has an estimate (null-population rows
# rank last and never reach the head).
PLACE_NATIONAL_FILE = "places-national.json"
PLACE_NATIONAL_FIELDS = PLACE_SHARD_FIELDS + ("state",)
PLACE_NATIONAL_HEAD = 1_500


def place_shard_name(state: str) -> str:
    return f"{PLACE_SHARD_PREFIX}{state}.json"


CANDIDATE_ENRICHED_FIELDS = set(
    "county_name stability_score distance_to_metro_mi nearest_metro sprawl_risk "
    "affordability_score quietness_score overall_score".split()
)

# and no longer part of the validated file set. Retired in the follow-up.

# counties-10m.json — the county geometry served to the browser — is TopoJSON,
# a nested document, not a list of records. Its contract is therefore a type
# marker, a path down to the geometry collection, and the shape of a geometry
# id, so it cannot ride the *_REQUIRED_FIELDS sets above (which are flat sets of
# top-level keys checked per entry). It lives here all the same: one module
# still says what the dashboard is entitled to find in every file it fetches.
TOPOJSON_TYPE = "Topology"
GEOMETRY_COLLECTION_PATH = ("objects", "counties", "geometries")
# Geometry ids are the 5-digit county FIPS strings the counties.json
# cross-check compares against; an int or a zero-stripped "1001" silently
# matches nothing.
GEOMETRY_ID_LENGTH = 5

_SCORES = (
    "stability_score affordability_score quietness_score overall_score sprawl_risk"
)

# Fields that, when non-null, must be numeric and within 0-100.
SCORE_RANGE_FIELDS: dict[str, set[str]] = {
    "counties.json": set(_SCORES.split()),
    "geo.json": set("stability_score sprawl_risk overall_score".split()),
    "candidates.json": set(_SCORES.split()),
}

# Fields that must be non-null in every entry: ranking always sets these,
# so a null-riddled export means the pipeline broke upstream.
NONNULL_FIELDS: dict[str, set[str]] = {
    "counties.json": set("fips state name quietness_score overall_score".split()),
    "geo.json": {"fips"},
}


def factor_status_of(entry: Mapping[str, object]) -> dict[str, str]:
    """The tolerant reader: every sprawl factor's status for one exported
    county or candidate record.

    A record from before the evidence block (no `sprawl_factor_status`), a
    factor the block omits and a value this version does not know all come back
    as "unknown" — never inferred from a score, because a 0.0 score is exactly
    what cannot say whether data existed. Safe against any producer version.
    """
    block = entry.get(SPRAWL_FACTOR_STATUS_KEY)
    given = block if isinstance(block, Mapping) else {}
    return {
        name: (
            given[name]
            if given.get(name) in SPRAWL_FACTOR_STATUS_VALUES
            else FACTOR_UNKNOWN
        )
        for name in sorted(SPRAWL_FACTOR_FIELDS)
    }
