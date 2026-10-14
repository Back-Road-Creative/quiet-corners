"""Candidate towns must be attached to the county they are in.

Finding A.24. SUB-EST's incorporated-place rows (SUMLEV 162) carry COUNTY=000,
so every candidate fell through to nearest-centroid matching and inherited the
scores of whichever county's centroid happened to be closest. Measured against
the 2026-07-31 export, 174 of 1597 published towns (10.9%) were attached to the
wrong county.

The same file already answers the question: SUMLEV 157 rows are place parts
within a county, with a population for each part. That is the Census's own
containment statement, split places included.
"""

from pathlib import Path

from src.ingest.place_populations import (
    load_place_county_map,
    load_subcounty_estimates,
)

HEADER = (
    "SUMLEV,STATE,COUNTY,PLACE,COUSUB,CONCIT,PRIMGEO_FLAG,FUNCSTAT,NAME,STNAME,"
    "ESTIMATESBASE2020,POPESTIMATE2020,POPESTIMATE2024\n"
)


def _csv(tmp_path: Path, *rows: str) -> Path:
    path = tmp_path / "sub-est2024.csv"
    path.write_text(HEADER + "".join(rows), encoding="latin-1")
    return path


class TestPlaceCountyMap:
    def test_a_place_is_mapped_to_the_county_its_row_names(self, tmp_path):
        path = _csv(
            tmp_path,
            "157,01,001,03220,00000,00000,0,A,Autaugaville town,Alabama,800,810,815\n",
        )
        assert load_place_county_map(path) == {"0103220": "01001"}

    def test_a_split_place_belongs_to_the_county_holding_most_of_it(self, tmp_path):
        path = _csv(
            tmp_path,
            "157,01,001,48712,00000,00000,0,A,Millbrook city (pt.),Alabama,400,405,409\n",
            "157,01,051,48712,00000,00000,0,A,Millbrook city (pt.),Alabama,16000,16500,16983\n",
        )
        assert load_place_county_map(path) == {"0148712": "01051"}

    def test_state_level_place_parts_are_ignored(self, tmp_path):
        path = _csv(
            tmp_path,
            "157,01,000,00000,00000,00000,0,A,Balance of state,Alabama,1,1,1\n",
        )
        assert load_place_county_map(path) == {}


class TestCandidatesUseTheRealCounty:
    def test_the_loader_resolves_county_from_the_place_part_rows(self, tmp_path):
        path = _csv(
            tmp_path,
            # The incorporated-place row carries COUNTY=000 …
            "162,05,000,02620,00000,00000,0,A,Atkins city,Arkansas,2900,2950,2000\n",
            # … while the place-part row names the real county.
            "157,05,115,02620,00000,00000,0,A,Atkins city,Arkansas,2900,2950,2000\n",
        )

        places = load_subcounty_estimates(path)

        assert len(places) == 1
        assert places[0].county_fips == "05115", (
            "the county must come from the place-part row, not a placeholder"
        )

    def test_a_place_outside_the_filter_is_dropped_on_its_real_county(self, tmp_path):
        path = _csv(
            tmp_path,
            "162,05,000,02620,00000,00000,0,A,Atkins city,Arkansas,2900,2950,2000\n",
            "157,05,115,02620,00000,00000,0,A,Atkins city,Arkansas,2900,2950,2000\n",
        )

        # 05029 was the county nearest-centroid used to guess; the real one is
        # 05115, which is not in the rural set here.
        assert load_subcounty_estimates(path, county_fips_filter={"05029"}) == []
        assert len(load_subcounty_estimates(path, county_fips_filter={"05115"})) == 1

    def test_a_place_with_no_part_row_keeps_the_placeholder(self, tmp_path):
        path = _csv(
            tmp_path,
            "162,05,000,09999,00000,00000,0,A,Nowhere city,Arkansas,2000,2000,2000\n",
        )

        places = load_subcounty_estimates(path)

        assert len(places) == 1
        assert places[0].county_fips == "05000", (
            "unresolved places stay flagged for the nearest-centroid fallback"
        )
