"""
Tests for stability scoring module.

Tests cover:
- Coefficient of variation with Bessel's correction (n-1 denominator)
- Linear regression slope calculation
- Composite stability score weighting (60% CV, 40% slope)
- Edge cases: too few data points, zero population, flat series
"""

import math

from src.scoring.stability import compute_stability


class TestCoefficientOfVariation:
    """CV must use Bessel's correction (sample variance, n-1)."""

    def test_known_series_cv(self):
        """With 5 data points, CV should use n-1 denominator."""
        series = {2020: 100, 2021: 100, 2022: 100, 2023: 100, 2024: 100}
        result = compute_stability(series)
        assert result.cv == 0.0

    def test_bessel_correction_matters(self):
        """Verify Bessel's correction: variance = sum((x-mean)^2)/(n-1).

        For [100, 200, 100], mean=133.33, sum_sq=13333.33.
        Population var = 13333.33/3 = 4444.44
        Sample var = 13333.33/2 = 6666.67 (Bessel's)

        With n-1 the CV is larger than with n. We verify we get the n-1 version.
        """
        series = {2020: 100, 2021: 200, 2022: 100}
        result = compute_stability(series)
        mean = 400 / 3
        sum_sq = sum((p - mean) ** 2 for p in [100, 200, 100])
        expected_cv = math.sqrt(sum_sq / 2) / mean  # n-1 = 2
        population_cv = math.sqrt(sum_sq / 3) / mean  # n = 3
        assert result.cv is not None
        assert abs(result.cv - expected_cv) < 0.001
        # Confirm it's NOT the population variance version
        assert abs(result.cv - population_cv) > 0.01

    def test_small_fluctuations(self, stable_pop_series):
        """Stable series should have very low CV."""
        result = compute_stability(stable_pop_series)
        assert result.cv is not None
        assert result.cv < 0.01

    def test_large_fluctuations(self, volatile_pop_series):
        """Volatile series should have high CV."""
        result = compute_stability(volatile_pop_series)
        assert result.cv is not None
        assert result.cv > 0.10


class TestLinearRegressionSlope:
    """Slope calculation via least squares."""

    def test_flat_series_zero_slope(self):
        """Constant population = zero slope."""
        series = {2020: 5000, 2021: 5000, 2022: 5000, 2023: 5000, 2024: 5000}
        result = compute_stability(series)
        assert result.slope == 0.0
        assert result.slope_pct == 0.0

    def test_linear_growth_slope(self):
        """Perfectly linear growth: 100/year from 10000."""
        series = {2020: 10000, 2021: 10100, 2022: 10200, 2023: 10300, 2024: 10400}
        result = compute_stability(series)
        assert result.slope is not None
        assert abs(result.slope - 100.0) < 0.1
        # slope_pct = (100 / mean) * 100; mean = 10200
        expected_pct = (100.0 / 10200) * 100
        assert abs(result.slope_pct - expected_pct) < 0.01

    def test_declining_slope_negative(self, declining_pop_series):
        """Declining population should yield negative slope."""
        result = compute_stability(declining_pop_series)
        assert result.slope is not None
        assert result.slope < 0

    def test_growing_slope_positive(self, growing_pop_series):
        """Growing population should yield positive slope."""
        result = compute_stability(growing_pop_series)
        assert result.slope is not None
        assert result.slope > 0


class TestCompositeStabilityScore:
    """Composite: cv_score * 0.6 + slope_score * 0.4."""

    def test_perfect_stability(self):
        """Flat series should score ~100."""
        series = {2020: 5000, 2021: 5000, 2022: 5000, 2023: 5000, 2024: 5000}
        result = compute_stability(series)
        assert result.stability_score is not None
        assert result.stability_score >= 95.0

    def test_volatile_series_low_score(self, volatile_pop_series):
        """Highly volatile series should score low."""
        result = compute_stability(volatile_pop_series)
        assert result.stability_score is not None
        assert result.stability_score < 50.0

    def test_score_bounded_0_100(self, growing_pop_series, declining_pop_series):
        """Score must always be between 0 and 100."""
        for series in [growing_pop_series, declining_pop_series]:
            result = compute_stability(series)
            assert result.stability_score is not None
            assert 0 <= result.stability_score <= 100

    def test_steady_growth_scores_low_after_recalibration(self, growing_pop_series):
        """Steady 3%/yr growth = instability at the recalibrated scale (W9).

        This series has CV 0.045 and slope 2.83%/yr. In the published export
        (1681 counties) CV p99 is 0.0517 and |slope_pct| p99 is 3.13 — both
        values sit at the ~p99 extreme of the real candidate pool, so the
        score must read near the bottom, not "moderate" (the old cv_max of
        0.15 compressed CV so this scored 44).
        """
        result = compute_stability(growing_pop_series)
        assert result.stability_score is not None
        assert result.stability_score < 20

    def test_weights_sum_to_one(self, config):
        """Verify CV + slope weights = 1.0."""
        w = config.stability
        assert abs(w.cv_weight + w.slope_weight - 1.0) < 1e-9


class TestEdgeCases:
    """Edge cases in stability calculation."""

    def test_too_few_points_returns_none(self):
        """Fewer than 3 data points → all None."""
        series = {2020: 5000, 2021: 5100}
        result = compute_stability(series)
        assert result.cv is None
        assert result.slope is None
        assert result.stability_score is None

    def test_single_point_returns_none(self):
        """Single data point → all None."""
        result = compute_stability({2024: 5000})
        assert result.stability_score is None

    def test_empty_series_returns_none(self):
        """Empty series → all None."""
        result = compute_stability({})
        assert result.stability_score is None

    def test_zero_mean_returns_none(self):
        """Zero mean population → all None (avoid division by zero)."""
        series = {2020: 0, 2021: 0, 2022: 0}
        result = compute_stability(series)
        assert result.cv is None
