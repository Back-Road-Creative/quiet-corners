"""
Tests for Hugo JSON export module.

Tests cover:
- summary.json structure and content
- counties.json pre-filtering (RUCC >= 6)
- candidates.json place format
- geo.json centroid format
- File writing to specified output directory
"""

import json
import tempfile
from pathlib import Path

import pytest

from src.core.models import (
    County,
    Place,
    RemotenessResult,
    SprawlRiskResult,
    StabilityResult,
)
from src.export.hugo_export import HugoExporter


@pytest.fixture
def sample_counties() -> list[County]:
    """Mix of rural and metro counties for export testing."""
    return [
        County(
            fips="37113",
            state="NC",
            name="Macon County",
            population=37000,
            rucc=7,
            lat=35.18,
            lon=-83.43,
            stability=StabilityResult(
                cv=0.02, slope=-5.0, slope_pct=-0.1, stability_score=85.0
            ),
            remoteness=RemotenessResult("Knoxville", 80.5),
            sprawl_risk=SprawlRiskResult(composite=35.0),
        ),
        County(
            fips="37021",
            state="NC",
            name="Buncombe County",
            population=270000,
            rucc=3,  # Metro — should be filtered out
            lat=35.61,
            lon=-82.57,
            stability=StabilityResult(
                cv=0.03, slope=500.0, slope_pct=0.5, stability_score=70.0
            ),
            remoteness=RemotenessResult("Charlotte", 112.0),
            sprawl_risk=SprawlRiskResult(composite=65.0),
        ),
        County(
            fips="37173",
            state="NC",
            name="Swain County",
            population=14800,
            rucc=8,
            lat=35.48,
            lon=-83.49,
            stability=StabilityResult(
                cv=0.015, slope=-2.0, slope_pct=-0.05, stability_score=90.0
            ),
            remoteness=RemotenessResult("Knoxville", 65.0),
            sprawl_risk=SprawlRiskResult(composite=20.0),
        ),
    ]


@pytest.fixture
def sample_candidates() -> list[Place]:
    return [
        Place(
            fips="3743480",
            name="Franklin",
            state="NC",
            county_fips="37113",
            population=4100,
            lat=35.18,
            lon=-83.38,
        ),
    ]


class TestSummaryExport:
    """summary.json: aggregate stats, loads immediately."""

    def test_summary_has_required_fields(self, sample_counties):
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_summary(sample_counties)

            data = json.loads((Path(tmpdir) / "summary.json").read_text())
            assert "total_counties_scored" in data
            assert "total_rural_counties" in data
            assert "total_states" in data
            assert "pipeline_version" in data
            assert "generated_at" in data

    def test_summary_counts_correct(self, sample_counties):
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_summary(sample_counties)

            data = json.loads((Path(tmpdir) / "summary.json").read_text())
            assert data["total_counties_scored"] == 3
            # RUCC >= 6: Macon (7) + Swain (8) = 2
            assert data["total_rural_counties"] == 2
            # Both are in NC, so the export spans one state.
            assert data["total_states"] == 1


