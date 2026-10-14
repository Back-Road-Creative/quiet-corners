"""
Composite ranking: quietness + affordability into overall score.

Quietness: stability 40%, remoteness 30%, anti-sprawl 30%
Overall: quietness 55%, affordability 45%

The former "livability" dimension was removed (audit 2026-07-30): it returned
affordability_score verbatim, silently double-weighting affordability while
advertising a dimension that did not exist. Re-add a livability composite only
if real inputs (safety, healthcare, connectivity) are built.

Missing inputs are excluded and remaining weights renormalized (audit D3):
scored over the evidence that exists; a live 0.0 still counts at full weight.
"""

import math

from src.core.config import DEFAULT_CONFIG
from src.core.models import County

STABILITY_WEIGHT = 0.4
REMOTENESS_WEIGHT = 0.3
ANTI_SPRAWL_WEIGHT = 0.3


def _compute_quietness(c: County) -> float | None:
    """Quietness (0-100) over available inputs; None when nothing is scoreable."""
    parts: list[tuple[float, float]] = []

    if c.stability.stability_score is not None:
        parts.append((c.stability.stability_score, STABILITY_WEIGHT))

    distance = c.remoteness.distance_to_metro_mi
    if distance is not None:
        # Normalize remoteness to 0-100, saturating at score_cap_mi
        # (calibrated to the observed p95 distance — see config).
        cap = DEFAULT_CONFIG.remoteness.score_cap_mi
        parts.append((min(100.0, distance / cap * 100.0), REMOTENESS_WEIGHT))
    elif c.lat is not None and c.lon is not None:
        # No qualifying metro found with real coords = maximally remote, not 0mi.
        parts.append((100.0, REMOTENESS_WEIGHT))
    # No centroid -> remoteness truly unknown -> excluded, not guessed.

    if math.isfinite(c.sprawl_risk.composite):
        # Invert sprawl risk (lower sprawl = better); NaN = never computed.
        parts.append((100.0 - c.sprawl_risk.composite, ANTI_SPRAWL_WEIGHT))

    if not parts:
        return None
    return sum(s * w for s, w in parts) / sum(w for _, w in parts)


def compute_composite_rank(counties: list[County]) -> list[County]:
    """Rank counties by overall suitability (quietness + affordability).

    Counties are sorted best-first by overall score. Counties with no
    scoreable inputs at all get None scores and sort last.
    """
    weights = DEFAULT_CONFIG.composite

    for c in counties:
        quietness = _compute_quietness(c)
        c.quietness_score = None if quietness is None else round(quietness, 1)

        parts: list[tuple[float, float]] = []
        if quietness is not None:
            parts.append((quietness, weights.quietness))
        affordability = c.affordability.affordability_score
        if affordability is not None:
            parts.append((affordability, weights.affordability))
        if parts:
            c.overall_score = round(
                sum(s * w for s, w in parts) / sum(w for _, w in parts), 1
            )
        else:
            c.overall_score = None

    return sorted(counties, key=lambda c: c.overall_score or 0.0, reverse=True)
