"""Refuse to publish a regeneration that is worse than what is published.

`validate_contract` proves an export is well-formed. It cannot tell a good
regeneration from one where a source quietly stopped carrying — both are
well-formed, and the degraded one replaces a working dashboard with a thinner
one. The refresh script prints a coverage diff for a human to judge; this is
that judgement written down, so the refresh can run without one.

Only losses are reported: improvement, a key the baseline never had (the
published file predates `sources`/`remoteness_cap_mi`), and a first run with
no baseline at all each publish without comment.

Runnable standalone — exit 0 = safe to publish, 1 = regressed:
    python -m src.export.regression_guard <previous.json> <current.json>
"""

import json
import sys
from pathlib import Path

Summary = dict[str, object]

# Wider than the fraction of a point coverage drifts as counties gain and lose
# rows, far narrower than a source dropping out (which lands a dimension near 0).
COVERAGE_PCT_TOLERANCE = 2.0
# Counts have no natural scale, so they get a relative budget: 2% of the
# 1681-county rural set is 33; releases have moved it by single counties.
COUNT_DROP_TOLERANCE = 0.02
# Published percentages are rounded to one decimal, so a comparison right at the
# tolerance is exact arithmetic wearing float noise (99.9 - 97.9 ==
# 2.0000000000000114, which a bare `>` calls a regression). Round the delta.
_DELTA_PRECISION = 6

_RELATIVE_COUNTS: tuple[tuple[str, ...], ...] = (
    ("total_rural_counties",),
    ("candidates", "with_county_match"),
)


def _block(container: Summary, key: str) -> Summary:
    """The nested object at `key`, or an empty one when absent or malformed."""
    value = container.get(key)
    return value if isinstance(value, dict) else {}


def _number(container: Summary, key: str) -> float | None:
    """The number at `key`, or None when there is nothing to compare."""
    value = container.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _check_coverage(previous: Summary, current: Summary, errors: list[str]) -> None:
    """A dimension that fell means the dashboard hides that filter."""
    now, tol = _block(current, "coverage"), COVERAGE_PCT_TOLERANCE
    for dim, was in sorted(_block(previous, "coverage").items()):
        before = _number(was, "pct") if isinstance(was, dict) else None
        if before is None:
            continue
        after = _number(_block(now, dim), "pct")
        if after is None:
            errors.append(f"coverage.{dim}: was {before}%, absent now")
        elif round(before - after, _DELTA_PRECISION) > tol:
            errors.append(f"coverage.{dim}: {before}% -> {after}%, over {tol} points")


def _check_sources(previous: Summary, current: Summary, errors: list[str]) -> None:
    """A source that carried last time and does not now."""
    now = _block(current, "data_sources")
    for key, carried in sorted(_block(previous, "data_sources").items()):
        if carried is not True:
            continue
        if key not in now:
            errors.append(f"data_sources.{key}: carried last time, absent now")
        elif now[key] is not True:
            errors.append(f"data_sources.{key}: carried last time, false now")


def _check_factors(previous: Summary, current: Summary, errors: list[str]) -> None:
    """Shrinking is noise; reaching zero means the factor stopped landing."""
    now = _block(current, "sprawl_factor_coverage")
    for factor, was in sorted(_block(previous, "sprawl_factor_coverage").items()):
        before = _number(was, "count") if isinstance(was, dict) else None
        if not before:
            continue  # never measured, or already zero — nothing to lose
        if not _number(_block(now, factor), "count"):
            errors.append(f"sprawl_factor_coverage.{factor}: {before:.0f} -> 0/absent")


def _check_counts(previous: Summary, current: Summary, errors: list[str]) -> None:
    """Absolute counts, each against a percentage of what it used to be."""
    budget = f"{COUNT_DROP_TOLERANCE:.0%}"
    for path in _RELATIVE_COUNTS:
        was, now = previous, current
        for parent in path[:-1]:
            was, now = _block(was, parent), _block(now, parent)
        before, after = _number(was, path[-1]), _number(now, path[-1])
        name = ".".join(path)
        if before is None:
            continue
        if after is None:
            errors.append(f"{name}: was {before:.0f}, absent now")
        elif round(before - after, _DELTA_PRECISION) > before * COUNT_DROP_TOLERANCE:
            errors.append(f"{name}: {before:.0f} -> {after:.0f}, over a {budget} drop")


def compare_summaries(previous: Summary, current: Summary) -> list[str]:
    """Reasons `current` must not replace `previous`. Empty list = publish."""
    errors: list[str] = []
    for check in (_check_coverage, _check_sources, _check_factors, _check_counts):
        check(previous, current, errors)
    return errors


def _load(path: Path) -> Summary:
    """A summary.json nobody can read is a comparison nobody can make."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"Regression guard: cannot read {path} — {e}") from e
    if not isinstance(data, dict):
        raise SystemExit(f"Regression guard: {path} is not a JSON object")
    return data


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python -m src.export.regression_guard <previous> <current>")
        sys.exit(1)
    errors = compare_summaries(_load(Path(sys.argv[1])), _load(Path(sys.argv[2])))
    if errors:
        print(f"Regression guard FAILED ({len(errors)} regression(s)):")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)
    print("Regression guard passed — no worse than what is published.")
    sys.exit(0)


if __name__ == "__main__":
    main()
