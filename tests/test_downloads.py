"""Downloader tests: TTL/--force + atomic writes (findings 1-2), zip-slip
guard + post-extraction check (findings 3-4), pruned DOWNLOADS (finding 5),
guarded gazetteer float parse (finding 6)."""

import io
import os
import time
import zipfile
from unittest.mock import MagicMock, patch

import pytest

from src.ingest.data_downloader import (
    DOWNLOADS,
    download_and_unzip,
    download_file,
    load_county_gazetteer,
)


def _mock_response(content: bytes, content_type: str = "text/csv") -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.content = content
    resp.headers = {"content-type": content_type}
    return resp


class TestDownloadFileTTL:
    """download_file should honour a max-age TTL, not 'exists forever'."""

    @patch("src.ingest.data_downloader.requests.get")
    def test_skip_when_fresh(self, mock_get, tmp_path):
        dest = tmp_path / "fresh.csv"
        dest.write_text("old data")
        assert download_file("http://x/f.csv", dest, max_age_days=30) is True
        mock_get.assert_not_called()
        assert dest.read_text() == "old data"

    @pytest.mark.parametrize("force,backdate_days", [(False, 400), (True, 0)])
    @patch("src.ingest.data_downloader.requests.get")
    def test_redownloads_when_stale_or_forced(
        self, mock_get, force, backdate_days, tmp_path
    ):
        dest = tmp_path / "x.csv"
        dest.write_text("old data")
        if backdate_days:
            old = time.time() - backdate_days * 86400
            os.utime(dest, (old, old))
        mock_get.return_value = _mock_response(b"fresh data")
        result = download_file("http://x/f.csv", dest, max_age_days=30, force=force)
        assert result is True
        mock_get.assert_called_once()
        assert dest.read_text() == "fresh data"
        assert [p for p in tmp_path.iterdir() if p != dest] == []  # atomic: no tmp left


class TestDownloadFileValidation:
    """download_file should reject bad responses instead of accepting them."""

    @pytest.mark.parametrize(
        "content,content_type",
        [(b"<html>missing_key</html>", "text/html"), (b"", "text/csv")],
    )
    @patch("src.ingest.data_downloader.requests.get")
    def test_rejects_bad_response(self, mock_get, content, content_type, tmp_path):
        dest = tmp_path / "out.csv"
        mock_get.return_value = _mock_response(content, content_type)
        assert download_file("http://x/f.csv", dest) is False
        assert not dest.exists()


class TestDownloadAndUnzipSkipIfFresh:
    """download_and_unzip should skip re-fetching a still-fresh target."""

    @patch("src.ingest.data_downloader.requests.get")
    def test_skip_when_fresh(self, mock_get, tmp_path):
        dest = tmp_path / "extracted.txt"
        dest.write_text("already here")
        assert download_and_unzip("http://x/f.zip", dest, max_age_days=30) is True
        mock_get.assert_not_called()


class TestZipExtractionGuards:
    """extractall should be guarded against zip-slip and verified afterward."""

    @patch("src.ingest.data_downloader.requests.get")
    def test_valid_zip_extracts_and_verifies(self, mock_get, tmp_path):
        dest = tmp_path / "expected.txt"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("expected.txt", "gazetteer rows")
        mock_get.return_value = _mock_response(buf.getvalue(), "application/zip")
        assert download_and_unzip("http://x/f.zip", dest) is True
        assert dest.read_text() == "gazetteer rows"

    @pytest.mark.parametrize(
        "member_name",
        ["something_else.txt", "../../etc/evil.txt"],
        ids=["wrong-member", "zip-slip"],
    )
    @patch("src.ingest.data_downloader.requests.get")
    def test_bad_member_returns_false(self, mock_get, member_name, tmp_path):
        dest_dir = tmp_path / "datadir"
        dest_dir.mkdir()
        dest = dest_dir / "expected.txt"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(member_name, "not the expected file")
        mock_get.return_value = _mock_response(buf.getvalue(), "application/zip")
        assert download_and_unzip("http://x/f.zip", dest) is False
        assert not (tmp_path.parent / "etc" / "evil.txt").exists()


class TestForcePlumbing:
    """--force should flow CLI -> do_fetch -> download_all -> both helpers."""

    @patch("src.ingest.data_downloader.download_and_unzip")
    @patch("src.ingest.data_downloader.download_file")
    def test_cli_force_reaches_download_helpers(
        self, mock_dl_file, mock_dl_zip, tmp_path, monkeypatch
    ):
        from src.main import main

        mock_dl_file.return_value = True
        mock_dl_zip.return_value = True
        monkeypatch.setattr(
            "sys.argv", ["main.py", "--fetch", "--force", "--data-dir", str(tmp_path)]
        )
        main()
        assert mock_dl_file.call_args.kwargs.get("force") is True
        assert mock_dl_zip.call_args.kwargs.get("force") is True


class TestDownloadsPruned:
    """Unread, re-fetched-every-run CSVs should not be in DOWNLOADS."""

    def test_unread_vintages_removed(self):
        assert "nst-est2025-alldata.csv" not in DOWNLOADS
        assert "nst-est2020-alldata.csv" not in DOWNLOADS
        assert "co-est2020-alldata.csv" not in DOWNLOADS


