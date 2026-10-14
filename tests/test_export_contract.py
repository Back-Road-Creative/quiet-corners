"""Export contract tests: ensure JSON output matches what the frontend dashboard expects.

The quiet-corners-dashboard.html reads these fields by name. If the export
schema drifts from what the frontend expects, the dashboard shows no data.
These tests enforce the contract so regressions are caught before deploy.
"""

import json
import tempfile
from pathlib import Path

from src.core.models import (
    AffordabilityResult,
    County,
    Place,
    RemotenessResult,
    SprawlRiskResult,
    StabilityResult,
)
from src.export.contract import (
    CANDIDATE_ENRICHED_FIELDS,
    CANDIDATE_REQUIRED_FIELDS,
    COUNTY_REQUIRED_FIELDS,
    GEO_REQUIRED_FIELDS,
    SPRAWL_FACTOR_FIELDS,
    SUMMARY_REQUIRED_FIELDS,
)
from src.export.hugo_export import HugoExporter
from src.scoring.composite import compute_composite_rank


def _make_scored_county(fips: str = "37113", rucc: int = 7) -> County:
    """Build a fully-scored county with all dimensions populated."""
    return County(
        fips=fips,
        state="NC",
        name="Macon County",
        population=37000,
        rucc=rucc,
        lat=35.18,
        lon=-83.43,
        stability=StabilityResult(
            cv=0.02, slope=-5.0, slope_pct=-0.1, stability_score=85.0
        ),
        remoteness=RemotenessResult("Knoxville", 80.5),
        sprawl_risk=SprawlRiskResult(
            interstate_proximity_score=20.0,
            building_permit_score=10.0,
            state_migration_score=5.0,
            fast_metro_score=15.0,
            recreation_retirement_score=0.0,
            composite=35.0,
        ),
        affordability=AffordabilityResult(
            median_home_value=180000,
            median_rent=800,
            median_income=45000,
            hvi_ratio=4.0,
            rti_ratio=21.3,
            affordability_score=72.0,
        ),
        quietness_score=75.0,
        overall_score=72.0,
    )


# ============================================================================
# counties.json contract
# ============================================================================

# Field-set constants live in src.export.contract (imported above) so the
# runtime validator never depends on the tests package.


class TestCountiesJsonContract:
    """counties.json must contain every field the dashboard reads."""

    def test_all_required_fields_present(self):
        county = _make_scored_county()
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_counties([county], min_rucc=6)
            data = json.loads((Path(tmpdir) / "counties.json").read_text())

        assert len(data) == 1
        entry = data[0]
        missing = COUNTY_REQUIRED_FIELDS - set(entry.keys())
        assert not missing, f"counties.json missing fields: {missing}"

    def test_sprawl_factors_sub_fields(self):
        county = _make_scored_county()
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_counties([county], min_rucc=6)
            data = json.loads((Path(tmpdir) / "counties.json").read_text())

        factors = data[0]["sprawl_factors"]
        missing = SPRAWL_FACTOR_FIELDS - set(factors.keys())
        assert not missing, f"sprawl_factors missing: {missing}"

    def test_sprawl_factors_map_to_correct_fields(self):
        """Key presence alone would let a permits/migration swap ship green."""
        county = _make_scored_county()
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_counties([county], min_rucc=6)
            data = json.loads((Path(tmpdir) / "counties.json").read_text())

        factors = data[0]["sprawl_factors"]
        sr = county.sprawl_risk
        assert factors["interstate"] == sr.interstate_proximity_score
        assert factors["permits"] == sr.building_permit_score
        assert factors["migration"] == sr.state_migration_score
        assert factors["metro_growth"] == sr.fast_metro_score
        assert factors["recreation"] == sr.recreation_retirement_score

    def test_metro_counties_filtered_out(self):
        rural = _make_scored_county("37113", rucc=7)
        metro = _make_scored_county("37021", rucc=3)
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_counties([rural, metro], min_rucc=6)
            data = json.loads((Path(tmpdir) / "counties.json").read_text())

        assert len(data) == 1
        assert data[0]["fips"] == "37113"

    def test_non_empty_output_with_rural_counties(self):
        """Dashboard shows nothing if counties.json is empty array."""
        county = _make_scored_county()
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_counties([county], min_rucc=6)
            data = json.loads((Path(tmpdir) / "counties.json").read_text())

        assert len(data) > 0, (
            "counties.json must not be empty when rural counties exist"
        )


# ============================================================================
# livability removal (audit 2026-07-30)
# ============================================================================


