"""Tests for the export contract validator."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from src.export.contract import PLACE_SHARD_MAX_BYTES
from src.export.hugo_export import HugoExporter
from src.export.navigation_places import RankedPlace
from src.export.validate_contract import (
    check_place_shard_completeness,
    validate_directory,
)
from tests.test_export_contract import _make_scored_county


def _make_valid_export(tmpdir: Path) -> None:
    """Create a minimal valid export directory using HugoExporter."""
    from src.core.models import Place
    from src.scoring.composite import compute_composite_rank

    county = _make_scored_county()
    counties = compute_composite_rank([county])
    candidate = Place(
        fips="3743480",
        name="Franklin",
        state="NC",
        county_fips="37113",
        population=4100,
        lat=35.18,
        lon=-83.38,
    )
    exporter = HugoExporter(tmpdir)
    exporter.export_all(counties, [candidate])
    exporter.export_place_shards(
        [RankedPlace("NC", "3724640", "Franklin town", 35.18, -83.38, 4362, 9)]
    )


def _topojson(*fips: str) -> dict:
    """A well-formed us-atlas-shaped TopoJSON carrying the given county ids."""
    return {
        "type": "Topology",
        "arcs": [],
        "objects": {
            "counties": {
                "type": "GeometryCollection",
                "geometries": [{"type": "Polygon", "id": f, "arcs": []} for f in fips],
            }
        },
    }


class TestValidateContract:
    def test_missing_file_returns_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            errors = validate_directory(Path(tmpdir))
        assert any("file not found" in e for e in errors)

    def test_valid_export_no_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_valid_export(Path(tmpdir))
            errors = validate_directory(Path(tmpdir))
        assert errors == [], f"Unexpected errors: {errors}"

    def test_missing_field_caught(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_valid_export(Path(tmpdir))
            path = Path(tmpdir) / "counties.json"
            data = json.loads(path.read_text())
            del data[0]["overall_score"]
            path.write_text(json.dumps(data))

            errors = validate_directory(Path(tmpdir))
        assert any("overall_score" in e for e in errors)

    def test_sub_field_check(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_valid_export(Path(tmpdir))
            path = Path(tmpdir) / "counties.json"
            data = json.loads(path.read_text())
            del data[0]["sprawl_factors"]["interstate"]
            path.write_text(json.dumps(data))

            errors = validate_directory(Path(tmpdir))
        assert any("sprawl_factors" in e and "interstate" in e for e in errors)

    def test_full_export_roundtrip(self):
        """HugoExporter output passes validation — the contract is self-consistent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_valid_export(Path(tmpdir))
            errors = validate_directory(Path(tmpdir))
        assert errors == []

    def test_validator_importable_without_tests_package(self):
        """Production installs must not need tests/ to run the validator."""
        code = "import sys; sys.modules['tests'] = None; import src.export.validate_contract"
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            cwd=Path(__file__).resolve().parents[1],
        )
        assert proc.returncode == 0, proc.stderr.decode()

    def test_null_score_in_later_entry_caught(self):
        """Every entry is validated, and core scores must be non-null."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_valid_export(Path(tmpdir))
            path = Path(tmpdir) / "counties.json"
            data = json.loads(path.read_text())
            data.append(dict(data[0], overall_score=None))
            path.write_text(json.dumps(data))
            errors = validate_directory(Path(tmpdir))
        assert any("counties.json[1]" in e and "overall_score" in e for e in errors)

    def test_out_of_range_score_caught(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_valid_export(Path(tmpdir))
            path = Path(tmpdir) / "counties.json"
            data = json.loads(path.read_text())
            data[0]["stability_score"] = 250
            path.write_text(json.dumps(data))
            errors = validate_directory(Path(tmpdir))
        assert any("stability_score" in e for e in errors)

    def test_counties_10m_geometry_cross_check(self):
        """counties.json FIPS without geometry in counties-10m.json must fail."""
        for topo_id, expect_error in [("01001", True), ("37113", False)]:
            topo = _topojson(topo_id)
            with tempfile.TemporaryDirectory() as tmpdir:
                _make_valid_export(Path(tmpdir))
                (Path(tmpdir) / "counties-10m.json").write_text(json.dumps(topo))
                errors = validate_directory(Path(tmpdir))
            assert any("counties-10m" in e for e in errors) is expect_error

    def test_post_2019_alaska_splits_are_exempt_but_nothing_else_is(self):
        """02063/02066 have no upstream polygon; other gaps still fail."""
        topo = _topojson("37113")
        for extra_fips, expect_error in [
            ("02063", False),
            ("02066", False),
            ("06001", True),
        ]:
            with tempfile.TemporaryDirectory() as tmpdir:
                _make_valid_export(Path(tmpdir))
                (Path(tmpdir) / "counties-10m.json").write_text(json.dumps(topo))
                path = Path(tmpdir) / "counties.json"
                data = json.loads(path.read_text())
                data.append({**data[0], "fips": extra_fips})
                path.write_text(json.dumps(data))
                errors = validate_directory(Path(tmpdir))
            assert any("counties-10m" in e for e in errors) is expect_error


class TestGeometryFileShape:
    """counties-10m.json is checked for its own shape, not just its contents.

    It sits in neither ARRAY_FILE_CONTRACTS nor OBJECT_FILE_CONTRACTS, so the
    only thing that ever read it was the FIPS cross-check — a truncated or
    restructured payload surfaced as one blanket "invalid TopoJSON structure".
    Finding B.10.
    """

    def _run(self, tmp_path, payload=None):
        _make_valid_export(tmp_path)
        if payload is not None:
            (tmp_path / "counties-10m.json").write_text(json.dumps(payload))
        return validate_directory(tmp_path)

    def test_a_well_formed_topojson_passes(self, tmp_path):
        assert self._run(tmp_path, _topojson("37113")) == []

    def test_a_payload_that_is_not_topojson_fails(self, tmp_path):
        payload = _topojson("37113")
        del payload["type"]
        errors = self._run(tmp_path, payload)
        assert any("Topology" in e for e in errors), errors

    def test_missing_objects_counties_fails_with_a_specific_message(self, tmp_path):
        payload = _topojson("37113")
        payload["objects"] = {"states": {"geometries": []}}
        errors = self._run(tmp_path, payload)
        assert any("objects.counties.geometries" in e for e in errors), errors

    def test_empty_geometry_list_fails(self, tmp_path):
        payload = _topojson()
        errors = self._run(tmp_path, payload)
        assert any("empty" in e for e in errors), errors

    def test_geometry_ids_that_are_not_five_digit_fips_fail(self, tmp_path):
        payload = _topojson("37113")
        payload["objects"]["counties"]["geometries"].append(
            {"type": "Polygon", "id": 1001, "arcs": []}
        )
        errors = self._run(tmp_path, payload)
        assert any("FIPS" in e for e in errors), errors

    def test_absence_is_not_an_error_but_is_reported(self, tmp_path, capsys):
        errors = self._run(tmp_path)
        assert errors == []
        assert "not present" in capsys.readouterr().out

    def test_a_checked_geometry_file_says_how_many_it_checked(self, tmp_path, capsys):
        assert self._run(tmp_path, _topojson("37113")) == []
        assert "1 county geometries" in capsys.readouterr().out


class TestCrossFileAgreement:
    """counties.json and geo.json describe the same set of counties.

    The validator checked each file's own shape and nothing between them, so an
    export where geo.json lost rows — every one of them a county the map cannot
    place — passed. Finding B.25.
    """

    def _write(self, tmpdir, counties, geo):
        import json

        d = Path(tmpdir)
        (d / "counties.json").write_text(json.dumps(counties))
        (d / "geo.json").write_text(json.dumps(geo))

    def test_a_county_missing_from_geo_is_an_error(self, tmp_path):
        from src.export.validate_contract import check_cross_file_agreement

        self._write(
            tmp_path,
            [{"fips": "37113"}, {"fips": "37173"}],
            [{"fips": "37113", "lat": 35.1, "lon": -83.4}],
        )
        errors: list[str] = []
        check_cross_file_agreement(tmp_path, errors)

        assert any("37173" in e for e in errors), errors

    def test_a_geo_row_with_no_county_is_an_error(self, tmp_path):
        from src.export.validate_contract import check_cross_file_agreement

        self._write(
            tmp_path,
            [{"fips": "37113"}],
            [
                {"fips": "37113", "lat": 35.1, "lon": -83.4},
                {"fips": "99999", "lat": 35.1, "lon": -83.4},
            ],
        )
        errors: list[str] = []
        check_cross_file_agreement(tmp_path, errors)

        assert any("99999" in e for e in errors), errors

    def test_coordinates_outside_the_us_envelope_are_an_error(self, tmp_path):
        from src.export.validate_contract import check_cross_file_agreement

        self._write(
            tmp_path,
            [{"fips": "37113"}],
            [{"fips": "37113", "lat": 0.0, "lon": 0.0}],
        )
        errors: list[str] = []
        check_cross_file_agreement(tmp_path, errors)

        assert any("37113" in e and "coordinates" in e for e in errors), errors

    def test_the_aleutians_cross_the_antimeridian_and_are_not_an_error(self, tmp_path):
        """FIPS 02016 sits at +179.6 — a real county with a positive longitude.

        A single [-180, -64] band rejected the live export, so the check carries
        two longitude bands.
        """
        from src.export.validate_contract import check_cross_file_agreement

        self._write(
            tmp_path,
            [{"fips": "02016"}],
            [{"fips": "02016", "lat": 51.948964, "lon": 179.621188}],
        )
        errors: list[str] = []
        check_cross_file_agreement(tmp_path, errors)

        assert errors == []

    def test_matching_files_produce_no_error(self, tmp_path):
        from src.export.validate_contract import check_cross_file_agreement

        self._write(
            tmp_path,
            [{"fips": "37113"}, {"fips": "02063"}],
            [
                {"fips": "37113", "lat": 35.1, "lon": -83.4},
                {"fips": "02063", "lat": 60.9, "lon": -147.3},
            ],
        )
        errors: list[str] = []
        check_cross_file_agreement(tmp_path, errors)

        assert errors == []


class TestPlaceShardContract:
    def test_a_valid_export_includes_shards_and_passes(self, tmp_path):
        _make_valid_export(tmp_path)

        assert (tmp_path / "places-NC.json").exists()
        assert validate_directory(tmp_path) == []

    def test_missing_index_fails(self, tmp_path):
        _make_valid_export(tmp_path)
        (tmp_path / "places-index.json").unlink()

        assert any(
            "places-index.json" in e and "not found" in e
            for e in validate_directory(tmp_path)
        )

    def test_missing_national_head_fails(self, tmp_path):
        _make_valid_export(tmp_path)
        (tmp_path / "places-national.json").unlink()

        assert any(
            "places-national.json" in e and "not found" in e
            for e in validate_directory(tmp_path)
        )

    def test_national_head_row_missing_from_its_state_shard_fails(self, tmp_path):
        # The head is a slice of the shards, never a third list of places.
        _make_valid_export(tmp_path)
        path = tmp_path / "places-national.json"
        head = json.loads(path.read_text())
        head["places"].append(["3799999", "Nowhere", 35.0, -83.0, 9000, "NC"])
        path.write_text(json.dumps(head))

        assert any(
            "places-national.json" in e and "3799999" in e
            for e in validate_directory(tmp_path)
        )

    def test_oversized_national_head_fails(self, tmp_path):
        _make_valid_export(tmp_path)
        path = tmp_path / "places-national.json"
        head = json.loads(path.read_text())
        head["padding"] = "x" * (PLACE_SHARD_MAX_BYTES + 1)
        path.write_text(json.dumps(head))

        assert any(
            "places-national.json" in e and "exceeds" in e
            for e in validate_directory(tmp_path)
        )

    def test_national_head_with_wrong_fields_fails(self, tmp_path):
        _make_valid_export(tmp_path)
        path = tmp_path / "places-national.json"
        head = json.loads(path.read_text())
        head["fields"] = head["fields"][:-1]
        path.write_text(json.dumps(head))

        assert any("fields" in e for e in validate_directory(tmp_path))

    def test_national_head_places_not_a_list_fails(self, tmp_path):
        _make_valid_export(tmp_path)
        path = tmp_path / "places-national.json"
        head = json.loads(path.read_text())
        head["places"] = {}
        path.write_text(json.dumps(head))

        assert any("places must be a list" in e for e in validate_directory(tmp_path))

    def test_national_head_malformed_record_fails(self, tmp_path):
        _make_valid_export(tmp_path)
        path = tmp_path / "places-national.json"
        head = json.loads(path.read_text())
        head["places"].append(["3799997", "Nameless", 35.0, -83.0, None, "NC"])
        path.write_text(json.dumps(head))

        assert any("malformed head record" in e for e in validate_directory(tmp_path))

    def test_national_head_longer_than_the_head_fails(self, tmp_path, monkeypatch):
        import src.export.validate_contract as vc

        _make_valid_export(tmp_path)
        monkeypatch.setattr(vc, "PLACE_NATIONAL_HEAD", 0)

        assert any("rows exceeds 0" in e for e in validate_directory(tmp_path))

    def test_national_head_out_of_rank_order_fails(self, tmp_path):
        _make_valid_export(tmp_path)
        shard_path = tmp_path / "places-NC.json"
        shard = json.loads(shard_path.read_text())
        shard["places"].append(["3799998", "Bigger", 35.1, -83.1, 99000])
        shard_path.write_text(json.dumps(shard))
        index_path = tmp_path / "places-index.json"
        index = json.loads(index_path.read_text())
        index["NC"]["count"] = 2
        index_path.write_text(json.dumps(index))
        path = tmp_path / "places-national.json"
        head = json.loads(path.read_text())
        head["places"].append(["3799998", "Bigger", 35.1, -83.1, 99000, "NC"])
        path.write_text(json.dumps(head))

        assert any(
            "places-national.json" in e and "rank" in e
            for e in validate_directory(tmp_path)
        )

    def test_missing_shard_listed_in_the_index_fails(self, tmp_path):
        _make_valid_export(tmp_path)
        (tmp_path / "places-NC.json").unlink()

        assert any("places-NC.json" in e for e in validate_directory(tmp_path))

    def test_malformed_shard_record_fails(self, tmp_path):
        _make_valid_export(tmp_path)
        path = tmp_path / "places-NC.json"
        shard = json.loads(path.read_text())
        shard["places"][0] = ["3724640", "Franklin"]
        path.write_text(json.dumps(shard))

        assert any("malformed place record" in e for e in validate_directory(tmp_path))

    def test_shard_count_disagreeing_with_the_index_fails(self, tmp_path):
        _make_valid_export(tmp_path)
        path = tmp_path / "places-NC.json"
        shard = json.loads(path.read_text())
        shard["places"] = []
        path.write_text(json.dumps(shard))

        assert any(
            "places-index.json says 1" in e for e in validate_directory(tmp_path)
        )

    def test_oversized_shard_fails(self, tmp_path):
        _make_valid_export(tmp_path)
        path = tmp_path / "places-NC.json"
        shard = json.loads(path.read_text())
        shard["places"][0][1] = "x" * 200_000
        path.write_text(json.dumps(shard))

        assert any("exceeds" in e for e in validate_directory(tmp_path))

    def test_shard_total_must_equal_gazetteer_rows_with_coordinates(self, tmp_path):
        _make_valid_export(tmp_path)
        gaz = tmp_path / "gaz.txt"
        header = (
            "USPS\tGEOID\tANSICODE\tNAME\tLSAD\tFUNCSTAT\tALAND\tAWATER\t"
            "ALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG\n"
        )
        row = "NC\t{g}\t1\t{g} town\t43\tA\t1\t0\t1.0\t0.0\t35.1\t-83.1\n"
        gaz.write_text(header + row.format(g="3724640") + row.format(g="3799999"))

        errors: list[str] = []
        check_place_shard_completeness(tmp_path, gaz, errors)

        assert errors and "gazetteer has 2" in errors[0]

        gaz.write_text(header + row.format(g="3724640"))
        errors = []
        check_place_shard_completeness(tmp_path, gaz, errors)
        assert errors == []
