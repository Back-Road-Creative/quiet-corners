"""
Metro growth computation for sprawl-risk proximity scoring.

Fast-growing metro-sized places from the PEP sub-county bulk CSV
(sub-est2024.csv, keyless on www2.census.gov — the API stopped publishing
place estimates after vintage 2021), keyed by place GEOID and joined to
gazetteer coordinates on the same key — place names are not unique
nationally ("Springfield city"), so a name join collides across states;
the GEOID join is state-qualified by construction.
"""

import csv
import re
from collections.abc import Sequence
from pathlib import Path

from src.ingest.consumption import consumed_open
from src.ingest.place_populations import _haversine_mi, load_place_gazetteer

# (lat, lon, growth_pct) for one fast-growing metro-sized place
FastMetro = tuple[float, float, float]

_POPESTIMATE_RE = re.compile(r"^POPESTIMATE(\d{4})$")


def _estimate_year_columns(fieldnames: Sequence[str]) -> tuple[str, str] | None:
    """(earliest, latest) POPESTIMATE column names, or None if under two —
    a single vintage cannot yield growth, and ESTIMATESBASE is a census
    base, not an estimate, so it does not count."""
    years = sorted(
        (m.group(1), col) for col in fieldnames if (m := _POPESTIMATE_RE.match(col))
    )
    if len(years) < 2:
        return None
    return years[0][1], years[-1][1]


def load_fast_metros(
    estimates_path: Path,
    place_gazetteer_path: Path,
    min_pop: int = 25_000,
) -> list[FastMetro]:
    """(lat, lon, growth_pct) for every gazetteer place >= min_pop whose
    population grew between the earliest and latest POPESTIMATE vintages
    in the sub-county bulk CSV already fetched for candidate towns."""
    if not estimates_path.exists():
        return []

    growth_by_geoid: dict[str, float] = {}
    with consumed_open(estimates_path, newline="", encoding="latin-1") as f:
        reader = csv.DictReader(f)
        year_cols = _estimate_year_columns(reader.fieldnames or [])
        if year_cols is None:
            return []
        earliest_col, latest_col = year_cols
        for row in reader:
            # Whole incorporated places only (SUMLEV=162): state/county
            # rollups and county place-parts would double-count.
            if row.get("SUMLEV") != "162":
                continue
            geoid = row.get("STATE", "").zfill(2) + row.get("PLACE", "").zfill(5)
            try:
                pop_early = int(row.get(earliest_col) or "")
                pop_late = int(row.get(latest_col) or "")
            except (TypeError, ValueError):
                continue
            if pop_early <= 0 or pop_late < min_pop:
                continue
            growth_pct = (pop_late - pop_early) / pop_early * 100
            if growth_pct > 0:
                growth_by_geoid[geoid] = round(growth_pct, 2)

    fast: list[FastMetro] = []
    for gaz_row in load_place_gazetteer(place_gazetteer_path):
        growth = growth_by_geoid.get(gaz_row.get("GEOID", "").strip())
        if growth is None:
            continue
        try:
            lat = float(gaz_row.get("INTPTLAT", ""))
            lon = float(gaz_row.get("INTPTLONG", ""))
        except (TypeError, ValueError):
            continue
        fast.append((lat, lon, growth))
    return fast


def nearest_fast_metro(
    lat: float | None,
    lon: float | None,
    fast_metros: list[FastMetro],
) -> tuple[float, float] | None:
    """(distance_mi, growth_pct) of the nearest fast-growing metro — the
    nearest metro overall may be slow while a boom town sits just beyond it,
    and that must not zero the fast-metro sprawl factor.

    None when the county has no centroid or no metro data exists at all:
    truly-missing data must reach the renormalizing sprawl composite as
    missing, not as a phantom "slow metro 200mi away" scoring 0 at full
    weight (W9)."""
    if lat is None or lon is None or not fast_metros:
        return None
    best_dist, best_growth = float("inf"), 0.0
    for m_lat, m_lon, growth in fast_metros:
        d = _haversine_mi(lat, lon, m_lat, m_lon)
        if d < best_dist:
            best_dist, best_growth = d, growth
    return best_dist, best_growth
