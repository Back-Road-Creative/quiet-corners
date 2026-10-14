"""Fail-loud run discipline (W10): abort instead of exporting on missing
inputs, 0%-coverage dimensions, or silently substituted sources."""

import importlib.metadata
import inspect
import sys

import pytest

import src.main as main_mod
from src.core.config import DEFAULT_CONFIG
from src.core.models import County, SprawlRiskResult
from src.export.hugo_export import PIPELINE_VERSION, HugoExporter


def _scored_county() -> County:
    county = County(fips="37113", state="NC", name="Macon County", rucc=7)
    county.stability.stability_score = 50.0
    county.remoteness.distance_to_metro_mi = 60.0
    county.affordability.affordability_score = 70.0
    # Every sprawl factor live too, so the fixture is what its name says: a
    # county nothing failed to score. Leaving them at the SprawlRiskResult()
    # default would mean five dead sources, which is what the gate below fires
    # on — the fixture would be testing the gate rather than passing it.
    county.sprawl_risk = SprawlRiskResult(
        interstate_proximity_score=20.0,
        building_permit_score=30.0,
        state_migration_score=40.0,
        fast_metro_score=50.0,
        recreation_retirement_score=60.0,
        composite=38.0,
    )
    return county


def _unclassified(n: int) -> list[County]:
    return [County(fips=f"{i:05d}", state="NC", name=f"C{i}") for i in range(n)]


def test_do_fetch_returns_download_results(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        main_mod, "download_all", lambda d, force=False: {"a.csv": True}
    )
    assert main_mod.do_fetch(tmp_path) == {"a.csv": True}


def test_check_downloads_exits_on_failure() -> None:
    main_mod.check_downloads({"a.csv": True})  # all OK passes
    with pytest.raises(SystemExit) as excinfo:
        main_mod.check_downloads({"a.csv": True, "b.csv": False})
    assert excinfo.value.code != 0
    assert "b.csv" in str(excinfo.value)


def test_all_aborts_when_a_download_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CENSUS_API_KEY", "k")
    monkeypatch.setattr(
        main_mod, "download_all", lambda d, force=False: {"x.csv": False}
    )
    monkeypatch.setattr(sys, "argv", ["qc", "--all", str(tmp_path / "out")])
    with pytest.raises(SystemExit) as excinfo:
        main_mod.main()
    assert excinfo.value.code != 0


def test_zero_coverage_dimension_exits() -> None:
    main_mod.check_dimension_coverage([_scored_county()], allow_degraded=False)
    county = _scored_county()
    county.affordability.affordability_score = None
    with pytest.raises(SystemExit) as excinfo:
        main_mod.check_dimension_coverage([county], allow_degraded=False)
    assert excinfo.value.code != 0
    assert "affordability" in str(excinfo.value)


def test_zero_coverage_warns_when_degraded_allowed(capsys) -> None:
    county = _scored_county()
    county.affordability.affordability_score = None
    main_mod.check_dimension_coverage([county], allow_degraded=True)
    captured = capsys.readouterr()
    assert "affordability" in captured.out + captured.err


def test_a_dead_sprawl_factor_exits() -> None:
    """A sprawl factor that scored nothing is a dead source, and the export
    hides it: compute_sprawl_risk renormalizes the composite over the factors
    that survived, so a run missing one still ships a complete-looking score.

    State migration is the sharpest case — of the three _try_keyed callers it
    is the only one with no other backstop. County populations fall back to a
    local CSV, and affordability is one of the dimensions checked above; state
    migration had nothing between a dead ACS endpoint and a published export.
    """
    county = _scored_county()
    county.sprawl_risk.state_migration_score = 0.0
    with pytest.raises(SystemExit) as excinfo:
        main_mod.check_dimension_coverage([county], allow_degraded=False)
    assert excinfo.value.code != 0
    assert "sprawl.migration" in str(excinfo.value)


def test_a_dead_sprawl_factor_warns_when_degraded_allowed(capsys) -> None:
    # --allow-degraded is exactly the run where state migration is expected to
    # be dead: it is one of the two KEYED_SOURCES.
    county = _scored_county()
    county.sprawl_risk.state_migration_score = 0.0
    main_mod.check_dimension_coverage([county], allow_degraded=True)
    assert "sprawl.migration" in "".join(capsys.readouterr())


