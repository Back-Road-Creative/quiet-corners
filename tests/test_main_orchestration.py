"""do_score()'s orchestration (CSV fallback, candidate enrichment) had zero
coverage. Every keyed source is forced to degrade via
CensusAPIKeyError, which is deterministic, hits no network, and doubles as
coverage for _try_keyed()'s conversion into each source's default.
"""

from pathlib import Path

import pytest

from src.ingest.census_api import CensusAPIKeyError, CensusClient
from src.main import _try_keyed, do_score


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Every keyed fetch degrades via CensusAPIKeyError — no real HTTP calls."""

    def _raise(self, *args, **kwargs):
        raise CensusAPIKeyError("no key configured")

    monkeypatch.setattr(CensusClient, "fetch", _raise)


def _write_fixture_data_dir(data_dir: Path) -> None:
    """Just enough of each file format for do_score's CSV-fallback path."""
    (data_dir / "co-est2024-alldata.csv").write_text(
        "SUMLEV,CTYNAME,POPESTIMATE2024,POPESTIMATE2023,POPESTIMATE2022,"
        "POPESTIMATE2021,ESTIMATESBASE2020,STATE,COUNTY\n"
        "050,Macon County,37500,37200,37000,36800,36600,37,113\n"
    )
    (data_dir / "2024_Gaz_counties_national.txt").write_text(
        "GEOID\tINTPTLAT\tINTPTLONG\n37113\t35.18\t-83.43\n"
    )
    (data_dir / "ruralurbancodes2023.csv").write_text("FIPS,RUCC_2023\n37113,7\n")
    (data_dir / "2024_Gaz_place_national.txt").write_text(
        "GEOID\tNAME\tINTPTLAT\tINTPTLONG\n3712345\tTinytown\t35.20\t-83.40\n"
        "3799999\tBigcity\t35.60\t-83.10\n"
    )
    (data_dir / "sub-est2024.csv").write_text(
        "SUMLEV,STATE,COUNTY,PLACE,POPESTIMATE2024,NAME,STNAME\n"
        "162,37,113,12345,1500,Tinytown,North Carolina\n"
        # Bigcity clears the 25k metro floor for the require_metros gate.
        "162,37,999,99999,50000,Bigcity,North Carolina\n"
    )


def test_do_score_orchestrates_csv_fallback_and_candidates(
    tmp_path: Path, capsys
) -> None:
    _write_fixture_data_dir(tmp_path)

    counties, candidates = do_score(tmp_path)

    # CSV fallback: county API degrades, co-est2024-alldata.csv is parsed
    # and enriched with gazetteer coords + RUCC.
    assert len(counties) == 1
    macon = counties[0]
    assert macon.fips == "37113"
    assert macon.state == "NC"
    assert macon.rucc == 7
    assert macon.lat == pytest.approx(35.18)
    assert macon.stability.stability_score is not None  # 5-point pop_series

    # Candidate enrichment: subcounty estimate joined to gazetteer coords.
    assert len(candidates) == 1
    tinytown = candidates[0]
    assert tinytown.county_fips == "37113"
    assert tinytown.lat == pytest.approx(35.20)

    out = capsys.readouterr().out
    assert "falling back to CSV" in out


def test_try_keyed_converts_key_error_to_documented_default(capsys) -> None:
    def _boom(client: object) -> str:
        raise CensusAPIKeyError("no key")

    result = _try_keyed(_boom, None, default="the-default", label="Some Source")

    assert result == "the-default"
    assert "Some Source" in capsys.readouterr().out
