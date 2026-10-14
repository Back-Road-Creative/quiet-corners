"""
Calibration tests: known reference counties must score within expected ranges.

Franklin, NC (Macon County, FIPS 37113):
- Population ~37,000 (county), ~4,100 (town)
- ~55mi from Asheville (95K), ~80mi from Knoxville (190K), ~140mi from Atlanta (500K)
- RUCC 7 (nonmetro, urban 2.5K-19.9K, not adjacent)
- Should score: moderate remoteness, moderate stability, moderate sprawl risk

All remoteness cases run at the shipped default metro threshold
(DEFAULT_CONFIG.remoteness.default_min_metro_pop) so they validate the metro
set the pipeline actually uses.

Additional reference counties for broader calibration.
"""

from src.core.config import DEFAULT_CONFIG
from src.core.models import Metro
from src.scoring.remoteness import compute_remoteness, haversine
from src.scoring.sprawl_risk import compute_sprawl_risk
from src.scoring.stability import compute_stability


class TestFranklinNCCalibration:
    """Franklin, NC is the primary calibration point."""

    # Macon County centroid
    LAT = 35.18
    LON = -83.43

    # Known metros for NC region
    REGIONAL_METROS = [
        Metro("Asheville", 35.5951, -82.5515, 95_000),
        Metro("Knoxville", 35.9606, -83.9207, 190_000),
        Metro("Charlotte", 35.2271, -80.8431, 900_000),
        Metro("Atlanta", 33.7490, -84.3880, 500_000),
        Metro("Greenville SC", 34.8526, -82.3940, 75_000),
    ]

    def test_distance_to_asheville(self):
        """Franklin to Asheville: ~55 miles (as the crow flies)."""
        d = haversine(self.LAT, self.LON, 35.5951, -82.5515)
        assert 45 < d < 70

    def test_distance_to_knoxville(self):
        """Franklin to Knoxville: ~80 miles."""
        d = haversine(self.LAT, self.LON, 35.9606, -83.9207)
        assert 55 < d < 100

    def test_distance_to_atlanta(self):
        """Franklin to Atlanta: ~130-150 miles."""
        d = haversine(self.LAT, self.LON, 33.7490, -84.3880)
        assert 100 < d < 170

    def test_remoteness_at_default_threshold(self):
        """At the shipped 25K threshold, every regional metro qualifies and
        the nearest is Asheville (95K) at ~55-60mi.
        """
        assert DEFAULT_CONFIG.remoteness.default_min_metro_pop == 25_000
        result = compute_remoteness(self.LAT, self.LON, self.REGIONAL_METROS)
        assert result.nearest_metro_name == "Asheville"
        assert result.distance_to_metro_mi is not None
        assert 45 < result.distance_to_metro_mi < 70

    def test_stability_moderate(self):
        """Macon County has had moderate, slightly declining population.
        Approximate series (2020-2024).
        """
        macon_pop = {2020: 37000, 2021: 37100, 2022: 37200, 2023: 37300, 2024: 37500}
        result = compute_stability(macon_pop)
        assert result.stability_score is not None
        # Moderate-to-high stability (small changes)
        assert result.stability_score > 60

    def test_sprawl_risk_moderate(self):
        """Franklin has some tourism draw but isn't booming.
        Moderate sprawl risk expected.
        """
        result = compute_sprawl_risk(
            interstate_distance_mi=45.0,  # ~45mi to I-40
            building_permits=[15, 18, 20, 22, 25],  # Modest growth
            state_migration_accel=1.2,  # NC is growing
            fast_metro_distance_mi=55.0,  # Asheville is nearby-ish
            fast_metro_growth_pct=2.5,  # Asheville growing moderately
            is_recreation=True,  # Mountain recreation area
            is_retirement=False,
        )
        # Should be moderate — not zero, not extreme
        assert 20 < result.composite < 70


class TestAdditionalCalibrationPoints:
    """Secondary calibration: a deeply rural county and a suburban county."""

    def test_extremely_rural_county(self):
        """Esmeralda County, NV: one of least populated US counties.
        Should have very high remoteness, high stability (stable low pop).
        """
        # Esmeralda County centroid: ~37.78, -117.63
        nevada_metros = [
            Metro("Las Vegas", 36.1699, -115.1398, 650_000),
            Metro("Reno", 39.5296, -119.8138, 270_000),
        ]
        result = compute_remoteness(37.78, -117.63, nevada_metros)
        assert result.distance_to_metro_mi is not None
        assert result.distance_to_metro_mi > 100

    def test_exurban_county_not_quiet(self):
        """Loudoun County, VA: fast-growing DC exurb.
        Should NOT qualify as a quiet corner (low remoteness, high sprawl).
        """
        dc_metros = [
            Metro("Washington DC", 38.9072, -77.0369, 690_000),
            Metro("Baltimore", 39.2904, -76.6122, 570_000),
        ]
        # Loudoun centroid: ~39.08, -77.64
        result = compute_remoteness(39.08, -77.64, dc_metros)
        assert result.distance_to_metro_mi is not None
        assert result.distance_to_metro_mi < 40  # Too close to DC


class TestW9RecalibratedConstants:
    """W9 recalibration: every constant below is derived from the published
    export (1681 counties, site-headlessmode static data) or from the cached
    keyless bulk sources. The derivations live next to each constant in
    src/core/config.py and src/scoring/sprawl_risk.py; these tests pin the
    chosen values so a silent revert or drive-by tweak fails loudly.
    """

    def test_cv_max_pinned_near_observed_p99(self):
        """cv_max 0.05 ~= observed CV p99 (0.0517; p50 0.0108, p95 0.0338).

        At the old 0.15 (~3x p99), cv_score compressed to sd 7.4 vs
        slope_score's 21.1, so the nominal 60/40 CV/slope blend realized
        ~34/66. At 0.05, sd(cv_score) = 19.9 ~= sd(slope_score) and only
        1.2% of counties floor at 0.
        """
        assert DEFAULT_CONFIG.stability.cv_max == 0.05

    def test_cv_slope_blend_kept_deliberately(self):
        """Explicit non-change: 60/40 stays. CV and slope scores correlate
        r=0.96 at the new cv_max (both track the same 5-point trend), and a
        60/40 vs 50/50 blend rank-correlates at rho=0.9993 over the export —
        the ratio carries no ranking power, so changing it would imply a
        precision that does not exist.
        """
        assert DEFAULT_CONFIG.stability.cv_weight == 0.6
        assert DEFAULT_CONFIG.stability.slope_weight == 0.4

    def test_remoteness_score_cap_pinned_to_observed_p95(self):
        """120mi ~= observed p95 distance-to-metro (115.9mi; p50 43.7).

        At the old 200mi cap only 13/1681 counties (0.8%) saturated and the
        median remoteness score was 21.9; at 120mi the median is 36.4, 4.5%
        saturate, and sd rises 15.4 -> 21.8, comparable to the other
        quietness dimensions.
        """
        assert DEFAULT_CONFIG.remoteness.score_cap_mi == 120.0

    def test_composite_weight_named_for_what_it_weighs(self):
        """The 0.45 has applied to affordability since livability was
        removed; the field name must say so."""
        assert DEFAULT_CONFIG.composite.affordability == 0.45
        assert not hasattr(DEFAULT_CONFIG.composite, "livability")
