"""Tests for affordability scoring and data ingestion."""

from src.scoring.affordability import compute_affordability


class TestAffordabilityScoring:
    """Test affordability score computation."""

    def test_affordable_county(self):
        """Low ratios produce high scores."""
        result = compute_affordability(home_value=100_000, rent=500, income=50_000)
        assert result.affordability_score is not None
        assert result.affordability_score > 80
        assert result.hvi_ratio == 2.0
        assert result.rti_ratio == 0.12

    def test_expensive_county(self):
        """High ratios produce low scores."""
        result = compute_affordability(home_value=500_000, rent=2500, income=60_000)
        assert result.affordability_score is not None
        assert result.affordability_score < 30

    def test_boundary_best(self):
        """Ratio at best threshold = 100."""
        result = compute_affordability(home_value=100_000, rent=625, income=50_000)
        # HVI ratio = 2.0 (best), RTI ratio = 0.15 (best)
        assert result.affordability_score == 100.0

    def test_boundary_worst(self):
        """Ratio at worst threshold = 0."""
        result = compute_affordability(home_value=400_000, rent=1875, income=50_000)
        # HVI ratio = 8.0 (worst), RTI ratio = 0.45 (worst)
        assert result.affordability_score == 0.0

    def test_zero_income(self):
        """Zero income returns None score (no division by zero)."""
        result = compute_affordability(home_value=200_000, rent=1000, income=0)
        assert result.affordability_score is None
        assert result.median_home_value == 200_000

    def test_none_income(self):
        """None income returns None score."""
        result = compute_affordability(home_value=200_000, rent=1000, income=None)
        assert result.affordability_score is None

    def test_none_home_value(self):
        """Missing home value still scores using rent only."""
        result = compute_affordability(home_value=None, rent=500, income=50_000)
        assert result.affordability_score is not None
        assert result.hvi_ratio is None
        assert result.rti_ratio is not None

    def test_none_rent(self):
        """Missing rent still scores using home value only."""
        result = compute_affordability(home_value=200_000, rent=None, income=50_000)
        assert result.affordability_score is not None
        assert result.rti_ratio is None
        assert result.hvi_ratio is not None

    def test_all_none_values(self):
        """All None returns empty result."""
        result = compute_affordability(home_value=None, rent=None, income=None)
        assert result.affordability_score is None

    def test_score_always_0_100(self):
        """Score is always clamped to 0-100."""
        # Extremely affordable
        result = compute_affordability(home_value=50_000, rent=200, income=100_000)
        assert 0 <= result.affordability_score <= 100

        # Extremely expensive
        result = compute_affordability(home_value=2_000_000, rent=5000, income=30_000)
        assert 0 <= result.affordability_score <= 100

    def test_custom_weights_renormalized(self):
        """Composite is a weighted average over available sub-scores, so
        weights that do not sum to 1 cannot push it off the 0-100 scale."""
        result = compute_affordability(
            home_value=100_000,
            rent=625,
            income=50_000,
            hvi_weight=0.7,
            rti_weight=0.5,
        )
        # Both ratios sit at their best thresholds (score 100 each); a
        # non-normalized blend would report 120.
        assert result.affordability_score == 100.0

    def test_weights_sum_to_one(self):
        """Default weights sum to 1.0."""
        from src.core.config import DEFAULT_CONFIG

        w = DEFAULT_CONFIG.affordability
        assert abs(w.hvi_weight + w.rti_weight - 1.0) < 1e-9


class TestAffordabilityIngest:
    """Test Census API response parsing."""

    def test_parse_int_values(self):
        """Census integer values parse; sentinels, None and blank are None."""
        from src.ingest.affordability import _parse_int

        assert _parse_int("200000") == 200_000
        assert _parse_int("-666666666") is None
        assert _parse_int(None) is None
        assert _parse_int("") is None

    def test_fetch_pads_fips(self):
        """Unpadded state/county codes zero-pad into 5-digit FIPS keys."""
        from unittest.mock import MagicMock

        from src.ingest.affordability import fetch_affordability_data

        client = MagicMock()
        client.fetch.return_value = [
            ["NAME", "B25077_001E", "B25064_001E", "B19013_001E", "state", "county"],
            ["Alameda County, California", "850000", "1800", "112000", "6", "1"],
        ]
        result = fetch_affordability_data(client)
        assert result == {
            "06001": {"home_value": 850_000, "rent": 1800, "income": 112_000}
        }

    def test_negative_census_values(self):
        """Census API uses negative numbers for missing data."""
        from src.ingest.affordability import _parse_int

        assert _parse_int("-666666666") is None
        assert _parse_int("-1") is None
        assert _parse_int("0") == 0
