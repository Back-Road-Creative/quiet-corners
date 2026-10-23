"""data_downloader.py's gazetteer/RUCC parsing and the two
load_metros_from_gazetteer join paths. download_file/download_and_unzip/
download_all are covered in test_downloads.py, so they are left out here.
"""

from pathlib import Path

from src.ingest.data_downloader import (
    load_county_gazetteer,
    load_metros_from_gazetteer,
    load_rucc_codes,
)


class TestLoadCountyGazetteer:
    def test_strips_header_whitespace_and_parses_coords(self, tmp_path: Path):
        path = tmp_path / "gaz.txt"
        path.write_text("GEOID \t INTPTLAT \t INTPTLONG \n37113\t35.18\t-83.43\n")
        assert load_county_gazetteer(path) == {"37113": {"lat": 35.18, "lon": -83.43}}

    def test_missing_file_returns_empty(self, tmp_path: Path):
        assert load_county_gazetteer(tmp_path / "nope.txt") == {}


class TestLoadRuccCodes:
    def test_long_format(self, tmp_path: Path):
        path = tmp_path / "rucc_long.csv"
        path.write_text("FIPS,Attribute,Value\n37113,RUCC_2023,7\n")
        assert load_rucc_codes(path) == {"37113": 7}

    def test_wide_format(self, tmp_path: Path):
        path = tmp_path / "rucc_wide.csv"
        path.write_text("FIPS,RUCC_2023\n37113,7\n")
        assert load_rucc_codes(path) == {"37113": 7}


class TestLoadMetrosFromGazetteer:
    def test_pop_column_branch(self, tmp_path: Path):
        path = tmp_path / "place_gaz.txt"
        path.write_text(
            "GEOID\tNAME\tINTPTLAT\tINTPTLONG\tPOP\n"
            "3712345\tBigCity\t35.20\t-80.80\t500000\n"
        )
        metros = load_metros_from_gazetteer(path, min_pop=25_000)
        assert [(m.name, m.population) for m in metros] == [("BigCity", 500000)]

    def test_sub_est_join_branch(self, tmp_path: Path):
        gaz_path = tmp_path / "place_gaz.txt"
        gaz_path.write_text(
            "GEOID\tNAME\tINTPTLAT\tINTPTLONG\n3712345\tBigCity\t35.20\t-80.80\n"
        )
        estimates_path = tmp_path / "sub-est2024.csv"
        estimates_path.write_text(
            "SUMLEV,STATE,COUNTY,PLACE,POPESTIMATE2024\n162,37,113,12345,500000\n"
        )
        metros = load_metros_from_gazetteer(
            gaz_path, min_pop=25_000, estimates_path=estimates_path
        )
        assert [(m.name, m.population) for m in metros] == [("BigCity", 500000)]

    def test_missing_gazetteer_returns_empty(self, tmp_path: Path):
        assert load_metros_from_gazetteer(tmp_path / "nope.txt") == []
