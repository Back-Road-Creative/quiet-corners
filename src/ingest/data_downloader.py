"""
Data downloader: fetches Census and USDA data files.

Replaces the shell-based download_data.sh with Python for portability
and integration with the caching system.
"""

import csv
import hashlib
import io
import os
import tempfile
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

import requests

from src.ingest.building_permits import BPS_YEARS, bps_county_filename
from src.ingest.consumption import consumed_open

if TYPE_CHECKING:
    from src.core.models import Metro

# Just under a calendar quarter. The monthly cron (`0 6 1 * *`, see CLAUDE.md)
# passes --force, so its runs always re-download; this TTL governs manual
# --fetch runs, which may legitimately reuse a file up to this age.
DEFAULT_MAX_AGE_DAYS = 80

DOWNLOADS: dict[str, str] = {
    # County populations
    "co-est2024-alldata.csv": (
        "https://www2.census.gov/programs-surveys/popest/datasets/"
        "2020-2024/counties/totals/co-est2024-alldata.csv"
    ),
    # USDA County Typology Codes
    "county-typology.csv": (
        "https://www.ers.usda.gov/media/6174/ers-county-typology-codes-2025-edition.csv"
    ),
    # USDA Rural-Urban Continuum Codes 2023
    "ruralurbancodes2023.csv": (
        "https://www.ers.usda.gov/media/5768/2023-rural-urban-continuum-codes.csv"
    ),
    # Subcounty population estimates (candidate towns)
    "sub-est2024.csv": (
        "https://www2.census.gov/programs-surveys/popest/datasets/"
        "2020-2024/cities/totals/sub-est2024.csv"
    ),
    # Map data. Pinned to an exact release: this is the one download that ends
    # up served to browsers, and it comes from a CDN rather than Census.
    "counties-10m.json": (
        "https://cdn.jsdelivr.net/npm/us-atlas@3.0.1/counties-10m.json"
    ),
    # Building permits: keyless BPS county annual files (keyed dataset retired)
    **{
        bps_county_filename(y): f"https://www2.census.gov/econ/bps/County/co{y}a.txt"
        for y in BPS_YEARS
    },
}

# Expected sha256 for downloads whose bytes must not change without someone
# deciding they should. A pin alone still trusts whatever the CDN answers with;
# the digest is what makes a substituted payload fail loudly. Recorded from
# us-atlas@3.0.1 on 2026-07-31 and confirmed byte-identical to the copy already
# committed in the site repo. Update it deliberately when refreshing the pin.
DOWNLOAD_SHA256: dict[str, str] = {
    # The pragma is for detect-secrets, which reads any 64-char hex string as a
    # possible credential. This one is a published file's digest — the whole
    # point is that it is public and pinned.
    "counties-10m.json": (
        "145aaf5d1433352a6a1d8e86b5f149c7c653f9171baf14aaf75ee66575def1b0"  # pragma: allowlist secret
    ),
}

GAZETTEER_ZIPS: dict[str, str] = {
    "2024_Gaz_counties_national.txt": (
        "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
        "2024_Gazetteer/2024_Gaz_counties_national.zip"
    ),
    "2024_Gaz_place_national.txt": (
        "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
        "2024_Gazetteer/2024_Gaz_place_national.zip"
    ),
}


def _is_fresh(dest: Path, max_age_days: float) -> bool:
    """True if dest exists and was written within max_age_days."""
    if not dest.exists():
        return False
    age_seconds = time.time() - dest.stat().st_mtime
    return age_seconds < max_age_days * 86400


def _looks_like_error_page(resp: "requests.Response") -> bool:
    """True if the server served HTML (e.g. an error page) as HTTP 200."""
    return "html" in resp.headers.get("content-type", "").lower()


