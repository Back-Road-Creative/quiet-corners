"""
Quiet Corners CLI entry point.

Usage:
    python -m src.main --fetch              # Download census data
    python -m src.main --score              # Compute all scores
    python -m src.main --export <path>      # Export JSON for Hugo
    python -m src.main --all <path>         # Full pipeline
"""

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, TypeVar

from src.core.config import DEFAULT_CONFIG
from src.core.models import County, Metro
from src.export.hugo_export import (
    PIPELINE_VERSION,
    SPRAWL_FACTOR_ATTRS,
    HugoExporter,
    factor_available,
)
from src.ingest import consumption
from src.ingest.affordability import fetch_affordability_data
from src.ingest.building_permits import fetch_building_permits
from src.ingest.census_api import (
    KEY_SIGNUP_URL,
    CensusAPIError,
    CensusClient,
)
from src.ingest.county_populations import (
    fetch_county_populations,
    load_county_populations_csv,
    parse_county_data,
)
from src.ingest.data_downloader import (
    download_all,
    load_county_gazetteer,
    load_county_typology,
    load_metros_from_gazetteer,
    load_rucc_codes,
)
from src.ingest.interstate_waypoints import distance_to_nearest_interstate
from src.ingest.metro_growth import load_fast_metros, nearest_fast_metro
from src.ingest.place_populations import (
    assign_county_fips,
    enrich_places_with_gazetteer,
    load_subcounty_estimates,
)
from src.ingest.state_migration import (
    fetch_state_migration_accel,
    get_state_migration_accel,
)
from src.scoring.affordability import compute_affordability
from src.scoring.composite import compute_composite_rank
from src.scoring.remoteness import compute_remoteness
from src.scoring.sprawl_risk import compute_sprawl_risk
from src.scoring.stability import compute_stability

# Fallback metros when gazetteer is unavailable
FALLBACK_METROS: list[Metro] = [
    Metro("New York", 40.7128, -74.0060, 8_300_000),
    Metro("Los Angeles", 34.0522, -118.2437, 3_900_000),
    Metro("Chicago", 41.8781, -87.6298, 2_700_000),
    Metro("Houston", 29.7604, -95.3698, 2_300_000),
    Metro("Phoenix", 33.4484, -112.0740, 1_650_000),
    Metro("Philadelphia", 39.9526, -75.1652, 1_600_000),
    Metro("San Antonio", 29.4241, -98.4936, 1_500_000),
    Metro("San Diego", 32.7157, -117.1611, 1_400_000),
    Metro("Dallas", 32.7767, -96.7970, 1_340_000),
    Metro("Austin", 30.2672, -97.7431, 1_000_000),
    Metro("San Jose", 37.3382, -121.8863, 1_000_000),
    Metro("Jacksonville", 30.3322, -81.6557, 1_000_000),
    Metro("Fort Worth", 32.7555, -97.3308, 960_000),
    Metro("Columbus", 39.9612, -82.9988, 900_000),
    Metro("Charlotte", 35.2271, -80.8431, 900_000),
    Metro("Indianapolis", 39.7684, -86.1581, 880_000),
    Metro("San Francisco", 37.7749, -122.4194, 870_000),
    Metro("Seattle", 47.6062, -122.3321, 740_000),
    Metro("Denver", 39.7392, -104.9903, 710_000),
    Metro("Nashville", 36.1627, -86.7816, 700_000),
    Metro("Washington DC", 38.9072, -77.0369, 690_000),
    Metro("Oklahoma City", 35.4676, -97.5164, 680_000),
    Metro("El Paso", 31.7619, -106.4850, 680_000),
    Metro("Boston", 42.3601, -71.0589, 670_000),
    Metro("Portland", 45.5152, -122.6784, 650_000),
    Metro("Las Vegas", 36.1699, -115.1398, 650_000),
    Metro("Memphis", 35.1495, -90.0490, 630_000),
    Metro("Louisville", 38.2527, -85.7585, 630_000),
    Metro("Baltimore", 39.2904, -76.6122, 570_000),
    Metro("Milwaukee", 43.0389, -87.9065, 570_000),
    Metro("Albuquerque", 35.0844, -106.6504, 560_000),
    Metro("Tucson", 32.2226, -110.9747, 550_000),
    Metro("Fresno", 36.7378, -119.7871, 540_000),
    Metro("Sacramento", 38.5816, -121.4944, 520_000),
    Metro("Kansas City", 39.0997, -94.5786, 510_000),
    Metro("Mesa", 33.4152, -111.8315, 510_000),
    Metro("Atlanta", 33.7490, -84.3880, 500_000),
    Metro("Omaha", 41.2565, -95.9345, 490_000),
    Metro("Raleigh", 35.7796, -78.6382, 480_000),
    Metro("Colorado Springs", 38.8339, -104.8214, 480_000),
    Metro("Long Beach", 33.7701, -118.1937, 470_000),
    Metro("Virginia Beach", 36.8529, -75.9780, 460_000),
    Metro("Miami", 25.7617, -80.1918, 440_000),
    Metro("Minneapolis", 44.9778, -93.2650, 430_000),
    Metro("Tampa", 27.9506, -82.4572, 400_000),
    Metro("New Orleans", 29.9511, -90.0715, 380_000),
    Metro("Cleveland", 41.4993, -81.6944, 370_000),
    Metro("Honolulu", 21.3069, -157.8583, 350_000),
    Metro("Cincinnati", 39.1031, -84.5120, 310_000),
    Metro("Orlando", 28.5383, -81.3792, 310_000),
    Metro("Pittsburgh", 40.4406, -79.9959, 300_000),
    Metro("St. Louis", 38.6270, -90.1994, 290_000),
    Metro("Spokane", 47.6588, -117.4260, 230_000),
    Metro("Richmond", 37.5407, -77.4360, 230_000),
    Metro("Boise", 43.6150, -116.2023, 230_000),
    Metro("Rochester", 43.1566, -77.6088, 210_000),
    Metro("Des Moines", 41.5868, -93.6250, 210_000),
    Metro("Salt Lake City", 40.7608, -111.8910, 200_000),
    Metro("Birmingham", 33.5207, -86.8025, 200_000),
    Metro("Little Rock", 34.7465, -92.2896, 200_000),
]


