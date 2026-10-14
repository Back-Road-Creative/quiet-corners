"""
Tests for remoteness scoring at 25K population threshold.

The pipeline now uses 25,000 as the minimum city population for remoteness
calculations instead of 100,000, capturing regional hubs like college towns
and county seats that rural residents actually drive to.
"""

from src.core.config import DEFAULT_CONFIG
from src.core.models import Metro
from src.scoring.remoteness import compute_remoteness


class TestDefaultThresholdIs25K:
    """Verify the config default changed from 100K to 25K."""

    def test_config_default(self):
        assert DEFAULT_CONFIG.remoteness.default_min_metro_pop == 25_000


class TestRemoteness25K:
    """Remoteness with 25K threshold captures smaller regional cities."""

    # Regional metros including small cities
    REGIONAL_METROS = [
        Metro("Asheville", 35.5951, -82.5515, 95_000),
        Metro("Knoxville", 35.9606, -83.9207, 190_000),
        Metro("Charlotte", 35.2271, -80.8431, 900_000),
        Metro("Atlanta", 33.7490, -84.3880, 500_000),
        Metro("Greenville SC", 34.8526, -82.3940, 75_000),
        Metro("Sylva", 35.3737, -83.2260, 2_800),  # Too small even for 25K
        Metro("Waynesville", 35.4887, -82.9887, 10_000),  # Too small for 25K
        Metro("Brevard", 35.2334, -82.7343, 8_000),  # Too small for 25K
        Metro("Hendersonville", 35.3187, -82.4612, 15_000),  # Too small for 25K
        Metro("Cullowhee", 35.3134, -83.1760, 6_500),  # Too small for 25K
        Metro("Morganton", 35.7454, -81.6848, 17_000),  # Too small for 25K
        Metro("Boone", 36.2168, -81.6746, 20_000),  # Too small for 25K
        Metro("Hickory", 35.7334, -81.3412, 43_000),  # Qualifies at 25K!
        Metro("Johnson City", 36.3134, -82.3535, 72_000),  # Qualifies at 25K
    ]

    def test_franklin_nc_nearest_25k(self):
        """Franklin, NC: at 25K, Hickory/Greenville/Asheville are all closer
        candidates than the 100K-only result of Knoxville.
        Asheville (95K) and Greenville SC (75K) both qualify and are nearest.
        """
        result = compute_remoteness(
            35.18, -83.43, self.REGIONAL_METROS, min_metro_pop=25_000
        )
        assert result.nearest_metro_name is not None
        # Asheville or Greenville SC should be nearest (both >25K)
        assert result.nearest_metro_name in ("Asheville", "Greenville SC")
        assert result.distance_to_metro_mi is not None
        # Should be closer than the old 100K result (~80mi to Knoxville)
        assert result.distance_to_metro_mi < 70

    def test_small_towns_excluded_at_25k(self):
        """Cities under 25K (Sylva, Waynesville, Brevard) should not qualify."""
        # Use only small towns
        small_only = [m for m in self.REGIONAL_METROS if m.population < 25_000]
        result = compute_remoteness(35.18, -83.43, small_only, min_metro_pop=25_000)
        assert result.nearest_metro_name is None

    def test_hickory_qualifies_at_25k(self):
        """Hickory (43K) qualifies at 25K threshold but not at 100K."""
        hickory_only = [Metro("Hickory", 35.7334, -81.3412, 43_000)]

        result_25k = compute_remoteness(
            35.18, -83.43, hickory_only, min_metro_pop=25_000
        )
        assert result_25k.nearest_metro_name == "Hickory"

        result_100k = compute_remoteness(
            35.18, -83.43, hickory_only, min_metro_pop=100_000
        )
        assert result_100k.nearest_metro_name is None

    def test_truly_remote_still_remote(self):
        """Esmeralda County, NV: still very remote even at 25K threshold.
        Nearest 25K+ city is probably Tonopah-area or further.
        """
        nevada_metros = [
            Metro("Las Vegas", 36.1699, -115.1398, 650_000),
            Metro("Reno", 39.5296, -119.8138, 270_000),
            Metro("Carson City", 39.1638, -119.7674, 58_000),
            Metro("Elko", 40.8324, -115.7631, 22_000),  # Under 25K
        ]
        # Esmeralda County centroid: ~37.78, -117.63
        result = compute_remoteness(37.78, -117.63, nevada_metros, min_metro_pop=25_000)
        assert result.distance_to_metro_mi is not None
        # Still very remote — nearest 25K+ is Las Vegas or Carson City (100+ mi)
        assert result.distance_to_metro_mi > 100

    def test_default_param_uses_25k(self):
        """compute_remoteness() without explicit min_metro_pop uses 25K default."""
        metros = [
            Metro("SmallCity", 35.5, -82.5, 30_000),
            Metro("BigCity", 36.0, -84.0, 200_000),
        ]
        # Default should include SmallCity (30K > 25K)
        result = compute_remoteness(35.18, -83.43, metros)
        assert result.nearest_metro_name == "SmallCity"
