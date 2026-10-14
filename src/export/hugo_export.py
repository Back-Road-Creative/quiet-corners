"""
Hugo JSON export: produces static data files for the dashboard.

Output files:
- summary.json — aggregate stats (~5KB)
- counties.json — scored counties (pre-filtered to RUCC >= 6)
- candidates.json — qualifying towns
- navigation-places.json — large places used as map orientation labels
- places-index.json, places-<ST>.json — every gazetteer place, one shard per state
- places-national.json — the head of that ranking, one fetch, for views below the shard zoom
- geo.json — county centroids for dot map
"""

import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from src.core.config import DEFAULT_CONFIG, STATE_FIPS
from src.core.models import (
    FACTOR_OBSERVED,
    FACTOR_PARTIAL,
    FACTOR_STATUSES,
    FACTOR_UNKNOWN,
    County,
    Place,
    SprawlRiskResult,
)
from src.export.contract import (
    EVIDENCE_SCHEMA_VERSION,
    PLACE_INDEX_FILE,
    PLACE_NATIONAL_FIELDS,
    PLACE_NATIONAL_FILE,
    PLACE_NATIONAL_HEAD,
    PLACE_SHARD_FIELDS,
    SPRAWL_FACTOR_STATUS_KEY,
    place_shard_name,
)
from src.export.provenance import REFRESH_SCHEDULE, source_vintage

PIPELINE_VERSION = "2.0.0"

SPRAWL_FACTOR_ATTRS = {
    "interstate": "interstate_proximity_score",
    "permits": "building_permit_score",
    "migration": "state_migration_score",
    "metro_growth": "fast_metro_score",
    "recreation": "recreation_retirement_score",
}


def is_live(value: float | None) -> bool:
    """True only when this factor actually scored.

    `compute_sprawl_risk` stores a missing factor as 0.0 so these counters read
    it as uncovered, but a county with no live factor at all gets a bare
    `SprawlRiskResult()` — scores `None`, composite `NaN` — and so does any
    county that never reached scoring (`County.sprawl_risk` defaults to it).
    `None != 0` and `NaN != 0` are both True, so the bare `!= 0` this replaces
    reported a source as present when nothing had scored. `regression_guard`
    reads both blocks below, so that false positive let a run whose sources all
    died pass the gate built to stop it.

    Public because `main.check_dimension_coverage` gates on the same question
    one step earlier, before anything is exported.
    """
    return value is not None and math.isfinite(value) and value != 0


def factor_status(result: SprawlRiskResult, name: str) -> str:
    """One factor's recorded evidence status; "unknown" for a result that never
    recorded one. Never inferred from the score — a 0.0 cannot say whether the
    source had data."""
    if result.factor_status is None:
        return FACTOR_UNKNOWN
    return result.factor_status.get(name, FACTOR_UNKNOWN)


def factor_available(result: SprawlRiskResult, name: str) -> bool:
    """Whether a county had source evidence for this factor.

    Reads the recorded status (observed or partial). Only a result that never
    recorded one — pre-evidence code, hand-built fixtures — falls back to the
    old `is_live(score)` reading, which is the best a legacy record allows.
    """
    status = factor_status(result, name)
    if status == FACTOR_UNKNOWN:
        return is_live(getattr(result, SPRAWL_FACTOR_ATTRS[name]))
    return status in (FACTOR_OBSERVED, FACTOR_PARTIAL)


def _status_block(result: SprawlRiskResult) -> dict[str, str]:
    return {name: factor_status(result, name) for name in SPRAWL_FACTOR_ATTRS}