_T = TypeVar("_T")

# Sources served only by api.census.gov, which now requires a key. Everything
# else in the pipeline comes from keyless bulk CSVs on www2.census.gov —
# including metro growth (sub-est2024.csv) and BPS permits (co{year}a.txt).
KEYED_SOURCES = (
    "affordability (ACS 5-year)",
    "state migration acceleration (ACS 1-year)",
)


def require_census_key(allow_degraded: bool) -> None:
    """Refuse to run keyless unless the operator opts in explicitly.

    Without this the run still 'succeeds': every keyed source degrades to a
    default and the export lands with 0% coverage on the affordability
    dimension and exit code 0. Make that state unreachable by accident.
    """
    if os.environ.get("CENSUS_API_KEY"):
        return
    if not allow_degraded:
        sys.exit(
            "ERROR: CENSUS_API_KEY is not set, and api.census.gov now refuses\n"
            "keyless queries (it answers with missing_key.html as HTTP 200).\n"
            "These sources would be silently absent:\n"
            + "".join(f"  - {s}\n" for s in KEYED_SOURCES)
            + f"A key is free: {KEY_SIGNUP_URL}\n"
            "To export anyway, with those dimensions empty, pass --allow-degraded."
        )
    print("WARNING: no CENSUS_API_KEY — exporting without these sources:")
    for source in KEYED_SOURCES:
        print(f"  - {source}")


def _try_keyed(fn: Callable[..., _T], *args: Any, default: _T, label: str) -> _T:
    """Call a keyed-API fetcher, degrading with the exception's own reason."""
    try:
        return fn(*args)
    except CensusAPIError as exc:
        print(f"  {label}: unavailable ({exc})")
        return default


def do_fetch(data_dir: Path, force: bool = False) -> dict[str, bool]:
    """Download all required data files. Returns {filename: success}."""
    print("Downloading census data files...")
    results = download_all(data_dir, force=force)
    for name, ok in results.items():
        status = "OK" if ok else "FAILED"
        print(f"  {name}: {status}")
    return results


def check_downloads(results: dict[str, bool]) -> None:
    """Exit non-zero when any download FAILED, instead of scoring without it."""
    failed = sorted(name for name, ok in results.items() if not ok)
    if failed:
        failures = ", ".join(failed)
        sys.exit(f"ERROR: {len(failed)} download(s) FAILED: {failures}")


