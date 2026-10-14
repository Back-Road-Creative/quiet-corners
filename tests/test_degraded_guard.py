"""The keyless run must be an explicit choice, not an accident.

Before this gate, a run without CENSUS_API_KEY exported a complete-looking
dataset with 0% coverage on affordability and exited 0.
"""

import pytest

from src.ingest.census_api import CensusEndpointError
from src.main import KEYED_SOURCES, _try_keyed, require_census_key


def test_missing_key_without_optin_exits(monkeypatch, capsys) -> None:
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)

    with pytest.raises(SystemExit) as excinfo:
        require_census_key(allow_degraded=False)

    assert excinfo.value.code != 0
    message = str(excinfo.value)
    assert "CENSUS_API_KEY" in message
    assert "key_signup" in message


def test_missing_key_with_optin_warns_and_continues(monkeypatch, capsys) -> None:
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)

    require_census_key(allow_degraded=True)

    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "affordability" in out.lower()


def test_present_key_is_silent(monkeypatch, capsys) -> None:
    monkeypatch.setenv("CENSUS_API_KEY", "a-key")

    require_census_key(allow_degraded=False)

    assert capsys.readouterr().out == ""


def test_keyed_sources_lists_only_key_recoverable() -> None:
    """A key cannot resurrect the retired BPS/PEP endpoints."""
    joined = " ".join(KEYED_SOURCES)
    assert "BPS" not in joined
    assert "PEP" not in joined
    assert any("ACS" in source for source in KEYED_SOURCES)


def test_try_keyed_does_not_blame_the_key_for_a_dead_endpoint(capsys) -> None:
    def boom() -> dict:
        raise CensusEndpointError("'BPS 2020' answered HTTP 404.")

    assert _try_keyed(boom, default={}, label="Permits") == {}
    out = capsys.readouterr().out
    assert "no API key" not in out
    assert "unavailable" in out