class TestLivabilityRemoved:
    """livability_score was affordability_score verbatim — dropped. It must be
    absent from the contract's required sets and from every export."""

    def test_contract_sets_do_not_require_livability(self):
        for fields in (
            COUNTY_REQUIRED_FIELDS,
            GEO_REQUIRED_FIELDS,
            CANDIDATE_ENRICHED_FIELDS,
            SUMMARY_REQUIRED_FIELDS,
        ):
            assert "livability_score" not in fields
            assert "livability" not in fields

    def test_exports_do_not_emit_livability(self):
        county = _make_scored_county()
        candidate = Place(
            fips="3743480", name="Franklin", state="NC", county_fips="37113"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_all([county], [candidate])
            counties = json.loads((Path(tmpdir) / "counties.json").read_text())
            geo = json.loads((Path(tmpdir) / "geo.json").read_text())
            candidates = json.loads((Path(tmpdir) / "candidates.json").read_text())
            summary = json.loads((Path(tmpdir) / "summary.json").read_text())

        assert "livability_score" not in counties[0]
        assert "livability_score" not in geo[0]
        assert "livability_score" not in candidates[0]
        # Coverage is three real dimensions, not four.
        assert set(summary["coverage"]) == {
            "stability",
            "remoteness",
            "affordability",
        }


# ============================================================================
# geo.json contract
# ============================================================================


class TestGeoJsonContract:
    """geo.json must contain fields used by the map layer."""

    def test_all_required_fields_present(self):
        county = _make_scored_county()
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_geo([county], min_rucc=6)
            data = json.loads((Path(tmpdir) / "geo.json").read_text())

        assert len(data) == 1
        missing = GEO_REQUIRED_FIELDS - set(data[0].keys())
        assert not missing, f"geo.json missing fields: {missing}"

    def test_lat_lon_populated(self):
        county = _make_scored_county()
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_geo([county], min_rucc=6)
            data = json.loads((Path(tmpdir) / "geo.json").read_text())

        assert data[0]["lat"] is not None
        assert data[0]["lon"] is not None


# ============================================================================
# summary.json contract
# ============================================================================


class TestSummaryJsonContract:
    """summary.json must contain fields used by renderMeta()."""

    def test_all_required_fields_present(self):
        county = _make_scored_county()
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_summary([county])
            data = json.loads((Path(tmpdir) / "summary.json").read_text())

        missing = SUMMARY_REQUIRED_FIELDS - set(data.keys())
        assert not missing, f"summary.json missing fields: {missing}"

        sfc = data["sprawl_factor_coverage"]
        assert set(sfc.keys()) == SPRAWL_FACTOR_FIELDS
        assert sfc["recreation"]["count"] == 0  # fixture factor is 0.0
        assert sfc["interstate"] == {"count": 1, "pct": 100.0}
        expected = set(
            "affordability permits metro_growth migration sprawl typology rucc".split()
        )
        assert set(data["data_sources"]) == expected
        assert data["data_sources"]["permits"] is True
        # The fixture carries a RUCC but no recreation/retirement typing.
        assert data["data_sources"]["rucc"] is True
        assert data["data_sources"]["typology"] is False


# ============================================================================
# candidates.json contract
# ============================================================================


class TestCandidatesJsonContract:
    """candidates.json must have base fields + enriched county fields."""

    def test_base_fields_present(self):
        candidate = Place(
            fips="3743480",
            name="Franklin",
            state="NC",
            county_fips="37113",
            population=4100,
            lat=35.18,
            lon=-83.38,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_candidates([candidate])
            data = json.loads((Path(tmpdir) / "candidates.json").read_text())

        missing = CANDIDATE_REQUIRED_FIELDS - set(data[0].keys())
        assert not missing, f"candidates.json missing base fields: {missing}"

    def test_enriched_fields_when_county_linked(self):
        county = _make_scored_county()
        candidate = Place(
            fips="3743480",
            name="Franklin",
            state="NC",
            county_fips="37113",
            population=4100,
            lat=35.18,
            lon=-83.38,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_candidates([candidate], counties=[county])
            data = json.loads((Path(tmpdir) / "candidates.json").read_text())

        missing = CANDIDATE_ENRICHED_FIELDS - set(data[0].keys())
        assert not missing, f"candidates.json missing enriched fields: {missing}"

    def test_candidate_state_exported_as_abbreviation(self):
        """counties.json carries abbrevs; candidates.json must agree."""
        candidate = Place(
            fips="0107000", name="Athens", state="Alabama", county_fips="01083"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_candidates([candidate])
            data = json.loads((Path(tmpdir) / "candidates.json").read_text())
        assert data[0]["state"] == "AL"


# ============================================================================
# states.json contract
# ============================================================================


# ============================================================================
# Composite scoring stores scores before export
# ============================================================================


class TestCompositeStoresScoresForExport:
    """compute_composite_rank must populate quietness_score and overall_score
    so the exporter can include them. Without this, the dashboard gets nulls."""

    def test_scores_populated_after_ranking(self):
        county = County(
            fips="37113",
            state="NC",
            name="Macon",
            population=37000,
            rucc=7,
            stability=StabilityResult(stability_score=85.0),
            remoteness=RemotenessResult("Knoxville", 80.5),
            sprawl_risk=SprawlRiskResult(composite=35.0),
            affordability=AffordabilityResult(affordability_score=70.0),
        )
        ranked = compute_composite_rank([county])
        assert ranked[0].quietness_score is not None, (
            "composite must set quietness_score"
        )
        assert ranked[0].overall_score is not None, "composite must set overall_score"

    def test_full_pipeline_to_export_roundtrip(self):
        """End-to-end: score -> rank -> export -> JSON has all fields."""
        county = County(
            fips="37113",
            state="NC",
            name="Macon",
            population=37000,
            rucc=7,
            lat=35.18,
            lon=-83.43,
            stability=StabilityResult(
                cv=0.02, slope=-5.0, slope_pct=-0.1, stability_score=85.0
            ),
            remoteness=RemotenessResult("Knoxville", 80.5),
            sprawl_risk=SprawlRiskResult(composite=35.0),
            affordability=AffordabilityResult(affordability_score=70.0),
        )
        ranked = compute_composite_rank([county])

        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_counties(ranked, min_rucc=6)
            data = json.loads((Path(tmpdir) / "counties.json").read_text())

        entry = data[0]
        assert entry["quietness_score"] is not None, (
            "Exported quietness_score must not be null"
        )
        assert entry["overall_score"] is not None, (
            "Exported overall_score must not be null"
        )
        missing = COUNTY_REQUIRED_FIELDS - set(entry.keys())
        assert not missing, f"Roundtrip export missing fields: {missing}"