def test_a_factor_none_from_no_live_scoring_is_dead_too() -> None:
    # The bare SprawlRiskResult() case: scores None, composite NaN. `!= 0`
    # read those as live; the gate must not inherit that bug.
    county = _scored_county()
    county.sprawl_risk = SprawlRiskResult()
    with pytest.raises(SystemExit) as excinfo:
        main_mod.check_dimension_coverage([county], allow_degraded=False)
    message = str(excinfo.value)
    for factor in ("interstate", "permits", "migration", "metro_growth", "recreation"):
        assert f"sprawl.{factor}" in message


def test_missing_rucc_requires_optin() -> None:
    """Any gap requires opting in, not just a majority one.

    This test used to assert that a minority gap passed ("minority ok"). That
    was the bug finding A.23 named: an unclassified county is filtered out of
    counties.json, geo.json and the candidate join, so a 30% gap silently
    published 30% fewer counties. See TestRuccPartialCoverage below.
    """
    counties = _unclassified(4)
    with pytest.raises(SystemExit) as excinfo:
        main_mod.require_rucc_coverage(counties, estimate_rucc=False)
    assert "--estimate-rucc" in str(excinfo.value)
    main_mod.require_rucc_coverage(counties, estimate_rucc=True)  # opt-in passes


def test_metros_required(sample_metros) -> None:
    assert main_mod.require_metros(sample_metros) is sample_metros
    with pytest.raises(SystemExit) as excinfo:
        main_mod.require_metros([])
    assert excinfo.value.code != 0


class _ScriptedClient:
    """CensusClient stand-in: each fetch() pops the next scripted outcome.

    An outcome is either an exception to raise or a payload to return —
    the two things the real client actually does. It never returns None.
    """

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.labels: list[str] = []

    def fetch(self, url, label="", cache_key=None):
        self.labels.append(label)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_version_flag_prints_pipeline_version(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["qc", "--version"])
    with pytest.raises(SystemExit) as excinfo:
        main_mod.main()
    assert excinfo.value.code == 0
    assert PIPELINE_VERSION in capsys.readouterr().out


def test_package_version_matches_pipeline_version() -> None:
    assert importlib.metadata.version("svc-quiet-corners") == PIPELINE_VERSION


def test_exporter_min_rucc_defaults_follow_config() -> None:
    for fn in (
        HugoExporter.export_counties,
        HugoExporter.export_geo,
        HugoExporter.export_all,
    ):
        default = inspect.signature(fn).parameters["min_rucc"].default
        assert default == DEFAULT_CONFIG.min_rucc


def test_acs_year_flag_reaches_do_score(monkeypatch) -> None:
    seen: dict = {}

    def fake_do_score(data_dir, **kwargs):
        seen.update(kwargs)
        return [_scored_county()], []

    monkeypatch.setenv("CENSUS_API_KEY", "k")
    monkeypatch.setattr(main_mod, "do_score", fake_do_score)
    monkeypatch.setattr(sys, "argv", ["qc", "--score", "--acs-year", "2026"])
    main_mod.main()
    assert seen["acs_year"] == 2026
    assert DEFAULT_CONFIG.acs_year == 2023


class TestRuccPartialCoverage:
    """A county with no RUCC code is dropped from every exported file.

    The guard only fired above 50% missing, so a partial gap — a renamed FIPS,
    a CSV that lost a few rows — silently shrank the published set with nothing
    said. Finding A.23.
    """

    def _counties(self, missing: int, total: int = 10) -> list[County]:
        return [
            County(
                fips=f"37{i:03d}",
                state="NC",
                name=f"County {i}",
                population=10_000,
                rucc=None if i < missing else 7,
            )
            for i in range(total)
        ]

    def test_a_partial_rucc_gap_stops_the_run(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main_mod.require_rucc_coverage(
                self._counties(missing=3), estimate_rucc=False
            )

        assert exc.value.code != 0
        message = str(exc.value)
        assert "3/10" in message, message
        assert "--estimate-rucc" in message

    def test_full_coverage_proceeds(self):
        main_mod.require_rucc_coverage(self._counties(missing=0), estimate_rucc=False)
