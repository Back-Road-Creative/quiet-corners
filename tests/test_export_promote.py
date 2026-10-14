"""The published directory must never see a file that failed validation.

`do_export` used to write straight into the site's data directory and validate
afterwards, so a contract failure exited 1 with the bad files already in place —
which is how a half-broken export reached the site during the 2026-07-30
campaign. Findings D.12 (staging -> promote) and D.50 (atomic writes).
"""

import json
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
from src.main import do_export


def _county(fips: str = "37113", rucc: int = 7) -> County:
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
        sprawl_risk=SprawlRiskResult(composite=35.0),
        quietness_score=78.0,
        overall_score=72.0,
    )


def _candidates() -> list[Place]:
    return [
        Place(
            fips="3743480",
            name="Franklin",
            state="NC",
            county_fips="37113",
            population=4100,
            lat=35.18,
            lon=-83.38,
        )
    ]


class TestFailedExportLeavesThePublishedDirectoryAlone:
    def test_contract_failure_does_not_touch_existing_files(
        self, tmp_path, monkeypatch
    ):
        published = tmp_path / "quiet-corners"
        published.mkdir()
        keep = published / "summary.json"
        keep.write_text('{"pipeline_version": "previous-good-export"}')

        monkeypatch.setattr(
            "src.export.validate_contract.validate_directory",
            lambda _dir: ["counties.json[3].overall_score: not in 0-100: 900"],
        )

        with pytest.raises(SystemExit) as exc:
            do_export(published, [_county()], _candidates(), tmp_path / "raw")

        assert exc.value.code == 1
        # The previous export is still the published one.
        assert (
            json.loads(keep.read_text())["pipeline_version"] == "previous-good-export"
        )
        assert not (published / "counties.json").exists()

    def test_failed_export_leaves_no_staging_directory_behind(
        self, tmp_path, monkeypatch
    ):
        published = tmp_path / "quiet-corners"
        published.mkdir()
        monkeypatch.setattr(
            "src.export.validate_contract.validate_directory",
            lambda _dir: ["boom"],
        )

        with pytest.raises(SystemExit):
            do_export(published, [_county()], _candidates(), tmp_path / "raw")

        assert list(published.parent.iterdir()) == [published]
        assert list(published.iterdir()) == []

    def test_successful_export_promotes_every_file(self, tmp_path):
        published = tmp_path / "quiet-corners"
        do_export(published, [_county()], _candidates(), tmp_path / "raw")

        for name in (
            "summary.json",
            "counties.json",
            "candidates.json",
            "geo.json",
        ):
            assert (published / name).exists(), f"{name} was not promoted"
        assert not any(p.name.startswith(".") for p in published.parent.iterdir())

    def test_geometry_check_still_sees_a_file_only_the_published_dir_has(
        self, tmp_path, monkeypatch
    ):
        """counties-10m.json is hand-copied and never written by the exporter.

        Validating a staging directory that lacks it would silently skip the
        orphan-FIPS check (_check_county_geometry returns early when the file is
        absent), turning a real gate into a no-op.
        """
        published = tmp_path / "quiet-corners"
        published.mkdir()
        (published / "counties-10m.json").write_text(
            json.dumps({"objects": {"counties": {"geometries": []}}})
        )

        seen: dict[str, bool] = {}

        def _spy(data_dir: Path) -> list[str]:
            seen["geometry_visible"] = (data_dir / "counties-10m.json").exists()
            return []

        monkeypatch.setattr("src.export.validate_contract.validate_directory", _spy)
        do_export(published, [_county()], _candidates(), tmp_path / "raw")

        assert seen["geometry_visible"], (
            "the validated directory could not see counties-10m.json"
        )


class TestAtomicWrites:
    def test_a_failed_write_leaves_the_previous_file_intact(
        self, tmp_path, monkeypatch
    ):
        exporter = HugoExporter(tmp_path)
        target = tmp_path / "summary.json"
        target.write_text('{"pipeline_version": "previous-good-export"}')

        def _boom(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("src.export.hugo_export.os.replace", _boom)
        with pytest.raises(OSError):
            exporter._write_json("summary.json", {"pipeline_version": "new"})

        assert (
            json.loads(target.read_text())["pipeline_version"] == "previous-good-export"
        )
        assert not [p for p in tmp_path.iterdir() if p.name.startswith(".summary")]


class TestPlaceShardsPromote:
    GAZ_HEADER = (
        "USPS\tGEOID\tANSICODE\tNAME\tLSAD\tFUNCSTAT\tALAND\tAWATER\t"
        "ALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG\n"
    )

    def _raw(self, tmp_path: Path, rows: str) -> Path:
        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "2024_Gaz_place_national.txt").write_text(self.GAZ_HEADER + rows)
        (raw / "sub-est2024.csv").write_text(
            "SUMLEV,STATE,COUNTY,PLACE,NAME,STNAME,POPESTIMATE2024\n"
            '162,37,000,24640,"Franklin town",North Carolina,4362\n'
        )
        return raw

    def test_promotes_flat_shards_and_drops_a_stale_one(self, tmp_path):
        published = tmp_path / "quiet-corners"
        published.mkdir()
        (published / "places-ZZ.json").write_text("{}")
        raw = self._raw(
            tmp_path,
            "NC\t3724640\t1\tFranklin town\t43\tA\t9\t0\t1.0\t0.0\t35.18\t-83.38\n",
        )

        do_export(published, [_county()], _candidates(), raw)

        assert (published / "places-index.json").exists()
        assert (published / "places-NC.json").exists()
        assert not (published / "places-ZZ.json").exists()
        assert not any(p.is_dir() for p in published.iterdir())
