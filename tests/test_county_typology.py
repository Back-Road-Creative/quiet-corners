"""
Tests for county typology data loading.
"""

import tempfile
from pathlib import Path

from src.ingest.data_downloader import load_county_typology


class TestCountyTypology:
    """USDA County Typology Codes 2015 loader."""

    def test_parse_standard_format(self):
        """Parse CSV with FIPStxt, Recreation, Retirement columns."""
        csv_content = (
            "FIPStxt,State,County,Metro_2013,Nonmetro_2013,"
            "Farming,Mining,Manufacturing,Government,"
            "Recreation,Retirement\n"
            "01001,AL,Autauga County,1,0,0,0,0,0,0,0\n"
            "01003,AL,Baldwin County,1,0,0,0,0,0,1,1\n"
            "01005,AL,Barbour County,0,1,0,0,0,0,0,0\n"
            "37113,NC,Macon County,0,1,0,0,0,0,1,1\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            f.flush()
            result = load_county_typology(Path(f.name))

        assert len(result) == 4
        assert result["01003"]["recreation"] is True
        assert result["01003"]["retirement"] is True
        assert result["01001"]["recreation"] is False
        assert result["01001"]["retirement"] is False
        assert result["37113"]["recreation"] is True
        assert result["37113"]["retirement"] is True

    def test_missing_file_returns_empty(self):
        """Non-existent file returns empty dict."""
        result = load_county_typology(Path("/tmp/nonexistent.csv"))
        assert result == {}

    def test_fips_zero_padded(self):
        """FIPS codes should be zero-padded to 5 digits."""
        csv_content = "FIPStxt,Recreation,Retirement\n1001,0,1\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            f.flush()
            result = load_county_typology(Path(f.name))

        assert "01001" in result
        assert result["01001"]["retirement"] is True