class TestTotalStatesCountsWhatShips:
    """total_states used to be "states the PEP fetch answered for" — 51,
    territories included, and unrelated to the export. Those endpoints are
    retired, so it is now the distinct states of the rural set that ships.
    The dashboard prints it in its provenance line (quiet-corners.js:270).
    """

    def test_total_states_is_the_exported_sets_distinct_states(self):
        counties = [
            County(fips="37113", state="NC", name="Macon", rucc=7),
            County(fips="56045", state="WY", name="Weston", rucc=9),
            # Metro: never reaches counties.json, so its state must not count.
            County(fips="09003", state="CT", name="Hartford", rucc=1),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_summary(counties)
            exporter.export_counties(counties)
            summary = json.loads((Path(tmpdir) / "summary.json").read_text())
            shipped = json.loads((Path(tmpdir) / "counties.json").read_text())

        assert summary["total_states"] == len({c["state"] for c in shipped}) == 2


class TestCountiesExport:
    """counties.json: pre-filtered to RUCC >= 6."""

    def test_filters_metro_counties(self, sample_counties):
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_counties(sample_counties, min_rucc=6)

            data = json.loads((Path(tmpdir) / "counties.json").read_text())
            # Buncombe (RUCC 3) filtered out, Macon (7) + Swain (8) remain
            assert len(data) == 2
            fips_list = [c["fips"] for c in data]
            assert "37021" not in fips_list
            assert "37113" in fips_list
            assert "37173" in fips_list

    def test_county_has_scoring_fields(self, sample_counties):
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_counties(sample_counties, min_rucc=6)

            data = json.loads((Path(tmpdir) / "counties.json").read_text())
            county = data[0]
            assert "stability_score" in county
            assert "distance_to_metro_mi" in county
            assert "sprawl_risk" in county
            assert "state" in county


class TestCandidatesExport:
    """candidates.json: qualifying towns."""

    def test_candidate_format(self, sample_candidates):
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_candidates(sample_candidates)

            data = json.loads((Path(tmpdir) / "candidates.json").read_text())
            assert len(data) == 1
            assert data[0]["name"] == "Franklin"
            assert data[0]["state"] == "NC"
            assert data[0]["population"] == 4100


class TestGeoExport:
    """geo.json: county centroids for dot map."""

    def test_geo_format(self, sample_counties):
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_geo(sample_counties, min_rucc=6)

            data = json.loads((Path(tmpdir) / "geo.json").read_text())
            assert len(data) == 2  # Only RUCC >= 6
            point = data[0]
            assert "fips" in point
            assert "lat" in point
            assert "lon" in point
            assert "stability_score" in point


class TestFullExport:
    """End-to-end export creates all files."""

    def test_creates_all_files(self, sample_counties, sample_candidates):
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_all(
                counties=sample_counties,
                candidates=sample_candidates,
            )

            expected_files = [
                "summary.json",
                "counties.json",
                "candidates.json",
                "geo.json",
            ]
            for fname in expected_files:
                fpath = Path(tmpdir) / fname
                assert fpath.exists(), f"Missing {fname}"
                data = json.loads(fpath.read_text())
                assert data is not None


class TestCoverageMeasuredAgainstTheExportedSet:
    """Coverage must describe the data the dashboard loads, not the working set.

    counties.json ships RUCC >= min_rucc only (1681 of 3144 in the live export),
    but summary.json used to divide by every county scored. A dimension complete
    for every rural county it ships therefore reported 53%, and a candidate
    pointing at a metro county — one the dashboard cannot resolve — still counted
    as matched. Both numbers are read by the trust panel.
    """

    def test_coverage_counts_only_the_counties_that_ship(self):
        counties = [
            County(
                fips="37113",
                state="NC",
                name="Scored rural",
                population=37000,
                rucc=7,
                lat=35.18,
                lon=-83.43,
                stability=StabilityResult(
                    cv=0.02, slope=-5.0, slope_pct=-0.1, stability_score=85.0
                ),
                remoteness=RemotenessResult("Knoxville", 80.5),
                sprawl_risk=SprawlRiskResult(composite=35.0),
            ),
            County(
                fips="37173",
                state="NC",
                name="Unscored rural",
                population=14800,
                rucc=8,
                lat=35.48,
                lon=-83.49,
                stability=StabilityResult(),
                remoteness=RemotenessResult(),
                sprawl_risk=SprawlRiskResult(),
            ),
            County(
                fips="37021",
                state="NC",
                name="Scored metro (never exported)",
                population=270000,
                rucc=3,
                lat=35.61,
                lon=-82.57,
                stability=StabilityResult(
                    cv=0.03, slope=500.0, slope_pct=0.5, stability_score=70.0
                ),
                remoteness=RemotenessResult("Charlotte", 112.0),
                sprawl_risk=SprawlRiskResult(composite=65.0),
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            HugoExporter(Path(tmpdir)).export_summary(counties)
            summary = json.loads((Path(tmpdir) / "summary.json").read_text())

        stability = summary["coverage"]["stability"]
        # One of the two exported counties is scored. The metro county is
        # scored too, but it never reaches counties.json.
        assert stability["count"] == 1
        assert stability["pct"] == 50.0

    def test_with_county_match_counts_against_the_exported_set(self):
        counties = [
            County(
                fips="37113",
                state="NC",
                name="Rural",
                population=37000,
                rucc=7,
                lat=35.18,
                lon=-83.43,
                stability=StabilityResult(
                    cv=0.02, slope=-5.0, slope_pct=-0.1, stability_score=85.0
                ),
                remoteness=RemotenessResult("Knoxville", 80.5),
                sprawl_risk=SprawlRiskResult(composite=35.0),
            ),
            County(
                fips="37021",
                state="NC",
                name="Metro (never exported)",
                population=270000,
                rucc=3,
                lat=35.61,
                lon=-82.57,
                stability=StabilityResult(
                    cv=0.03, slope=500.0, slope_pct=0.5, stability_score=70.0
                ),
                remoteness=RemotenessResult("Charlotte", 112.0),
                sprawl_risk=SprawlRiskResult(composite=65.0),
            ),
        ]
        candidates = [
            Place(
                fips="3743480",
                name="In a county that ships",
                state="NC",
                county_fips="37113",
                population=4100,
                lat=35.18,
                lon=-83.38,
            ),
            Place(
                fips="3710740",
                name="In a metro county that does not",
                state="NC",
                county_fips="37021",
                population=2600,
                lat=35.61,
                lon=-82.57,
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            HugoExporter(Path(tmpdir)).export_summary(counties, candidates)
            summary = json.loads((Path(tmpdir) / "summary.json").read_text())

        assert summary["candidates"]["total"] == 2
        # Only the first candidate's parent county is in counties.json.
        assert summary["candidates"]["with_county_match"] == 1


class TestDataSourcesNamesEverySource:
    """data_sources must say which inputs carried the run — including the two
    that used to go unreported.

    The trust panel renders whatever key data_sources ships, so a source that
    is never named cannot be shown as missing. Typology (USDA County Typology
    Codes, the recreation/retirement factor) and RUCC (which defines the rural
    set at all) were both absent from the block. Finding D.8.
    """

    def _data_sources(self, counties: list[County]) -> dict:
        with tempfile.TemporaryDirectory() as tmpdir:
            HugoExporter(Path(tmpdir)).export_summary(counties)
            summary = json.loads((Path(tmpdir) / "summary.json").read_text())
        return summary["data_sources"]

    def test_reports_typology_and_rucc_as_absent(self):
        # Sprawl scoring ran, but the typology source carried nothing: the
        # factor is stored as 0.0. No RUCC codes were loaded either.
        counties = [
            County(
                fips="37113",
                state="NC",
                name="Macon County",
                population=37000,
                rucc=None,
                sprawl_risk=SprawlRiskResult(
                    recreation_retirement_score=0.0, composite=35.0
                ),
            ),
            # No live sprawl factor at all: every score is None, not 0.0. A
            # bare `!= 0` would read that as the typology source being present.
            County(
                fips="37173",
                state="NC",
                name="Swain County",
                population=14800,
                rucc=None,
                sprawl_risk=SprawlRiskResult(),
            ),
        ]
        ds = self._data_sources(counties)
        assert ds["typology"] is False
        assert ds["rucc"] is False

    def test_reports_typology_and_rucc_as_present(self):
        counties = [
            County(
                fips="37113",
                state="NC",
                name="Macon County",
                population=37000,
                rucc=7,
                sprawl_risk=SprawlRiskResult(
                    recreation_retirement_score=0.0, composite=35.0
                ),
            ),
            County(
                fips="37173",
                state="NC",
                name="Swain County",
                population=14800,
                rucc=8,
                sprawl_risk=SprawlRiskResult(
                    recreation_retirement_score=60.0, composite=20.0
                ),
            ),
        ]
        ds = self._data_sources(counties)
        assert ds["typology"] is True
        assert ds["rucc"] is True


class TestSprawlLivenessIgnoresNoneAndNaN:
    """A source is live only when a factor actually scored — not when it is
    missing.

    `compute_sprawl_risk` returns a bare `SprawlRiskResult()` for a county with
    no live factor at all (`sprawl_risk.py`), and `County.sprawl_risk` defaults
    to the same object for a county that never reached scoring. Its factor
    scores are `None` and its composite is `NaN`, and both `None != 0` and
    `NaN != 0` are True — so a bare `!= 0` reports every one of those sources as
    present when not one of them carried anything.

    That is not cosmetic. `regression_guard._check_sources` reads `data_sources`
    and `_check_factors` reads `sprawl_factor_coverage`; the false-positive lets
    a run whose sources all died pass the gate that exists to stop exactly that.
    """

    def _summary(self, counties: list[County]) -> dict:
        with tempfile.TemporaryDirectory() as tmpdir:
            HugoExporter(Path(tmpdir)).export_summary(counties)
            return json.loads((Path(tmpdir) / "summary.json").read_text())

    def _dead(self) -> list[County]:
        # Rural (RUCC >= 6) so they land in the sprawl_factor_coverage set, but
        # nothing scored: every factor is None and the composite is NaN.
        return [
            County(
                fips="37113",
                state="NC",
                name="Macon County",
                population=37000,
                rucc=7,
                sprawl_risk=SprawlRiskResult(),
            ),
            County(
                fips="37173",
                state="NC",
                name="Swain County",
                population=14800,
                rucc=8,
                sprawl_risk=SprawlRiskResult(),
            ),
        ]

    def test_data_sources_does_not_carry_a_source_nothing_scored(self):
        ds = self._summary(self._dead())["data_sources"]
        assert ds["permits"] is False
        assert ds["metro_growth"] is False
        assert ds["migration"] is False
        assert ds["sprawl"] is False

    def test_factor_coverage_does_not_count_a_county_that_scored_nothing(self):
        factors = self._summary(self._dead())["sprawl_factor_coverage"]
        for name in (
            "interstate",
            "permits",
            "migration",
            "metro_growth",
            "recreation",
        ):
            assert factors[name]["count"] == 0, name
            assert factors[name]["pct"] == 0.0, name

    def test_a_factor_that_really_scored_still_counts(self):
        # Guards the fix from over-correcting: a live 0.0 means "scored, and the
        # answer was zero", which stays uncovered, while a real value carries.
        counties = self._dead()
        counties[0].sprawl_risk = SprawlRiskResult(
            building_permit_score=42.0,
            state_migration_score=0.0,
            composite=31.5,
        )
        summary = self._summary(counties)
        assert summary["data_sources"]["permits"] is True
        assert summary["data_sources"]["sprawl"] is True
        assert summary["data_sources"]["migration"] is False
        assert summary["sprawl_factor_coverage"]["permits"]["count"] == 1
        assert summary["sprawl_factor_coverage"]["migration"]["count"] == 0


class TestCandidatesEnrichOnlyFromCountiesThatShip:
    """export_candidates must filter its own enrichment lookup.

    export_counties and export_geo filter by min_rucc; export_candidates took
    whatever list it was handed, so a metro county could enrich a candidate
    with scores the dashboard cannot resolve — counties.json never ships it.
    The output is correct today only because the caller happens to hand it
    candidates whose parents are already rural. Finding B.23.
    """

    def _counties(self) -> list[County]:
        return [
            County(
                fips="37113",
                state="NC",
                name="Macon County",
                population=37000,
                rucc=7,
                lat=35.18,
                lon=-83.43,
                stability=StabilityResult(stability_score=85.0),
                remoteness=RemotenessResult("Knoxville", 80.5),
                sprawl_risk=SprawlRiskResult(composite=35.0),
                overall_score=72.0,
            ),
            County(
                fips="37021",
                state="NC",
                name="Buncombe County",
                population=270000,
                rucc=3,  # Metro — never reaches counties.json
                lat=35.61,
                lon=-82.57,
                stability=StabilityResult(stability_score=70.0),
                remoteness=RemotenessResult("Charlotte", 112.0),
                sprawl_risk=SprawlRiskResult(composite=65.0),
                overall_score=55.0,
            ),
        ]

    def _export(self, candidates: list[Place], counties: list[County]) -> str:
        with tempfile.TemporaryDirectory() as tmpdir:
            HugoExporter(Path(tmpdir)).export_candidates(candidates, counties=counties)
            return (Path(tmpdir) / "candidates.json").read_text()

    def test_full_county_list_exports_what_the_rural_list_does(self):
        counties = self._counties()
        rural = [c for c in counties if c.rucc is not None and c.rucc >= 6]
        candidates = [
            Place(
                fips="3743480",
                name="Franklin",
                state="NC",
                county_fips="37113",
                population=4100,
                lat=35.18,
                lon=-83.38,
            ),
            Place(
                fips="3710740",
                name="Black Mountain",
                state="NC",
                county_fips="37021",
                population=8400,
                lat=35.61,
                lon=-82.32,
            ),
        ]

        assert self._export(candidates, counties) == self._export(candidates, rural)

    def test_metro_parent_does_not_enrich(self):
        candidate = Place(
            fips="3710740",
            name="Black Mountain",
            state="NC",
            county_fips="37021",
            population=8400,
            lat=None,
            lon=None,
        )
        entry = json.loads(self._export([candidate], self._counties()))[0]

        assert "overall_score" not in entry
        assert "county_name" not in entry
        # The metro centroid must not be borrowed as the town's location either.
        assert entry["lat"] is None


class TestExportPayloadIsMinified:
    """The dashboard fetches these files; humans do not read them.

    indent=2 shipped roughly 480KB of whitespace across the four data files —
    25% of the export — on every page load. summary.json stays pretty: it is
    1KB, and it is the file the refresh script diffs and an operator reads
    before approving a regeneration. Finding E.18.
    """

    def test_the_data_files_carry_no_indentation(
        self, sample_counties, sample_candidates
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_all(sample_counties, sample_candidates)

            for name in ("counties.json", "candidates.json", "geo.json"):
                text = (Path(tmpdir) / name).read_text()
                assert "\n" not in text, f"{name} still contains newlines"
                assert ", " not in text and ": " not in text, (
                    f"{name} still uses spaced separators"
                )
                json.loads(text)  # still valid JSON

    def test_summary_stays_readable(self, sample_counties, sample_candidates):
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_all(sample_counties, sample_candidates)
            text = (Path(tmpdir) / "summary.json").read_text()

        assert "\n" in text, "summary.json is operator-facing and stays indented"
        json.loads(text)


class TestPlaceShards:
    """places-index.json + places-<ST>.json: every gazetteer place, flat files."""

    @staticmethod
    def _places():
        from src.export.navigation_places import RankedPlace

        return [
            RankedPlace("NC", "3724640", "Franklin town", 35.18, -83.38, 4362, 9),
            RankedPlace("NC", "3700001", "Lonesome CDP", 35.5, -82.5, None, 3),
            RankedPlace("MT", "3070000", "Stevensville town", 46.51, -114.09, 2100, 5),
        ]

    def test_writes_only_flat_files(self, tmp_path):
        HugoExporter(tmp_path).export_place_shards(self._places())

        assert sorted(p.name for p in tmp_path.iterdir()) == [
            "places-MT.json",
            "places-NC.json",
            "places-index.json",
            "places-national.json",
        ]

    def test_national_head_is_the_populated_top_of_the_same_ranking(self, tmp_path):
        # One ladder: below the zoom where a state's shard is fetched, the map
        # labels from this head of the SAME ranked list, each row carrying its
        # state so the client can dedupe it against the shard once that loads.
        # A place with no estimate ranks last, so it never reaches the head.
        HugoExporter(tmp_path).export_place_shards(self._places())
        head = json.loads((tmp_path / "places-national.json").read_text())

        assert head["fields"] == [
            "geoid",
            "display_name",
            "lat",
            "lon",
            "population",
            "state",
        ]
        assert head["places"] == [
            ["3724640", "Franklin", 35.18, -83.38, 4362, "NC"],
            ["3070000", "Stevensville", 46.51, -114.09, 2100, "MT"],
        ]

    def test_index_carries_bbox_and_count_per_state(self, tmp_path):
        HugoExporter(tmp_path).export_place_shards(self._places())
        index = json.loads((tmp_path / "places-index.json").read_text())

        assert index["NC"] == {"bbox": [-83.38, 35.18, -82.5, 35.5], "count": 2}
        assert index["MT"]["count"] == 1

    def test_shard_keeps_ranking_and_compact_records(self, tmp_path):
        HugoExporter(tmp_path).export_place_shards(self._places())
        shard = json.loads((tmp_path / "places-NC.json").read_text())

        assert shard["state"] == "NC"
        assert shard["fields"] == ["geoid", "display_name", "lat", "lon", "population"]
        assert shard["places"] == [
            ["3724640", "Franklin", 35.18, -83.38, 4362],
            ["3700001", "Lonesome", 35.5, -82.5, None],
        ]
