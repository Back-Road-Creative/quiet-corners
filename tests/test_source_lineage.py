"""WS-QC2: the export records what the run consumed, not what sat on disk.

Acceptance: (a) an unused cached file is not in the receipt, (b) changed
consumed bytes change it, (c) API-derived factors carry reproducible lineage
with no key or token, (d) the docs name the schedule that actually runs.
"""

import json
import re
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.export import provenance
from src.export.hugo_export import HugoExporter
from src.export.provenance import (
    REFRESH_SCHEDULE,
    collect_api_lineage,
    collect_input_provenance,
)
from src.ingest import consumption
from src.ingest.census_api import CensusClient
from src.ingest.county_populations import (
    fetch_county_populations,
    load_county_populations_csv,
)
from src.ingest.data_downloader import load_county_gazetteer, load_rucc_codes
from src.ingest.state_migration import fetch_state_migration_accel

SERVICE_DIR = Path(__file__).resolve().parent.parent
SECRET = "k3y-SECRET-0123456789abcdef"  # noqa: S105  # gitleaks:allow  # pragma: allowlist secret
ROWS = [["NAME", "B07001_001E", "state"], ["Alabama", "100", "01"]]


@pytest.fixture
def ledger() -> consumption.ConsumptionLedger:
    led = consumption.ConsumptionLedger()
    with patch.object(consumption, "LEDGER", led):
        yield led


def _response(rows=ROWS) -> MagicMock:
    resp = MagicMock()
    resp.headers = {"content-type": "application/json"}
    resp.url = "https://api.census.gov/data/ok"
    resp.json.return_value = rows
    resp.raise_for_status.return_value = None
    return resp


class TestOnlyConsumedFilesAreReceipted:
    def test_a_cached_file_no_loader_opened_is_excluded(self, tmp_path, ledger):
        # Both files are real downloads sitting in data/raw. Only one is read.
        (tmp_path / "co-est2024-alldata.csv").write_text("SUMLEV,CTYNAME\n")
        (tmp_path / "ruralurbancodes2023.csv").write_text("FIPS,RUCC_2023\n")

        load_rucc_codes(tmp_path / "ruralurbancodes2023.csv")

        assert set(collect_input_provenance(tmp_path, ledger)) == {
            "ruralurbancodes2023.csv"
        }

    def test_the_csv_is_unused_when_the_api_answered(self, tmp_path, ledger):
        # do_score reads the CSV only as the API fallback; a run the API
        # served must not claim the CSV it never opened.
        (tmp_path / "co-est2024-alldata.csv").write_text("SUMLEV,CTYNAME\n")
        client = CensusClient(cache_dir=tmp_path / "cache", api_key=None)
        with patch("src.ingest.census_api.requests.get", return_value=_response()):
            fetch_county_populations(client)

        assert collect_input_provenance(tmp_path, ledger) == {}

    def test_the_fallback_csv_is_receipted_once_it_is_read(self, tmp_path, ledger):
        (tmp_path / "co-est2024-alldata.csv").write_text("SUMLEV,CTYNAME\n")
        load_county_populations_csv(tmp_path / "co-est2024-alldata.csv")
        assert set(collect_input_provenance(tmp_path, ledger)) == {
            "co-est2024-alldata.csv"
        }

    def test_changed_consumed_bytes_change_the_receipt(self, tmp_path, ledger):
        gaz = tmp_path / "2024_Gaz_counties_national.txt"
        gaz.write_text("USPS\tGEOID\tINTPTLAT\tINTPTLONG\nNC\t37113\t35.1\t-83.4\n")
        load_county_gazetteer(gaz)
        before = collect_input_provenance(gaz.parent, ledger)

        gaz.write_text("USPS\tGEOID\tINTPTLAT\tINTPTLONG\nNC\t37113\t35.2\t-83.4\n")
        after = collect_input_provenance(gaz.parent, ledger)

        name = gaz.name
        assert before[name]["sha256"] != after[name]["sha256"]

    def test_file_entries_name_the_pinned_download_url(self, tmp_path, ledger):
        (tmp_path / "ruralurbancodes2023.csv").write_text("FIPS,RUCC_2023\n")
        load_rucc_codes(tmp_path / "ruralurbancodes2023.csv")
        entry = collect_input_provenance(tmp_path, ledger)["ruralurbancodes2023.csv"]
        assert entry["url"].startswith("https://www.ers.usda.gov/")
        assert {"bytes", "modified", "sha256"} <= set(entry)

    def test_a_consumed_file_outside_the_data_dir_is_not_dropped(
        self, tmp_path, ledger
    ):
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "x.csv").write_text("a\n")
        with consumption.consumed_open(elsewhere / "x.csv"):
            pass
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        assert list(collect_input_provenance(data_dir, ledger)) == [
            str(elsewhere / "x.csv")
        ]


