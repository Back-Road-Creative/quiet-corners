"""Census API key handling.

The Census Bureau began requiring an API key for `api.census.gov/data`
queries. A keyless request is answered with a 302 to `missing_key.html`,
which is served as **HTTP 200 with an HTML body** — so neither the status
code nor `raise_for_status()` notices anything is wrong. The old client
let `resp.json()` raise, swallowed it, retried twice more, and returned
None, which every caller read as "this source is unavailable" and
defaulted to zero. A full run then exported a complete-looking dataset
with 0% coverage and exit code 0.
"""

from pathlib import Path

import pytest

from src.ingest.census_api import (
    CensusAPIKeyError,
    CensusClient,
    CensusFetchError,
    CensusKeyRejectedError,
)


class _Resp:
    """Minimal stand-in for requests.Response."""

    def __init__(
        self,
        url: str,
        payload: object = None,
        content_type: str = "application/json",
    ) -> None:
        self.url = url
        self.headers = {"content-type": content_type}
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload


def test_key_appended_to_request_url(monkeypatch, tmp_path: Path) -> None:
    seen: list[str] = []

    def fake_get(url: str, timeout: int = 0) -> _Resp:
        seen.append(url)
        return _Resp(url, payload=[["NAME"], ["Iowa"]])

    monkeypatch.setattr("src.ingest.census_api.requests.get", fake_get)
    client = CensusClient(cache_dir=tmp_path, api_key="abc123")
    client.fetch("https://api.census.gov/data/2023/acs/acs5?get=NAME")

    assert seen and "key=abc123" in seen[0]


def test_missing_key_redirect_raises_rather_than_returning_none(
    monkeypatch, tmp_path: Path
) -> None:
    """The failure mode that produced a 0%-coverage export must be loud."""

    def fake_get(url: str, timeout: int = 0) -> _Resp:
        return _Resp(
            "https://api.census.gov/data/missing_key.html",
            payload=None,
            content_type="text/html",
        )

    monkeypatch.setattr("src.ingest.census_api.requests.get", fake_get)
    client = CensusClient(cache_dir=tmp_path, api_key=None)

    with pytest.raises(CensusAPIKeyError):
        client.fetch("https://api.census.gov/data/2023/acs/acs5?get=NAME")


def test_missing_key_is_not_retried(monkeypatch, tmp_path: Path) -> None:
    """Retrying a missing key wastes three round trips to reach the same wall."""
    calls: list[str] = []

    def fake_get(url: str, timeout: int = 0) -> _Resp:
        calls.append(url)
        return _Resp(
            "https://api.census.gov/data/missing_key.html",
            payload=None,
            content_type="text/html",
        )

    monkeypatch.setattr("src.ingest.census_api.requests.get", fake_get)
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    client = CensusClient(cache_dir=tmp_path, max_retries=3)

    with pytest.raises(CensusAPIKeyError):
        client.fetch("https://api.census.gov/data/2023/acs/acs5?get=NAME")

    assert len(calls) == 1


def test_keyed_request_answered_html_is_not_blamed_on_missing_key(
    monkeypatch, tmp_path: Path
) -> None:
    """Never tell the operator to set the key they already set."""

    def fake_get(url: str, timeout: int = 0) -> _Resp:
        return _Resp(url, payload=None, content_type="text/html")

    monkeypatch.setattr("src.ingest.census_api.requests.get", fake_get)
    client = CensusClient(cache_dir=tmp_path, api_key="configured-key")

    with pytest.raises(CensusKeyRejectedError) as excinfo:
        client.fetch("https://api.census.gov/data/timeseries/bps/houseunits")

    assert "Set CENSUS_API_KEY" not in str(excinfo.value)


def test_fetch_failure_message_redacts_key(monkeypatch, tmp_path: Path) -> None:
    """requests error text embeds the keyed URL — message and chain must not."""

    def fake_get(url: str, timeout: int = 0) -> _Resp:
        raise ValueError(f"failed to reach {url}")

    monkeypatch.setattr("src.ingest.census_api.requests.get", fake_get)
    client = CensusClient(cache_dir=tmp_path, api_key="sekrit-key", max_retries=1)

    with pytest.raises(CensusFetchError) as excinfo:
        client.fetch("https://api.census.gov/data/2023/acs/acs5?get=NAME")

    assert "sekrit-key" not in str(excinfo.value)
    assert "REDACTED" in str(excinfo.value)
    assert (excinfo.value.__cause__, excinfo.value.__context__) == (None, None)


def test_key_read_from_environment(monkeypatch, tmp_path: Path) -> None:
    fake_key = "from-env"
    monkeypatch.setenv("CENSUS_API_KEY", fake_key)
    client = CensusClient(cache_dir=tmp_path)
    assert client.api_key == fake_key


def test_absent_key_is_none_not_empty_string(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    client = CensusClient(cache_dir=tmp_path)
    assert client.api_key is None
