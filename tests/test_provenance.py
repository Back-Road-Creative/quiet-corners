"""summary.json must record which input bytes the run actually read.

Findings B.18 and D.30: the export pinned only `generated_at` and
`pipeline_version`, so nothing distinguished an export built from today's
downloads from one built from months-old cached copies that the TTL let stand.
"""

import hashlib
import json
import tempfile
from pathlib import Path

from src.core.models import County, RemotenessResult, SprawlRiskResult, StabilityResult
from src.export.contract import SUMMARY_REQUIRED_FIELDS
from src.export.hugo_export import HugoExporter
from src.export.provenance import INPUT_FILENAMES, collect_input_provenance
from src.ingest.consumption import ConsumptionLedger, consumed_open


def _county() -> County:
    return County(
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
        quietness_score=78.0,
        overall_score=72.0,
    )


class TestInputProvenance:
    def test_records_size_mtime_and_digest_of_each_input_present(self, tmp_path):
        name = INPUT_FILENAMES[0]
        payload = b"col_a,col_b\n1,2\n"
        (tmp_path / name).write_bytes(payload)
        # A file counts only once something read it (WS-QC2).
        ledger = ConsumptionLedger()
        with consumed_open(tmp_path / name, "rb", ledger=ledger):
            pass

        sources = collect_input_provenance(tmp_path, ledger)

        assert set(sources) == {name}
        entry = sources[name]
        assert entry["bytes"] == len(payload)
        assert entry["sha256"] == hashlib.sha256(payload).hexdigest()
        assert entry["modified"].endswith("+00:00"), "mtime must be recorded in UTC"

    def test_a_file_the_run_never_downloaded_is_absent_not_null(self, tmp_path):
        assert collect_input_provenance(tmp_path, ConsumptionLedger()) == {}

    def test_a_file_on_disk_that_nothing_read_is_absent(self, tmp_path):
        (tmp_path / INPUT_FILENAMES[0]).write_bytes(b"x")
        assert collect_input_provenance(tmp_path, ConsumptionLedger()) == {}

    def test_every_known_download_is_a_candidate_input(self):
        # Derived from the downloader's own tables, so adding a source cannot
        # silently drop out of provenance.
        assert "co-est2024-alldata.csv" in INPUT_FILENAMES
        assert "sub-est2024.csv" in INPUT_FILENAMES
        assert "2024_Gaz_counties_national.txt" in INPUT_FILENAMES


class TestSummaryCarriesProvenance:
    def test_summary_writes_the_sources_block(self):
        sources = {
            "co-est2024-alldata.csv": {"bytes": 12, "modified": "x", "sha256": "y"}
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            HugoExporter(Path(tmpdir)).export_summary([_county()], [], sources=sources)
            summary = json.loads((Path(tmpdir) / "summary.json").read_text())

        assert summary["sources"] == sources

    def test_summary_always_has_the_key_even_with_nothing_to_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            HugoExporter(Path(tmpdir)).export_summary([_county()], [])
            summary = json.loads((Path(tmpdir) / "summary.json").read_text())

        assert summary["sources"] == {}

    def test_the_contract_requires_sources(self):
        assert "sources" in SUMMARY_REQUIRED_FIELDS


class TestScoringConstantsArePublished:
    """The dashboard should not have to hardcode a pipeline constant.

    The Remote column bands distance by normalizing against the same cap the
    quietness term uses (RemotenessThresholds.score_cap_mi). The site currently
    carries its own copy of 120 as a fallback; publishing the real value means
    a recalibration reaches the page with the data instead of needing a second
    PR that nobody remembers to write.
    """

    def test_summary_publishes_the_remoteness_cap(self):
        from src.core.config import DEFAULT_CONFIG

        with tempfile.TemporaryDirectory() as tmpdir:
            HugoExporter(Path(tmpdir)).export_summary([_county()], [])
            summary = json.loads((Path(tmpdir) / "summary.json").read_text())

        assert summary["remoteness_cap_mi"] == DEFAULT_CONFIG.remoteness.score_cap_mi

    def test_the_contract_requires_it(self):
        assert "remoteness_cap_mi" in SUMMARY_REQUIRED_FIELDS
