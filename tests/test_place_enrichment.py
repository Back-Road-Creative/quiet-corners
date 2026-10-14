"""Tests for place enrichment: gazetteer coordinates and county FIPS resolution."""

from pathlib import Path

from src.core.models import County, Place
from src.ingest.place_populations import (
    assign_county_fips,
    enrich_places_with_gazetteer,
)


class TestEnrichPlacesWithGazetteer:
    """enrich_places_with_gazetteer should set lat/lon from Census place gazetteer."""

    def test_matches_on_geoid(self, tmp_path):
        """Place.fips (7-digit: 2 state + 5 place) should match gazetteer GEOID."""
        gaz_file = tmp_path / "gaz.txt"
        gaz_file.write_text(
            "USPS\tGEOID\tANSICODE\tNAME\tLSAD\tFUNCSTAT\tALAND\tAWATER\t"
            "ALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG\n"
            "NC\t3743480\t123456\tFranklin town\t43\tA\t100\t0\t"
            "1.0\t0.0\t35.180000\t-83.380000\n"
        )
        places = [
            Place(
                fips="3743480",
                name="Franklin",
                state="NC",
                county_fips="37113",
                population=2500,
            ),
        ]

        result = enrich_places_with_gazetteer(places, gaz_file)

        assert len(result) == 1
        assert result[0].lat is not None
        assert abs(result[0].lat - 35.18) < 0.01
        assert abs(result[0].lon - (-83.38)) < 0.01

    def test_handles_trailing_whitespace_in_column_names(self, tmp_path):
        """Census gazetteer TSV has trailing whitespace on INTPTLONG header.

        Regression: without stripping fieldnames, row.get("INTPTLONG") returns
        None because the actual key is "INTPTLONG                   ".
        """
        gaz_file = tmp_path / "gaz.txt"
        # Simulate real Census format: INTPTLONG header has trailing spaces
        gaz_file.write_text(
            "USPS\tGEOID\tANSICODE\tNAME\tLSAD\tFUNCSTAT\tALAND\tAWATER\t"
            "ALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG                    \n"
            "NC\t3743480\t123456\tFranklin town\t43\tA\t100\t0\t"
            "1.0\t0.0\t35.180000\t-83.380000\n"
        )
        places = [
            Place(
                fips="3743480",
                name="Franklin",
                state="NC",
                county_fips="37113",
                population=2500,
            ),
        ]

        result = enrich_places_with_gazetteer(places, gaz_file)

        assert result[0].lat is not None, (
            "Should match despite whitespace in column name"
        )
        assert abs(result[0].lon - (-83.38)) < 0.01

    def test_no_match_leaves_none(self, tmp_path):
        """Places without gazetteer match keep lat/lon as None."""
        gaz_file = tmp_path / "gaz.txt"
        gaz_file.write_text(
            "USPS\tGEOID\tANSICODE\tNAME\tLSAD\tFUNCSTAT\tALAND\tAWATER\t"
            "ALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG\n"
            "NC\t9999999\t123456\tNowhere town\t43\tA\t100\t0\t"
            "1.0\t0.0\t35.000000\t-83.000000\n"
        )
        places = [
            Place(
                fips="3743480",
                name="Franklin",
                state="NC",
                county_fips="37113",
                population=2500,
            ),
        ]

        result = enrich_places_with_gazetteer(places, gaz_file)

        assert result[0].lat is None
        assert result[0].lon is None

    def test_missing_file_returns_unchanged(self):
        """Missing gazetteer file should return places unchanged."""
        places = [
            Place(
                fips="3743480",
                name="Franklin",
                state="NC",
                county_fips="37113",
                population=2500,
            ),
        ]

        result = enrich_places_with_gazetteer(places, Path("/nonexistent/gaz.txt"))

        assert len(result) == 1
        assert result[0].lat is None

    def test_high_match_rate(self, tmp_path):
        """With a real-format gazetteer, >90% of places should get coordinates."""
        gaz_file = tmp_path / "gaz.txt"
        header = (
            "USPS\tGEOID\tANSICODE\tNAME\tLSAD\tFUNCSTAT\tALAND\tAWATER\t"
            "ALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG\n"
        )
        rows = []
        places = []
        for i in range(100):
            geoid = f"37{i:05d}"
            rows.append(
                f"NC\t{geoid}\t{100000 + i}\tTown{i}\t43\tA\t100\t0\t"
                f"1.0\t0.0\t{35.0 + i * 0.01:.6f}\t{-83.0 - i * 0.01:.6f}\n"
            )
            places.append(
                Place(
                    fips=geoid,
                    name=f"Town{i}",
                    state="NC",
                    county_fips="37113",
                    population=2000,
                )
            )

        gaz_file.write_text(header + "".join(rows))

        result = enrich_places_with_gazetteer(places, gaz_file)
        with_coords = sum(1 for p in result if p.lat is not None)

        assert with_coords / len(result) > 0.9


