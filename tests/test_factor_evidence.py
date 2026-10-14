"""WS-QC1: an observed zero, a partly-missing input and a dead source are three
different facts, and a 0.0 score cannot tell them apart. These tests pin the
per-factor evidence status that does, from ingestion through scoring, the
availability gate and the exported contract. Fixtures only — no network.
"""

import json
import math
from pathlib import Path

import pytest

import src.main as main_mod
from src.core.models import (
    FACTOR_OBSERVED,
    FACTOR_PARTIAL,
    FACTOR_STATUSES,
    FACTOR_UNAVAILABLE,
    FACTOR_UNKNOWN,
    SPRAWL_FACTOR_NAMES,
    County,
    SprawlRiskResult,
)
from src.export.contract import (
    EVIDENCE_SCHEMA_VERSION,
    SPRAWL_FACTOR_FIELDS,
    factor_status_of,
)
from src.export.hugo_export import HugoExporter
from src.export.validate_contract import validate_directory
from src.ingest.building_permits import fetch_building_permits
from src.ingest.census_api import CensusAPIKeyError, CensusClient
from src.scoring.sprawl_risk import compute_sprawl_risk
from tests.test_building_permits import _write_year
from tests.test_main_orchestration import _write_fixture_data_dir
from tests.test_run_discipline import _scored_county

# Every input present, every score legitimately zero: 150 mi from an interstate,
# flat permits, outward migration, a slow metro far away, no recreation typing.
ALL_ZERO_OBSERVED = dict(
    interstate_distance_mi=150.0,
    building_permits=[5, 5, 5, 5, 5],
    state_migration_accel=-1.0,
    fast_metro_distance_mi=300.0,
    fast_metro_growth_pct=0.0,
    is_recreation=False,
    is_retirement=False,
)


def _statuses(result: SprawlRiskResult) -> dict[str, str]:
    assert result.factor_status is not None
    return result.factor_status


class TestScoringRecordsEvidence:
    def test_all_zero_observed(self):
        r = compute_sprawl_risk(**ALL_ZERO_OBSERVED)
        assert _statuses(r) == {n: FACTOR_OBSERVED for n in SPRAWL_FACTOR_NAMES}
        assert r.composite == 0.0  # a real zero, not the NaN of "no data"

    def test_partial_missing_permit_year(self):
        r = compute_sprawl_risk(
            **{**ALL_ZERO_OBSERVED, "building_permits": [10, None, 15, 20, 25]}
        )
        st = _statuses(r)
        assert st["permits"] == FACTOR_PARTIAL
        assert all(v == FACTOR_OBSERVED for k, v in st.items() if k != "permits")

    def test_gap_filled_score_is_unchanged(self):
        """Ranking policy is preserved: the gap still scores as zero, only the
        status says so."""
        gappy = compute_sprawl_risk(building_permits=[10, None, 15, 20, 25])
        zero_filled = compute_sprawl_risk(building_permits=[10, 0, 15, 20, 25])
        assert gappy.building_permit_score == zero_filled.building_permit_score
        assert gappy.composite == zero_filled.composite

    def test_total_source_failure(self):
        r = compute_sprawl_risk()
        assert _statuses(r) == {n: FACTOR_UNAVAILABLE for n in SPRAWL_FACTOR_NAMES}
        assert math.isnan(r.composite)

    def test_mixed_unavailable_factors_named_individually(self):
        r = compute_sprawl_risk(interstate_distance_mi=20.0, is_recreation=True)
        st = _statuses(r)
        assert st["interstate"] == FACTOR_OBSERVED
        # recreation needs BOTH flags; one alone is not evidence
        assert st["recreation"] == FACTOR_UNAVAILABLE
        assert st["migration"] == FACTOR_UNAVAILABLE

    def test_short_permit_series_is_unavailable(self):
        assert _statuses(compute_sprawl_risk(building_permits=[3]))["permits"] == (
            FACTOR_UNAVAILABLE
        )

    def test_never_scored_result_has_no_recorded_status(self):
        assert SprawlRiskResult().factor_status is None


class TestPermitReaderKeepsGaps:
    def test_missing_year_is_none_not_zero(self, tmp_path):
        _write_year(tmp_path, 2020, {("01", "001"): 10, ("37", "113"): 0})
        _write_year(tmp_path, 2021, {("01", "001"): 15})
        result = fetch_building_permits(tmp_path, years=[2020, 2021])
        assert result["01001"] == [10, 15]
        # 37113 reported zero in 2020 (observed) and nothing in 2021 (unknown)
        assert result["37113"] == [0, None]


