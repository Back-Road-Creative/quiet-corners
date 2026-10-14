"""What a run actually consumed, recorded where the consuming happens.

The published receipt used to list every known download present on disk, so a
stale cached file the run never opened (the population CSV on a run the API
served) was claimed as an input. The fix is structural: loaders open files
through `consumed_open`, and `CensusClient` records each response it returns,
so the receipt can only name what was read. Nothing has to remember to "also
report" a new input.

Census API lineage is recorded from the *sanitized* request: endpoint,
dataset/vintage, variables and geography, plus a digest of the parsed
response. The API key never enters an entry — it is dropped by parameter name
and scrubbed by value.
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any
from urllib.parse import parse_qsl, urlsplit

_REDACTED = "REDACTED"


class ConsumptionLedger:
    """Files opened and API responses consumed since the last reset."""

    def __init__(self) -> None:
        self._files: dict[str, Path] = {}
        self._api: dict[str, dict[str, Any]] = {}

    def reset(self) -> None:
        self._files.clear()
        self._api.clear()

    def record_file(self, path: str | Path) -> None:
        absolute = Path(os.path.abspath(path))
        self._files[str(absolute)] = absolute

    def record_api(self, entry: dict[str, Any]) -> None:
        # One entry per distinct request; the latest retrieval wins.
        identity = json.dumps(
            [entry["endpoint"], entry["query"]], sort_keys=True, separators=(",", ":")
        )
        self._api[identity] = entry

    @property
    def files(self) -> list[Path]:
        return [self._files[k] for k in sorted(self._files)]

    @property
    def api_entries(self) -> list[dict[str, Any]]:
        return [self._api[k] for k in sorted(self._api)]


# The run's ledger. do_score resets it; loaders and CensusClient append to it.
LEDGER = ConsumptionLedger()


def consumed_open(
    path: str | Path,
    mode: str = "r",
    *,
    ledger: ConsumptionLedger | None = None,
    **kwargs: Any,
) -> IO[Any]:
    """`open()` that records the file as consumed once the open succeeds."""
    handle = open(path, mode, **kwargs)
    (ledger if ledger is not None else LEDGER).record_file(path)
    return handle


def digest_json(data: Any) -> str:
    """sha256 of the canonical JSON form — identical for a live fetch and the
    cache file it was written to, whatever the whitespace on disk."""
    canonical = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _scrub(text: str, secret: str | None) -> str:
    return text.replace(secret, _REDACTED) if secret else text


def sanitize_request(url: str, api_key: str | None = None) -> dict[str, Any]:
    """Describe a Census API request without any credential in it."""
    parts = urlsplit(_scrub(url, api_key))
    endpoint = f"{parts.scheme}://{parts.netloc}{parts.path}"
    dataset = parts.path.split("/data/", 1)[1] if "/data/" in parts.path else None
    first = dataset.split("/", 1)[0] if dataset else ""
    query: dict[str, Any] = {}
    for name, value in parse_qsl(parts.query, keep_blank_values=True):
        if name.lower() in {"key", "api_key", "apikey", "token"}:
            continue
        query[name] = value.split(",") if name == "get" else value
    return {
        "endpoint": endpoint,
        "dataset": dataset,
        "vintage": first if first.isdigit() and len(first) == 4 else None,
        "query": dict(sorted(query.items())),
    }


def api_entry(
    *,
    url: str,
    label: str,
    api_key: str | None,
    data: list[list[str]],
    origin: str,
    retrieved_at: datetime,
) -> dict[str, Any]:
    """One lineage entry for a consumed Census response (`origin` is live|cache)."""
    entry = sanitize_request(url, api_key)
    entry.update(
        {
            "label": _scrub(label, api_key),
            "origin": origin,
            "retrieved_at": retrieved_at.astimezone(timezone.utc).isoformat(),
            "rows": max(len(data) - 1, 0),
            "sha256": digest_json(data),
        }
    )
    return entry
