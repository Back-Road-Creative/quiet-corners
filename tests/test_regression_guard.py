"""The refresh script's coverage diff, as a machine check.

A human reading that diff is the only thing standing between a source that
quietly stopped carrying and a published dashboard that hides the filter it can
no longer fill. These cases pin that judgement — and its asymmetry: better never
blocks, a key the baseline never had never blocks (the published summary.json
predates `sources`/`remoteness_cap_mi`), and a first run never blocks.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.export.regression_guard import compare_summaries

SVC_DIR = Path(__file__).resolve().parents[1]  # tests/ -> svc-quiet-corners
# A byte copy of the export the dashboard is actually serving, committed here
# rather than read from the site submodule: CI does not check submodules out,
# so the submodule path made this test skip everywhere and it never once ran.
# It legitimately predates `sources`/`remoteness_cap_mi`, which is exactly the
# missing-baseline-key case worth pinning against real bytes.
PUBLISHED = SVC_DIR / "tests" / "fixtures" / "published_summary.json"

BASELINE: dict[str, object] = {
    "pipeline_version": "2.0.0",
    "generated_at": "2026-07-31T00:58:20.154774+00:00",
    "total_rural_counties": 1681,
    "coverage": {
        "stability": {"count": 1681, "pct": 100.0},
        "affordability": {"count": 1679, "pct": 99.9},
    },
    "sprawl_factor_coverage": {
        "permits": {"count": 849, "pct": 50.5},
        "migration": {"count": 0, "pct": 0.0},
    },
    "data_sources": {"affordability": True, "permits": True, "rucc": True},
    "candidates": {"with_county_match": 1597},
}

# 99.9 - 2.0 must still ride: the tolerance is inclusive, not floaty. 2% of
# 1681 is 33.62 and of 1597 is 31.94, so 1648 and 1566 are the last that fit.
PUBLISHES: list[tuple[str, dict[str, object]]] = [
    ("unchanged", {}),
    ("baseline lacked the field", {"sources": {}, "remoteness_cap_mi": 120.0}),
    ("new source key arrives false", {"data_sources": {"typology": False}}),
    ("metadata churn", {"generated_at": "2026-10-01", "pipeline_version": "2.1"}),
    ("coverage improved", {"coverage": {"affordability": {"pct": 100.0}}}),
    ("coverage inside tolerance", {"coverage": {"affordability": {"pct": 97.9}}}),
    ("rural set inside tolerance", {"total_rural_counties": 1648}),
    ("candidates inside tolerance", {"candidates": {"with_county_match": 1566}}),
    ("factor shrank", {"sprawl_factor_coverage": {"permits": {"count": 1}}}),
    ("factor already zero", {"sprawl_factor_coverage": {"migration": {"count": 0}}}),
]

# The name doubles as the error substring the guard must name.
BLOCKS: list[tuple[str, dict[str, object]]] = [
    ("coverage.affordability", {"coverage": {"affordability": {"pct": 97.8}}}),
    ("coverage.affordability", {"coverage": {"affordability": None}}),
    ("data_sources.permits", {"data_sources": {"permits": False}}),
    ("data_sources.permits", {"data_sources": {"permits": None}}),
    ("sprawl_factor_coverage.permits", {"sprawl_factor_coverage": {"permits": {}}}),
    ("total_rural_counties", {"total_rural_counties": 1647}),
    ("candidates.with_county_match", {"candidates": {"with_county_match": 1565}}),
]


def _new(**overrides: object) -> dict[str, object]:
    """Deep copy of BASELINE. A dict override merges; a None value deletes."""
    fresh: dict[str, object] = json.loads(json.dumps(BASELINE))
    for key, value in overrides.items():
        block = fresh.get(key)
        if not (isinstance(value, dict) and isinstance(block, dict)):
            fresh[key] = value
            continue
        for field, sub in value.items():
            if sub is None:
                del block[field]
            else:
                block[field] = sub
    return fresh


@pytest.mark.parametrize(
    "overrides", [o for _, o in PUBLISHES], ids=[n for n, _ in PUBLISHES]
)
def test_a_regeneration_that_did_not_get_worse_publishes(
    overrides: dict[str, object],
) -> None:
    assert compare_summaries(BASELINE, _new(**overrides)) == []


def test_a_first_run_with_no_baseline_publishes() -> None:
    """The script writes `{}` when the site has no committed summary.json."""
    assert compare_summaries({}, BASELINE) == []


@pytest.mark.parametrize("named,overrides", BLOCKS, ids=[n for n, _ in BLOCKS])
def test_a_regeneration_that_got_worse_blocks(
    named: str, overrides: dict[str, object]
) -> None:
    errors = compare_summaries(BASELINE, _new(**overrides))
    assert any(named in e for e in errors), errors


def _cli(
    tmp_path: Path, current: dict[str, object]
) -> subprocess.CompletedProcess[str]:
    prev, cur = tmp_path / "prev.json", tmp_path / "cur.json"
    prev.write_text(json.dumps(BASELINE))
    cur.write_text(json.dumps(current))
    cmd = [sys.executable, "-m", "src.export.regression_guard", str(prev), str(cur)]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=SVC_DIR, timeout=60)


def test_the_cli_puts_the_verdict_in_its_exit_code(tmp_path: Path) -> None:
    """The refresh script reads the code; an operator reads the reason."""
    assert _cli(tmp_path, _new()).returncode == 0
    proc = _cli(tmp_path, _new(data_sources={"permits": False}))
    assert proc.returncode == 1, proc.stdout
    assert "permits" in proc.stdout, proc.stdout


def test_the_real_published_summary_passes_against_itself() -> None:
    """A gate that rejects honest data is worse than no gate."""
    real: dict[str, object] = json.loads(PUBLISHED.read_text())
    assert compare_summaries(real, real) == []

    degraded: dict[str, object] = json.loads(PUBLISHED.read_text())
    sources = degraded["data_sources"]
    assert isinstance(sources, dict)
    sources["permits"] = False
    assert compare_summaries(real, degraded), "a dead source must not publish"
