"""
Affordability data from Census ACS 5-Year estimates.

Uses table variables:
- B25077_001E: Median home value
- B25064_001E: Median gross rent
- B19013_001E: Median household income

Returns data keyed by 5-digit county FIPS.
"""

from src.ingest.census_api import CensusClient

ACS5_VARS = "B25077_001E,B25064_001E,B19013_001E"


def fetch_affordability_data(
    client: CensusClient,
    year: int = 2023,
) -> dict[str, dict]:
    """Fetch ACS 5-year affordability variables for all counties.

    Returns {fips: {home_value, rent, income}} where values are int or None.
    """
    url = (
        f"https://api.census.gov/data/{year}/acs/acs5"
        f"?get=NAME,{ACS5_VARS}&for=county:*&in=state:*"
    )
    data = client.fetch(
        url, f"ACS5 {year} affordability", cache_key=f"acs5_affordability_{year}"
    )
    if data is None:
        return {}

    result: dict[str, dict] = {}
    headers = data[0] if data else []
    for row in data[1:]:
        rec = dict(zip(headers, row))
        state_fips = rec.get("state", "").zfill(2)
        county_fips = rec.get("county", "").zfill(3)
        fips = state_fips + county_fips

        home_value = _parse_int(rec.get("B25077_001E"))
        rent = _parse_int(rec.get("B25064_001E"))
        income = _parse_int(rec.get("B19013_001E"))

        result[fips] = {
            "home_value": home_value,
            "rent": rent,
            "income": income,
        }

    return result


def _parse_int(val: str | None) -> int | None:
    """Parse Census API value to int, treating negatives and blanks as None."""
    if val is None:
        return None
    try:
        v = int(val)
        return v if v >= 0 else None
    except (ValueError, TypeError):
        return None
