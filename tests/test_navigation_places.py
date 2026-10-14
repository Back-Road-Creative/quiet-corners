"""Contract tests for navigation-places.json — the map's orientation labels."""

import json
from pathlib import Path

import pytest

from src.export.contract import NAVIGATION_PLACE_REQUIRED_FIELDS
from src.export.hugo_export import HugoExporter
from src.export.navigation_places import (
    TIER_MAJOR,
    TIER_REGIONAL,
    build_all_places,
    build_navigation_places,
    curate_display_name,
    gazetteer_located_count,
)

NASHVILLE = "Nashville-Davidson metropolitan government (balance)"

# (usps, geoid, name, lat, lon, population). Asheville is under the floor in a
# state already covered; Burlington is under it in a state that is not.
SOURCE_ROWS = [
    ("TN", "4752006", NASHVILLE, "36.174", "-86.767", "690000"),
    ("NC", "3774440", "Wilmington city", "34.225", "-77.944", "120000"),
    ("NC", "3702140", "Asheville city", "35.595", "-82.551", "95000"),
    ("VT", "5010675", "Burlington city", "44.475", "-73.212", "44000"),
]
# Large, but the gazetteer has no row for it, so it has no coordinates.
UNLOCATED = ("NC", "3799999", "Nowhere city", "", "", "300000")


def _write_sources(tmp_path: Path) -> tuple[Path, Path]:
    gaz = tmp_path / "gaz.txt"
    gaz.write_text(
        "USPS\tGEOID\tANSICODE\tNAME\tLSAD\tFUNCSTAT\tALAND\tAWATER\t"
        "ALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG\n"
        + "".join(
            f"{u}\t{g}\t1\t{n}\t25\tA\t1\t0\t1.0\t0.0\t{lat}\t{lon}\n"
            for u, g, n, lat, lon, _ in SOURCE_ROWS
        )
    )
    est = tmp_path / "sub-est.csv"
    est.write_text(
        "SUMLEV,STATE,COUNTY,PLACE,NAME,STNAME,POPESTIMATE2024\n"
        + "".join(
            f'162,{g[:2]},000,{g[2:]},"{n}",{u},{pop}\n'
            for u, g, n, _lat, _lon, pop in [*SOURCE_ROWS, UNLOCATED]
        )
    )
    return gaz, est


def _by_display(tmp_path: Path) -> dict[str, dict]:
    gaz, est = _write_sources(tmp_path)
    return {p["display_name"]: p for p in build_navigation_places(gaz, est)}


class TestCurateDisplayName:
    def test_strips_lowercase_legal_suffix(self):
        assert curate_display_name("Wilmington city") == "Wilmington"
        assert curate_display_name("Franklin town") == "Franklin"
        assert curate_display_name("Sun Valley CDP") == "Sun Valley"
        assert curate_display_name("Juneau city and borough") == "Juneau"
        assert (
            curate_display_name("Lexington-Fayette urban county") == "Lexington-Fayette"
        )

    def test_strips_balance_parenthetical(self):
        assert curate_display_name("Indianapolis city (balance)") == "Indianapolis"
        assert curate_display_name(NASHVILLE) == "Nashville-Davidson"

    def test_keeps_a_capitalised_word_that_looks_like_a_suffix(self):
        # "Bullhead City city" is a city named Bullhead City, not Bullhead.
        assert curate_display_name("Bullhead City city") == "Bullhead City"
        assert curate_display_name("Macon-Bibb County") == "Macon-Bibb County"


class TestBuildNavigationPlaces:
    def test_keeps_the_official_name_beside_the_display_name(self, tmp_path):
        nashville = _by_display(tmp_path)["Nashville-Davidson"]

        assert nashville["name"] == NASHVILLE
        assert nashville["state"] == "TN"
        assert abs(nashville["lat"] - 36.174) < 0.001
        assert abs(nashville["lon"] - (-86.767)) < 0.001

    def test_tier_comes_from_population(self, tmp_path):
        places = _by_display(tmp_path)

        assert places["Nashville-Davidson"]["tier"] == TIER_MAJOR
        assert places["Wilmington"]["tier"] == TIER_REGIONAL

    def test_drops_places_under_the_floor(self, tmp_path):
        assert "Asheville" not in _by_display(tmp_path)

    def test_a_state_with_nothing_over_the_floor_still_gets_one_label(self, tmp_path):
        burlington = _by_display(tmp_path)["Burlington"]

        assert burlington["state"] == "VT"
        assert burlington["tier"] == TIER_REGIONAL

    def test_drops_a_place_the_gazetteer_cannot_locate(self, tmp_path):
        assert "Nowhere" not in _by_display(tmp_path)

    def test_missing_sources_yield_no_places(self, tmp_path):
        assert build_navigation_places(tmp_path / "no.txt", tmp_path / "no.csv") == []


class TestNavigationPlacesExport:
    def test_writes_every_contract_field(self, tmp_path):
        gaz, est = _write_sources(tmp_path)
        out = tmp_path / "export"
        HugoExporter(out).export_navigation_places(build_navigation_places(gaz, est))

        data = json.loads((out / "navigation-places.json").read_text())
        assert data
        for entry in data:
            assert NAVIGATION_PLACE_REQUIRED_FIELDS <= set(entry)

    def test_avoids_the_identifiers_reserved_for_candidate_records(self, tmp_path):
        """site-headlessmode's data contract reserves `t` and `pt` for candidates."""
        for entry in _by_display(tmp_path).values():
            assert "t" not in entry
            assert "pt" not in entry


RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
REAL_GAZ = RAW / "2024_Gaz_place_national.txt"
REAL_EST = RAW / "sub-est2024.csv"
needs_raw = pytest.mark.skipif(
    not (REAL_GAZ.exists() and REAL_EST.exists()),
    reason="Census raw inputs are gitignored and absent from this checkout",
)


def _write_gaz_with_cdp(tmp_path: Path) -> tuple[Path, Path]:
    """The base sources plus a CDP the estimates omit and a bigger-area tie."""
    gaz, est = _write_sources(tmp_path)
    with gaz.open("a") as f:
        f.write(
            "MT\t3070000\t1\tStevensville town\t43\tA\t5\t0\t1.0\t0.0\t46.51\t-114.09\n"
        )
        f.write("MT\t3070001\t1\tLonesome CDP\t57\tS\t9\t0\t1.0\t0.0\t46.60\t-114.10\n")
        f.write("MT\t3070002\t1\tNo Coords CDP\t57\tS\t9\t0\t1.0\t0.0\t\t\n")
    with est.open("a") as f:
        f.write('162,30,000,70000,"Stevensville town",MT,2100\n')
    return gaz, est


class TestBuildAllPlaces:
    def test_keeps_a_place_the_estimates_omit_with_null_population(self, tmp_path):
        gaz, est = _write_gaz_with_cdp(tmp_path)
        by_id = {p.geoid: p for p in build_all_places(gaz, est)}

        assert by_id["3070001"].population is None
        assert by_id["3070000"].population == 2100
        assert by_id["3070000"].state == "MT"

    def test_drops_a_gazetteer_row_with_no_coordinates(self, tmp_path):
        gaz, est = _write_gaz_with_cdp(tmp_path)
        ids = {p.geoid for p in build_all_places(gaz, est)}

        assert "3070002" not in ids
        assert len(ids) == gazetteer_located_count(gaz)

    def test_ranks_population_desc_then_land_area_with_null_last(self, tmp_path):
        gaz, est = _write_gaz_with_cdp(tmp_path)
        ids = [p.geoid for p in build_all_places(gaz, est)]

        assert ids[0] == "4752006"
        assert ids.index("3070000") < ids.index("3070001")
        assert ids[-1] == "3070001"

    def test_compact_record_is_five_positions_at_four_decimals(self, tmp_path):
        gaz, est = _write_gaz_with_cdp(tmp_path)
        rec = {p.geoid: p for p in build_all_places(gaz, est)}["3070000"].record()

        assert rec == ["3070000", "Stevensville", 46.51, -114.09, 2100]

    def test_navigation_layer_is_the_head_of_the_same_ranking(self, tmp_path):
        gaz, est = _write_gaz_with_cdp(tmp_path)
        ranked = [p.geoid for p in build_all_places(gaz, est)]
        nav = [p["geoid"] for p in build_navigation_places(gaz, est)]

        assert nav == [g for g in ranked if g in set(nav)]


@needs_raw
class TestRealSources:
    def test_franklin_nc_and_stevensville_mt_are_present(self):
        by_id = {p.geoid: p for p in build_all_places(REAL_GAZ, REAL_EST)}

        franklin = by_id["3724640"]
        assert (franklin.display_name, franklin.state) == ("Franklin", "NC")
        assert franklin.population == 4362
        assert any(
            p.state == "MT" and p.display_name == "Stevensville" for p in by_id.values()
        )

    def test_every_located_gazetteer_row_is_kept(self):
        assert len(build_all_places(REAL_GAZ, REAL_EST)) == gazetteer_located_count(
            REAL_GAZ
        )

    def test_national_head_is_one_fetch_of_populated_places_in_rank_order(
        self, tmp_path
    ):
        from src.export.contract import PLACE_NATIONAL_HEAD, PLACE_SHARD_MAX_BYTES

        places = build_all_places(REAL_GAZ, REAL_EST)
        HugoExporter(tmp_path).export_place_shards(places)

        path = tmp_path / "places-national.json"
        head = json.loads(path.read_text())["places"]
        assert len(head) == PLACE_NATIONAL_HEAD
        assert path.stat().st_size <= PLACE_SHARD_MAX_BYTES
        pops = [row[4] for row in head]
        assert all(isinstance(p, int) for p in pops)
        assert pops == sorted(pops, reverse=True)
        assert {row[5] for row in head} <= {p.state for p in places}

    def test_shards_cover_every_row_and_fit_the_data_budget(self, tmp_path):
        places = build_all_places(REAL_GAZ, REAL_EST)
        HugoExporter(tmp_path).export_place_shards(places)

        index = json.loads((tmp_path / "places-index.json").read_text())
        assert sum(e["count"] for e in index.values()) == 32_333
        sizes = [p.stat().st_size for p in tmp_path.glob("places-??.json")]
        assert len(sizes) == len(index)
        assert max(sizes) <= 150_000
        assert sum(p.stat().st_size for p in tmp_path.iterdir()) <= 2_000_000
