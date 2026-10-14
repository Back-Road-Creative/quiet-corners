"""
Shared configuration for Quiet Corners pipeline.

Single source of truth for FIPS codes, land areas, scoring thresholds,
and pipeline weights. Extracted from scripts 01-02 to eliminate duplication.
"""

from dataclasses import dataclass, field

# =============================================================================
# FIPS CODES
# =============================================================================

STATE_FIPS: dict[str, str] = {
    "01": "AL",
    "02": "AK",
    "04": "AZ",
    "05": "AR",
    "06": "CA",
    "08": "CO",
    "09": "CT",
    "10": "DE",
    "11": "DC",
    "12": "FL",
    "13": "GA",
    "15": "HI",
    "16": "ID",
    "17": "IL",
    "18": "IN",
    "19": "IA",
    "20": "KS",
    "21": "KY",
    "22": "LA",
    "23": "ME",
    "24": "MD",
    "25": "MA",
    "26": "MI",
    "27": "MN",
    "28": "MS",
    "29": "MO",
    "30": "MT",
    "31": "NE",
    "32": "NV",
    "33": "NH",
    "34": "NJ",
    "35": "NM",
    "36": "NY",
    "37": "NC",
    "38": "ND",
    "39": "OH",
    "40": "OK",
    "41": "OR",
    "42": "PA",
    "44": "RI",
    "45": "SC",
    "46": "SD",
    "47": "TN",
    "48": "TX",
    "49": "UT",
    "50": "VT",
    "51": "VA",
    "53": "WA",
    "54": "WV",
    "55": "WI",
    "56": "WY",
}

FIPS_TO_STATE: dict[str, str] = {v: k for k, v in STATE_FIPS.items()}

# State land area in square miles (Census TIGER Gazetteer)
STATE_LAND_AREA: dict[str, int] = {
    "AL": 50645,
    "AK": 570641,
    "AZ": 113594,
    "AR": 52035,
    "CA": 155779,
    "CO": 103642,
    "CT": 4842,
    "DE": 1949,
    "FL": 53625,
    "GA": 57513,
    "HI": 6423,
    "ID": 82643,
    "IL": 55519,
    "IN": 35826,
    "IA": 55857,
    "KS": 81759,
    "KY": 39486,
    "LA": 43204,
    "ME": 30843,
    "MD": 9707,
    "MA": 7800,
    "MI": 56539,
    "MN": 79627,
    "MS": 46923,
    "MO": 68742,
    "MT": 145546,
    "NE": 76824,
    "NV": 109781,
    "NH": 8953,
    "NJ": 7354,
    "NM": 121298,
    "NY": 47126,
    "NC": 48618,
    "ND": 69001,
    "OH": 40861,
    "OK": 68595,
    "OR": 95988,
    "PA": 44743,
    "RI": 1034,
    "SC": 30061,
    "SD": 75811,
    "TN": 41235,
    "TX": 261232,
    "UT": 82170,
    "VT": 9217,
    "VA": 39490,
    "WA": 66456,
    "WV": 24038,
    "WI": 54158,
    "WY": 97093,
    "DC": 61,
}

# =============================================================================
# SCORING THRESHOLDS
# =============================================================================


@dataclass(frozen=True)
class StabilityWeights:
    """Weights and thresholds for stability scoring.

    The 60/40 CV/slope split is kept deliberately (W9): at cv_max 0.05 the
    two sub-scores correlate r=0.96 on the 5-point series and 60/40 vs 50/50
    rank-orders the export identically (rho=0.9993), so the ratio carries no
    ranking power worth re-tuning.
    """

    cv_weight: float = 0.6
    slope_weight: float = 0.4
    # CV at/above this scores 0. W9: 0.05 ~= p99 of observed CV in the
    # published export (p50 0.0108, p95 0.0338, p99 0.0517, n=1681); the old
    # 0.15 was ~3x p99 and compressed cv_score to sd 7.4 vs slope's 21.1.
    cv_max: float = 0.05
    slope_pct_max: float = 3.0  # |slope_pct| above this = score 0 (~= p99 3.13)


@dataclass(frozen=True)
class RemotenessThresholds:
    """Thresholds for remoteness classification."""

    default_min_metro_pop: int = 25_000
    earth_radius_miles: float = 3959.0
    # Distance at which the quietness remoteness term saturates at 100. W9:
    # 120 ~= p95 of observed distance-to-metro (115.9mi; p50 43.7, n=1681);
    # the old 200mi cap saturated only 13 counties (0.8%) and pinned the
    # median remoteness score at 21.9.
    score_cap_mi: float = 120.0


@dataclass(frozen=True)
class SprawlRiskWeights:
    """Weights for the 5-factor sprawl risk composite (0-100).

    Dropped: best_places_presence (fragile scraping), broadband_availability
    (FCC API complexity not worth 5% weight). Redistributed to remaining factors.
    """

    interstate_proximity: float = 0.30
    building_permit_trend: float = 0.25
    state_migration_accel: float = 0.20
    fast_metro_proximity: float = 0.15
    recreation_retirement: float = 0.10


@dataclass(frozen=True)
class AffordabilityThresholds:
    """Thresholds for affordability scoring."""

    hvi_ratio_best: float = 2.0  # Home-value-to-income ratio: score 100
    hvi_ratio_worst: float = 8.0  # Home-value-to-income ratio: score 0
    rti_ratio_best: float = 0.15  # Rent-to-income ratio: score 100
    rti_ratio_worst: float = 0.45  # Rent-to-income ratio: score 0
    hvi_weight: float = 0.6
    rti_weight: float = 0.4


@dataclass(frozen=True)
class CompositeWeights:
    """Weights for the overall composite score."""

    quietness: float = 0.55
    affordability: float = 0.45  # was misnamed `livability` after that dim died


@dataclass(frozen=True)
class PipelineConfig:
    """Top-level pipeline configuration."""

    stability: StabilityWeights = field(default_factory=StabilityWeights)
    remoteness: RemotenessThresholds = field(default_factory=RemotenessThresholds)
    sprawl_risk: SprawlRiskWeights = field(default_factory=SprawlRiskWeights)
    affordability: AffordabilityThresholds = field(
        default_factory=AffordabilityThresholds
    )
    composite: CompositeWeights = field(default_factory=CompositeWeights)
    min_rucc: int = 6  # Minimum RUCC code for candidate counties
    acs_year: int = 2023  # ACS vintage for affordability + migration fetches
    candidate_pop_min: int = 1000
    candidate_pop_max: int = 3000
    cache_max_age_hours: int = 24


DEFAULT_CONFIG = PipelineConfig()
