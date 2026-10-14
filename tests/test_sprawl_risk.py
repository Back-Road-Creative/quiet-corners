"""
Tests for sprawl risk scoring module (5-factor model).

Tests cover:
- Each of 5 factors independently
- Composite score calculation and weighting
- Score bounds (0-100)
- Edge cases: missing data, zero values
- Missing sources renormalize weights instead of deflating (audit D3)
"""

import math

from src.core.models import SprawlRiskResult
from src.scoring.sprawl_risk import (
    compute_sprawl_risk,
    score_building_permits,
    score_fast_metro_proximity,
    score_interstate_proximity,
    score_recreation_retirement,
    score_state_migration,
)


class TestInterstateProximity:
    """Distance to nearest interstate interchange (30% weight)."""

    def test_on_interstate_max_risk(self):
        """County on an interstate -> score ~100 (max risk)."""
        assert score_interstate_proximity(0.0) >= 90

    def test_far_from_interstate_low_risk(self):
        """County >100mi from interstate -> score near 0."""
        assert score_interstate_proximity(150.0) < 10

    def test_moderate_distance(self):
        """County 30mi from interstate -> moderate risk."""
        s = score_interstate_proximity(30.0)
        assert 20 < s < 80

    def test_negative_distance_clamped(self):
        """Negative distance treated as 0."""
        assert score_interstate_proximity(-5.0) == score_interstate_proximity(0.0)


class TestBuildingPermits:
    """Building permit trend over last 5 years (25% weight)."""

    def test_no_permits_low_risk(self):
        """Zero permits all years -> 0 risk."""
        assert score_building_permits([0, 0, 0, 0, 0]) == 0.0

    def test_rapidly_increasing_permits_high_risk(self):
        """Rapidly increasing permits -> high risk."""
        s = score_building_permits([10, 20, 40, 80, 160])
        assert s > 70

    def test_declining_permits_low_risk(self):
        """Declining permits -> low risk."""
        s = score_building_permits([100, 80, 60, 40, 20])
        assert s < 30

    def test_empty_list_zero(self):
        """Empty permit data -> 0 risk."""
        assert score_building_permits([]) == 0.0

    def test_zero_base_explosion_reaches_full_scale(self):
        """W9: a 0->20 permit explosion scores 100, like a 200% grower.

        The old min(last*10, 100) cap meant a zero-base boom topped out at 50
        while a 10->30 county reached 100. In BPS 2020-2024 over the exported
        counties, 106/1582 series are zero-base; capping at 200 growth-points
        (full scale at 20 last-year permits) makes 11.3% of them saturate,
        matching the 11.7% of nonzero-base series at >=200% growth.
        """
        assert score_building_permits([0, 0, 0, 0, 20]) == 100.0
        # Parity with a nonzero-base tripling (200% growth -> full scale).
        assert score_building_permits([0, 0, 0, 0, 20]) == score_building_permits(
            [10, 15, 20, 25, 30]
        )

    def test_zero_base_midscale_no_longer_capped(self):
        """W9: 0->15 permits reads 75 (old cap froze everything >=10 at 50)."""
        assert score_building_permits([0, 0, 0, 0, 15]) == 75.0

    def test_zero_base_blip_stays_low(self):
        """A 0->4 blip (median zero-base last-year count is 3) stays low."""
        assert score_building_permits([0, 0, 0, 0, 4]) == 20.0


class TestStateMigration:
    """Parent state migration acceleration (20% weight)."""

    def test_zero_acceleration_zero_risk(self):
        """No acceleration -> 0 risk."""
        assert score_state_migration(0.0) == 0.0

    def test_high_positive_acceleration_high_risk(self):
        """Strong inward migration acceleration -> high risk."""
        s = score_state_migration(5.0)
        assert s > 70

    def test_negative_acceleration_low_risk(self):
        """Outward migration -> low/no risk."""
        s = score_state_migration(-3.0)
        assert s < 10

    def test_observed_scale_half_point_is_full_scale(self):
        """W9: full scale at +0.5pp, grounded in ACS B07001 deltas.

        Observed 2022->2023 state deltas (cached ACS files) span
        -1.39..+0.37pp; the old /5.0 scale mapped the hottest state to 7.4.
        At /0.5 the observed max positive (+0.37pp) reads 74 with headroom
        for a stronger year before saturating.
        """
        assert score_state_migration(0.5) == 100.0
        assert score_state_migration(0.25) == 50.0

    def test_observed_max_positive_accel_reads_high(self):
        """The hottest observed state (+0.37pp) must register, not sit at ~7."""
        assert score_state_migration(0.37) > 70


class TestFastMetroProximity:
    """Proximity to fast-growing metro (15% weight)."""

    def test_near_fast_growing_metro_high_risk(self):
        """Close to a fast-growing metro -> high risk."""
        s = score_fast_metro_proximity(distance_mi=30.0, metro_growth_pct=5.0)
        assert s > 60

    def test_far_from_any_metro_low_risk(self):
        """Far from fast-growing metro -> low risk."""
        s = score_fast_metro_proximity(distance_mi=200.0, metro_growth_pct=5.0)
        assert s < 20

    def test_near_slow_metro_moderate(self):
        """Near a slow-growing metro -> lower risk."""
        s = score_fast_metro_proximity(distance_mi=30.0, metro_growth_pct=0.5)
        assert s < 40


