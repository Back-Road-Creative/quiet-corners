"""
Data models for Quiet Corners pipeline.

Dataclasses for pipeline entities: states, counties, places, and scoring results.
"""

from dataclasses import dataclass, field

# Per-factor evidence status for the sprawl model. A factor's score says how
# much sprawl pressure the data shows; its status says whether there was data.
# A score of 0.0 cannot tell those apart, which is the whole reason this exists.
FACTOR_OBSERVED = "observed"  # every input came from a source record
FACTOR_PARTIAL = "partial"  # scored, but on a gap-filled input (see sprawl_risk)
FACTOR_UNAVAILABLE = "unavailable"  # no source evidence for this county
FACTOR_UNKNOWN = "unknown"  # legacy record that never recorded a status
FACTOR_STATUSES = (
    FACTOR_OBSERVED,
    FACTOR_PARTIAL,
    FACTOR_UNAVAILABLE,
    FACTOR_UNKNOWN,
)

SPRAWL_FACTOR_NAMES = (
    "interstate",
    "permits",
    "migration",
    "metro_growth",
    "recreation",
)


@dataclass
class StabilityResult:
    """Output of stability scoring for a county."""

    cv: float | None = None
    slope: float | None = None
    slope_pct: float | None = None
    stability_score: float | None = None


@dataclass
class RemotenessResult:
    """Output of remoteness calculation for a county."""

    nearest_metro_name: str | None = None
    distance_to_metro_mi: float | None = None


@dataclass
class SprawlRiskResult:
    """Output of sprawl risk scoring for a county (5-factor model).

    Factor scores default to None (never computed), like the other result
    dataclasses. `composite` stays a plain float because downstream consumers
    aggregate it numerically, so its never-computed default is NaN — the
    composite ranker treats a non-finite composite as missing data instead of
    reading it as zero sprawl risk (a 0.0 default inverted to a perfect 100
    anti-sprawl score)."""

    interstate_proximity_score: float | None = None
    building_permit_score: float | None = None
    state_migration_score: float | None = None
    fast_metro_score: float | None = None
    recreation_retirement_score: float | None = None
    composite: float = float("nan")
    # {factor name: FACTOR_*}. None = a result built without evidence tracking
    # (older code, hand-built fixtures): readers must say "unknown", never infer
    # a status from a zero score. compute_sprawl_risk always sets it.
    factor_status: dict[str, str] | None = None


@dataclass
class AffordabilityResult:
    """Output of affordability scoring for a county."""

    median_home_value: int | None = None
    median_rent: int | None = None
    median_income: int | None = None
    hvi_ratio: float | None = None
    rti_ratio: float | None = None
    affordability_score: float | None = None


@dataclass
class County:
    """A scored county with all pipeline outputs."""

    fips: str
    state: str
    name: str
    population: int | None = None
    rucc: int | None = None
    lat: float | None = None
    lon: float | None = None
    stability: StabilityResult = field(default_factory=StabilityResult)
    remoteness: RemotenessResult = field(default_factory=RemotenessResult)
    sprawl_risk: SprawlRiskResult = field(default_factory=SprawlRiskResult)
    affordability: AffordabilityResult = field(default_factory=AffordabilityResult)
    quietness_score: float | None = None
    overall_score: float | None = None
    pop_series: dict[int, int] = field(default_factory=dict)


@dataclass
class Place:
    """A subcounty place (town/city) for candidate identification."""

    fips: str
    name: str
    state: str
    county_fips: str
    population: int | None = None
    lat: float | None = None
    lon: float | None = None


@dataclass
class Metro:
    """A metro area used for remoteness calculations."""

    name: str
    lat: float
    lon: float
    population: int
