"""Orientation places for the map's label layer.

`candidates.json` is the scored small-town export and the only thing the map
labels today, so at national zoom a reader sees 47 towns they have never heard
of and no larger place to locate them against. This builds the other layer from
sources the pipeline already downloads: the place gazetteer for coordinates,
the subcounty estimates for population. It adds the two things the site cannot
work out for itself — a `display_name` free of the legal suffix, and a `tier`
saying how sparse the layer must be. The pipeline knows no county seats, so the
tiers are population bands.

The same ranked list also feeds `places-<ST>.json`, the per-state shards that
carry EVERY gazetteer place so the map can label a town the scored layers do
not know (Franklin, NC is a 4,362-person town no candidate list contains).
`build_all_places` ranks the whole gazetteer once; the navigation layer is the
head of that list, so orientation labels and local labels cannot diverge.
"""

import csv
import re
from pathlib import Path
from typing import NamedTuple

from src.core.config import STATE_FIPS
from src.core.models import Place
from src.ingest.consumption import consumed_open
from src.ingest.place_populations import (
    enrich_places_with_gazetteer,
    load_subcounty_estimates,
)

TIER_MAJOR = "major"
TIER_REGIONAL = "regional"

# Measured against the shipped 2024 sources (32,333 gazetteer rows,
# sub-est2024.csv SUMLEV 162): >= 250K gives 93 places, >= 100K gives 342,
# >= 50K gives 817, >= 25K gives 1,606 — about the size of candidates.json,
# which would make this a second gazetteer dump rather than a label layer.
# 342 plus the five state anchors below is 347 records, 46KB minified.
NAV_POP_FLOOR = 100_000
MAJOR_POP_FLOOR = 250_000

# load_subcounty_estimates filters to a population window; this has no ceiling.
_NO_POP_CEILING = 100_000_000

# Longest first: "city and borough" must win over "borough".
_LEGAL_SUFFIXES = (
    "city and borough|city and county|metropolitan government|metro government|"
    "consolidated government|unified government|urban county|charter township|"
    "municipality|township|borough|village|town|city|CDP"
).split("|")

_BALANCE = re.compile(r"\s*\(balance\)\s*$")


def curate_display_name(official_name: str) -> str:
    """Return the label form of a Census place name.

    Matching is case-sensitive on purpose: Census writes the legal suffix in
    lower case, so "Bullhead City city" keeps its name and "Macon-Bibb County"
    is left alone. A case-insensitive strip would mangle both.
    """
    name = _BALANCE.sub("", official_name.strip())
    for suffix in _LEGAL_SUFFIXES:
        if name.endswith(" " + suffix):
            return name[: -(len(suffix) + 1)].rstrip()
    return name


def tier_for_population(population: int) -> str | None:
    """Which label tier a place belongs to, or None if it is below the floor."""
    if population >= MAJOR_POP_FLOOR:
        return TIER_MAJOR
    if population >= NAV_POP_FLOOR:
        return TIER_REGIONAL
    return None


class RankedPlace(NamedTuple):
    """One gazetteer place, positioned in the national population ranking."""

    state: str
    geoid: str
    name: str
    lat: float
    lon: float
    population: int | None
    aland: int

    @property
    def display_name(self) -> str:
        return curate_display_name(self.name)

    def record(self) -> list[object]:
        """The compact shard form; `PLACE_SHARD_FIELDS` names the positions."""
        return [
            self.geoid,
            self.display_name,
            round(self.lat, 4),
            round(self.lon, 4),
            self.population,
        ]


def _gazetteer_rows(gazetteer_path: Path) -> list[tuple[Place, int]]:
    """Every gazetteer row as a coordinate-less Place plus its land area."""
    if not gazetteer_path.exists():
        return []
    rows: list[tuple[Place, int]] = []
    with consumed_open(gazetteer_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter="\t")
        if reader.fieldnames:
            reader.fieldnames = [fn.strip() for fn in reader.fieldnames]
        for row in reader:
            geoid = (row.get("GEOID") or "").strip()
            if not geoid:
                continue
            try:
                aland = int((row.get("ALAND") or "0").strip())
            except ValueError:
                aland = 0
            rows.append(
                (
                    Place(
                        fips=geoid,
                        name=(row.get("NAME") or "").strip(),
                        state=(row.get("USPS") or "").strip(),
                        county_fips="",
                    ),
                    aland,
                )
            )
    return rows