def _atomic_write(dest: Path, data: bytes) -> None:
    """Write via temp file + rename so an interrupted download can't leave a partial file dest.exists() later treats as done."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=dest.parent, prefix=f".{dest.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_name, dest)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _safe_extract(zf: zipfile.ZipFile, target_dir: Path) -> None:
    """Extract zf into target_dir, refusing zip-slip members that would land outside it."""
    resolved_target = target_dir.resolve()
    for member in zf.infolist():
        member_path = (resolved_target / member.filename).resolve()
        if (
            resolved_target != member_path
            and resolved_target not in member_path.parents
        ):
            raise ValueError(f"unsafe zip member path: {member.filename!r}")
    zf.extractall(resolved_target)  # noqa: S202 -- members validated above


def download_file(
    url: str,
    dest: Path,
    timeout: int = 60,
    max_age_days: float = DEFAULT_MAX_AGE_DAYS,
    force: bool = False,
    expected_sha256: str | None = None,
) -> bool:
    """Download url to dest. Skips the request when dest is younger than
    max_age_days, unless force=True. Returns True on success.

    When expected_sha256 is given, content that does not match it is discarded
    without being written — verification happens before the file exists, so a
    substituted payload never reaches the pipeline or a later freshness skip.
    """
    if not force and _is_fresh(dest, max_age_days):
        return True

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        resp = requests.get(url, timeout=timeout, stream=True)
        resp.raise_for_status()
        if _looks_like_error_page(resp):
            return False
        content = resp.content
        if not content:
            return False
        if expected_sha256 is not None:
            actual = hashlib.sha256(content).hexdigest()
            if actual != expected_sha256:
                print(
                    f"  {dest.name}: checksum mismatch — expected "
                    f"{expected_sha256[:12]}…, got {actual[:12]}…; discarded"
                )
                return False
        _atomic_write(dest, content)
        return True
    except Exception:
        return False


def download_and_unzip(
    url: str,
    dest: Path,
    timeout: int = 60,
    max_age_days: float = DEFAULT_MAX_AGE_DAYS,
    force: bool = False,
) -> bool:
    """Download a zip and extract it next to dest (the expected extracted
    file). Skips like download_file; returns True only once dest exists."""
    if not force and _is_fresh(dest, max_age_days):
        return True

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        if _looks_like_error_page(resp):
            return False
        buf = io.BytesIO(resp.content)
        if not zipfile.is_zipfile(buf):
            return False
        buf.seek(0)
        with zipfile.ZipFile(buf) as zf:
            _safe_extract(zf, dest.parent)
        return dest.exists()
    except Exception:
        return False


def download_all(data_dir: Path, force: bool = False) -> dict[str, bool]:
    """Download all required data files. Returns {filename: success}."""
    results: dict[str, bool] = {}

    for filename, url in DOWNLOADS.items():
        dest = data_dir / filename
        results[filename] = download_file(
            url, dest, force=force, expected_sha256=DOWNLOAD_SHA256.get(filename)
        )

    for filename, url in GAZETTEER_ZIPS.items():
        dest = data_dir / filename
        results[filename] = download_and_unzip(url, dest, force=force)

    return results


def load_county_gazetteer(
    gazetteer_path: Path,
) -> dict[str, dict[str, float]]:
    """Parse county gazetteer TSV into {fips: {lat, lon}}.

    File format: tab-separated with GEOID, INTPTLAT, INTPTLONG columns.
    """
    if not gazetteer_path.exists():
        return {}

    result: dict[str, dict[str, float]] = {}
    with consumed_open(gazetteer_path, newline="", encoding="utf-8-sig") as f:
        # Strip whitespace from headers and values (Census gazetteers
        # have trailing whitespace in column names and some values)
        first_line = f.readline()
        headers = [h.strip() for h in first_line.split("\t")]
        reader = csv.DictReader(f, fieldnames=headers, delimiter="\t")
        for row in reader:
            stripped = {k: (v.strip() if v else "") for k, v in row.items()}
            geoid = stripped.get("GEOID", "").zfill(5)
            lat_str = stripped.get("INTPTLAT", "")
            lon_str = stripped.get("INTPTLONG", "")
            if lat_str and lon_str:
                try:
                    result[geoid] = {
                        "lat": float(lat_str),
                        "lon": float(lon_str),
                    }
                except (ValueError, TypeError):
                    continue
    return result


def load_rucc_codes(rucc_path: Path) -> dict[str, int]:
    """Parse USDA Rural-Urban Continuum Codes CSV into {fips: code}."""
    if not rucc_path.exists():
        return {}

    result: dict[str, int] = {}
    with consumed_open(rucc_path, newline="", encoding="latin-1") as f:
        reader = csv.DictReader(f)
        for row in reader:
            fips = row.get("FIPS", "").zfill(5)

            # Long format: Attribute/Value columns
            attr = row.get("Attribute", "")
            val = row.get("Value", "")
            if attr and "RUCC" in attr and val and val.isdigit():
                result[fips] = int(val)
                continue

            # Wide format: RUCC_2023 or RUCC_2013 column
            code = row.get("RUCC_2023", row.get("RUCC_2013", ""))
            if code and code.isdigit():
                result[fips] = int(code)
    return result


def load_county_typology(
    typology_path: Path,
) -> dict[str, dict[str, bool]]:
    """Parse USDA County Typology Codes CSV.

    Returns {fips: {recreation: bool, retirement: bool}}.
    Supports both the 2025 long format (Attribute/Value columns)
    and the legacy 2015 wide format (Recreation/Retirement columns).
    """
    if not typology_path.exists():
        return {}

    result: dict[str, dict[str, bool]] = {}
    with consumed_open(typology_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            fips = row.get("FIPStxt", row.get("FIPS", "")).zfill(5)
            if not fips or len(fips) != 5:
                continue

            # 2025 long format: one attribute per row
            attr = row.get("Attribute", "")
            val = row.get("Value", "")
            if attr:
                result.setdefault(fips, {"recreation": False, "retirement": False})
                if "Recreation" in attr and val == "1":
                    result[fips]["recreation"] = True
                elif "Retirement" in attr and val == "1":
                    result[fips]["retirement"] = True
                continue

            # Legacy wide format
            rec_val = row.get("Recreation", row.get("Recreational", "0"))
            ret_val = row.get("Retirement", row.get("RetirementDestination", "0"))
            result[fips] = {
                "recreation": str(rec_val).strip() == "1",
                "retirement": str(ret_val).strip() == "1",
            }
    return result


def load_metros_from_gazetteer(
    place_gazetteer_path: Path,
    min_pop: int = 25_000,
    estimates_path: Path | None = None,
) -> list["Metro"]:
    """Load cities >= min_pop by joining gazetteer coords with population estimates.

    The place gazetteer has coordinates but no population column.
    The subcounty estimates (sub-est2024.csv) have population but no coordinates.
    This function joins them on GEOID to produce a complete metro list.

    Falls back to gazetteer-only parsing if estimates_path is not provided
    (in case a future gazetteer includes a POP column).
    """
    from src.core.models import Metro

    if not place_gazetteer_path.exists():
        return []

    # Step 1: Load coordinates from gazetteer (GEOID -> lat, lon, name)
    coords: dict[str, tuple[str, float, float]] = {}
    gazetteer_pops: dict[str, int] = {}
    with consumed_open(place_gazetteer_path, newline="", encoding="utf-8-sig") as f:
        first_line = f.readline()
        headers = [h.strip() for h in first_line.split("\t")]
        reader = csv.DictReader(f, fieldnames=headers, delimiter="\t")
        for row in reader:
            row = {k: (v.strip() if v else "") for k, v in row.items()}
            geoid = row.get("GEOID", "")
            lat_str = row.get("INTPTLAT", "")
            lon_str = row.get("INTPTLONG", "")
            name = row.get("NAME", "")

            # Try gazetteer-only path if POP column exists
            pop_str = row.get("POP", row.get("POPULATION", ""))
            if pop_str and pop_str.isdigit():
                pop = int(pop_str)
                if pop >= min_pop and lat_str and lon_str and name:
                    coords[geoid] = (name, float(lat_str), float(lon_str))
                    gazetteer_pops[geoid] = pop
                continue

            if geoid and lat_str and lon_str and name:
                coords[geoid] = (name, float(lat_str), float(lon_str))

    # If gazetteer had POP column, build directly
    if gazetteer_pops:
        metros: list[Metro] = []
        for geoid, pop in gazetteer_pops.items():
            name, lat, lon = coords[geoid]
            metros.append(Metro(name=name, lat=lat, lon=lon, population=pop))
        return metros

    # Step 2: Load populations from subcounty estimates
    if estimates_path is None or not estimates_path.exists():
        return []

    metros = []
    with consumed_open(estimates_path, newline="", encoding="latin-1") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("SUMLEV") != "162":
                continue
            state_fips = row.get("STATE", "").zfill(2)
            place_fips = row.get("PLACE", "").zfill(5)
            geoid = state_fips + place_fips
            pop_str = row.get("POPESTIMATE2024", row.get("POPESTIMATE2023", ""))
            if not pop_str:
                continue
            try:
                pop = int(pop_str)
            except ValueError:
                continue  # one malformed cell must not abort the whole join
            if pop < min_pop:
                continue
            if geoid not in coords:
                continue
            name, lat, lon = coords[geoid]
            metros.append(Metro(name=name, lat=lat, lon=lon, population=pop))

    return metros
