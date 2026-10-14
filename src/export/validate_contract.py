"""Validate exported JSON files against the frontend contract.

Imports contract constants from src.export.contract (single source of truth).
Runnable standalone: python -m src.export.validate_contract <data_dir>
Exit code 0 = pass, 1 = fail.
"""

import json
import sys
from pathlib import Path

from src.export.contract import (
    CANDIDATE_ENRICHED_FIELDS,
    CANDIDATE_REQUIRED_FIELDS,
    COUNTY_REQUIRED_FIELDS,
    GEO_REQUIRED_FIELDS,
    GEOMETRY_COLLECTION_PATH,
    GEOMETRY_ID_LENGTH,
    NONNULL_FIELDS,
    PLACE_INDEX_ENTRY_FIELDS,
    PLACE_INDEX_FILE,
    PLACE_NATIONAL_FIELDS,
    PLACE_NATIONAL_FILE,
    PLACE_NATIONAL_HEAD,
    PLACE_SHARD_FIELDS,
    PLACE_SHARD_MAX_BYTES,
    PLACE_SHARD_REQUIRED_FIELDS,
    PLACE_SHARDS_MAX_TOTAL_BYTES,
    SCORE_RANGE_FIELDS,
    SPRAWL_FACTOR_FIELDS,
    SPRAWL_FACTOR_STATUS_KEY,
    SPRAWL_FACTOR_STATUS_VALUES,
    SUMMARY_REQUIRED_FIELDS,
    TOPOJSON_TYPE,
    place_shard_name,
)

GEOMETRY_FILE = "counties-10m.json"

# Files that contain arrays — every entry is validated
ARRAY_FILE_CONTRACTS: dict[str, set[str]] = {
    "counties.json": COUNTY_REQUIRED_FIELDS,
    "geo.json": GEO_REQUIRED_FIELDS,
    "candidates.json": CANDIDATE_REQUIRED_FIELDS | CANDIDATE_ENRICHED_FIELDS,
}

# Files that contain a top-level object
OBJECT_FILE_CONTRACTS: dict[str, set[str]] = {
    "summary.json": SUMMARY_REQUIRED_FIELDS,
    # Required but with no fixed keys: the keys are states. Each entry, and the
    # shard it names, is checked by _check_place_shards.
    PLACE_INDEX_FILE: set(),
}


def _validate_array_file(
    path: Path, required_fields: set[str], errors: list[str]
) -> None:
    """Validate a JSON file containing an array of objects."""
    if not path.exists():
        errors.append(f"{path.name}: file not found")
        return

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        errors.append(f"{path.name}: invalid JSON — {e}")
        return

    if not isinstance(data, list):
        errors.append(f"{path.name}: expected array, got {type(data).__name__}")
        return

    if len(data) == 0:
        errors.append(f"{path.name}: empty array")
        return

    score_fields = SCORE_RANGE_FIELDS.get(path.name, set())
    nonnull_fields = NONNULL_FIELDS.get(path.name, set())
    for i, entry in enumerate(data):
        missing = required_fields - set(entry.keys())
        if missing:
            errors.append(f"{path.name}[{i}]: missing fields: {sorted(missing)}")
            return
        for fld in sorted(score_fields | nonnull_fields):
            val = entry.get(fld)
            if val is None:
                if fld in nonnull_fields:
                    errors.append(f"{path.name}[{i}].{fld}: must not be null")
                    return
            elif fld in score_fields and not (
                isinstance(val, (int, float)) and 0 <= val <= 100
            ):
                errors.append(f"{path.name}[{i}].{fld}: not in 0-100: {val!r}")
                return
        # The evidence block is optional (published files predate it) but,
        # where present, must name every factor with a known status.
        if SPRAWL_FACTOR_STATUS_KEY in entry:
            block = entry[SPRAWL_FACTOR_STATUS_KEY]
            bad = (
                sorted(SPRAWL_FACTOR_FIELDS)
                if not isinstance(block, dict)
                else sorted(
                    n
                    for n in SPRAWL_FACTOR_FIELDS
                    if block.get(n) not in SPRAWL_FACTOR_STATUS_VALUES
                )
            )
            if bad:
                errors.append(
                    f"{path.name}[{i}].{SPRAWL_FACTOR_STATUS_KEY}: "
                    f"missing or unknown status for {bad}"
                )
                return
        # Sub-field check for counties.json sprawl_factors
        if path.name == "counties.json":
            sf = entry["sprawl_factors"]
            sf_missing = SPRAWL_FACTOR_FIELDS - set(sf if isinstance(sf, dict) else ())
            if sf_missing:
                errors.append(
                    f"{path.name}[{i}]: sprawl_factors missing: {sorted(sf_missing)}"
                )
                return