class TestApiLineage:
    URL = (
        "https://api.census.gov/data/2023/acs/acs5"
        "?get=NAME,B25077_001E,B19013_001E&for=county:*&in=state:*"
    )

    def _fetch(self, tmp_path, *, api_key=None, cache_key=None):
        client = CensusClient(cache_dir=tmp_path / "cache", api_key=api_key)
        with patch(
            "src.ingest.census_api.requests.get", return_value=_response()
        ) as get:
            client.fetch(self.URL, "ACS5 affordability", cache_key=cache_key)
        return get

    def test_a_live_fetch_records_request_vintage_and_digest(self, tmp_path, ledger):
        self._fetch(tmp_path)
        (entry,) = collect_api_lineage(ledger)

        assert entry["endpoint"] == "https://api.census.gov/data/2023/acs/acs5"
        assert entry["dataset"] == "2023/acs/acs5"
        assert entry["vintage"] == "2023"
        assert entry["query"] == {
            "get": ["NAME", "B25077_001E", "B19013_001E"],
            "for": "county:*",
            "in": "state:*",
        }
        assert entry["origin"] == "live"
        assert entry["rows"] == len(ROWS) - 1
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])

    def test_the_digest_follows_the_response_bytes(self, tmp_path, ledger):
        self._fetch(tmp_path)
        first = collect_api_lineage(ledger)[0]["sha256"]
        ledger.reset()
        client = CensusClient(cache_dir=tmp_path / "cache", api_key=None)
        changed = [ROWS[0], ["Alabama", "101", "01"]]
        with patch(
            "src.ingest.census_api.requests.get", return_value=_response(changed)
        ):
            client.fetch(self.URL, "ACS5 affordability")
        assert collect_api_lineage(ledger)[0]["sha256"] != first

    def test_a_cache_hit_is_marked_cache_with_the_same_digest(self, tmp_path, ledger):
        self._fetch(tmp_path, cache_key="acs5_test")
        live = collect_api_lineage(ledger)[0]
        ledger.reset()

        get = self._fetch(tmp_path, cache_key="acs5_test")
        cached = collect_api_lineage(ledger)[0]

        get.assert_not_called()
        assert cached["origin"] == "cache"
        assert cached["sha256"] == live["sha256"]
        assert cached["retrieved_at"].endswith("+00:00")

    def test_the_key_never_reaches_the_receipt(self, tmp_path, ledger):
        get = self._fetch(tmp_path, api_key=SECRET)
        # Sanity: the key did go on the wire, so the assertion below is about
        # the receipt, not about a request that never carried one.
        assert SECRET in get.call_args.args[0]

        blob = json.dumps(collect_api_lineage(ledger))
        assert SECRET not in blob
        assert "key=" not in blob.lower()
        assert '"key"' not in blob

    def test_a_key_smuggled_into_the_url_is_dropped_too(self, tmp_path, ledger):
        client = CensusClient(cache_dir=tmp_path / "cache", api_key=SECRET)
        url = self.URL + f"&key={SECRET}&note={SECRET}"
        with patch("src.ingest.census_api.requests.get", return_value=_response()):
            client.fetch(url, f"label {SECRET}")
        blob = json.dumps(collect_api_lineage(ledger))
        assert SECRET not in blob

    def test_a_failed_fetch_records_nothing(self, tmp_path, ledger):
        client = CensusClient(cache_dir=tmp_path / "cache", api_key=None, max_retries=1)
        with patch("src.ingest.census_api.requests.get", side_effect=Exception("down")):
            with pytest.raises(Exception):
                client.fetch(self.URL, "x")
        assert collect_api_lineage(ledger) == []

    def test_state_migration_records_both_vintages(self, tmp_path, ledger):
        client = CensusClient(cache_dir=tmp_path / "cache", api_key=None)
        with patch("src.ingest.census_api.requests.get", return_value=_response()):
            fetch_state_migration_accel(client, 2023, 2022)
        assert sorted(e["vintage"] for e in collect_api_lineage(ledger)) == [
            "2022",
            "2023",
        ]


