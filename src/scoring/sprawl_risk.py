"""
Sprawl risk scoring: 5-factor composite (0-100).

Factors:
1. Interstate proximity (30%)
2. Building permit trend (25%)
3. State migration acceleration (20%)
4. Fast-growing metro proximity (15%)
5. Recreation/retirement county type (10%)

Dropped from original 7-factor model:
- "Best places" list presence — requires fragile web scraping
- Broadband availability — FCC API complexity not worth 5% weight

Weights apply to factors with data; missing factors are excluded and the
remaining weights renormalized per county (audit decision D3).

Each result also records a per-factor evidence status (`factor_status`):
observed, partial (scored on a gap-filled input) or unavailable (no source
evidence). Availability comes from what the inputs were, never from the score.
"""

from src.core.config import DEFAULT_CONFIG
from src.core.models import (
    FACTOR_OBSERVED,
    FACTOR_PARTIAL,
    FACTOR_UNAVAILABLE,
    SPRAWL_FACTOR_NAMES,
    SprawlRiskResult,
)


def score_interstate_proximity(distance_mi: float) -> float:
    """Score 0-100: closer to interstate = higher sprawl risk.

    0 mi -> 100, 100+ mi -> 0, linear interpolation.
    """
    distance_mi = max(0.0, distance_mi)
    return max(0.0, min(100.0, (100.0 - distance_mi) / 100.0 * 100.0))


def score_building_permits(permits: list[int | None]) -> float:
    """Score 0-100 based on 5-year building permit trend.

    Measures growth rate of permits. Rapid increase = high sprawl risk. A
    year with no report (None) is scored as zero permits — the pre-existing
    ranking policy, kept until a separate decision; `compute_sprawl_risk`
    records such a series as "partial" so the substitution is visible.
    """
    if not permits or len(permits) < 2:
        return 0.0
    series = [p or 0 for p in permits]

    first = series[0]
    last = series[-1]

    if first == 0 and last == 0:
        return 0.0

    # Growth rate as percentage
    if first == 0:
        # Zero-base growth is unbounded as a ratio, so map each last-year
        # permit to 10 growth-points, capped at the SAME 200-point full scale
        # nonzero-base counties face (the old 100 cap froze a 0->N explosion
        # at score 50 while a 10->30 county reached 100). W9, from BPS
        # 2020-2024 over the exported counties: full scale at 20 last-year
        # permits makes 12 of the 106 zero-base series saturate (11.3%),
        # matching the 11.7% of nonzero-base series at >=200% growth.
        growth_pct = min(last * 10.0, 200.0)
    else:
        growth_pct = ((last - first) / first) * 100

    # Scale: 0% growth -> 0, 200%+ growth -> 100
    return max(0.0, min(100.0, growth_pct / 2.0))


def score_state_migration(acceleration: float) -> float:
    """Score 0-100 based on state's migration acceleration.

    Positive acceleration = inward migration picking up = higher risk.
    Negative acceleration = outward flow = low risk.
    """
    if acceleration <= 0:
        return 0.0
    # Scale: 0 -> 0, +0.5pp -> 100. W9: ACS B07001 year-over-year state
    # deltas are fractions of a point (2022->2023 observed span -1.39..+0.37pp;
    # cached acs_b07001_{2022,2023}.json), so the old /5.0 scale mapped the
    # hottest state to 7.4 and flattened the whole factor. 0.5 puts the
    # observed max positive at 74 with headroom before saturation.
    return max(0.0, min(100.0, acceleration / 0.5 * 100.0))


def score_fast_metro_proximity(distance_mi: float, metro_growth_pct: float) -> float:
    """Score 0-100 based on proximity to a fast-growing metro.

    Close + fast growth = high risk. Far or slow growth = low risk.
    """
    # Distance factor: 0mi -> 1.0, 200mi -> 0.0
    dist_factor = max(0.0, min(1.0, (200.0 - distance_mi) / 200.0))
    # Growth factor: 0% -> 0.0, 5%+ -> 1.0
    growth_factor = max(0.0, min(1.0, metro_growth_pct / 5.0))
    return round(dist_factor * growth_factor * 100, 1)


def score_recreation_retirement(is_recreation: bool, is_retirement: bool) -> float:
    """Score 0-100 based on recreation/retirement county typing."""
    if is_recreation and is_retirement:
        return 100.0
    if is_recreation or is_retirement:
        return 60.0
    return 0.0


def compute_sprawl_risk(
    interstate_distance_mi: float | None = None,
    building_permits: list[int | None] | None = None,
    state_migration_accel: float | None = None,
    fast_metro_distance_mi: float | None = None,
    fast_metro_growth_pct: float | None = None,
    is_recreation: bool | None = None,
    is_retirement: bool | None = None,
) -> SprawlRiskResult:
    """Compute the 5-factor sprawl composite over factors that have data.

    A None input means the source had no data for this county. Such factors
    are excluded and the remaining weights renormalized (audit decision D3),
    so a county missing a dead source is not silently deflated toward "no
    sprawl risk" — while a factor that scores 0.0 from live data still counts
    at full weight. With no live factors at all, the no-data SprawlRiskResult
    (NaN composite) is returned. Missing factors' score fields are stored as
    0.0 so the exporter's `!= 0` coverage counters keep reporting dead
    sources as uncovered.

    `factor_status` says which is which, from the inputs alone: a factor whose
    input was given is "observed" (even when it scored 0.0); a permit series
    with a year missing is "partial" (the gap scored as zero); a None input is
    "unavailable". Callers must pass None — not a default — when the source had
    nothing for the county, or the factor is recorded as observed.
    """
    weights = DEFAULT_CONFIG.sprawl_risk

    i_score = b_score = m_score = f_score = r_score = 0.0
    live: list[tuple[float, float]] = []
    status = {name: FACTOR_UNAVAILABLE for name in SPRAWL_FACTOR_NAMES}

    if interstate_distance_mi is not None:
        i_score = score_interstate_proximity(interstate_distance_mi)
        live.append((i_score, weights.interstate_proximity))
        status["interstate"] = FACTOR_OBSERVED
    if building_permits is not None and len(building_permits) >= 2:
        # A 0- or 1-year series cannot measure a trend: that is missing data.
        b_score = score_building_permits(building_permits)
        live.append((b_score, weights.building_permit_trend))
        status["permits"] = (
            FACTOR_PARTIAL if None in building_permits else FACTOR_OBSERVED
        )
    if state_migration_accel is not None:
        m_score = score_state_migration(state_migration_accel)
        live.append((m_score, weights.state_migration_accel))
        status["migration"] = FACTOR_OBSERVED
    if fast_metro_distance_mi is not None and fast_metro_growth_pct is not None:
        f_score = score_fast_metro_proximity(
            fast_metro_distance_mi, fast_metro_growth_pct
        )
        live.append((f_score, weights.fast_metro_proximity))
        status["metro_growth"] = FACTOR_OBSERVED
    if is_recreation is not None and is_retirement is not None:
        r_score = score_recreation_retirement(is_recreation, is_retirement)
        live.append((r_score, weights.recreation_retirement))
        status["recreation"] = FACTOR_OBSERVED

    if not live:
        return SprawlRiskResult(factor_status=status)

    composite = sum(s * w for s, w in live) / sum(w for _, w in live)

    return SprawlRiskResult(
        interstate_proximity_score=round(i_score, 1),
        building_permit_score=round(b_score, 1),
        state_migration_score=round(m_score, 1),
        fast_metro_score=round(f_score, 1),
        recreation_retirement_score=round(r_score, 1),
        composite=round(composite, 1),
        factor_status=status,
    )
