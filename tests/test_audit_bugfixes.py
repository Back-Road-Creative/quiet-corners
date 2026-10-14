"""Tests for bugs identified in the audit.

Fix 4: County has quietness_score and overall_score; composite stores them; export includes them
"""

import json
import tempfile
from pathlib import Path

from src.core.models import (
    AffordabilityResult,
    County,
    RemotenessResult,
    SprawlRiskResult,
    StabilityResult,
)
from src.export.hugo_export import HugoExporter
from src.scoring.composite import compute_composite_rank


class TestCompositeScoresStored:
    """Fix 4: compute_composite_rank stores quietness_score and overall_score."""

    def _make_county(
        self,
        fips: str,
        stability: float,
        remoteness_mi: float,
        sprawl: float,
        affordability: float | None = None,
    ) -> County:
        return County(
            fips=fips,
            state="NC",
            name=f"County {fips}",
            population=10000,
            rucc=7,
            stability=StabilityResult(stability_score=stability),
            remoteness=RemotenessResult("Metro", remoteness_mi),
            sprawl_risk=SprawlRiskResult(composite=sprawl),
            affordability=AffordabilityResult(affordability_score=affordability),
        )

    def test_quietness_score_stored(self):
        counties = [self._make_county("37113", 80.0, 100.0, 30.0, 60.0)]
        ranked = compute_composite_rank(counties)
        assert ranked[0].quietness_score is not None
        assert 0 <= ranked[0].quietness_score <= 100

    def test_overall_score_stored(self):
        counties = [self._make_county("37113", 80.0, 100.0, 30.0, 60.0)]
        ranked = compute_composite_rank(counties)
        # quietness = 80*.4 + (100/120*100)*.3 + 70*.3 = 78.0 (W9: remoteness
        # cap recalibrated to 120mi); 0.55/0.45 blend with affordability 60.
        assert ranked[0].overall_score == round(78.0 * 0.55 + 60.0 * 0.45, 1)

    def test_overall_score_without_affordability(self):
        counties = [self._make_county("37113", 80.0, 100.0, 30.0, None)]
        ranked = compute_composite_rank(counties)
        # Affordability missing -> overall = q*w_q/w_q: equals quietness by
        # renormalization derivation, not a bare fallback on another scale.
        assert ranked[0].quietness_score == 78.0
        assert ranked[0].overall_score == ranked[0].quietness_score


class TestMissingInputsScoreHonestly:
    """Missing composite inputs renormalize; they never masquerade as data."""

    _BASE = {"state": "NC", "name": "X", "population": 10000, "rucc": 7}

    def test_never_computed_sprawl_earns_no_free_points(self):
        """Default SprawlRiskResult is 'no data', not 'perfect anti-sprawl'."""
        c = County(
            fips="1",
            stability=StabilityResult(stability_score=80.0),
            remoteness=RemotenessResult("M", 100.0),
            **self._BASE,
        )
        ranked = compute_composite_rank([c])
        # Renormalized over stability .4 + remoteness .3: (32 + 25) / 0.7
        # (remoteness at 100mi = 100/120*100 = 83.3 after W9 recalibration)
        assert ranked[0].quietness_score == 81.4

    def test_live_zero_sprawl_still_counts(self):
        c = County(
            fips="1",
            stability=StabilityResult(stability_score=80.0),
            remoteness=RemotenessResult("M", 100.0),
            sprawl_risk=SprawlRiskResult(composite=0.0),
            **self._BASE,
        )
        ranked = compute_composite_rank([c])
        # A real 0-risk composite contributes its full anti-sprawl weight.
        assert ranked[0].quietness_score == 87.0

    def test_no_metro_found_means_max_remote_not_adjacent(self):
        """Coords present, no qualifying metro -> maximally remote, not 0 mi."""
        c = County(
            fips="1",
            lat=35.0,
            lon=-83.0,
            stability=StabilityResult(stability_score=80.0),
            remoteness=RemotenessResult(),
            sprawl_risk=SprawlRiskResult(composite=30.0),
            **self._BASE,
        )
        ranked = compute_composite_rank([c])
        assert ranked[0].quietness_score == 83.0  # 32 + 100*.3 + 21

    def test_no_coords_excludes_remoteness(self):
        """No centroid -> remoteness unknown -> renormalize, don't guess."""
        c = County(
            fips="1",
            stability=StabilityResult(stability_score=80.0),
            sprawl_risk=SprawlRiskResult(composite=30.0),
            **self._BASE,
        )
        ranked = compute_composite_rank([c])
        assert ranked[0].quietness_score == 75.7  # (32 + 21) / 0.7

    def test_no_data_county_ranks_last(self):
        bare = County(fips="0", **self._BASE)
        low = County(
            fips="1",
            stability=StabilityResult(stability_score=5.0),
            remoteness=RemotenessResult("M", 0.0),
            sprawl_risk=SprawlRiskResult(composite=95.0),
            **self._BASE,
        )
        ranked = compute_composite_rank([bare, low])
        assert ranked[0].fips == "1"  # scored county beats no-data county
        assert ranked[1].quietness_score is None
        assert ranked[1].overall_score is None

    def test_affordability_only_county_gets_affordability(self):
        c = County(fips="2", **self._BASE)
        c.affordability.affordability_score = 55.0
        ranked = compute_composite_rank([c])
        assert ranked[0].quietness_score is None
        assert ranked[0].overall_score == 55.0


class TestExportIncludesNewScores:
    """Fix 4: counties.json and geo.json include quietness_score and overall_score."""

    def test_counties_json_has_scores(self):
        county = County(
            fips="37113",
            state="NC",
            name="Macon",
            population=37000,
            rucc=7,
            lat=35.18,
            lon=-83.43,
            stability=StabilityResult(stability_score=85.0),
            remoteness=RemotenessResult("Knoxville", 80.5),
            sprawl_risk=SprawlRiskResult(composite=35.0),
            affordability=AffordabilityResult(affordability_score=60.0),
            quietness_score=75.0,
            overall_score=68.0,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_counties([county], min_rucc=6)
            data = json.loads((Path(tmpdir) / "counties.json").read_text())
            assert data[0]["quietness_score"] == 75.0
            assert data[0]["overall_score"] == 68.0

    def test_geo_json_has_overall_score(self):
        county = County(
            fips="37113",
            state="NC",
            name="Macon",
            population=37000,
            rucc=7,
            lat=35.18,
            lon=-83.43,
            stability=StabilityResult(stability_score=85.0),
            remoteness=RemotenessResult("Knoxville", 80.5),
            sprawl_risk=SprawlRiskResult(composite=35.0),
            overall_score=68.0,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_geo([county], min_rucc=6)
            data = json.loads((Path(tmpdir) / "geo.json").read_text())
            assert data[0]["overall_score"] == 68.0

    def test_candidates_json_has_scores(self):
        from src.core.models import Place

        county = County(
            fips="37113",
            state="NC",
            name="Macon",
            population=37000,
            rucc=7,
            lat=35.18,
            lon=-83.43,
            quietness_score=75.0,
            overall_score=68.0,
        )
        candidate = Place(
            fips="3743480",
            name="Franklin",
            state="NC",
            county_fips="37113",
            population=4100,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = HugoExporter(Path(tmpdir))
            exporter.export_candidates([candidate], counties=[county])
            data = json.loads((Path(tmpdir) / "candidates.json").read_text())
            assert data[0]["quietness_score"] == 75.0
            assert data[0]["overall_score"] == 68.0
