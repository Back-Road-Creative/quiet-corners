"""
Place (subcounty town) population ingestion.

Identifies candidate towns in the 1K-3K population range within qualifying counties.
"""

import csv
import math
from pathlib import Path

from src.core.config import DEFAULT_CONFIG
from src.core.models import County, Place
from src.ingest.consumption import consumed_open

EARTH_RADIUS_MI = 3959.0


def _haversine_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in miles between two lat/lon points."""
    rlat1, rlon1 = math.radians(lat1), math.radians(lon1)
    rlat2, rlon2 = math.radians(lat2), math.radians(lon2)
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    )
    return EARTH_RADIUS_MI * 2 * math.asin(math.sqrt(a))


def enrich_places_with_gazetteer(
    places: list[Place], gazetteer_path: Path
) -> list[Place]:
    """Set lat/lon on places by matching Place.fips to gazetteer GEOID.

    Args:
        places: List of Place objects (fips is 7-digit: 2 state + 5 place).
        gazetteer_path: Path to 2024_Gaz_place_national.txt.

    Returns:
        The same list with lat/lon populated where matched.
    """
    if not gazetteer_path.exists():
        return places

    lookup: dict[str, tuple[float, float]] = {}
    with consumed_open(gazetteer_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter="\t")
        # Census gazetteer TSV has trailing whitespace in column names
        if reader.fieldnames:
            reader.fieldnames = [fn.strip() for fn in reader.fieldnames]
        for row in reader:
            geoid = row.get("GEOID", "").strip()
            try:
                lat = float(row.get("INTPTLAT", "").strip())
                lon = float(row.get("INTPTLONG", "").strip())
                lookup[geoid] = (lat, lon)
            except (ValueError, TypeError):
                continue

    for place in places:
        coords = lookup.get(place.fips)
        if coords:
            place.lat, place.lon = coords

    return places


def assign_county_fips(
    places: list[Place],
    counties: list[County],
    county_fips_filter: set[str] | None = None,
) -> list[Place]:
    """Resolve county FIPS for places with unspecified county (ending in '000').

    Uses nearest-county matching by haversine distance, restricted to same state.

    Args:
        places: List of Place objects, some with county_fips ending in '000'.
        counties: List of County objects with lat/lon centroids.
        county_fips_filter: If set, drop places whose RESOLVED county is not
            in this set — COUNTY=000 places were admitted by state prefix
            only and may resolve to a non-qualifying county. Places still
            unresolved after matching are kept.

    Returns:
        The list with county_fips updated where resolved, filtered if asked.
    """
    state_counties: dict[str, list[County]] = {}
    for c in counties:
        if c.lat is not None and c.lon is not None:
            state = c.fips[:2]
            state_counties.setdefault(state, []).append(c)

    for place in places:
        if not place.county_fips.endswith("000"):
            continue
        if place.lat is None or place.lon is None:
            continue

        state_prefix = place.county_fips[:2]
        candidates = state_counties.get(state_prefix, [])
        if not candidates:
            continue

        # Bind the narrowed coordinates to locals: mypy does not carry the
        # `is not None` narrowing of an attribute into a lambda body, and a
        # candidate county may itself have no centroid.
        place_lat, place_lon = place.lat, place.lon
        located = [
            (c, c.lat, c.lon)
            for c in candidates
            if c.lat is not None and c.lon is not None
        ]
        if not located:
            continue

        best_county = min(
            located,
            key=lambda t: _haversine_mi(place_lat, place_lon, t[1], t[2]),
        )[0]
        place.county_fips = best_county.fips

    if county_fips_filter is not None:
        return [
            p
            for p in places
            if p.county_fips.endswith("000") or p.county_fips in county_fips_filter
        ]
    return places


def load_place_gazetteer(
    gazetteer_path: Path,
) -> list[dict[str, str]]:
    """Load Census place gazetteer TSV file.

    Returns list of dicts with GEOID, NAME, STUSAB, INTPTLAT, INTPTLONG, POP.
    """
    if not gazetteer_path.exists():
        return []

    records: list[dict[str, str]] = []
    with consumed_open(gazetteer_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter="\t")
        # Census gazetteer TSV has trailing whitespace in column names
        if reader.fieldnames:
            reader.fieldnames = [fn.strip() for fn in reader.fieldnames]
        for row in reader:
            records.append(row)
    return records


def load_place_county_map(estimates_path: Path) -> dict[str, str]:
    """Map each place GEOID to the county that contains it.

    SUB-EST's incorporated-place rows (SUMLEV 162) carry COUNTY=000, which is
    why every candidate used to fall through to nearest-centroid matching. The
    same file already answers the question: SUMLEV 157 rows are place *parts*
    within a county, one per county the place touches, each with its own
    population. A place spanning a county line is assigned to the county holding
    most of it — the same rule a reader would apply to "which county is this
    town in".
    """
    parts: dict[str, dict[str, int]] = {}
    if not estimates_path.exists():
        return {}

    with consumed_open(estimates_path, newline="", encoding="latin-1") as f:
        for row in csv.DictReader(f):
            if row.get("SUMLEV", "") != "157":
                continue
            place_fips = row.get("PLACE", "").zfill(5)
            county_fips = row.get("COUNTY", "").zfill(3)
            # 00000/000 are state-remainder rows, not places.
            if place_fips == "00000" or county_fips == "000":
                continue
            state_fips = row.get("STATE", "").zfill(2)
            pop_str = row.get("POPESTIMATE2024", "") or row.get("POPESTIMATE2023", "")
            try:
                pop = int(pop_str)
            except ValueError:
                continue  # one bad row must not lose the whole mapping
            parts.setdefault(state_fips + place_fips, {})[state_fips + county_fips] = (
                pop
            )

    return {
        geoid: max(counties, key=lambda fips: counties[fips])
        for geoid, counties in parts.items()
    }


def load_subcounty_estimates(
    estimates_path: Path,
    county_fips_filter: set[str] | None = None,
    pop_min: int = DEFAULT_CONFIG.candidate_pop_min,
    pop_max: int = DEFAULT_CONFIG.candidate_pop_max,
) -> list[Place]:
    """Load subcounty population estimates and filter to candidate range.

    Args:
        estimates_path: Path to sub-est2024.csv or similar.
        county_fips_filter: If set, only include places in these counties.
            A place's county comes from its SUMLEV 157 part rows; only a place
            with no part row keeps COUNTY=000 and is matched by state prefix,
            leaving `assign_county_fips` to guess it from centroids.
        pop_min: Minimum population for candidates.
        pop_max: Maximum population for candidates.
    """
    if not estimates_path.exists():
        return []

    # Build state filter from county FIPS (first 2 digits)
    state_fips_filter: set[str] | None = None
    if county_fips_filter:
        state_fips_filter = {f[:2] for f in county_fips_filter}

    # The place-part rows in this same file say which county each place is in.
    # Without them every place carries COUNTY=000 and gets guessed later by
    # nearest centroid, which put 174 of the 1597 published towns (10.9%) in the
    # wrong county — inheriting that county's scores.
    place_counties = load_place_county_map(estimates_path)

    places: list[Place] = []
    with consumed_open(estimates_path, newline="", encoding="latin-1") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Filter to incorporated places (SUMLEV=162)
            sumlev = row.get("SUMLEV", "")
            if sumlev != "162":
                continue

            state_fips = row.get("STATE", "").zfill(2)
            place_fips = row.get("PLACE", "").zfill(5)
            resolved = place_counties.get(state_fips + place_fips)
            county_fips_col = (
                resolved[2:] if resolved else row.get("COUNTY", "").zfill(3)
            )
            full_county_fips = state_fips + county_fips_col

            # COUNTY=000 means county not specified; filter by state instead
            if county_fips_filter:
                if county_fips_col != "000":
                    if full_county_fips not in county_fips_filter:
                        continue
                elif state_fips_filter and state_fips not in state_fips_filter:
                    continue

            pop_str = row.get("POPESTIMATE2024", row.get("POPESTIMATE2023", ""))
            if not pop_str:
                continue
            try:
                pop = int(pop_str)
            except ValueError:
                continue  # one bad row must not abort candidate loading

            if not (pop_min <= pop <= pop_max):
                continue

            places.append(
                Place(
                    fips=state_fips + place_fips,
                    name=row.get("NAME", ""),
                    state=row.get("STNAME", ""),
                    county_fips=full_county_fips,
                    population=pop,
                )
            )

    return places
