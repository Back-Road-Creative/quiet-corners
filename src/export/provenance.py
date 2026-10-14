"""Input provenance for the published export.

summary.json pinned only `generated_at` and `pipeline_version`, so a published
export could not answer the first question anyone asks of it: which inputs did
this run actually read? `--fetch` skips a download whose local copy is younger
than the TTL, which means a "fresh" export can legitimately be built from
months-old inputs and look identical to one built from today's.

The receipt lists only what the run consumed (files opened through
`src.ingest.consumption.consumed_open`, Census responses recorded by
`CensusClient`), never every download that happens to sit on disk. Two exports
with the same `sources` and `api_lineage` were built from the same bytes,
whatever their timestamps say.

Three different freshness notions are kept apart on purpose:
  source vintage    which data release the inputs are (`freshness.source_vintage`,
                    and the pinned `url` of each file)
  run time          when this export was generated (`generated_at`)
  refresh schedule  how often the pipeline is meant to run (REFRESH_SCHEDULE)
"""

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from src.ingest import consumption
from src.ingest.data_downloader import DOWNLOADS, GAZETTEER_ZIPS

# Every raw input the pipeline reads, named by the modules that fetch them.
# Derived rather than hand-listed so a new download cannot be silently omitted.
INPUT_FILENAMES: tuple[str, ...] = tuple(sorted(set(DOWNLOADS) | set(GAZETTEER_ZIPS)))


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# The refresh cadence, declared once. It is the crontab documented in this
# service's CLAUDE.md ("Regenerate the published dashboard data"), installed in
# joe's crontab; tests/test_source_lineage.py fails if the two drift. This is
# the *intended* schedule, not evidence that any given run happened: the
# export's own run time is `generated_at`.
REFRESH_SCHEDULE: dict[str, str] = {
    "cadence": "monthly",
    "cron": "0 6 1 * *",
    "command": "bin/quiet-corners-refresh.sh --publish",
    "documented_in": "CLAUDE.md",
}

_DOWNLOAD_URLS = {**DOWNLOADS, **GAZETTEER_ZIPS}


def _relative_name(path: Path, data_dir: Path) -> str:
    """Name a consumed file by its place in data_dir; outside it, by full path."""
    try:
        return path.relative_to(Path(os.path.abspath(data_dir))).as_posix()
    except ValueError:
        return str(path)


def collect_input_provenance(
    data_dir: Path, ledger: consumption.ConsumptionLedger | None = None
) -> dict[str, dict]:
    """Describe the files this run opened.

    A file nothing read is absent from the result even when it is on disk, and
    a consumed file that has since vanished is absent rather than reported with
    null fields — the export says what it read, not what it hoped for.
    """
    ledger = ledger if ledger is not None else consumption.LEDGER
    sources: dict[str, dict] = {}
    for path in ledger.files:
        try:
            stat = path.stat()
        except OSError:
            continue
        name = _relative_name(path, data_dir)
        entry = {
            "bytes": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "sha256": _digest(path),
        }
        if name in _DOWNLOAD_URLS:
            entry["url"] = _DOWNLOAD_URLS[name]
        sources[name] = entry
    return sources


def collect_api_lineage(
    ledger: consumption.ConsumptionLedger | None = None,
) -> list[dict]:
    """Census API responses this run consumed: sanitized request, cache-or-live,
    retrieval time and a digest of the response. Never carries a key."""
    ledger = ledger if ledger is not None else consumption.LEDGER
    return list(ledger.api_entries)


def source_vintage(api_lineage: list[dict]) -> dict[str, str]:
    """{dataset: vintage} for the consumed API responses that name a vintage."""
    return {
        e["dataset"]: e["vintage"]
        for e in api_lineage
        if e.get("dataset") and e.get("vintage")
    }