def test_bps_county_files_in_downloads():
    """Every scored BPS year downloads its keyless bulk file."""
    from src.ingest.building_permits import BPS_YEARS, bps_county_filename

    for year in BPS_YEARS:
        url = DOWNLOADS[bps_county_filename(year)]
        assert url == f"https://www2.census.gov/econ/bps/County/co{year}a.txt"


class TestGazetteerFloatGuard:
    """load_county_gazetteer should skip rows with unparseable coordinates."""

    def test_bad_lat_lon_skipped_not_raised(self, tmp_path):
        gaz_file = tmp_path / "gaz.txt"
        gaz_file.write_text(
            "GEOID\tINTPTLAT\tINTPTLONG\n"
            "37113\tN/A\t-83.430000\n"
            "37021\t35.600000\t-82.550000\n"
        )
        result = load_county_gazetteer(gaz_file)
        assert "37113" not in result
        assert result["37021"] == {"lat": 35.6, "lon": -82.55}


class TestGeometryIntegrity:
    """counties-10m.json is the one download served straight to browsers.

    It comes from a CDN rather than Census, so a floating major tag (`us-atlas@3`)
    means the bytes can change under the pipeline between runs with nothing
    recording it. Pin the release AND verify the digest: a pin alone still trusts
    whatever the CDN answers with.
    """

    def test_download_rejects_a_payload_whose_digest_does_not_match(self, tmp_path):
        from src.ingest.data_downloader import download_file

        dest = tmp_path / "counties-10m.json"
        wrong = _mock_response(
            b'{"type":"Topology","substituted":true}', "application/json"
        )
        with patch("src.ingest.data_downloader.requests.get", return_value=wrong):
            ok = download_file(
                "http://cdn/counties-10m.json",
                dest,
                expected_sha256="0" * 64,
            )

        assert ok is False
        assert not dest.exists(), "a payload that failed verification must not be kept"

    def test_download_accepts_a_payload_whose_digest_matches(self, tmp_path):
        import hashlib

        from src.ingest.data_downloader import download_file

        payload = b'{"type":"Topology"}'
        digest = hashlib.sha256(payload).hexdigest()
        dest = tmp_path / "counties-10m.json"
        with patch(
            "src.ingest.data_downloader.requests.get",
            return_value=_mock_response(payload, "application/json"),
        ):
            ok = download_file(
                "http://cdn/counties-10m.json", dest, expected_sha256=digest
            )

        assert ok is True
        assert dest.read_bytes() == payload

    def test_the_geometry_download_is_pinned_and_registered(self):
        from src.ingest.data_downloader import DOWNLOAD_SHA256, DOWNLOADS

        url = DOWNLOADS["counties-10m.json"]
        assert "us-atlas@3/" not in url, "floating major tag: the bytes can change"
        assert "us-atlas@3.0.1/" in url
        assert len(DOWNLOAD_SHA256["counties-10m.json"]) == 64


class TestIngestRoughEdges:
    """The last unguarded parses and the untested failure path.

    Findings D.25 (a bare int() in the sub-est metro join), D.15 (no test drives
    an HTTP error through download_file) and D.54 (a noqa for a rule that no
    longer fires).
    """

    def test_a_malformed_population_does_not_abort_the_metro_join(self, tmp_path):
        from src.ingest.data_downloader import load_metros_from_gazetteer

        gaz = tmp_path / "gaz.txt"
        gaz.write_text(
            "GEOID\tNAME\tINTPTLAT\tINTPTLONG\n"
            "3712345\tCharlotte city\t35.22\t-80.84\n"
            "3767890\tRaleigh city\t35.77\t-78.63\n",
            encoding="utf-8-sig",
        )
        est = tmp_path / "sub-est2024.csv"
        est.write_text(
            "SUMLEV,STATE,PLACE,POPESTIMATE2024\n"
            "162,37,12345,not-a-number\n"
            "162,37,67890,467000\n",
            encoding="latin-1",
        )

        metros = load_metros_from_gazetteer(gaz, estimates_path=est, min_pop=25_000)

        assert [m.name for m in metros] == ["Raleigh city"], (
            "one unparseable row must not take the rest of the file with it"
        )

    def test_an_http_error_is_reported_not_raised(self, tmp_path):
        import requests

        from src.ingest.data_downloader import download_file

        dest = tmp_path / "co-est2024-alldata.csv"
        with patch(
            "src.ingest.data_downloader.requests.get",
            side_effect=requests.HTTPError("503 Server Error"),
        ):
            assert download_file("http://x/f.csv", dest) is False
        assert not dest.exists()

    def test_no_stale_noqa_for_a_timeout_that_is_passed(self):
        from pathlib import Path as _Path

        source = _Path("src/ingest/census_api.py").read_text()
        for line in source.splitlines():
            if "noqa: S113" in line:
                assert "timeout=" not in line, (
                    f"S113 suppressed on a call that does pass a timeout: {line.strip()}"
                )