class TestRecreationRetirement:
    """Recreation/retirement county type (10% weight)."""

    def test_recreation_county_high_risk(self):
        """Flagged as recreation county -> high risk."""
        assert score_recreation_retirement(is_recreation=True, is_retirement=False) > 50

    def test_retirement_county_high_risk(self):
        """Flagged as retirement county -> high risk."""
        assert score_recreation_retirement(is_recreation=False, is_retirement=True) > 50

    def test_both_flags_max_risk(self):
        """Both recreation + retirement -> max risk."""
        assert score_recreation_retirement(is_recreation=True, is_retirement=True) >= 90

    def test_neither_flag_zero_risk(self):
        """Neither flag -> 0 risk."""
        assert (
            score_recreation_retirement(is_recreation=False, is_retirement=False) == 0.0
        )


class TestCompositeSprawlRisk:
    """Composite score with 5-factor weighting."""

    def test_all_zero_factors_zero_composite(self):
        """All factors at 0 -> composite 0."""
        result = compute_sprawl_risk(
            interstate_distance_mi=200.0,
            building_permits=[0, 0, 0, 0, 0],
            state_migration_accel=0.0,
            fast_metro_distance_mi=300.0,
            fast_metro_growth_pct=0.0,
            is_recreation=False,
            is_retirement=False,
        )
        assert result.composite < 10

    def test_all_max_factors_high_composite(self):
        """All factors maxed -> composite near 100."""
        result = compute_sprawl_risk(
            interstate_distance_mi=0.0,
            building_permits=[10, 20, 40, 80, 160],
            state_migration_accel=10.0,
            fast_metro_distance_mi=10.0,
            fast_metro_growth_pct=10.0,
            is_recreation=True,
            is_retirement=True,
        )
        assert result.composite > 70

    def test_composite_bounded(self):
        """Score always between 0 and 100."""
        result = compute_sprawl_risk(
            interstate_distance_mi=50.0,
            building_permits=[30, 35, 40, 42, 45],
            state_migration_accel=1.5,
            fast_metro_distance_mi=80.0,
            fast_metro_growth_pct=2.0,
            is_recreation=False,
            is_retirement=False,
        )
        assert 0 <= result.composite <= 100

    def test_weights_sum_to_one(self, config):
        """Sprawl risk weights must sum to 1.0."""
        w = config.sprawl_risk
        total = (
            w.interstate_proximity
            + w.building_permit_trend
            + w.state_migration_accel
            + w.fast_metro_proximity
            + w.recreation_retirement
        )
        assert abs(total - 1.0) < 1e-9

    def test_no_best_places_or_broadband_fields(self):
        """5-factor model should not have best_places or broadband fields."""
        result = compute_sprawl_risk()
        assert not hasattr(result, "best_places_score")
        assert not hasattr(result, "broadband_score")


class TestMissingDataRenormalization:
    """Audit D3: weights renormalize over factors that actually have data."""

    LIVE_MAX = {
        "interstate_distance_mi": 0.0,  # -> 100
        "state_migration_accel": 5.0,  # -> 100
        "fast_metro_distance_mi": 0.0,
        "fast_metro_growth_pct": 5.0,  # -> 100
        "is_recreation": True,
        "is_retirement": True,  # -> 100
    }

    def test_missing_permits_and_metro_do_not_deflate(self):
        """Dead sources drop out; live factors carry their full weight."""
        result = compute_sprawl_risk(
            interstate_distance_mi=0.0,
            building_permits=None,
            state_migration_accel=5.0,
            fast_metro_distance_mi=None,
            fast_metro_growth_pct=None,
            is_recreation=True,
            is_retirement=True,
        )
        # All live factors at 100 -> composite 100, not 100 * 0.6.
        assert result.composite == 100.0

    def test_live_zero_permits_still_count(self):
        """A 0 scored from real permit data weighs in; a missing source doesn't."""
        live_zero = compute_sprawl_risk(
            building_permits=[100, 100, 100, 100, 100], **self.LIVE_MAX
        )
        missing = compute_sprawl_risk(building_permits=None, **self.LIVE_MAX)
        assert live_zero.composite == 75.0  # flat permits drag the composite
        assert missing.composite == 100.0  # absent permits do not

    def test_no_live_factors_means_no_data(self):
        """Nothing to score -> no-data result, not a flattering 0.0."""
        assert math.isnan(compute_sprawl_risk().composite)
        # A single-year permit series cannot measure a trend either.
        assert math.isnan(compute_sprawl_risk(building_permits=[7]).composite)


class TestSprawlRiskResultDefaults:
    """A never-computed result must be distinguishable from computed zeros."""

    def test_factor_scores_default_to_none(self):
        r = SprawlRiskResult()
        assert r.interstate_proximity_score is None
        assert r.building_permit_score is None
        assert r.state_migration_score is None
        assert r.fast_metro_score is None
        assert r.recreation_retirement_score is None

    def test_composite_defaults_to_nan_not_zero(self):
        """0.0 would read as 'no sprawl risk'; NaN reads as 'never computed'."""
        assert math.isnan(SprawlRiskResult().composite)
