"""
State migration acceleration from Census ACS 1-Year estimates.

Uses table B07001 (Geographical Mobility in the Past Year) to compute
the rate of inward migration per state across two years, then derives
acceleration (change in rate). Positive acceleration = migration picking up.
"""

from src.ingest.census_api import CensusClient

# ACS 1-Year variables from B07001:
# B07001_001E = Total population 1 year and over
# B07001_065E = Moved from different state
ACS_VARS = "B07001_001E,B07001_065E"


def _fetch_acs_migration(client: CensusClient, year: int) -> dict[str, float]:
    """Fetch ACS 1-year B07001 and return {state_fips: inward_migration_rate}.

    Rate = (moved from different state) / (total pop 1yr+).
    """
    url = f"https://api.census.gov/data/{year}/acs/acs1?get=NAME,{ACS_VARS}&for=state:*"
    data = client.fetch(url, f"ACS {year} B07001", cache_key=f"acs_b07001_{year}")
    if data is None:
        return {}

    rates: dict[str, float] = {}
    headers = data[0] if data else []
    for row in data[1:]:
        rec = dict(zip(headers, row))
        state_fips = rec.get("state", "")
        total_str = rec.get("B07001_001E", "")
        moved_str = rec.get("B07001_065E", "")
        if not total_str or not moved_str or not state_fips:
            continue
        try:
            total = int(total_str)
            moved = int(moved_str)
            if total > 0:
                rates[state_fips] = moved / total * 100  # percentage
        except (ValueError, TypeError):
            continue

    return rates


def fetch_state_migration_accel(
    client: CensusClient,
    year_recent: int = 2023,
    year_prior: int = 2022,
) -> dict[str, float]:
    """Compute migration acceleration per state.

    Acceleration = recent_rate - prior_rate (percentage points).
    Positive = inward migration increasing. Returns {state_abbr: accel}.
    """
    from src.core.config import STATE_FIPS

    rates_recent = _fetch_acs_migration(client, year_recent)
    rates_prior = _fetch_acs_migration(client, year_prior)

    if not rates_recent or not rates_prior:
        return {}

    result: dict[str, float] = {}
    for state_fips, abbr in STATE_FIPS.items():
        r1 = rates_prior.get(state_fips)
        r2 = rates_recent.get(state_fips)
        if r1 is not None and r2 is not None:
            result[abbr] = round(r2 - r1, 4)

    return result


def get_state_migration_accel(
    state_abbr: str,
    migration_lookup: dict[str, float],
) -> float | None:
    """Get migration acceleration for a county's state.

    Returns None when the lookup has no entry for the state (source failed, or
    it never covered that state). 0.0 is a real observation — no acceleration —
    and is not what "missing" means.
    """
    return migration_lookup.get(state_abbr)