class TestSummaryCarriesLineageAndFreshness:
    def _summary(self, **kw) -> dict:
        with tempfile.TemporaryDirectory() as tmpdir:
            HugoExporter(Path(tmpdir)).export_summary([], [], **kw)
            return json.loads((Path(tmpdir) / "summary.json").read_text())

    def test_api_lineage_key_is_always_present(self):
        assert self._summary()["api_lineage"] == []

    def test_lineage_is_written_as_given(self):
        lineage = [{"dataset": "2023/acs/acs5", "vintage": "2023"}]
        assert self._summary(api_lineage=lineage)["api_lineage"] == lineage

    def test_three_freshness_notions_are_separate_fields(self):
        lineage = [{"dataset": "2023/acs/acs5", "vintage": "2023"}]
        fresh = self._summary(api_lineage=lineage)["freshness"]

        assert fresh["source_vintage"] == {"2023/acs/acs5": "2023"}
        assert "run_time" in fresh
        assert fresh["refresh_schedule"] == REFRESH_SCHEDULE

    def test_run_time_is_generated_at(self):
        s = self._summary()
        assert s["freshness"]["run_time"] == s["generated_at"]

    def test_existing_keys_are_unchanged(self):
        s = self._summary()
        assert {"sources", "data_sources", "generated_at", "pipeline_version"} <= set(s)


class TestScheduleProse:
    """The declared schedule is data in code; the docs must agree with it."""

    def test_the_declared_cron_is_the_one_documented_in_the_readme(self):
        readme = (SERVICE_DIR / "README.md").read_text()
        assert REFRESH_SCHEDULE["cron"] == "0 6 1 * *"
        assert REFRESH_SCHEDULE["cron"] in readme
        assert REFRESH_SCHEDULE["command"] in readme
        assert REFRESH_SCHEDULE["cadence"] == "monthly"

    def test_the_schedule_names_where_it_is_documented(self):
        assert (SERVICE_DIR / REFRESH_SCHEDULE["documented_in"]).is_file()

    def test_readme_names_the_monthly_cron_not_a_quarterly_cadence(self):
        readme = (SERVICE_DIR / "README.md").read_text()
        assert "quarterly cadence" not in readme
        assert REFRESH_SCHEDULE["cron"] in readme
        assert "monthly" in readme.lower()

    def test_no_prose_calls_the_refresh_quarterly(self):
        text = (SERVICE_DIR / "README.md").read_text().lower()
        assert "quarterly regeneration" not in text
        assert "routine quarterly" not in text
        src = (SERVICE_DIR / "src/ingest/data_downloader.py").read_text().lower()
        assert "routine quarterly" not in src

    def test_readme_names_the_headlessmode_dashboard_and_its_data_dir(self):
        """Quiet Corners is a Headlessmode feature: the docs name its one destination."""
        readme = (SERVICE_DIR / "README.md").read_text()
        assert "https://headlessmode.com/quiet-corners/" in readme
        assert "static/data/quiet-corners/" in readme
        assert "scripts/test_data_contract.py" in readme

    def test_no_doc_points_at_a_file_this_repository_lacks(self):
        for rel in (
            "README.md",
            "src/export/provenance.py",
            "src/ingest/data_downloader.py",
        ):
            assert "CLAUDE.md" not in (SERVICE_DIR / rel).read_text(), rel

    def test_provenance_module_exports_the_schedule(self):
        assert provenance.REFRESH_SCHEDULE is REFRESH_SCHEDULE
