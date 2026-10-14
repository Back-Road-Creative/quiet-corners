"""
Building permit trend data from Census Building Permits Survey (BPS).

Fetches county-level residential building permits for multiple years
to identify areas with accelerating construction activity (sprawl signal).

Source: the keyless county annual bulk files on www2.census.gov/econ/bps/
(co{year}a.txt), downloaded by --fetch via the data downloader. The keyed
api.census.gov timeseries dataset is retired — this module reads only disk.
"""

import csv
from pathlib import Path

from src.ingest.consumption import consumed_open

BPS_YEARS = [2020, 2021, 2022, 2023, 2024]

# First-header-line group labels whose "Units" column counts toward the total;
# the "... rep" groups are a reported subset — counting them double-counts.
_UNIT_GROUPS = {"1-unit", "2-units", "3-4 units", "5+ units"}


def bps_county_filename(year: int) -> str:
    """Bulk-file name for one BPS county annual vintage (e.g. co2024a.txt)."""
    return f"co{year}a.txt"


def load_bps_county_csv(path: Path) -> dict[str, int]:
    """Parse one BPS county annual file. Returns {fips: permit_count}.

    Comma-separated; two header lines (group labels sit over each group's
    Units column) and sometimes a blank spacer row before the data.
    """
    if not path.exists():
        return {}

    with consumed_open(path, newline="", encoding="latin-1") as f:
        rows = list(csv.reader(f))
    if len(rows) < 2:
        return {}
    groups = [g.strip() for g in rows[0]]
    columns = [c.strip() for c in rows[1]]
    unit_cols = [
        i
        for i, (grp, col) in enumerate(zip(groups, columns))
        if col == "Units" and grp in _UNIT_GROUPS
    ]
    if not unit_cols or "State" not in columns or "County" not in columns:
        return {}
    state_col = columns.index("State")
    county_col = columns.index("County")

    result: dict[str, int] = {}
    for row in rows[2:]:
        if len(row) <= max(unit_cols):
            continue  # spacer / malformed row
        state_fips = row[state_col].strip()
        county_fips = row[county_col].strip()
        if not state_fips or not county_fips:
            continue
        try:
            # BPS codes come back unpadded; pad or the FIPS never joins county.fips
            fips = f"{state_fips.zfill(2)}{county_fips.zfill(3)}"
            permits = sum(int(row[i]) for i in unit_cols)
            result[fips] = max(0, permits)  # Negative values = corrections
        except (ValueError, TypeError):
            continue

    return result


def fetch_building_permits(
    data_dir: Path,
    years: list[int] | None = None,
) -> dict[str, list[int | None]]:
    """Load multi-year building permit data per county from the bulk files.

    Returns {fips: [permits_year0, permits_year1, ...]} ordered by year.
    A county absent from a year's file has no observation for that year: it
    gets None there, not 0. A reported 0 stays 0 — the two are different
    facts, and the scorer records a gapped series as "partial" evidence.
    """
    years = years or BPS_YEARS

    yearly_data: list[dict[str, int]] = []
    for year in years:
        yearly_data.append(load_bps_county_csv(data_dir / bps_county_filename(year)))

    if not any(yearly_data):
        return {}

    # Collect all FIPS that appear in any year
    all_fips: set[str] = set()
    for yd in yearly_data:
        all_fips.update(yd.keys())

    # Build per-county timeseries
    result: dict[str, list[int | None]] = {}
    for fips in all_fips:
        series = [yd.get(fips) for yd in yearly_data]
        result[fips] = series

    return result
