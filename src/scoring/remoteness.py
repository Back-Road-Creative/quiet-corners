"""
Remoteness scoring: haversine distance to nearest qualifying metro.
"""

import math

from src.core.config import DEFAULT_CONFIG
from src.core.models import Metro, RemotenessResult


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles between two lat/lon points."""
    R = DEFAULT_CONFIG.remoteness.earth_radius_miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


def compute_remoteness(
    lat: float,
    lon: float,
    metros: list[Metro],
    min_metro_pop: int = DEFAULT_CONFIG.remoteness.default_min_metro_pop,
) -> RemotenessResult:
    """Find nearest metro above population threshold.

    Returns RemotenessResult with metro name and distance in miles.
    """
    best_name: str | None = None
    best_dist = float("inf")

    for metro in metros:
        if metro.population < min_metro_pop:
            continue
        d = haversine(lat, lon, metro.lat, metro.lon)
        if d < best_dist:
            best_dist = d
            best_name = metro.name

    if best_name is None:
        return RemotenessResult()

    return RemotenessResult(
        nearest_metro_name=best_name,
        distance_to_metro_mi=round(best_dist, 1),
    )
