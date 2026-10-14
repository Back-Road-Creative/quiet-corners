"""
County population ingestion from Census Bureau API and CSV fallback.

Produces County objects with population time series for stability scoring.
"""

import csv
from pathlib import Path

from src.core.config import STATE_FIPS
from src.core.models import County
from src.ingest.census_api import CensusClient
from src.ingest.consumption import consumed_open


def fetch_county_populations(
    client: CensusClient,
) -> list[list[str]] | None:
    """Fetch county populations from Census API (Vintage 2024: 2020-2024)."""
    url = (
        "https://api.census.gov/data/2024/pep/population"
        # NOTE: this API fallback still takes 2020 from the April 1 census base,
        # so its series carries the uneven first interval the CSV path no longer
        # has. Whether Vintage 2024 exposes a July-1 POP_2020 variable is
        # unverified (checking needs a key), and the CSV is the path every real
        # run takes — `--fetch` downloads co-est2024-alldata.csv.
        "?get=NAME,POP_2024,POP_2023,POP_2022,POP_2021,ESTIMATESBASE2020"
        "&for=county:*&in=state:*"
    )
    cache_key = "county_pops_v2024"  # gitleaks:allow — a cache filename, not a key
    return client.fetch(url, "County populations", cache_key=cache_key)


def load_county_populations_csv(
    csv_path: Path,
) -> list[list[str]] | None:
    """Load county populations from downloaded co-est2024-alldata.csv.

    Converts CSV to the same list-of-lists format as the Census API response
    so parse_county_data() works with either source.
    """
    if not csv_path.exists():
        return None

    header = [
        "NAME",
        "POP_2024",
        "POP_2023",
        "POP_2022",
        "POP_2021",
        "ESTIMATESBASE2020",
        "state",
        "county",
    ]
    rows: list[list[str]] = [header]

    with consumed_open(csv_path, newline="", encoding="latin-1") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Only county-level records (SUMLEV=050)
            if row.get("SUMLEV", "") != "050":
                continue
            rows.append(
                [
                    row.get("CTYNAME", ""),
                    row.get("POPESTIMATE2024", ""),
                    row.get("POPESTIMATE2023", ""),
                    row.get("POPESTIMATE2022", ""),
                    row.get("POPESTIMATE2021", ""),
                    # July 1, 2020 — the same reference date as every other
                    # year in the series. ESTIMATESBASE2020 is the April 1
                    # census base, so using it made the first interval ~1.25
                    # years while stability's regression and CV treat the five
                    # points as evenly spaced. Fall back to the base only when
                    # the estimate is absent.
                    row.get("POPESTIMATE2020", "") or row.get("ESTIMATESBASE2020", ""),
                    row.get("STATE", "").zfill(2),
                    row.get("COUNTY", "").zfill(3),
                ]
            )

    return rows if len(rows) > 1 else None


def parse_county_data(
    raw_data: list[list[str]],
    gazetteer: dict[str, dict[str, float]] | None = None,
    rucc_codes: dict[str, int] | None = None,
) -> list[County]:
    """Parse Census API response into County objects.

    Args:
        raw_data: Census API response (list-of-lists).
        gazetteer: {fips: {lat, lon}} from county gazetteer.
        rucc_codes: {fips: rucc_code} from USDA.
    """
    if not raw_data or len(raw_data) < 2:
        return []

    gaz = gazetteer or {}
    rucc = rucc_codes or {}
    headers = raw_data[0]
    counties: list[County] = []

    for row in raw_data[1:]:
        rec = dict(zip(headers, row))
        state_fips = rec.get("state", "")
        county_fips = rec.get("county", "")
        full_fips = state_fips + county_fips

        if state_fips not in STATE_FIPS:
            continue

        state_abbr = STATE_FIPS[state_fips]
        name = rec.get("NAME", "")

        # Build population series
        pop_series: dict[int, int] = {}
        for key in [
            "ESTIMATESBASE2020",
            "POP_2021",
            "POP_2022",
            "POP_2023",
            "POP_2024",
        ]:
            year_str = key.replace("POP_", "").replace("ESTIMATESBASE", "")
            if year_str.isdigit() and rec.get(key, ""):
                try:
                    pop_series[int(year_str)] = int(rec[key])
                except ValueError:
                    continue  # one malformed cell must not abort the parse

        latest_pop = pop_series.get(2024, pop_series.get(2023))

        gaz_info = gaz.get(full_fips, {})
        lat = gaz_info.get("lat")
        lon = gaz_info.get("lon")

        counties.append(
            County(
                fips=full_fips,
                state=state_abbr,
                name=name,
                population=latest_pop,
                rucc=rucc.get(full_fips),
                lat=lat,
                lon=lon,
                pop_series=pop_series,
            )
        )

    return counties
