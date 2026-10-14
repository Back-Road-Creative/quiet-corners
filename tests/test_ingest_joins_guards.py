"""Tests for ingest join keys and parse guards (2026-07 audit, wave 4)."""

from src.core.models import County, Place
from src.ingest.county_populations import parse_county_data
from src.ingest.metro_growth import load_fast_metros, nearest_fast_metro
from src.ingest.place_populations import assign_county_fips, load_subcounty_estimates

SUB_EST_HEADER = (
    "SUMLEV,STATE,COUNTY,PLACE,NAME,STNAME,"
    "ESTIMATESBASE2020,POPESTIMATE2020,POPESTIMATE2022,POPESTIMATE2024\n"
)


def _gazetteer(tmp_path):
    path = tmp_path / "gaz_place.txt"
    path.write_text(
        "USPS\tGEOID\tNAME\tINTPTLAT\tINTPTLONG\n"
        "IL\t1772000\tSpringfield city\t39.76\t-89.65\n"
        "MA\t2567000\tSpringfield city\t42.10\t-72.59\n"
        "IL\t1799990\tShrink city\t40.00\t-89.00\n"
        "MA\t2599991\tTiny town\t42.00\t-72.00\n"
    )
    return path


def _estimates(tmp_path, rows):
    path = tmp_path / "sub-est2024.csv"
    path.write_text(SUB_EST_HEADER + "".join(rows))
    return path


# Finding 1 (unpadded BPS codes must still join county.fips) moved with the
# BPS bulk-CSV re-source: tests/test_building_permits.py::test_unpadded_fips_zero_filled


def test_fast_metros_keyed_by_geoid_excluding_slow_and_small(tmp_path):
    # GEOID-keyed, state-qualified join; growing metro-size places only.
    # Growth spans earliest vs latest POPESTIMATE year, not adjacent years.
    estimates = _estimates(
        tmp_path,
        [
            "162,17,000,72000,Springfield city,Illinois,99000,100000,109000,110000\n",
            "162,25,000,67000,Springfield city,Massachusetts,149000,150000,150000,156000\n",
            "162,17,000,99990,Shrink city,Illinois,101000,100000,95000,90000\n",
            "162,25,000,99991,Tiny town,Massachusetts,900,1000,1500,2000\n",
        ],
    )
    fast = load_fast_metros(estimates, _gazetteer(tmp_path))
    assert sorted(fast) == [(39.76, -89.65, 10.0), (42.10, -72.59, 4.0)]


def test_fast_metros_ignores_non_place_sumlev_and_bad_cells(tmp_path):
    # State-level 040 rows and unparseable estimates must not poison the join.
    estimates = _estimates(
        tmp_path,
        [
            "040,17,000,00000,Illinois,Illinois,12000000,12500000,12600000,12700000\n",
            "162,17,000,72000,Springfield city,Illinois,99000,100000,N/A,110000\n",
            "162,25,000,67000,Springfield city,Massachusetts,149000,150000,,X\n",
        ],
    )
    fast = load_fast_metros(estimates, _gazetteer(tmp_path))
    assert fast == [(39.76, -89.65, 10.0)]


def test_fast_metros_requires_two_estimate_years(tmp_path):
    # A single POPESTIMATE column cannot yield growth — refuse, don't invent.
    path = tmp_path / "sub-est2024.csv"
    path.write_text(
        "SUMLEV,STATE,COUNTY,PLACE,NAME,STNAME,POPESTIMATE2024\n"
        "162,17,000,72000,Springfield city,Illinois,110000\n"
    )
    assert load_fast_metros(path, _gazetteer(tmp_path)) == []


def test_fast_metros_missing_file_returns_empty(tmp_path):
    missing = tmp_path / "nope.csv"
    assert load_fast_metros(missing, _gazetteer(tmp_path)) == []


def test_nearest_fast_metro_picks_nearest_growing():
    fast = [(40.0, -90.0, 8.0), (41.0, -90.0, 3.0)]
    dist, growth = nearest_fast_metro(40.9, -90.0, fast)
    assert growth == 3.0 and dist < 10.0


def test_nearest_fast_metro_none_when_unknown():
    """W9: no coords / no metro data -> None, not a phantom slow metro at
    200mi — the renormalizing sprawl composite must treat it as missing."""
    assert nearest_fast_metro(None, -90.0, [(40.0, -90.0, 8.0)]) is None
    assert nearest_fast_metro(40.0, -90.0, []) is None


def test_resolved_nonrural_place_dropped():
    # Finding 8: place resolved to a non-qualifying county must not ship
    counties = [
        County(fips="55001", state="WI", name="Rural", lat=44.0, lon=-89.0),
        County(fips="55003", state="WI", name="Urban", lat=43.0, lon=-88.0),
    ]
    places = [
        Place("5500001", "NearUrban", "WI", "55000", lat=43.0, lon=-88.0),
        Place("5500002", "NearRural", "WI", "55000", lat=44.0, lon=-89.0),
        Place("5500003", "NoCoords", "WI", "55000"),
    ]
    out = assign_county_fips(places, counties, county_fips_filter={"55001"})
    assert [p.name for p in out] == ["NearRural", "NoCoords"]


def test_malformed_county_cell_skips_year_not_parse():
    # Finding 9: one bad cell must not abort the whole county parse
    raw = [
        ["NAME", "POP_2024", "POP_2023", "ESTIMATESBASE2020", "state", "county"],
        ["Adams County", "N/A", "20100", "20000", "55", "001"],
        ["Ashland County", "16000", "15900", "15800", "55", "003"],
    ]
    counties = parse_county_data(raw)
    assert len(counties) == 2
    assert counties[0].pop_series == {2023: 20100, 2020: 20000}


def test_bad_popestimate_row_skipped(tmp_path):
    # Finding 11: one bad POPESTIMATE row must not abort candidate loading
    csv_path = tmp_path / "sub-est.csv"
    csv_path.write_text(
        "SUMLEV,STATE,COUNTY,PLACE,NAME,STNAME,POPESTIMATE2024\n"
        "162,55,001,00100,Badville,Wisconsin,N/A\n"
        "162,55,001,00200,Goodville,Wisconsin,1500\n"
    )
    assert [p.name for p in load_subcounty_estimates(csv_path)] == ["Goodville"]