def _validate_object_file(
    path: Path, required_fields: set[str], errors: list[str]
) -> None:
    """Validate a JSON file containing a top-level object."""
    if not path.exists():
        errors.append(f"{path.name}: file not found")
        return

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        errors.append(f"{path.name}: invalid JSON — {e}")
        return

    if not isinstance(data, dict):
        errors.append(f"{path.name}: expected object, got {type(data).__name__}")
        return

    missing = required_fields - set(data.keys())
    if missing:
        errors.append(f"{path.name}: missing fields: {sorted(missing)}")


# Alaska split Valdez-Cordova Census Area (02261) into Chugach (02063) and
# Copper River (02066) in 2019. Census population/RUCC data uses the new areas;
# us-atlas — including 3.0.1, the newest release (2023-04) — still ships the
# pre-split 02261 polygon, so no upstream geometry exists for either successor.
# The dashboard renders a county with no polygon as a clickable centroid marker
# from geo.json, so these two are drawn and usable, just not shaded. Any OTHER
# missing FIPS is a real defect and still fails.
GEOMETRY_EXEMPT_FIPS = frozenset({"02063", "02066"})


def _geometry_ids(topo: object, errors: list[str]) -> set[str] | None:
    """Check counties-10m.json's own shape and return the FIPS it draws.

    The five exported files each have their structure checked; this one had
    nothing, because it is TopoJSON rather than a list of records. A truncated
    or restructured payload used to surface as a single "invalid TopoJSON
    structure", which cannot tell a corrupt file from a missing key. Returns
    None when the shape is wrong — the caller then has nothing to cross-check.
    """
    dotted = ".".join(GEOMETRY_COLLECTION_PATH)
    if not isinstance(topo, dict):
        errors.append(f"{GEOMETRY_FILE}: expected object, got {type(topo).__name__}")
        return None
    if topo.get("type") != TOPOJSON_TYPE:
        errors.append(
            f"{GEOMETRY_FILE}: not TopoJSON — type is {topo.get('type')!r}, "
            f"expected {TOPOJSON_TYPE!r}"
        )
        return None

    node: object = topo
    for key in GEOMETRY_COLLECTION_PATH:
        if not isinstance(node, dict) or key not in node:
            errors.append(f"{GEOMETRY_FILE}: missing {dotted}")
            return None
        node = node[key]
    if not isinstance(node, list):
        errors.append(f"{GEOMETRY_FILE}: {dotted} is not a list")
        return None
    if not node:
        errors.append(f"{GEOMETRY_FILE}: {dotted} is empty — no county is drawable")
        return None

    ids = [g.get("id") for g in node if isinstance(g, dict)]
    malformed = [
        i
        for i in ids
        if not (isinstance(i, str) and len(i) == GEOMETRY_ID_LENGTH and i.isdigit())
    ]
    if malformed:
        errors.append(
            f"{GEOMETRY_FILE}: {len(malformed)} of {len(node)} geometry ids are "
            f"not {GEOMETRY_ID_LENGTH}-digit FIPS strings: "
            f"{[repr(i) for i in malformed[:5]]}"
        )
        return None
    return {i for i in ids if isinstance(i, str)}


