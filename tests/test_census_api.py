"""
Tests for Census API client.

Tests cover:
- Retry logic on transient failures
- Response parsing (Census JSON → dict)
- Rate limiting behavior
- Caching (file-based, age-based expiry)
- Error handling for malformed responses
"""

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.core.config import DEFAULT_CONFIG
from src.ingest.census_api import (
    CensusClient,
    CensusEndpointError,
    CensusFetchError,
)


class TestResponseParsing:
    """Census API returns list-of-lists with header row."""

    def test_parse_basic_response(self):
        """Standard Census API format: header row + data rows."""
        raw = [
            ["NAME", "POP_2024", "state"],
            ["Alabama", "5100000", "01"],
            ["Alaska", "730000", "02"],
        ]
        client = CensusClient()
        records = client.parse_response(raw)
        assert len(records) == 2
        assert records[0]["NAME"] == "Alabama"
        assert records[0]["POP_2024"] == "5100000"
        assert records[0]["state"] == "01"

    def test_parse_empty_response(self):
        """Only header row → empty result."""
        raw = [["NAME", "POP_2024", "state"]]
        client = CensusClient()
        records = client.parse_response(raw)
        assert records == []

    def test_parse_none_response(self):
        """None input → empty result."""
        client = CensusClient()
        records = client.parse_response(None)
        assert records == []

    def test_parse_single_element(self):
        """Malformed: list with non-list → empty result."""
        client = CensusClient()
        records = client.parse_response("not a list")
        assert records == []


class TestRetryLogic:
    """Client should retry on transient HTTP failures."""

    @patch("src.ingest.census_api.requests.get")
    def test_succeeds_on_third_attempt(self, mock_get):
        """Retry twice, succeed on third."""
        response_ok = MagicMock()
        response_ok.status_code = 200
        response_ok.json.return_value = [["NAME", "state"], ["Alabama", "01"]]
        response_ok.raise_for_status.return_value = None

        response_fail = MagicMock()
        response_fail.raise_for_status.side_effect = Exception("503")

        mock_get.side_effect = [response_fail, response_fail, response_ok]

        client = CensusClient()
        result = client.fetch("http://example.com/api", label="test")
        assert result is not None
        assert len(result) == 2

    @patch("src.ingest.census_api.time.sleep")
    @patch("src.ingest.census_api.requests.get")
    def test_raises_typed_error_after_max_retries(self, mock_get, _sleep):
        """Exhausted retries surface as a typed error, not a silent None."""
        mock_get.side_effect = Exception("timeout")

        client = CensusClient()
        with pytest.raises(CensusFetchError):
            client.fetch("http://example.com/api", label="test")
        assert mock_get.call_count == 3

    @patch("src.ingest.census_api.time.sleep")
    @patch("src.ingest.census_api.requests.get")
    def test_4xx_fails_fast_without_retry(self, mock_get, mock_sleep):
        """A 404/403 cannot be retried away — fail typed on attempt one."""
        resp = MagicMock()
        resp.raise_for_status.side_effect = requests.HTTPError(
            response=MagicMock(status_code=404)
        )
        mock_get.return_value = resp

        client = CensusClient()
        with pytest.raises(CensusEndpointError):
            client.fetch("http://example.com/api", label="test")
        assert mock_get.call_count == 1
        mock_sleep.assert_not_called()

    @patch("src.ingest.census_api.time.sleep")
    @patch("src.ingest.census_api.requests.get")
    def test_retry_budget_caps_wall_time(self, mock_get, mock_sleep):
        """The 2s backoff fits a 3s budget; the next 4s one must not."""
        mock_get.side_effect = Exception("503")

        client = CensusClient(max_retries=5, retry_budget_seconds=3)
        with pytest.raises(CensusFetchError):
            client.fetch("http://example.com/api", label="test")
        assert mock_get.call_count == 2
        assert sum(c.args[0] for c in mock_sleep.call_args_list) <= 3


class TestCaching:
    """File-based caching for Census API responses."""

    def test_cache_hit(self):
        """Cached file younger than max_age → use cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = CensusClient(cache_dir=Path(tmpdir))
            cache_data = [["NAME", "state"], ["Alabama", "01"]]

            # Write cache file
            cache_file = Path(tmpdir) / "test_cache.json"
            cache_file.write_text(json.dumps(cache_data))

            result = client.load_from_cache("test_cache", max_age_hours=24)
            assert result is not None
            assert len(result) == 2

    def test_cache_miss_expired(self):
        """Cached file older than max_age → return None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = CensusClient(cache_dir=Path(tmpdir))
            cache_data = [["NAME", "state"], ["Alabama", "01"]]

            cache_file = Path(tmpdir) / "test_cache.json"
            cache_file.write_text(json.dumps(cache_data))

            # Set mtime to 48 hours ago
            old_time = time.time() - 48 * 3600
            os.utime(cache_file, (old_time, old_time))

            result = client.load_from_cache("test_cache", max_age_hours=24)
            assert result is None

    def test_corrupt_cache_is_a_miss_not_a_crash(self):
        """Truncated JSON reads as a cache miss, not an aborted run."""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = CensusClient(cache_dir=Path(tmpdir))
            (Path(tmpdir) / "bad.json").write_text('[["NAME", "sta')
            assert client.load_from_cache("bad", max_age_hours=24) is None

    def test_interrupted_write_leaves_no_partial_cache(self, monkeypatch):
        """A dying write must not leave partial JSON at the final path."""

        def partial_write(self, text, *args, **kwargs):
            self.write_bytes(text[:10].encode())
            raise OSError("interrupted")

        with tempfile.TemporaryDirectory() as tmpdir:
            client = CensusClient(cache_dir=Path(tmpdir))
            monkeypatch.setattr(Path, "write_text", partial_write)
            with pytest.raises(OSError):
                client.write_to_cache("k", [["NAME"], ["Iowa"]])
            assert not (Path(tmpdir) / "k.json").exists()

    def test_client_ttl_used_when_no_explicit_age(self):
        """load_from_cache defaults to the client TTL (config-driven)."""
        assert CensusClient().cache_max_age_hours == DEFAULT_CONFIG.cache_max_age_hours
        with tempfile.TemporaryDirectory() as tmpdir:
            client = CensusClient(cache_dir=Path(tmpdir), cache_max_age_hours=1000)
            cache_file = Path(tmpdir) / "k.json"
            cache_file.write_text(json.dumps([["NAME"], ["Iowa"]]))
            old_time = time.time() - 48 * 3600
            os.utime(cache_file, (old_time, old_time))
            assert client.load_from_cache("k") is not None

    def test_cache_miss_no_file(self):
        """No cache file → return None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = CensusClient(cache_dir=Path(tmpdir))
            result = client.load_from_cache("nonexistent", max_age_hours=24)
            assert result is None

    @patch("src.ingest.census_api.requests.get")
    def test_fetch_writes_cache(self, mock_get):
        """Successful fetch writes result to cache."""
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = [["NAME", "state"], ["Alabama", "01"]]
        response.raise_for_status.return_value = None
        mock_get.return_value = response

        with tempfile.TemporaryDirectory() as tmpdir:
            client = CensusClient(cache_dir=Path(tmpdir))
            result = client.fetch(
                "http://example.com/api", label="test", cache_key="test_fetch"
            )
            assert result is not None

            # Verify cache file exists
            cache_file = Path(tmpdir) / "test_fetch.json"
            assert cache_file.exists()
