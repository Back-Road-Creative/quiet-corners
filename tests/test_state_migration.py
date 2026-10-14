"""
Tests for state migration acceleration ingestion.
"""

from unittest.mock import MagicMock

from src.ingest.state_migration import (
    _fetch_acs_migration,
    fetch_state_migration_accel,
    get_state_migration_accel,
)


class TestFetchAcsMigration:
    """ACS B07001 migration rate parsing."""

    def test_parse_valid_response(self):
        """Parses ACS response into migration rates by state FIPS."""
        client = MagicMock()
        client.fetch.return_value = [
            ["NAME", "B07001_001E", "B07001_065E", "state"],
            ["Alabama", "4800000", "120000", "01"],
            ["Alaska", "700000", "35000", "02"],
        ]
        rates = _fetch_acs_migration(client, 2023)
        assert "01" in rates
        assert "02" in rates
        # 120000/4800000 * 100 = 2.5%
        assert abs(rates["01"] - 2.5) < 0.01
        # 35000/700000 * 100 = 5.0%
        assert abs(rates["02"] - 5.0) < 0.01

    def test_api_failure_returns_empty(self):
        """API failure returns empty dict."""
        client = MagicMock()
        client.fetch.return_value = None
        rates = _fetch_acs_migration(client, 2023)
        assert rates == {}

    def test_invalid_values_skipped(self):
        """Non-numeric values are skipped."""
        client = MagicMock()
        client.fetch.return_value = [
            ["NAME", "B07001_001E", "B07001_065E", "state"],
            ["Alabama", "4800000", "120000", "01"],
            ["BadState", "N/A", "N/A", "99"],
        ]
        rates = _fetch_acs_migration(client, 2023)
        assert "01" in rates
        assert "99" not in rates

    def test_zero_total_skipped(self):
        """State with zero total population is skipped."""
        client = MagicMock()
        client.fetch.return_value = [
            ["NAME", "B07001_001E", "B07001_065E", "state"],
            ["Empty", "0", "0", "01"],
        ]
        rates = _fetch_acs_migration(client, 2023)
        assert "01" not in rates


class TestStateMigrationAccel:
    """Migration acceleration computation."""

    def test_positive_acceleration(self):
        """Increasing migration rate -> positive acceleration."""
        client = MagicMock()
        # 2022: AL rate = 2.0%, 2023: AL rate = 3.0% -> accel = +1.0
        client.fetch.side_effect = [
            # 2023 (recent)
            [
                ["NAME", "B07001_001E", "B07001_065E", "state"],
                ["Alabama", "5000000", "150000", "01"],  # 3.0%
            ],
            # 2022 (prior)
            [
                ["NAME", "B07001_001E", "B07001_065E", "state"],
                ["Alabama", "5000000", "100000", "01"],  # 2.0%
            ],
        ]
        result = fetch_state_migration_accel(client)
        assert "AL" in result
        assert abs(result["AL"] - 1.0) < 0.01

    def test_negative_acceleration(self):
        """Decreasing migration rate -> negative acceleration."""
        client = MagicMock()
        client.fetch.side_effect = [
            # 2023: rate = 2.0%
            [
                ["NAME", "B07001_001E", "B07001_065E", "state"],
                ["Alabama", "5000000", "100000", "01"],
            ],
            # 2022: rate = 3.0%
            [
                ["NAME", "B07001_001E", "B07001_065E", "state"],
                ["Alabama", "5000000", "150000", "01"],
            ],
        ]
        result = fetch_state_migration_accel(client)
        assert "AL" in result
        assert result["AL"] < 0

    def test_api_failure_returns_empty(self):
        """If either year fails, return empty."""
        client = MagicMock()
        client.fetch.return_value = None
        result = fetch_state_migration_accel(client)
        assert result == {}


class TestGetStateMigrationAccel:
    """Lookup helper."""

    def test_found(self):
        lookup = {"NC": 1.5, "TX": -0.3}
        assert get_state_migration_accel("NC", lookup) == 1.5

    def test_missing_returns_none(self):
        assert get_state_migration_accel("ZZ", {"NC": 1.5}) is None