def _check_county_geometry(data_dir: Path, errors: list[str]) -> None:
    """Check the geometry file's own shape, then every counties.json FIPS."""
    topo_path = data_dir / GEOMETRY_FILE
    counties_path = data_dir / "counties.json"
    if not topo_path.exists():
        # Absence stays non-fatal: the file is linked into the staging dir for
        # validation and never promoted, so runs without it are legitimate.
        # Say so anyway — a silent return reads exactly like a clean check.
        print(f"  {GEOMETRY_FILE}: not present — county geometry not checked")
        return

    try:
        topo = json.loads(topo_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        errors.append(f"{GEOMETRY_FILE}: invalid JSON — {e}")
        return

    geometry_fips = _geometry_ids(topo, errors)
    if geometry_fips is None:
        return
    print(f"  {GEOMETRY_FILE}: {len(geometry_fips)} county geometries checked")

    if not counties_path.exists():
        return  # absence is already reported by the per-file check
    try:
        counties = json.loads(counties_path.read_text())
    except (OSError, json.JSONDecodeError):
        return  # malformed files are already reported by the per-file check
    if not isinstance(counties, list):
        return

    orphans = (
        {c.get("fips") for c in counties if isinstance(c, dict)}
        - geometry_fips
        - GEOMETRY_EXEMPT_FIPS
    )
    orphans.discard(None)
    if orphans:
        listed = sorted(str(f) for f in orphans)
        errors.append(
            f"{GEOMETRY_FILE}: no geometry for {len(orphans)} "
            f"counties.json fips: {listed[:5]}"
        )


# Generous envelope around the 50 states plus territories the export can carry.
# Wide enough that a real centroid never trips it, tight enough to catch the
# failure this guards: a 0.0/0.0 or sign-flipped coordinate that puts a county
# in the Gulf of Guinea and renders a marker in the ocean.
#
# Longitude needs two bands, not one. Aleutians West (02016) crosses the
# antimeridian and its centroid is at +179.6 — a real county with a positive
# longitude. A single [-180, -64] band rejected the live export.
US_LAT_RANGE = (17.0, 72.0)
US_LON_BANDS = ((-180.0, -64.0), (172.0, 180.0))


def check_cross_file_agreement(data_dir: Path, errors: list[str]) -> None:
    """counties.json and geo.json must describe the same counties.

    Each file's own shape was checked and nothing between them, so an export
    where geo.json lost rows passed — every missing row a county the map cannot
    place. Also sanity-checks the coordinates themselves: geo.json is the
    fallback the dashboard uses for counties with no polygon, so a bad centroid
    there is a marker in the wrong hemisphere rather than a missing one.
    """
    counties_path = data_dir / "counties.json"
    geo_path = data_dir / "geo.json"
    if not counties_path.exists() or not geo_path.exists():
        return  # absence is already reported by the per-file checks

    try:
        counties = json.loads(counties_path.read_text())
        geo = json.loads(geo_path.read_text())
    except (OSError, json.JSONDecodeError):
        return  # malformed files are already reported by the per-file checks

    county_fips = {c.get("fips") for c in counties if isinstance(c, dict)}
    geo_fips = {g.get("fips") for g in geo if isinstance(g, dict)}

    missing = sorted(f for f in county_fips - geo_fips if f)
    if missing:
        errors.append(
            f"geo.json: no centroid for {len(missing)} counties.json fips: "
            f"{missing[:5]}"
        )
    orphan = sorted(f for f in geo_fips - county_fips if f)
    if orphan:
        errors.append(
            f"geo.json: {len(orphan)} fips absent from counties.json: {orphan[:5]}"
        )

    for entry in geo:
        if not isinstance(entry, dict):
            continue
        lat, lon = entry.get("lat"), entry.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            errors.append(f"geo.json[{entry.get('fips')}]: coordinates missing")
            continue
        in_lat = US_LAT_RANGE[0] <= lat <= US_LAT_RANGE[1]
        in_lon = any(lo <= lon <= hi for lo, hi in US_LON_BANDS)
        if not (in_lat and in_lon):
            errors.append(
                f"geo.json[{entry.get('fips')}]: coordinates outside the US "
                f"envelope: ({lat}, {lon})"
            )


def _load_json(path: Path, errors: list[str]) -> object | None:
    try:
        loaded: object = json.loads(path.read_text())
        return loaded
    except OSError:
        errors.append(f"{path.name}: file not found")
    except json.JSONDecodeError as e:
        errors.append(f"{path.name}: invalid JSON — {e}")
    return None


def _check_place_shards(data_dir: Path, errors: list[str]) -> None:
    """Every state the index lists must have a well-formed shard of that count."""
    index_path = data_dir / PLACE_INDEX_FILE
    if not index_path.exists():
        return  # absence is already reported by the per-file check
    try:
        index = json.loads(index_path.read_text())
    except json.JSONDecodeError:
        return  # malformed files are already reported by the per-file check
    if not isinstance(index, dict):
        return

    total_bytes = 0
    for state, entry in sorted(index.items()):
        if not isinstance(entry, dict) or PLACE_INDEX_ENTRY_FIELDS - set(entry):
            errors.append(
                f"{PLACE_INDEX_FILE}[{state}]: needs {sorted(PLACE_INDEX_ENTRY_FIELDS)}"
            )
            continue
        bbox = entry["bbox"]
        if not (
            isinstance(bbox, list)
            and len(bbox) == 4
            and all(isinstance(v, (int, float)) for v in bbox)
        ):
            errors.append(f"{PLACE_INDEX_FILE}[{state}].bbox: expected [w, s, e, n]")

        name = place_shard_name(state)
        path = data_dir / name
        if not path.exists():
            errors.append(f"{name}: file not found (listed in {PLACE_INDEX_FILE})")
            continue
        size = path.stat().st_size
        total_bytes += size
        if size > PLACE_SHARD_MAX_BYTES:
            errors.append(f"{name}: {size} bytes exceeds {PLACE_SHARD_MAX_BYTES}")
        shard = _load_json(path, errors)
        if shard is None:
            continue
        if not isinstance(shard, dict):
            errors.append(f"{name}: expected object, got {type(shard).__name__}")
            continue
        missing = PLACE_SHARD_REQUIRED_FIELDS - set(shard)
        if missing:
            errors.append(f"{name}: missing fields: {sorted(missing)}")
            continue
        if shard["state"] != state:
            errors.append(f"{name}: state is {shard['state']!r}, expected {state!r}")
        if shard["fields"] != list(PLACE_SHARD_FIELDS):
            errors.append(f"{name}: fields must be {list(PLACE_SHARD_FIELDS)}")
            continue
        places = shard["places"]
        if not isinstance(places, list) or len(places) != entry["count"]:
            got = len(places) if isinstance(places, list) else type(places).__name__
            errors.append(
                f"{name}: {got} places but {PLACE_INDEX_FILE} says {entry['count']}"
            )
            continue
        for i, rec in enumerate(places):
            pop = rec[4] if isinstance(rec, list) and len(rec) == 5 else 0
            if not (
                isinstance(rec, list)
                and len(rec) == len(PLACE_SHARD_FIELDS)
                and isinstance(rec[0], str)
                and isinstance(rec[1], str)
                and isinstance(rec[2], (int, float))
                and isinstance(rec[3], (int, float))
                and (pop is None or isinstance(pop, int))
            ):
                errors.append(f"{name}[{i}]: malformed place record {rec!r}")
                break
    if total_bytes > PLACE_SHARDS_MAX_TOTAL_BYTES:
        errors.append(
            f"place shards total {total_bytes} bytes exceeds "
            f"{PLACE_SHARDS_MAX_TOTAL_BYTES}"
        )
    _check_national_head(data_dir, index, errors)


def _check_national_head(data_dir: Path, index: dict, errors: list[str]) -> None:
    """places-national.json is a slice of the shards: every row is in its
    state's shard with the same record, rows run largest first, and the whole
    file is one fetch (PLACE_SHARD_MAX_BYTES) of at most PLACE_NATIONAL_HEAD."""
    name = PLACE_NATIONAL_FILE
    path = data_dir / name
    if not path.exists():
        errors.append(f"{name}: file not found")
        return
    if path.stat().st_size > PLACE_SHARD_MAX_BYTES:
        errors.append(
            f"{name}: {path.stat().st_size} bytes exceeds {PLACE_SHARD_MAX_BYTES}"
        )
    head = _load_json(path, errors)
    if head is None:
        return
    if not isinstance(head, dict) or head.get("fields") != list(PLACE_NATIONAL_FIELDS):
        errors.append(f"{name}: fields must be {list(PLACE_NATIONAL_FIELDS)}")
        return
    rows = head.get("places")
    if not isinstance(rows, list):
        errors.append(f"{name}: places must be a list")
        return
    if len(rows) > PLACE_NATIONAL_HEAD:
        errors.append(f"{name}: {len(rows)} rows exceeds {PLACE_NATIONAL_HEAD}")
    shard_rows: dict[str, dict[str, list]] = {}
    last_pop: int | None = None
    for i, rec in enumerate(rows):
        if not (
            isinstance(rec, list)
            and len(rec) == len(PLACE_NATIONAL_FIELDS)
            and isinstance(rec[4], int)
            and isinstance(rec[5], str)
        ):
            errors.append(f"{name}[{i}]: malformed head record {rec!r}")
            return
        state = rec[5]
        if state not in shard_rows:
            shard = (
                _load_json(data_dir / place_shard_name(state), errors)
                if state in index
                else None
            )
            places = shard.get("places") if isinstance(shard, dict) else None
            shard_rows[state] = {
                r[0]: r for r in (places or []) if isinstance(r, list) and r
            }
        if shard_rows[state].get(rec[0]) != rec[:5]:
            errors.append(
                f"{name}[{i}]: geoid {rec[0]} is not in {place_shard_name(state)} "
                "with this record"
            )
            return
        if last_pop is not None and rec[4] > last_pop:
            errors.append(f"{name}[{i}]: out of rank order ({rec[4]} after {last_pop})")
            return
        last_pop = rec[4]


def check_place_shard_completeness(
    data_dir: Path, gazetteer_path: Path, errors: list[str]
) -> None:
    """The shard record total must equal the gazetteer rows that have
    coordinates, so no population window or filter can drop a town."""
    from src.export.navigation_places import gazetteer_located_count

    index_path = data_dir / PLACE_INDEX_FILE
    if not index_path.exists():
        return  # absence is already reported by the per-file check
    try:
        index = json.loads(index_path.read_text())
        shard_total = sum(e["count"] for e in index.values())
    except (json.JSONDecodeError, TypeError, KeyError, AttributeError):
        return  # malformed files are already reported by _check_place_shards
    expected = gazetteer_located_count(gazetteer_path)
    if shard_total != expected:
        errors.append(
            f"{PLACE_INDEX_FILE}: shards hold {shard_total} places but the "
            f"gazetteer has {expected} with coordinates"
        )


def validate_directory(data_dir: Path) -> list[str]:
    """Validate all exported JSON files in a directory. Returns list of errors."""
    errors: list[str] = []

    for filename, fields in ARRAY_FILE_CONTRACTS.items():
        _validate_array_file(data_dir / filename, fields, errors)

    for filename, fields in OBJECT_FILE_CONTRACTS.items():
        _validate_object_file(data_dir / filename, fields, errors)

    _check_county_geometry(data_dir, errors)
    check_cross_file_agreement(data_dir, errors)
    _check_place_shards(data_dir, errors)
    return errors


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python -m src.export.validate_contract <data_dir>")
        sys.exit(1)

    data_dir = Path(sys.argv[1])
    if not data_dir.is_dir():
        print(f"Error: {data_dir} is not a directory")
        sys.exit(1)

    errors = validate_directory(data_dir)
    if errors:
        print(f"Contract validation FAILED ({len(errors)} error(s)):")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)
    else:
        print("Contract validation passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
