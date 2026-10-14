"""Tests for data preparation and coverage reporting."""

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
from src.export.hugo_export import HugoExporter


class TestSummaryCoverageBlock:
    """summary.json should include per-dimension coverage stats."""

    def test_coverage_block_present(self):
        """summary.json should have a 'coverage' key."""
        counties = [
            County(
                fips="37113",
                state="NC",
                name="Macon",
                population=10000,
                rucc=7,
                stability=StabilityResult(stability_score=80.0),
                remoteness=RemotenessResult("Knoxville", 80.0),
                sprawl_risk=SprawlRiskResult(composite=30.0),
                affordability=AffordabilityResult(affordability_score=70.0),
            )
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_summary(counties)
            data = json.loads((Path(tmpdir) / "summary.json").read_text())

        assert "coverage" in data

    def test_coverage_counts_dimensions(self):
        """Coverage should report count and pct for each dimension."""
        counties = [
            County(
                fips="37001",
                state="NC",
                name="County 1",
                population=10000,
                rucc=7,
                stability=StabilityResult(stability_score=80.0),
                affordability=AffordabilityResult(affordability_score=70.0),
            ),
            County(
                fips="37002",
                state="NC",
                name="County 2",
                population=10000,
                rucc=7,
                stability=StabilityResult(stability_score=75.0),
                affordability=AffordabilityResult(affordability_score=65.0),
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_summary(counties)
            data = json.loads((Path(tmpdir) / "summary.json").read_text())

        cov = data["coverage"]
        assert cov["affordability"]["count"] == 2
        assert cov["affordability"]["pct"] == 100.0


class TestSummaryCandidateStats:
    """summary.json should include candidate statistics."""

    def test_candidates_block_present(self):
        """summary.json should have a 'candidates' key with stats."""
        counties = [
            County(
                fips="37113",
                state="NC",
                name="Macon",
                population=37000,
                rucc=7,
                stability=StabilityResult(stability_score=80.0),
                remoteness=RemotenessResult("Knoxville", 80.0),
                sprawl_risk=SprawlRiskResult(composite=30.0),
            )
        ]
        candidates = [
            Place(
                fips="3743480",
                name="Franklin",
                state="NC",
                county_fips="37113",
                population=2500,
                lat=35.18,
                lon=-83.38,
            ),
            Place(
                fips="3700100",
                name="NoCoords",
                state="NC",
                county_fips="37113",
                population=1500,
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_summary(counties, candidates=candidates)
            data = json.loads((Path(tmpdir) / "summary.json").read_text())

        cand = data["candidates"]
        assert cand["total"] == 2
        assert cand["with_coordinates"] == 1
        assert cand["with_county_match"] == 2


class TestSummaryDataSources:
    """summary.json should include data_sources status map."""

    def test_data_sources_block_present(self):
        """summary.json should have 'data_sources' with boolean flags."""
        counties = [
            County(
                fips="37113",
                state="NC",
                name="Macon",
                population=37000,
                rucc=7,
                affordability=AffordabilityResult(affordability_score=72.0),
            )
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_summary(counties)
            data = json.loads((Path(tmpdir) / "summary.json").read_text())

        ds = data["data_sources"]
        assert ds["affordability"] is True
