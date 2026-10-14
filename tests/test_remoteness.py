"""
Tests for remoteness scoring module.

Tests cover:
- Haversine formula accuracy against known distances
- Nearest metro lookup with population threshold
- Alaska edge case (large distances)
- Metro list filtering by population
"""

from src.core.models import Metro
from src.scoring.remoteness import compute_remoteness, haversine


class TestHaversine:
    """Haversine distance calculation accuracy."""

    def test_same_point_zero_distance(self):
        """Same coordinates = 0 miles."""
        assert haversine(40.0, -74.0, 40.0, -74.0) == 0.0

    def test_nyc_to_la(self):
        """NYC to LA: ~2,451 miles (well-known reference)."""
        d = haversine(40.7128, -74.0060, 34.0522, -118.2437)
        assert 2440 < d < 2470

    def test_short_distance(self):
        """Asheville to Charlotte: ~100 miles (great-circle)."""
        d = haversine(35.5951, -82.5515, 35.2271, -80.8431)
        assert 90 < d < 115

    def test_symmetry(self):
        """Distance A→B == B→A."""
        d1 = haversine(40.7128, -74.0060, 34.0522, -118.2437)
        d2 = haversine(34.0522, -118.2437, 40.7128, -74.0060)
        assert abs(d1 - d2) < 0.01

    def test_antipodal_points(self):
        """Antipodal points: ~12,450 miles (half Earth circumference)."""
        d = haversine(0.0, 0.0, 0.0, 180.0)
        assert 12400 < d < 12500

    def test_alaska_large_distance(self):
        """Fairbanks, AK to Seattle: ~1,530 miles."""
        d = haversine(64.8378, -147.7164, 47.6062, -122.3321)
        assert 1400 < d < 1700


class TestComputeRemoteness:
    """Nearest metro lookup with population thresholds."""

    def test_franklin_nc_nearest_100k(self, sample_metros):
        """Franklin, NC (~35.18, -83.43): nearest 100K+ is Knoxville (~80mi)
        or Atlanta (~140mi). Asheville is only 95K so excluded at 100K threshold.
        """
        result = compute_remoteness(35.18, -83.43, sample_metros, min_metro_pop=100_000)
        assert result.nearest_metro_name is not None
        # Asheville (95K) excluded, so nearest 100K+ should be Knoxville or Charlotte
        assert result.nearest_metro_name in ("Knoxville", "Charlotte")
        assert result.distance_to_metro_mi is not None
        assert result.distance_to_metro_mi > 50

    def test_franklin_nc_nearest_50k(self, sample_metros):
        """At 50K threshold, Asheville (95K) qualifies and is nearest."""
        result = compute_remoteness(35.18, -83.43, sample_metros, min_metro_pop=50_000)
        assert result.nearest_metro_name == "Asheville"
        assert result.distance_to_metro_mi is not None
        assert result.distance_to_metro_mi < 70  # ~55 miles

    def test_urban_county_near_metro(self, sample_metros):
        """A county right next to Atlanta should be very close."""
        result = compute_remoteness(33.80, -84.40, sample_metros, min_metro_pop=100_000)
        assert result.distance_to_metro_mi is not None
        assert result.distance_to_metro_mi < 10

    def test_empty_metro_list(self):
        """Empty metro list → None results."""
        result = compute_remoteness(35.0, -83.0, [], min_metro_pop=100_000)
        assert result.nearest_metro_name is None
        assert result.distance_to_metro_mi is None

    def test_no_metros_above_threshold(self, sample_metros):
        """If threshold is very high, no metros qualify."""
        result = compute_remoteness(
            35.0, -83.0, sample_metros, min_metro_pop=10_000_000
        )
        assert result.nearest_metro_name is None

    def test_alaska_fairbanks(self):
        """Fairbanks is very remote — >1000 miles from any lower-48 metro."""
        lower48_metros = [
            Metro("Seattle", 47.6062, -122.3321, 740_000),
            Metro("Minneapolis", 44.9778, -93.2650, 430_000),
        ]
        result = compute_remoteness(
            64.8378, -147.7164, lower48_metros, min_metro_pop=100_000
        )
        assert result.distance_to_metro_mi is not None
        assert result.distance_to_metro_mi > 1000
