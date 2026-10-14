"""
Stability scoring: coefficient of variation + linear regression slope.

Fixes Bessel's correction bug from original 02_fetch_county_data.py:
- Uses sample variance (n-1 denominator) instead of population variance (n).
- With only 5 data points this materially impacts the CV calculation.
"""

import math

from src.core.config import DEFAULT_CONFIG
from src.core.models import StabilityResult


def compute_stability(
    pop_series: dict[int, int],
    cv_weight: float = DEFAULT_CONFIG.stability.cv_weight,
    slope_weight: float = DEFAULT_CONFIG.stability.slope_weight,
    cv_max: float = DEFAULT_CONFIG.stability.cv_max,
    slope_pct_max: float = DEFAULT_CONFIG.stability.slope_pct_max,
) -> StabilityResult:
    """Compute stability metrics from a population time series.

    Returns StabilityResult with cv, slope, slope_pct, and composite score.
    Uses Bessel's correction (n-1) for sample standard deviation.
    """
    if len(pop_series) < 3:
        return StabilityResult()

    years = sorted(pop_series.keys())
    pops = [pop_series[y] for y in years]
    n = len(pops)

    mean_pop = sum(pops) / n
    if mean_pop == 0:
        return StabilityResult()

    # Coefficient of variation with Bessel's correction (n-1)
    sum_sq_diff = sum((p - mean_pop) ** 2 for p in pops)
    variance = sum_sq_diff / (n - 1)  # Bessel's correction
    std_dev = math.sqrt(variance)
    cv = std_dev / mean_pop

    # Linear regression slope (least squares)
    sum_x = sum(years)
    sum_y = sum(pops)
    sum_xy = sum(x * y for x, y in zip(years, pops))
    sum_x2 = sum(x * x for x in years)

    denom = n * sum_x2 - sum_x * sum_x
    slope = (n * sum_xy - sum_x * sum_y) / denom if denom != 0 else 0.0

    slope_pct = (slope / mean_pop) * 100  # Annual % change

    # Composite stability score: 0-100, higher = more stable
    cv_score = max(0.0, min(100.0, (cv_max - cv) / cv_max * 100))
    slope_score = max(
        0.0, min(100.0, (slope_pct_max - abs(slope_pct)) / slope_pct_max * 100)
    )
    stability_score = round(cv_score * cv_weight + slope_score * slope_weight, 1)

    return StabilityResult(
        cv=round(cv, 4),
        slope=round(slope, 1),
        slope_pct=round(slope_pct, 3),
        stability_score=stability_score,
    )