class TestAvailabilityGate:
    def _gate(self, county: County, allow: bool = False) -> None:
        main_mod.check_dimension_coverage([county], allow_degraded=allow)

    def test_all_zero_observed_factors_pass(self):
        county = _scored_county()
        county.sprawl_risk = compute_sprawl_risk(**ALL_ZERO_OBSERVED)
        self._gate(county)  # no SystemExit: zero evidence is not a dead source

    def test_total_source_failure_fails(self):
        county = _scored_county()
        county.sprawl_risk = compute_sprawl_risk(
            **{**ALL_ZERO_OBSERVED, "state_migration_accel": None}
        )
        with pytest.raises(SystemExit) as excinfo:
            self._gate(county)
        assert "sprawl.migration" in str(excinfo.value)

    def test_partial_counts_as_available(self):
        county = _scored_county()
        county.sprawl_risk = compute_sprawl_risk(
            **{**ALL_ZERO_OBSERVED, "building_permits": [1, None, 3]}
        )
        self._gate(county)

    def test_unrecorded_legacy_result_keeps_the_old_reading(self):
        county = _scored_county()  # hand-built, factor_status None
        assert county.sprawl_risk.factor_status is None
        self._gate(county)
        county.sprawl_risk.state_migration_score = 0.0
        with pytest.raises(SystemExit):
            self._gate(county)


def _county(fips: str, sprawl: SprawlRiskResult) -> County:
    return County(
        fips=fips,
        state="NC",
        name=f"C{fips}",
        rucc=7,
        lat=35.0,
        lon=-83.0,
        sprawl_risk=sprawl,
    )


def _export(tmp_path: Path, counties: list[County]) -> tuple[list, dict]:
    HugoExporter(tmp_path).export_all(counties, [], sources={})
    counties_json = json.loads((tmp_path / "counties.json").read_text())
    summary = json.loads((tmp_path / "summary.json").read_text())
    return counties_json, summary


class TestExportedContract:
    @pytest.fixture
    def exported(self, tmp_path):
        counties = [
            _county("00001", compute_sprawl_risk(**ALL_ZERO_OBSERVED)),
            _county(
                "00002",
                compute_sprawl_risk(
                    **{**ALL_ZERO_OBSERVED, "building_permits": [1, None, 3]}
                ),
            ),
            _county("00003", compute_sprawl_risk()),
            _county("00004", SprawlRiskResult(interstate_proximity_score=20.0)),
        ]
        rows, summary = _export(tmp_path, counties)
        return {r["fips"]: r for r in rows}, summary

    def test_three_states_are_distinguishable(self, exported):
        rows, _ = exported
        zero = rows["00001"]["sprawl_factor_status"]
        partial = rows["00002"]["sprawl_factor_status"]
        dead = rows["00003"]["sprawl_factor_status"]
        assert set(zero.values()) == {FACTOR_OBSERVED}
        assert partial["permits"] == FACTOR_PARTIAL
        assert set(dead.values()) == {FACTOR_UNAVAILABLE}
        # ...while the legacy score fields keep their old values: 0.0 for an
        # observed zero, None for a county nothing scored.
        assert rows["00001"]["sprawl_factors"]["migration"] == 0.0
        assert rows["00003"]["sprawl_factors"]["migration"] is None

    def test_unavailable_factor_keeps_its_legacy_zero_score(self, tmp_path):
        """compute_sprawl_risk stores a dead factor as 0.0 next to live ones;
        that field is unchanged, and the status is what says it was missing."""
        county = _county("00001", compute_sprawl_risk(interstate_distance_mi=20.0))
        rows, _ = _export(tmp_path, [county])
        assert rows[0]["sprawl_factors"]["migration"] == 0.0
        assert rows[0]["sprawl_factor_status"]["migration"] == FACTOR_UNAVAILABLE
        assert rows[0]["sprawl_factor_status"]["interstate"] == FACTOR_OBSERVED

    def test_legacy_record_is_unknown_never_inferred(self, exported):
        rows, _ = exported
        legacy = rows["00004"]["sprawl_factor_status"]
        assert set(legacy.values()) == {FACTOR_UNKNOWN}

    def test_existing_fields_unchanged(self, exported):
        rows, summary = exported
        assert set(rows["00001"]["sprawl_factors"]) == SPRAWL_FACTOR_FIELDS
        assert "sprawl_factor_coverage" in summary
        assert "data_sources" in summary

    def test_summary_availability_block(self, exported):
        _, summary = exported
        assert summary["evidence_schema_version"] == EVIDENCE_SCHEMA_VERSION
        avail = summary["sprawl_factor_availability"]
        assert set(avail) == SPRAWL_FACTOR_FIELDS
        assert avail["permits"] == {
            FACTOR_OBSERVED: 1,
            FACTOR_PARTIAL: 1,
            FACTOR_UNAVAILABLE: 1,
            FACTOR_UNKNOWN: 1,
        }
        assert avail["migration"][FACTOR_OBSERVED] == 2

    def test_candidates_carry_the_parent_status(self, tmp_path):
        from src.core.models import Place

        county = _county("37113", compute_sprawl_risk(**ALL_ZERO_OBSERVED))
        place = Place(
            fips="3743480",
            name="Franklin",
            state="NC",
            county_fips="37113",
            population=4100,
            lat=35.1,
            lon=-83.3,
        )
        HugoExporter(tmp_path).export_candidates([place], counties=[county])
        entry = json.loads((tmp_path / "candidates.json").read_text())[0]
        assert set(entry["sprawl_factor_status"].values()) == {FACTOR_OBSERVED}