class HugoExporter:
    """Exports pipeline results as JSON for Hugo static site."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _write_json(self, filename: str, data: object, pretty: bool = False) -> None:
        """Write via temp file + rename: a crash mid-write leaves the previous
        file intact rather than a truncated one the dashboard would still fetch.

        Output is minified unless `pretty`. The data files are fetched by the
        dashboard and read by nobody, and indent=2 across them shipped roughly
        480KB of whitespace — a quarter of the export — on every page load.
        """
        dumped = (
            json.dumps(data, indent=2)
            if pretty
            else json.dumps(data, separators=(",", ":"))
        )
        path = self.output_dir / filename
        fd, tmp_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(dumped)
            os.replace(tmp_name, path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def export_summary(
        self,
        counties: list[County],
        candidates: list[Place] | None = None,
        sources: dict[str, dict] | None = None,
        api_lineage: list[dict] | None = None,
    ) -> None:
        """Write summary.json with aggregate stats, coverage, and data source status."""
        min_rucc = DEFAULT_CONFIG.min_rucc
        rural = [c for c in counties if c.rucc is not None and c.rucc >= min_rucc]
        # Coverage describes counties.json, which ships the rural set only.
        # Dividing by every scored county understated every dimension by the
        # metro half of the working set (1681 of 3144 exported, so a dimension
        # complete for everything the dashboard loads reported ~53%).
        total = len(rural) or 1  # avoid division by zero

        def _coverage(count: int) -> dict:
            return {"count": count, "pct": round(count / total * 100, 1)}

        coverage = {
            "stability": _coverage(
                sum(1 for c in rural if c.stability.stability_score is not None)
            ),
            "remoteness": _coverage(
                sum(1 for c in rural if c.remoteness.distance_to_metro_mi is not None)
            ),
            "affordability": _coverage(
                sum(1 for c in rural if c.affordability.affordability_score is not None)
            ),
        }

        # Per-factor non-zero coverage, measured against the exported rural set
        rural_total = len(rural) or 1
        sprawl_factor_coverage: dict[str, dict] = {}
        for name, attr in SPRAWL_FACTOR_ATTRS.items():
            n = sum(1 for c in rural if is_live(getattr(c.sprawl_risk, attr)))
            sprawl_factor_coverage[name] = {
                "count": n,
                "pct": round(n / rural_total * 100, 1),
            }

        # Evidence, not signal: counts of each status per factor over the same
        # rural set. Unlike sprawl_factor_coverage above, an observed zero and a
        # dead source land in different buckets here.
        sprawl_factor_availability = {
            name: {
                status: sum(
                    1 for c in rural if factor_status(c.sprawl_risk, name) == status
                )
                for status in FACTOR_STATUSES
            }
            for name in SPRAWL_FACTOR_ATTRS
        }

        data_sources = {
            "affordability": any(
                c.affordability.affordability_score is not None for c in counties
            ),
            "permits": any(
                is_live(c.sprawl_risk.building_permit_score) for c in counties
            ),
            "metro_growth": any(
                is_live(c.sprawl_risk.fast_metro_score) for c in counties
            ),
            "migration": any(
                is_live(c.sprawl_risk.state_migration_score) for c in counties
            ),
            "sprawl": any(is_live(c.sprawl_risk.composite) for c in counties),
            # USDA County Typology Codes — the recreation/retirement factor.
            "typology": any(
                is_live(c.sprawl_risk.recreation_retirement_score) for c in counties
            ),
            # USDA Rural-Urban Continuum Codes. Without them nothing has a
            # RUCC, so the rural set — everything counties.json ships — is
            # empty. The most consequential source to report on.
            "rucc": any(c.rucc is not None for c in counties),
        }

        generated_at = datetime.now(timezone.utc).isoformat()
        summary: dict = {
            "pipeline_version": PIPELINE_VERSION,
            "generated_at": generated_at,
            "total_counties_scored": len(counties),
            "total_rural_counties": len(rural),
            # Derived from what actually ships. It used to count the states a
            # retired PEP endpoint returned population for (51, territories
            # included) — a number about the fetch, not about the dashboard.
            "total_states": len({c.state for c in rural}),
            "coverage": coverage,
            "sprawl_factor_coverage": sprawl_factor_coverage,
            "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
            "sprawl_factor_availability": sprawl_factor_availability,
            # The distance at which the quietness remoteness term saturates.
            # The dashboard bands its Remote column against this; publishing it
            # means a recalibration reaches the page with the data rather than
            # needing a second, easily-forgotten change on the site.
            "remoteness_cap_mi": DEFAULT_CONFIG.remoteness.score_cap_mi,
            "data_sources": data_sources,
            # Which input bytes this export was built from. Always present:
            # an export that records nothing is a fact worth publishing too.
            "sources": sources or {},
            # Census API responses the run consumed (sanitized request, cache
            # or live, response digest) — the lineage of the API-derived
            # factors. Additive: readers that predate it ignore it.
            "api_lineage": api_lineage or [],
            # Three separate notions of "how fresh": which data release the
            # inputs are, when this export ran, and how often it is meant to.
            "freshness": {
                "source_vintage": source_vintage(api_lineage or []),
                "run_time": generated_at,
                "refresh_schedule": REFRESH_SCHEDULE,
            },
        }

        if candidates is not None:
            # A candidate whose parent county is not in counties.json cannot be
            # enriched by the dashboard, so it is not a match.
            valid_county_set = {c.fips for c in rural}
            summary["candidates"] = {
                "total": len(candidates),
                "with_coordinates": sum(1 for p in candidates if p.lat is not None),
                "with_county_match": sum(
                    1
                    for p in candidates
                    if not p.county_fips.endswith("000")
                    and p.county_fips in valid_county_set
                ),
            }

        # summary.json is 1KB and operator-facing: the refresh script diffs it
        # and a human reads the coverage block before approving a regeneration.
        self._write_json("summary.json", summary, pretty=True)

    def export_counties(
        self, counties: list[County], min_rucc: int = DEFAULT_CONFIG.min_rucc
    ) -> None:
        """Write counties.json, filtered to RUCC >= min_rucc."""
        filtered = [c for c in counties if c.rucc is not None and c.rucc >= min_rucc]
        data = []
        for c in filtered:
            entry = {
                "fips": c.fips,
                "state": c.state,
                "name": c.name,
                "population": c.population,
                "rucc": c.rucc,
                "stability_score": c.stability.stability_score,
                "cv": c.stability.cv,
                "slope_pct": c.stability.slope_pct,
                "nearest_metro": c.remoteness.nearest_metro_name,
                "distance_to_metro_mi": c.remoteness.distance_to_metro_mi,
                "sprawl_risk": c.sprawl_risk.composite,
                "sprawl_factors": {
                    "interstate": c.sprawl_risk.interstate_proximity_score,
                    "permits": c.sprawl_risk.building_permit_score,
                    "migration": c.sprawl_risk.state_migration_score,
                    "metro_growth": c.sprawl_risk.fast_metro_score,
                    "recreation": c.sprawl_risk.recreation_retirement_score,
                },
                SPRAWL_FACTOR_STATUS_KEY: _status_block(c.sprawl_risk),
                "affordability_score": c.affordability.affordability_score,
                "median_home_value": c.affordability.median_home_value,
                "median_rent": c.affordability.median_rent,
                "quietness_score": c.quietness_score,
                "overall_score": c.overall_score,
            }
            data.append(entry)
        self._write_json("counties.json", data)

    def export_candidates(
        self,
        candidates: list[Place],
        counties: list[County] | None = None,
        min_rucc: int = DEFAULT_CONFIG.min_rucc,
    ) -> None:
        """Write candidates.json with qualifying towns, enriched with county scores.

        The enrichment lookup is filtered to the same RUCC >= min_rucc set
        export_counties ships, so a candidate can only ever be enriched by a
        county the dashboard can actually resolve. Filtering here rather than
        trusting the caller: main.py hands this the full 3144-county list and
        the output is correct only because it pre-filters the *candidates* to
        rural parents. A caller that stopped doing that would silently attach
        metro scores to towns counties.json never mentions.
        """
        county_lookup: dict[str, County] = {}
        if counties:
            county_lookup = {
                c.fips: c for c in counties if c.rucc is not None and c.rucc >= min_rucc
            }

        data = []
        for p in candidates:
            entry: dict = {
                "fips": p.fips,
                "name": p.name,
                # Canonical state form is the abbreviation (matches counties.json)
                "state": STATE_FIPS.get(p.county_fips[:2], p.state),
                "county_fips": p.county_fips,
                "population": p.population,
                "lat": p.lat,
                "lon": p.lon,
            }
            # Enrich with parent county data
            parent = county_lookup.get(p.county_fips)
            if parent:
                entry["county_name"] = parent.name
                entry["stability_score"] = parent.stability.stability_score
                entry["distance_to_metro_mi"] = parent.remoteness.distance_to_metro_mi
                entry["nearest_metro"] = parent.remoteness.nearest_metro_name
                entry["sprawl_risk"] = parent.sprawl_risk.composite
                entry[SPRAWL_FACTOR_STATUS_KEY] = _status_block(parent.sprawl_risk)
                entry["affordability_score"] = parent.affordability.affordability_score
                entry["quietness_score"] = parent.quietness_score
                entry["overall_score"] = parent.overall_score
                # Use county centroid if place has no coords
                if p.lat is None and parent.lat is not None:
                    entry["lat"] = parent.lat
                    entry["lon"] = parent.lon
            data.append(entry)

        self._write_json("candidates.json", data)

    def export_navigation_places(self, places: list[dict]) -> None:
        """Write navigation-places.json — the map's orientation label layer,
        built by `src/export/navigation_places.py`. Separate from the scored
        towns: widening candidates.json would change its ranking contract."""
        self._write_json("navigation-places.json", places)

    def export_place_shards(self, places: list) -> None:
        """Write places-index.json and one places-<ST>.json per state from the
        ranked `build_all_places` list. Flat files, never a subdirectory: the
        promote step moves each staged file with os.replace, which fails on a
        non-empty directory. Within a shard, places keep the national ranking."""
        by_state: dict[str, list] = {}
        for place in places:
            by_state.setdefault(place.state, []).append(place)

        index: dict[str, dict] = {}
        for state in sorted(by_state):
            members = by_state[state]
            lats = [m.lat for m in members]
            lons = [m.lon for m in members]
            index[state] = {
                "bbox": [
                    round(min(lons), 4),
                    round(min(lats), 4),
                    round(max(lons), 4),
                    round(max(lats), 4),
                ],
                "count": len(members),
            }
            self._write_json(
                place_shard_name(state),
                {
                    "state": state,
                    "fields": list(PLACE_SHARD_FIELDS),
                    "places": [m.record() for m in members],
                },
            )
        self._write_json(PLACE_INDEX_FILE, index)
        # The head of the same list, state attached per row: the rungs a view
        # can reach before it is zoomed in enough to fetch a state's shard.
        head = [p for p in places if p.population is not None][:PLACE_NATIONAL_HEAD]
        self._write_json(
            PLACE_NATIONAL_FILE,
            {
                "fields": list(PLACE_NATIONAL_FIELDS),
                "places": [[*m.record(), m.state] for m in head],
            },
        )

    def export_geo(
        self, counties: list[County], min_rucc: int = DEFAULT_CONFIG.min_rucc
    ) -> None:
        """Write geo.json with county centroids for dot map."""
        filtered = [c for c in counties if c.rucc is not None and c.rucc >= min_rucc]
        data = [
            {
                "fips": c.fips,
                "lat": c.lat,
                "lon": c.lon,
                "stability_score": c.stability.stability_score,
                "sprawl_risk": c.sprawl_risk.composite,
                "overall_score": c.overall_score,
            }
            for c in filtered
        ]
        self._write_json("geo.json", data)

    def export_all(
        self,
        counties: list[County],
        candidates: list[Place],
        min_rucc: int = DEFAULT_CONFIG.min_rucc,
        sources: dict[str, dict] | None = None,
        api_lineage: list[dict] | None = None,
    ) -> None:
        """Export all JSON files for Hugo."""
        self.export_summary(
            counties, candidates=candidates, sources=sources, api_lineage=api_lineage
        )
        self.export_counties(counties, min_rucc=min_rucc)
        self.export_candidates(candidates, counties=counties, min_rucc=min_rucc)
        self.export_geo(counties, min_rucc=min_rucc)
