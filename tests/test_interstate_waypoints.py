"""
Tests for interstate corridor waypoint data and point-to-segment distance.
"""

from src.ingest.interstate_waypoints import (
    INTERSTATE_ROUTES,
    INTERSTATE_WAYPOINTS,
    distance_to_nearest_interstate,
)

# Towns that sit ON an interstate but are deliberately NOT waypoints —
# point-to-segment distance must stay small even midway between waypoints.
ON_INTERSTATE_TOWNS = [
    ("Miles City MT (I-94)", 46.4083, -105.8406),
    ("Kingman AZ (I-40)", 35.19, -114.05),
    ("Santa Rosa NM (I-40)", 34.94, -104.68),
    ("Tuskegee AL (I-85)", 32.44, -85.70),
    ("Elyria OH (I-80/I-90)", 41.37, -82.11),
]

# Open-water boxes (lat_min, lat_max, lon_min, lon_max) covering Great Lakes
# basins while excluding shoreline cities. No waypoint may fall inside one —
# this catches the class of error where (42.26, -81.52) shipped ~53mi
# offshore in Lake Erie.
GREAT_LAKES_OPEN_WATER = [
    ("Lake Erie (west basin)", 41.75, 42.70, -82.80, -81.05),
    ("Lake Erie (east basin)", 42.25, 42.85, -81.05, -79.35),
    ("Lake Michigan", 42.00, 45.50, -87.30, -86.40),
    ("Lake Huron", 44.00, 45.80, -83.00, -82.00),
    ("Lake Ontario", 43.35, 44.10, -79.60, -76.90),
    ("Lake Superior", 46.75, 48.70, -91.80, -84.80),
]

# Per-route corridor envelopes (lat_min, lat_max, lon_min, lon_max) with a
# 0.3-degree margin. A waypoint outside its route's envelope is on the wrong
# road — this catches the class of error where I-65 cities (Clanton,
# Birmingham) shipped inside the I-85 chain.
ROUTE_BBOXES = {
    "I-5": (32.2, 49.1, -123.6, -116.8),
    "I-10": (29.1, 34.3, -118.8, -81.4),
    "I-15": (32.4, 48.9, -118.0, -110.1),
    "I-16": (31.8, 33.1, -83.9, -80.8),
    "I-20": (32.1, 34.5, -101.8, -79.5),
    "I-24": (34.7, 38.0, -89.2, -85.0),
    "I-25": (32.0, 44.6, -107.3, -104.5),
    "I-29": (38.8, 48.9, -97.5, -94.3),
    "I-30": (32.5, 35.0, -97.4, -92.0),
    "I-35": (27.2, 47.1, -99.8, -91.8),
    "I-40": (34.6, 36.5, -117.3, -77.6),
    "I-44": (33.6, 38.9, -98.8, -89.9),
    "I-45": (29.0, 33.1, -97.1, -94.5),
    "I-55": (29.6, 42.2, -90.8, -87.3),
    "I-59": (30.0, 35.3, -90.1, -85.0),
    "I-64": (37.0, 38.9, -90.5, -76.3),
    "I-65": (30.4, 41.9, -88.3, -85.5),
    "I-70": (38.3, 40.7, -112.9, -76.3),
    "I-71": (37.9, 41.8, -86.1, -81.4),
    "I-75": (25.5, 46.8, -85.6, -79.9),
    "I-77": (33.7, 41.8, -82.0, -80.5),
    "I-80": (37.5, 42.0, -122.7, -73.8),
    "I-81": (35.7, 44.3, -83.7, -75.4),
    "I-84-E": (41.2, 42.4, -74.2, -71.9),
    "I-84-W": (42.3, 45.9, -123.0, -113.5),
    "I-85": (32.1, 37.5, -86.6, -77.1),
    "I-90": (41.2, 48.0, -122.6, -70.8),
    "I-94": (41.3, 47.2, -108.8, -82.1),
    "I-95": (25.5, 46.4, -82.0, -67.5),
    "I-96": (42.0, 43.5, -86.5, -82.8),
}


class TestInterstateWaypoints:
    """Interstate proximity via corridor waypoint chains."""

    def test_waypoints_populated(self):
        """Waypoint list has reasonable coverage."""
        assert len(INTERSTATE_WAYPOINTS) > 200

    def test_no_duplicate_waypoints(self):
        """The flat waypoint list carries no exact duplicates."""
        assert len(INTERSTATE_WAYPOINTS) == len(set(INTERSTATE_WAYPOINTS))

    def test_no_waypoint_in_open_water(self):
        """No waypoint sits in a Great Lake."""
        for name, lat_min, lat_max, lon_min, lon_max in GREAT_LAKES_OPEN_WATER:
            for lat, lon in INTERSTATE_WAYPOINTS:
                in_box = lat_min <= lat <= lat_max and lon_min <= lon <= lon_max
                assert not in_box, f"({lat}, {lon}) is open water: {name}"

    def test_route_bbox_coverage(self):
        """Every route chain has an envelope, and vice versa."""
        assert set(ROUTE_BBOXES) == set(INTERSTATE_ROUTES)

    def test_waypoints_within_route_bbox(self):
        """Every waypoint lies inside its own route's corridor envelope."""
        for route, chain in INTERSTATE_ROUTES.items():
            lat_min, lat_max, lon_min, lon_max = ROUTE_BBOXES[route]
            for lat, lon in chain:
                assert lat_min <= lat <= lat_max, f"{route}: bad lat ({lat}, {lon})"
                assert lon_min <= lon <= lon_max, f"{route}: bad lon ({lat}, {lon})"

    def test_chains_have_segments(self):
        """Each chain needs >= 2 points; the distance model walks segments."""
        for route, chain in INTERSTATE_ROUTES.items():
            assert len(chain) >= 2, f"{route} has no segment"

    def test_near_interstate_county(self):
        """County near I-40 in Oklahoma City area should be close."""
        # Oklahoma City is right on I-35/I-40
        dist = distance_to_nearest_interstate(35.47, -97.52)
        assert dist < 5.0, f"Expected <5mi, got {dist}"

    def test_remote_county(self):
        """County in remote Nevada should be far from interstates."""
        # Humboldt County, NV — deep basin and range
        dist = distance_to_nearest_interstate(41.0, -118.0)
        assert dist > 10.0, f"Expected >10mi, got {dist}"

    def test_on_interstate_towns_close(self):
        """Towns on an interstate score close even between waypoints."""
        for name, lat, lon in ON_INTERSTATE_TOWNS:
            # Guard: the test is void if the town becomes a waypoint.
            assert (lat, lon) not in INTERSTATE_WAYPOINTS, f"{name} is a waypoint"
            dist = distance_to_nearest_interstate(lat, lon)
            assert dist < 15.0, f"{name}: expected <15mi, got {dist}"

    def test_returns_float(self):
        """Distance is a rounded float."""
        dist = distance_to_nearest_interstate(35.0, -85.0)
        assert isinstance(dist, float)
