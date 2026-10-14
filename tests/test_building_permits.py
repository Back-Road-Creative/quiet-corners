"""
Tests for building permits ingestion.

api.census.gov retired the BPS timeseries dataset; permits now come from the
keyless county annual bulk files on www2.census.gov/econ/bps/County/. Tests
use fixture files replicating that format — never the network.
"""

from src.ingest import building_permits
from src.ingest.building_permits import (
    BPS_YEARS,
    bps_county_filename,
    fetch_building_permits,
    load_bps_county_csv,
)

# Real co{year}a.txt header: group labels sit over each group's "Units" column;
# "rep" groups are the reported subset and must not be double-counted.
HEADER = (
    "Survey,FIPS,FIPS,Region,Division,County,,1-unit,,,2-units,,,3-4 units,,,5+ units,,,1-unit rep,,\n"
    "Date,State,County,Code,Code,Name,Bldgs,Units,Value,Bldgs,Units,Value,Bldgs,Units,Value,Bldgs,Units,Value,Bldgs,Units,Value\n"
    " \n"
)


def _row(year, state, county, u1=0, u2=0, u34=0, u5=0):
    # Bldgs=1 / Value=9 / rep Units=u1, so a sum over the wrong columns is caught
    return (
        f"{year},{state},{county},3,6,Test County                ,"
        f"1,{u1},9,1,{u2},9,1,{u34},9,1,{u5},9,1,{u1},9\n"
    )


def _write_year(tmp_path, year, county_units):
    """Write one co{year}a.txt fixture from {(state, county): units-or-tuple}."""
    path = tmp_path / bps_county_filename(year)
    rows = "".join(
        _row(year, s, c, *(u if isinstance(u, tuple) else (u,)))
        for (s, c), u in county_units.items()
    )
    path.write_text(HEADER + rows)
    return path


def test_retired_api_gate_removed():
    """The dead api.census.gov path was deleted outright — no gate flag left."""
    assert not hasattr(building_permits, "BPS_TIMESERIES_RETIRED")


class TestLoadBpsCountyCsv:
    """Single-year BPS parsing."""

    def test_parse_valid_file(self, tmp_path):
        """Sums the four estimate Units columns; ignores Bldgs, Value and rep."""
        path = _write_year(
            tmp_path, 2024, {("01", "001"): (100, 4, 0, 46), ("37", "113"): 25}
        )
        result = load_bps_county_csv(path)
        assert result["01001"] == 150
        assert result["37113"] == 25

    def test_unpadded_fips_zero_filled(self, tmp_path):
        # Unpadded BPS codes must still join county.fips
        path = _write_year(tmp_path, 2024, {("6", "1"): 150})
        assert load_bps_county_csv(path) == {"06001": 150}

    def test_negative_permits_clamped(self, tmp_path):
        """Negative permit values (corrections) clamped to 0."""
        path = _write_year(tmp_path, 2024, {("01", "001"): -5})
        assert load_bps_county_csv(path) == {"01001": 0}

    def test_invalid_values_skipped(self, tmp_path):
        """Non-numeric permit values are skipped."""
        path = _write_year(tmp_path, 2024, {("01", "001"): "N/A", ("01", "003"): 100})
        result = load_bps_county_csv(path)
        assert "01001" not in result
        assert result["01003"] == 100

    def test_missing_file_returns_empty(self, tmp_path):
        assert load_bps_county_csv(tmp_path / "co1990a.txt") == {}


class TestFetchBuildingPermits:
    """Multi-year permit timeseries assembled from the on-disk bulk files."""

    def test_builds_timeseries(self, tmp_path):
        """Builds per-county timeseries across years."""
        _write_year(tmp_path, 2020, {("01", "001"): 10, ("37", "113"): 50})
        _write_year(tmp_path, 2021, {("01", "001"): 15, ("37", "113"): 55})
        _write_year(tmp_path, 2022, {("01", "001"): 20})
        result = fetch_building_permits(tmp_path, years=[2020, 2021, 2022])
        assert result["01001"] == [10, 15, 20]
        assert result["37113"] == [50, 55, None]  # Missing year = no observation

    def test_all_years_missing_returns_empty(self, tmp_path):
        """If all years fail, return empty."""
        assert fetch_building_permits(tmp_path, years=BPS_YEARS) == {}

    def test_partial_years(self, tmp_path):
        """Some years succeed, others fail — still produces output; the gap is None."""
        _write_year(tmp_path, 2020, {("01", "001"): 10})
        result = fetch_building_permits(tmp_path, years=[2020, 2021])
        assert result["01001"] == [10, None]