def gazetteer_located_count(gazetteer_path: Path) -> int:
    """How many gazetteer places have coordinates — the number every shard set
    must add up to, whatever population window anything else applies."""
    base = [p for p, _ in _gazetteer_rows(gazetteer_path)]
    return sum(
        1
        for p in enrich_places_with_gazetteer(base, gazetteer_path)
        if p.lat is not None and p.lon is not None
    )


def build_all_places(gazetteer_path: Path, estimates_path: Path) -> list[RankedPlace]:
    """Every located gazetteer place, ranked: population descending (places with
    no estimate last), then land area descending.

    The gazetteer is the base set, so a CDP the subcounty estimates omit is kept
    with a null population rather than dropped; population is left-joined by
    GEOID. A place the gazetteer cannot locate is dropped — it cannot be drawn.
    """
    rows = _gazetteer_rows(gazetteer_path)
    aland = {p.fips: a for p, a in rows}
    located = [
        p
        for p in enrich_places_with_gazetteer([p for p, _ in rows], gazetteer_path)
        if p.lat is not None and p.lon is not None
    ]
    estimates: dict[str, Place] = {}
    for row in load_subcounty_estimates(
        estimates_path, pop_min=0, pop_max=_NO_POP_CEILING
    ):
        estimates.setdefault(row.fips, row)

    ranked = []
    for p in located:
        est = estimates.get(p.fips)
        ranked.append(
            RankedPlace(
                state=STATE_FIPS.get(p.fips[:2], p.state),
                geoid=p.fips,
                # The estimates' legal name when there is one, so the label
                # layer reads exactly as it did before the gazetteer was the base.
                name=est.name if est else p.name,
                lat=p.lat,  # type: ignore[arg-type]
                lon=p.lon,  # type: ignore[arg-type]
                population=est.population if est else None,
                aland=aland.get(p.fips, 0),
            )
        )
    ranked.sort(key=lambda r: (r.population is None, -(r.population or 0), -r.aland))
    return ranked


def _record(place: RankedPlace, tier: str) -> dict[str, object]:
    return {
        "geoid": place.geoid,
        "name": place.name,
        "display_name": place.display_name,
        "state": place.state,
        "tier": tier,
        "lat": place.lat,
        "lon": place.lon,
    }


def select_navigation_places(ranked: list[RankedPlace]) -> list[dict[str, object]]:
    """The navigation layer: the head of the ranked list plus state anchors."""
    populated = [p for p in ranked if p.population is not None]
    chosen: list[tuple[RankedPlace, str]] = []
    covered: set[str] = set()
    for place in populated:
        tier = tier_for_population(place.population or 0)
        if tier is None:
            break  # ranked descending: nothing past the floor qualifies
        chosen.append((place, tier))
        covered.add(place.state)

    # Five states — DE, ME, VT, WV and WY under the 2024 estimates — have no
    # place over the floor at all. A state with no label is exactly the
    # orientation gap this export exists to close, so each contributes its
    # largest place as a regional anchor.
    for place in populated:
        if place.state in covered:
            continue
        covered.add(place.state)
        chosen.append((place, TIER_REGIONAL))

    # Largest first: a label layer thins by dropping the tail, so the order is
    # part of what the site is handed.
    chosen.sort(key=lambda pair: -(pair[0].population or 0))
    return [_record(place, tier) for place, tier in chosen]


def build_navigation_places(
    gazetteer_path: Path, estimates_path: Path
) -> list[dict[str, object]]:
    """Build the navigation-place records, largest first. A place the gazetteer
    cannot locate is dropped — a label with no coordinates cannot be drawn."""
    return select_navigation_places(build_all_places(gazetteer_path, estimates_path))