class TestProducerConsumerAgreement:
    def test_reader_returns_producer_statuses(self, tmp_path):
        county = _county("00001", compute_sprawl_risk(**ALL_ZERO_OBSERVED))
        rows, _ = _export(tmp_path, [county])
        assert factor_status_of(rows[0]) == county.sprawl_risk.factor_status

    def test_reader_tolerates_a_pre_evidence_record(self):
        legacy = {
            "fips": "37113",
            "sprawl_factors": {n: 0.0 for n in SPRAWL_FACTOR_FIELDS},
        }
        assert factor_status_of(legacy) == {
            n: FACTOR_UNKNOWN for n in SPRAWL_FACTOR_FIELDS
        }

    def test_reader_maps_unrecognized_values_to_unknown(self):
        entry = {"sprawl_factor_status": {"interstate": "from-the-future"}}
        got = factor_status_of(entry)
        assert got["interstate"] == FACTOR_UNKNOWN
        assert set(got) == SPRAWL_FACTOR_FIELDS

    def test_statuses_are_a_closed_set(self):
        assert set(FACTOR_STATUSES) == {"observed", "partial", "unavailable", "unknown"}

    def test_validator_accepts_producer_output(self, tmp_path):
        from tests.test_validate_contract import _make_valid_export

        _make_valid_export(tmp_path)
        assert validate_directory(tmp_path) == []
        rows = json.loads((tmp_path / "counties.json").read_text())
        assert "sprawl_factor_status" in rows[0]

    def test_validator_rejects_an_unknown_status_value(self, tmp_path):
        from tests.test_validate_contract import _make_valid_export

        _make_valid_export(tmp_path)
        path = tmp_path / "counties.json"
        rows = json.loads(path.read_text())
        rows[0]["sprawl_factor_status"]["permits"] = "maybe"
        path.write_text(json.dumps(rows))
        errors = validate_directory(tmp_path)
        assert any("sprawl_factor_status" in e for e in errors)

    def test_validator_accepts_a_record_without_the_block(self, tmp_path):
        from tests.test_validate_contract import _make_valid_export

        _make_valid_export(tmp_path)
        path = tmp_path / "counties.json"
        rows = json.loads(path.read_text())
        for r in rows:
            del r["sprawl_factor_status"]
        path.write_text(json.dumps(rows))
        assert validate_directory(tmp_path) == []


def test_do_score_records_which_sources_were_missing(tmp_path, monkeypatch):
    """End to end on the orchestration fixture: coordinates exist, but no
    typology, permits, migration or fast-metro source does."""

    def _raise(self, *args, **kwargs):
        raise CensusAPIKeyError("no key configured")

    monkeypatch.setattr(CensusClient, "fetch", _raise)
    _write_fixture_data_dir(tmp_path)

    counties, _ = main_mod.do_score(tmp_path)

    st = _statuses(counties[0].sprawl_risk)
    assert st["interstate"] == FACTOR_OBSERVED
    for name in ("permits", "migration", "metro_growth", "recreation"):
        assert st[name] == FACTOR_UNAVAILABLE, name
