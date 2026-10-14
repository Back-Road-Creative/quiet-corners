"""
Generic Census Bureau API client with retry, rate limiting, and file-based caching.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from src.core.config import DEFAULT_CONFIG
from src.ingest import consumption

KEY_SIGNUP_URL = "https://api.census.gov/data/key_signup.html"


class CensusAPIError(RuntimeError):
    """Base class for observable Census API fetch failures."""


class CensusAPIKeyError(CensusAPIError):
    """Raised when api.census.gov answers with its "missing key" page.

    That page is served as HTTP 200 with an HTML body, so it passes both the
    status check and raise_for_status(). Without this the JSON decode failed,
    the exception was swallowed by the retry loop, and the caller received
    None — indistinguishable from a genuinely unavailable source.
    Only raised when no key is configured (else CensusKeyRejectedError).
    """


class CensusKeyRejectedError(CensusAPIKeyError):
    """Raised when a request WITH a configured key is still answered as HTML.

    The key is set, so "set CENSUS_API_KEY" would be a lie — the dataset is
    retired/moved, or the key itself was rejected. Subclasses
    CensusAPIKeyError so key-error handlers still catch it.
    """


class CensusEndpointError(CensusAPIError):
    """Raised on a non-retryable HTTP client error (4xx) — dead endpoint."""


class CensusFetchError(CensusAPIError):
    """Raised when retries are exhausted on transient failures."""


class CensusClient:
    """Census API client with retry logic and file-based caching."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        max_retries: int = 3,
        timeout: int = 30,
        api_key: str | None = None,
        cache_max_age_hours: int = DEFAULT_CONFIG.cache_max_age_hours,
        retry_budget_seconds: float = 10.0,
        ledger: consumption.ConsumptionLedger | None = None,
    ) -> None:
        self.cache_dir = cache_dir or Path("data/raw/cache")
        self.max_retries = max_retries
        self.timeout = timeout
        self.api_key = api_key or os.environ.get("CENSUS_API_KEY") or None
        self.cache_max_age_hours = cache_max_age_hours
        self.retry_budget_seconds = retry_budget_seconds
        self._ledger = ledger

    def _record(
        self, url: str, label: str, data: list[list[str]], origin: str, when: datetime
    ) -> None:
        """Note a consumed response in the run's lineage (never the key)."""
        ledger = self._ledger if self._ledger is not None else consumption.LEDGER
        ledger.record_api(
            consumption.api_entry(
                url=url,
                label=label or "",
                api_key=self.api_key,
                data=data,
                origin=origin,
                retrieved_at=when,
            )
        )

    def _redact(self, text: str) -> str:
        """Blank the API key in error text (requests embeds keyed URLs)."""
        return text.replace(self.api_key, "REDACTED") if self.api_key else text

    def _with_key(self, url: str) -> str:
        """Append the API key to a query URL, if one is configured."""
        if not self.api_key:
            return url
        sep = "&" if "?" in url else "?"
        return url + sep + urlencode({"key": self.api_key})

    def parse_response(self, raw: Any) -> list[dict[str, str]]:
        """Parse Census API response (list-of-lists) into list of dicts."""
        if not isinstance(raw, list) or len(raw) < 1:
            return []
        if not isinstance(raw[0], list):
            return []

        headers = raw[0]
        return [dict(zip(headers, row)) for row in raw[1:]]

    def fetch(
        self,
        url: str,
        label: str = "",
        cache_key: str | None = None,
    ) -> list[list[str]]:
        """Fetch JSON from Census API with retry logic and optional caching.

        Returns the raw list-of-lists response. Failures raise a typed
        CensusAPIError subclass instead of degrading to a silent None the
        caller would read as "no data".
        """
        # Check cache first
        if cache_key:
            cached = self.load_from_cache(cache_key)
            if cached is not None:
                cache_file = self.cache_dir / f"{cache_key}.json"
                when = datetime.fromtimestamp(cache_file.stat().st_mtime, timezone.utc)
                self._record(url, label, cached, "cache", when)
                return cached

        last_error: Exception | None = None
        slept = 0.0
        for attempt in range(self.max_retries):
            try:
                resp = requests.get(self._with_key(url), timeout=self.timeout)
                resp.raise_for_status()

                # api.census.gov serves its failure pages as 200/text-html,
                # so they pass raise_for_status(). Retrying cannot fix
                # either cause — fail loudly on the first attempt.
                if "missing_key" in getattr(
                    resp, "url", ""
                ) or "html" in resp.headers.get("content-type", ""):
                    if not self.api_key:
                        raise CensusAPIKeyError(
                            f"api.census.gov refused '{label or url}': no API "
                            f"key. Set CENSUS_API_KEY (free: {KEY_SIGNUP_URL})."
                        )
                    raise CensusKeyRejectedError(
                        f"api.census.gov answered HTML for '{label or url}' "
                        "despite a configured API key: the key was rejected "
                        "or the dataset is retired/moved."
                    )

                data = resp.json()

                # Write to cache
                if cache_key:
                    self.write_to_cache(cache_key, data)

                self._record(url, label, data, "live", datetime.now(timezone.utc))
                result: list[list[str]] = data
                return result
            except CensusAPIError:
                raise
            except requests.HTTPError as exc:
                status = getattr(exc.response, "status_code", None)
                if status is not None and 400 <= int(status) < 500:
                    # A 4xx cannot be retried away — the endpoint is gone or
                    # the request is malformed.
                    raise CensusEndpointError(
                        f"'{label or url}' answered HTTP {status}."
                    ) from None  # cause text embeds the keyed URL
                last_error = exc
            except Exception as exc:
                last_error = exc
            if attempt < self.max_retries - 1:
                delay = 2 * (attempt + 1)
                if slept + delay > self.retry_budget_seconds:
                    break  # backoff would blow the wall-time budget
                time.sleep(delay)
                slept += delay

        raise CensusFetchError(
            f"'{label or url}': retries exhausted ({self._redact(str(last_error))})"
        ) from None  # cause text embeds the keyed URL

    def load_from_cache(
        self, cache_key: str, max_age_hours: int | None = None
    ) -> list[list[str]] | None:
        """Load cached response if it exists, is fresh enough, and parses.

        max_age_hours defaults to the client's configured TTL
        (PipelineConfig.cache_max_age_hours).
        """
        if max_age_hours is None:
            max_age_hours = self.cache_max_age_hours
        cache_file = self.cache_dir / f"{cache_key}.json"
        if not cache_file.exists():
            return None

        age_seconds = time.time() - cache_file.stat().st_mtime
        if age_seconds > max_age_hours * 3600:
            return None

        try:
            cached: list[list[str]] = json.loads(cache_file.read_text())
        except (ValueError, OSError):
            # Corrupt/truncated cache (e.g. an interrupted write) is a miss,
            # not a crash — the caller refetches and overwrites it.
            return None
        return cached

    def write_to_cache(self, cache_key: str, data: Any) -> None:
        """Write API response to cache atomically (temp file + rename)."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = self.cache_dir / f"{cache_key}.json"
        tmp_file = cache_file.with_suffix(".json.tmp")
        tmp_file.write_text(json.dumps(data))
        os.replace(tmp_file, cache_file)