def check_dimension_coverage(counties: list[County], allow_degraded: bool) -> None:
    """Exit non-zero when a scored dimension or sprawl factor lands at 0%
    coverage — a source can fail with a valid key, and the export would land
    silently empty.

    The sprawl factors need checking separately from the dimensions because a
    dead one does not read as absent: `compute_sprawl_risk` stores it as 0.0 and
    renormalizes the composite over the factors that survived, so an export
    missing a whole source still ships a complete-looking score. A factor is
    dead when no county has evidence for it (`factor_available`) — not when
    every score happens to be zero, which a real, quiet region also produces.

    State migration is why this is here. Of the three `_try_keyed` callers it is
    the only one with no other backstop — county populations fall back to a
    local CSV, affordability is one of the dimensions above, and state migration
    had nothing between a dead ACS endpoint and a published export but
    `regression_guard`, which runs after the fact. This is the upstream gate.
    """
    dims = {
        "stability": [c.stability.stability_score for c in counties],
        "remoteness": [c.remoteness.distance_to_metro_mi for c in counties],
        "affordability": [c.affordability.affordability_score for c in counties],
    }
    empty = sorted(k for k, v in dims.items() if all(x is None for x in v))
    empty += sorted(
        f"sprawl.{name}"
        for name, attr in SPRAWL_FACTOR_ATTRS.items()
        if not any(factor_available(c.sprawl_risk, name) for c in counties)
    )
    if not empty:
        return
    msg = f"scored dimension(s) at 0% coverage: {', '.join(empty)}"
    if allow_degraded:
        print(f"WARNING: {msg} (--allow-degraded)")
        return
    sys.exit(f"ERROR: {msg} — a source failed. Pass --allow-degraded to override.")


def require_rucc_coverage(counties: list[County], estimate_rucc: bool) -> None:
    """Refuse to publish while any county has no RUCC code.

    A county without one is filtered out of counties.json, geo.json and the
    candidate join — it simply is not in the export. The old guard only fired
    above 50% missing, so a partial gap (a renamed FIPS, a CSV that lost rows)
    shrank the published set with nothing said. Any gap now stops the run:
    either fix the source or opt into estimation, which is a choice, not a
    default.
    """
    missing = sum(1 for c in counties if c.rucc is None)
    if missing == 0 or estimate_rucc:
        return
    sample = sorted(c.fips for c in counties if c.rucc is None)[:5]
    sys.exit(
        f"ERROR: RUCC codes missing for {missing}/{len(counties)} counties "
        f"(e.g. {', '.join(sample)}) — ruralurbancodes2023.csv is absent, "
        "unparsed, or does not cover these FIPS. They would be dropped from "
        "the export without appearing anywhere. Fix the source, or rerun with "
        "--estimate-rucc to classify them from population + remoteness."
    )


def require_metros(metros: list[Metro]) -> list[Metro]:
    """Exit when no metros loaded, instead of scoring against FALLBACK_METROS."""
    if not metros:
        sys.exit(
            "ERROR: no metros loaded — place gazetteer or subcounty "
            "estimates missing from the data dir. Run --fetch first."
        )
    return metros


