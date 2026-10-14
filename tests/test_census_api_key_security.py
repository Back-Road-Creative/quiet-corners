"""Regression: the API key (appended to every request, test_census_api_key.py)
must never surface in the CensusAPIKeyError message, stdout, or a return
value — the blanket `except Exception` in CensusClient.fetch() is all that
keeps a failed request's key-bearing URL out of a traceback.
"""

from pathlib import Path

import pytest

from src.ingest.census_api import CensusAPIKeyError, CensusClient

FAKE_KEY = "totally-not-a-real-key-98765"


class _MissingKeyResp:
    """A configured-but-rejected key looks identical to no key at all."""

    url = "https://api.census.gov/data/missing_key.html"
    headers = {"content-type": "text/html"}

    def raise_for_status(self) -> None:
        return None


def test_key_error_message_never_contains_the_key(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "src.ingest.census_api.requests.get", lambda url, timeout=0: _MissingKeyResp()
    )
    client = CensusClient(cache_dir=tmp_path, api_key=FAKE_KEY)

    with pytest.raises(CensusAPIKeyError) as excinfo:
        client.fetch("https://api.census.gov/data/2023/acs/acs5?get=NAME")

    assert FAKE_KEY not in str(excinfo.value)


def test_generic_request_failure_never_leaks_key(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    """A network exception's str() often embeds the failing URL (key and all).

    Whether fetch degrades to None or raises a typed error, the key-bearing
    URL must reach neither stdout nor the raised value.
    """

    def fake_get(url: str, timeout: int = 0) -> None:
        raise ConnectionError(f"failed to reach {url}")

    monkeypatch.setattr("src.ingest.census_api.requests.get", fake_get)
    client = CensusClient(cache_dir=tmp_path, api_key=FAKE_KEY, max_retries=1)

    try:
        client.fetch("https://api.census.gov/data/2023/acs/acs5?get=NAME")
    except Exception as raised:
        assert FAKE_KEY not in repr(raised)
    assert FAKE_KEY not in capsys.readouterr().out