class TestAssignCountyFips:
    """assign_county_fips should resolve COUNTY=000 via nearest-county matching."""

    def test_resolves_000_county(self):
        """Place with county_fips ending in '000' should be reassigned."""
        places = [
            Place(
                fips="3743480",
                name="Franklin",
                state="NC",
                county_fips="37000",
                population=2500,
                lat=35.18,
                lon=-83.38,
            ),
        ]
        counties = [
            County(
                fips="37113",
                state="NC",
                name="Macon",
                population=37000,
                lat=35.15,
                lon=-83.50,
            ),
            County(
                fips="37021",
                state="NC",
                name="Buncombe",
                population=260000,
                lat=35.60,
                lon=-82.55,
            ),
        ]

        result = assign_county_fips(places, counties)

        assert result[0].county_fips == "37113"  # Macon is closer

    def test_skips_valid_county_fips(self):
        """Place with valid county_fips should not be changed."""
        places = [
            Place(
                fips="3743480",
                name="Franklin",
                state="NC",
                county_fips="37113",
                population=2500,
                lat=35.18,
                lon=-83.38,
            ),
        ]
        counties = [
            County(
                fips="37113",
                state="NC",
                name="Macon",
                population=37000,
                lat=35.15,
                lon=-83.50,
            ),
        ]

        result = assign_county_fips(places, counties)

        assert result[0].county_fips == "37113"

    def test_skips_without_coordinates(self):
        """Place without lat/lon cannot be matched, should stay as-is."""
        places = [
            Place(
                fips="3743480",
                name="Franklin",
                state="NC",
                county_fips="37000",
                population=2500,
            ),
        ]
        counties = [
            County(
                fips="37113",
                state="NC",
                name="Macon",
                population=37000,
                lat=35.15,
                lon=-83.50,
            ),
        ]

        result = assign_county_fips(places, counties)

        assert result[0].county_fips == "37000"

    def test_same_state_only(self):
        """Should only match counties in the same state."""
        places = [
            Place(
                fips="1300100",
                name="SomePlace",
                state="GA",
                county_fips="13000",
                population=2000,
                lat=35.15,
                lon=-83.50,
            ),  # Same coords as NC county
        ]
        counties = [
            County(
                fips="37113",
                state="NC",
                name="Macon",
                population=37000,
                lat=35.15,
                lon=-83.50,
            ),
            County(
                fips="13001",
                state="GA",
                name="Appling",
                population=18000,
                lat=31.75,
                lon=-82.30,
            ),
        ]

        result = assign_county_fips(places, counties)

        # Must match GA county even though NC county is closer geographically
        assert result[0].county_fips == "13001"

    def test_no_fips_all_000_after_enrichment(self):
        """After enrichment, candidates should not all have 000 county FIPS."""
        places = [
            Place(
                fips=f"37{i:05d}",
                name=f"Town{i}",
                state="NC",
                county_fips="37000",
                population=2000,
                lat=35.0 + i * 0.1,
                lon=-83.0 - i * 0.1,
            )
            for i in range(10)
        ]
        counties = [
            County(
                fips="37113",
                state="NC",
                name="Macon",
                population=37000,
                lat=35.15,
                lon=-83.50,
            ),
            County(
                fips="37021",
                state="NC",
                name="Buncombe",
                population=260000,
                lat=35.60,
                lon=-82.55,
            ),
        ]

        result = assign_county_fips(places, counties)
        still_000 = sum(1 for p in result if p.county_fips.endswith("000"))

        assert still_000 < len(result), (
            "Not all candidates should still have 000 county FIPS"
        )