def do_score(
    data_dir: Path,
    estimate_rucc: bool = False,
    acs_year: int = DEFAULT_CONFIG.acs_year,
) -> tuple:
    """Run full scoring pipeline. Returns (counties, candidates)."""
    # A fresh run starts a fresh consumption record.
    consumption.LEDGER.reset()
    client = CensusClient(cache_dir=data_dir / "cache")

    # --- County populations ---
    print("Fetching county populations...")
    raw_counties = _try_keyed(
        fetch_county_populations, client, default=None, label="County populations"
    )
    if raw_counties is None:
        print("  API unavailable, falling back to CSV...")
        raw_counties = load_county_populations_csv(data_dir / "co-est2024-alldata.csv")
    if raw_counties is None:
        print("  ERROR: Could not fetch county data.")
        return [], []

    gazetteer = load_county_gazetteer(data_dir / "2024_Gaz_counties_national.txt")
    rucc = load_rucc_codes(data_dir / "ruralurbancodes2023.csv")

    counties = parse_county_data(raw_counties, gazetteer, rucc)
    print(f"  Counties parsed: {len(counties)}")

    has_rucc = sum(1 for c in counties if c.rucc is not None)
    print(f"  Counties with RUCC: {has_rucc}")

    # --- Load metros (from gazetteer + estimates, or fallback) ---
    place_gaz_path = data_dir / "2024_Gaz_place_national.txt"
    estimates_path = data_dir / "sub-est2024.csv"
    metros = require_metros(
        load_metros_from_gazetteer(place_gaz_path, estimates_path=estimates_path)
    )
    print(f"  Loaded {len(metros)} cities (pop >= 25K) from gazetteer")

    # --- Load sprawl risk data sources ---
    # County typology (recreation/retirement flags)
    typology_path = data_dir / "county-typology.csv"
    county_typology = load_county_typology(typology_path)
    if county_typology:
        print(f"  County typology loaded: {len(county_typology)} counties")
    else:
        print(
            "  County typology not available (recreation factor excluded from the composite)"
        )

    # Fast-growing metros (growth keyed by place GEOID, joined to coordinates,
    # computed keyless from the sub-county bulk CSV already on disk)
    fast_metros = load_fast_metros(estimates_path, place_gaz_path)
    if fast_metros:
        print(f"  Fast-growing metros (pop >= 25K): {len(fast_metros)}")
    else:
        print("  Metro growth rates not available (metro-growth factor excluded)")

    # State migration acceleration
    empty_migration: dict[str, float] = {}
    state_migration = _try_keyed(
        fetch_state_migration_accel,
        client,
        acs_year,
        acs_year - 1,
        default=empty_migration,
        label="State migration",
    )
    if state_migration:
        accel_pos = sum(1 for v in state_migration.values() if v > 0)
        print(
            f"  State migration: {len(state_migration)} states ({accel_pos} accelerating)"
        )
    else:
        print("  State migration data not available (migration factor excluded)")

    # Building permits (keyless BPS bulk files, downloaded by --fetch)
    building_permits = fetch_building_permits(data_dir)
    if building_permits:
        print(f"  Building permits: {len(building_permits)} counties with timeseries")
    else:
        print("  Building permits not available (permits factor excluded)")

    # --- Affordability data (Census ACS 5-Year) ---
    print("Fetching affordability data...")
    empty_afford: dict[str, dict[str, int | None]] = {}
    affordability_data = _try_keyed(
        fetch_affordability_data,
        client,
        acs_year,
        default=empty_afford,
        label="Affordability",
    )
    if affordability_data:
        print(f"  Affordability: {len(affordability_data)} counties")
    else:
        print("  Affordability data not available")

    # --- Score counties ---
    print("Scoring counties...")
    for county in counties:
        # Stability
        if county.pop_series:
            county.stability = compute_stability(county.pop_series)

        # Remoteness
        if county.lat is not None and county.lon is not None:
            county.remoteness = compute_remoteness(county.lat, county.lon, metros)

        # Sprawl risk — wire in available data sources
        interstate_dist: float | None = None  # no centroid = no evidence
        if county.lat is not None and county.lon is not None:
            interstate_dist = distance_to_nearest_interstate(county.lat, county.lon)

        # A county the typology file does not list has no flags, not False flags.
        typology = county_typology.get(county.fips)
        is_recreation = typology["recreation"] if typology else None
        is_retirement = typology["retirement"] if typology else None

        fast_metro = nearest_fast_metro(county.lat, county.lon, fast_metros)

        migration_accel = get_state_migration_accel(county.state, state_migration)
        permits = building_permits.get(county.fips)

        county.sprawl_risk = compute_sprawl_risk(
            interstate_distance_mi=interstate_dist,
            building_permits=permits,
            state_migration_accel=migration_accel,
            is_recreation=is_recreation,
            is_retirement=is_retirement,
            fast_metro_distance_mi=fast_metro[0] if fast_metro else None,
            fast_metro_growth_pct=fast_metro[1] if fast_metro else None,
        )

        # Affordability
        aff = affordability_data.get(county.fips)
        if aff:
            county.affordability = compute_affordability(
                home_value=aff.get("home_value"),
                rent=aff.get("rent"),
                income=aff.get("income"),
            )

    # If RUCC data is missing, estimate from population + remoteness (opt-in).
    # require_rucc_coverage has already exited unless the operator opted in, so
    # estimate every gap rather than only a majority one — a run that opted in
    # and then left 30% unclassified would drop them silently.
    require_rucc_coverage(counties, estimate_rucc)
    missing_rucc = sum(1 for c in counties if c.rucc is None)
    if missing_rucc:
        print("  RUCC data unavailable, estimating from population + remoteness...")
        for c in counties:
            if c.rucc is not None:
                continue
            pop = c.population or 0
            dist = c.remoteness.distance_to_metro_mi or 0
            if pop > 250_000:
                c.rucc = 1
            elif pop > 50_000:
                c.rucc = 3
            elif pop > 20_000 and dist < 50:
                c.rucc = 4
            elif pop > 20_000:
                c.rucc = 5
            elif pop > 2500 and dist < 50:
                c.rucc = 6
            elif pop > 2500:
                c.rucc = 7
            elif dist < 50:
                c.rucc = 8
            else:
                c.rucc = 9

    counties = compute_composite_rank(counties)

    min_rucc = DEFAULT_CONFIG.min_rucc
    rural = [c for c in counties if c.rucc is not None and c.rucc >= min_rucc]
    print(f"  Rural counties (RUCC >= {min_rucc}): {len(rural)}")

    # Report sprawl score distribution
    sprawl_scores = [c.sprawl_risk.composite for c in rural]
    if sprawl_scores:
        nonzero = sum(1 for s in sprawl_scores if s > 0)
        avg = sum(sprawl_scores) / len(sprawl_scores)
        print(
            f"  Sprawl scores: {nonzero}/{len(sprawl_scores)} non-zero, avg={avg:.1f}"
        )

    # Report affordability coverage
    afford_scores = [
        c.affordability.affordability_score
        for c in rural
        if c.affordability.affordability_score is not None
    ]
    if afford_scores:
        avg_afford = sum(afford_scores) / len(afford_scores)
        print(
            f"  Affordability scores: {len(afford_scores)}/{len(rural)} "
            f"counties, avg={avg_afford:.1f}"
        )

    # --- Place candidates ---
    candidates_path = data_dir / "sub-est2024.csv"
    rural_fips = {c.fips for c in rural}
    candidates = load_subcounty_estimates(
        candidates_path, county_fips_filter=rural_fips
    )
    print(f"  Candidate towns: {len(candidates)}")

    # Enrich candidates with gazetteer coordinates
    candidates = enrich_places_with_gazetteer(candidates, place_gaz_path)
    with_coords = sum(1 for p in candidates if p.lat is not None)
    print(f"  Candidates with coordinates: {with_coords}/{len(candidates)}")

    # Resolve unspecified county FIPS; drop places resolving to non-rural
    candidates = assign_county_fips(candidates, counties, county_fips_filter=rural_fips)
    with_county = sum(1 for p in candidates if not p.county_fips.endswith("000"))
    print(f"  Candidates with county match: {with_county}/{len(candidates)}")

    return counties, candidates


