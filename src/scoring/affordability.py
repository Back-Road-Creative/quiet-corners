"""
Affordability scoring: housing value-to-income and rent-to-income ratios.

Scoring logic:
- Housing value-to-income ratio: 2.0 = 100, 8.0 = 0 (linear)
- Rent-to-income ratio: 0.15 = 100, 0.45 = 0 (linear)
- Composite: weighted average of available sub-scores (HVI 0.6, RTI 0.4),
  renormalized so a missing input keeps the same 0-100 scale
"""

from src.core.config import DEFAULT_CONFIG
from src.core.models import AffordabilityResult


def _interpolate(value: float, best: float, worst: float) -> float:
    """Linear interpolation clamped to 0-100."""
    if worst == best:
        return 50.0
    score = (worst - value) / (worst - best) * 100.0
    return max(0.0, min(100.0, score))


def compute_affordability(
    home_value: int | None,
    rent: int | None,
    income: int | None,
    hvi_best: float = DEFAULT_CONFIG.affordability.hvi_ratio_best,
    hvi_worst: float = DEFAULT_CONFIG.affordability.hvi_ratio_worst,
    rti_best: float = DEFAULT_CONFIG.affordability.rti_ratio_best,
    rti_worst: float = DEFAULT_CONFIG.affordability.rti_ratio_worst,
    hvi_weight: float = DEFAULT_CONFIG.affordability.hvi_weight,
    rti_weight: float = DEFAULT_CONFIG.affordability.rti_weight,
) -> AffordabilityResult:
    """Compute affordability score from Census ACS data.

    Returns AffordabilityResult with component scores and composite.
    Returns empty result if income is missing or zero.
    """
    if income is None or income <= 0:
        return AffordabilityResult(
            median_home_value=home_value,
            median_rent=rent,
            median_income=income,
        )

    hvi_ratio = None
    hvi_score = None
    if home_value is not None:
        hvi_ratio = round(home_value / income, 2)
        hvi_score = round(_interpolate(hvi_ratio, hvi_best, hvi_worst), 1)

    rti_ratio = None
    rti_score = None
    if rent is not None:
        rti_ratio = round((rent * 12) / income, 4)
        rti_score = round(_interpolate(rti_ratio, rti_best, rti_worst), 1)

    # Composite: weighted average renormalized over available sub-scores, so
    # a county missing one input is scored on the same 0-100 scale.
    affordability_score = None
    parts = [
        (score, weight)
        for score, weight in ((hvi_score, hvi_weight), (rti_score, rti_weight))
        if score is not None
    ]
    total_weight = sum(weight for _, weight in parts)
    if parts and total_weight > 0:
        affordability_score = round(
            sum(score * weight for score, weight in parts) / total_weight, 1
        )

    return AffordabilityResult(
        median_home_value=home_value,
        median_rent=rent,
        median_income=income,
        hvi_ratio=hvi_ratio,
        rti_ratio=rti_ratio,
        affordability_score=affordability_score,
    )
