"""County population parsers. Also the first consumer of conftest's
mock_census_response and franklin_nc_county
fixtures.
"""

from pathlib import Path

from src.ingest.county_populations import load_county_populations_csv, parse_county_data


class TestParseCountyData:
    def test_maps_gazetteer_and_rucc_onto_parsed_rows(
        self, mock_census_response, franklin_nc_county
    ):
        gazetteer = {
            franklin_nc_county.fips: {
                "lat": franklin_nc_county.lat,
                "lon": franklin_nc_county.lon,
            }
        }
        rucc = {franklin_nc_county.fips: franklin_nc_county.rucc}

        counties = parse_county_data(mock_census_response, gazetteer, rucc)

        assert len(counties) == 2
        macon = next(c for c in counties if c.fips == franklin_nc_county.fips)
        assert macon.state == franklin_nc_county.state
        assert macon.lat == franklin_nc_county.lat
        assert macon.lon == franklin_nc_county.lon
        assert macon.rucc == franklin_nc_county.rucc
        assert macon.pop_series[2024] == 37500
        assert macon.pop_series[2020] == 36600  # ESTIMATESBASE2020


class TestLoadCountyPopulationsCsv:
    def test_filters_to_county_rows_and_converts_to_api_shape(self, tmp_path: Path):
        csv_path = tmp_path / "co-est2024-alldata.csv"
        csv_path.write_text(
            "SUMLEV,CTYNAME,POPESTIMATE2024,POPESTIMATE2023,POPESTIMATE2022,"
            "POPESTIMATE2021,ESTIMATESBASE2020,STATE,COUNTY\n"
            "040,North Carolina,10000000,,,,,37,000\n"
            "050,Macon County,37500,37200,37000,36800,36600,37,113\n"
        )
        rows = load_county_populations_csv(csv_path)
        assert rows is not None
        assert len(rows) == 2  # header + the one SUMLEV=050 row
        assert rows[1][0] == "Macon County"
        assert rows[1][-2:] == ["37", "113"]

    def test_missing_file_returns_none(self, tmp_path: Path):
        assert load_county_populations_csv(tmp_path / "nope.csv") is None


class TestJulyFirstSeries:
    """The 2020 point must be a July-1 estimate like every other year.

    ESTIMATESBASE2020 is the April 1, 2020 census base, so pairing it with
    July-1 estimates for 2021-2024 made the first interval ~1.25 years while the
    regression and CV treat all five as evenly spaced. Finding A.30: the county
    CSV publishes POPESTIMATE2020 alongside the base — use it.
    """

    def test_the_csv_loader_prefers_the_july_estimate(self, tmp_path):
        csv_path = tmp_path / "co-est2024-alldata.csv"
        csv_path.write_text(
            "SUMLEV,STATE,COUNTY,STNAME,CTYNAME,ESTIMATESBASE2020,POPESTIMATE2020,"
            "POPESTIMATE2021,POPESTIMATE2022,POPESTIMATE2023,POPESTIMATE2024\n"
            "050,37,113,North Carolina,Macon County,36600,36750,37000,37200,37400,37600\n",
            encoding="latin-1",
        )

        rows = load_county_populations_csv(csv_path)
        counties = parse_county_data(rows)

        assert counties[0].pop_series[2020] == 36750, (
            "2020 must come from POPESTIMATE2020 (July 1), not the April 1 base"
        )
        assert sorted(counties[0].pop_series) == [2020, 2021, 2022, 2023, 2024]

    def test_the_april_base_is_the_fallback_when_july_is_missing(self, tmp_path):
        csv_path = tmp_path / "co-est2024-alldata.csv"
        csv_path.write_text(
            "SUMLEV,STATE,COUNTY,STNAME,CTYNAME,ESTIMATESBASE2020,POPESTIMATE2020,"
            "POPESTIMATE2021,POPESTIMATE2022,POPESTIMATE2023,POPESTIMATE2024\n"
            "050,37,113,North Carolina,Macon County,36600,,37000,37200,37400,37600\n",
            encoding="latin-1",
        )

        counties = parse_county_data(load_county_populations_csv(csv_path))

        assert counties[0].pop_series[2020] == 36600