def do_export(
    export_path: Path,
    counties: list,
    candidates: list,
    data_dir: Path,
) -> None:
    """Export scored data as JSON for Hugo, staging it until it validates.

    Writing into the published directory and validating afterwards means a
    contract failure exits 1 with the bad files already serving. Build in a
    staging directory beside it, validate there, and only then promote — so the
    published export is only ever replaced by one that passed.
    """
    from src.export.navigation_places import (
        build_all_places,
        select_navigation_places,
    )
    from src.export.provenance import collect_api_lineage, collect_input_provenance
    from src.export.validate_contract import (
        check_place_shard_completeness,
        validate_directory,
    )

    print(f"Exporting to {export_path}...")
    # Built before the receipt is collected: the labels read the place
    # gazetteer and estimates, and the receipt names only what was opened.
    gazetteer = data_dir / "2024_Gaz_place_national.txt"
    all_places = build_all_places(gazetteer, data_dir / "sub-est2024.csv")
    nav_places = select_navigation_places(all_places)
    sources = collect_input_provenance(data_dir)
    api_lineage = collect_api_lineage()
    print(
        f"  Recording provenance for {len(sources)} consumed file(s) and "
        f"{len(api_lineage)} API response(s)."
    )
    export_path.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(dir=export_path.parent, prefix=f".{export_path.name}.staging.")
    )
    try:
        exporter = HugoExporter(staging)
        exporter.export_all(
            counties, candidates, sources=sources, api_lineage=api_lineage
        )

        # Map orientation labels: large places, built from the same gazetteer
        # and subcounty estimates the scoring run already read.
        print(f"  {len(nav_places)} navigation places for map labels.")
        exporter.export_navigation_places(nav_places)

        # Every gazetteer place, one flat shard per state (not a subdirectory:
        # the promote loop below moves files with os.replace).
        print(f"  {len(all_places)} places in per-state shards.")
        exporter.export_place_shards(all_places)

        # counties-10m.json is hand-copied and never written by the exporter.
        # Without it in the staging dir the geometry cross-check returns early
        # and the gate silently becomes a no-op, so link the published copy in.
        geometry = export_path / "counties-10m.json"
        if geometry.exists():
            staged_geometry = staging / geometry.name
            try:
                os.link(geometry, staged_geometry)
            except OSError:
                shutil.copy2(geometry, staged_geometry)

        print("  Validating export contract...")
        errors = validate_directory(staging)
        check_place_shard_completeness(staging, gazetteer, errors)
        if errors:
            print(f"  Contract validation FAILED ({len(errors)} error(s)):")
            for err in errors:
                print(f"    - {err}")
            print(f"  Published export at {export_path} left untouched.")
            sys.exit(1)

        # Promote per file rather than swapping the directory: the published
        # directory also holds counties-10m.json, which the exporter never
        # writes and must not lose.
        staged = {p.name for p in staging.iterdir()}
        for produced in sorted(staging.iterdir()):
            if produced.name == "counties-10m.json":
                continue
            os.replace(produced, export_path / produced.name)
        # A state that left the gazetteer must not keep serving its old shard.
        for stale in export_path.glob("places-*.json"):
            if stale.name not in staged:
                stale.unlink()
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    print("  Export complete.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Quiet Corners of America — census data pipeline"
    )
    parser.add_argument(
        "--fetch", action="store_true", help="Download census data files"
    )
    parser.add_argument("--score", action="store_true", help="Run scoring pipeline")
    parser.add_argument("--export", type=str, help="Export JSON to this directory")
    parser.add_argument("--all", type=str, help="Full pipeline: fetch + score + export")
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data/raw",
        help="Data directory (default: data/raw)",
    )
    parser.add_argument(
        "--allow-degraded",
        action="store_true",
        help=(
            "Run without CENSUS_API_KEY. Affordability and state migration "
            "will be absent from the export."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-download of --fetch data files even if still fresh",
    )
    parser.add_argument(
        "--estimate-rucc",
        action="store_true",
        help="Opt in to estimating missing RUCC codes from population + remoteness",
    )
    parser.add_argument(
        "--acs-year",
        type=int,
        default=DEFAULT_CONFIG.acs_year,
        help="ACS vintage for affordability/migration fetches",
    )
    parser.add_argument(
        "--version", action="version", version=f"svc-quiet-corners {PIPELINE_VERSION}"
    )

    args = parser.parse_args()
    data_dir = Path(args.data_dir)

    if args.score or args.export or args.all:
        require_census_key(args.allow_degraded)

    if not any([args.fetch, args.score, args.export, args.all]):
        parser.print_help()
        sys.exit(1)

    if args.all:
        check_downloads(do_fetch(data_dir, force=args.force))
        counties, candidates = do_score(
            data_dir, estimate_rucc=args.estimate_rucc, acs_year=args.acs_year
        )
        check_dimension_coverage(counties, args.allow_degraded)
        do_export(Path(args.all), counties, candidates, data_dir)
    else:
        if args.fetch:
            check_downloads(do_fetch(data_dir, force=args.force))
        if args.score or args.export:
            counties, candidates = do_score(
                data_dir, estimate_rucc=args.estimate_rucc, acs_year=args.acs_year
            )
            check_dimension_coverage(counties, args.allow_degraded)
            if args.export:
                do_export(Path(args.export), counties, candidates, data_dir)


if __name__ == "__main__":
    main()
