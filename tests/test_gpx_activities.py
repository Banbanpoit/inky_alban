from src.plugins.gpx_activities.gpx_activities import (
    GpxActivities,
    decode_polyline,
    extract_points_from_gpx_bytes,
    extract_polyline_points,
    extract_start_coordinates,
    is_in_brussels,
)


def test_decode_polyline_basic():
    # Encoded polyline for [(38.5,-120.2),(40.7,-120.95),(43.252,-126.453)]
    encoded = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    points = decode_polyline(encoded)

    assert len(points) == 3
    assert abs(points[0][0] - 38.5) < 1e-5
    assert abs(points[0][1] + 120.2) < 1e-5


def test_extract_start_coordinates_from_activity_fields():
    activity = {
        "startLatitude": 50.85,
        "startLongitude": 4.36,
    }
    lat, lon = extract_start_coordinates(activity)

    assert lat == 50.85
    assert lon == 4.36


def test_extract_start_coordinates_from_summarydto():
    activity = {
        "summaryDTO": {
            "startLatitude": 50.84,
            "startLongitude": 4.35,
        }
    }
    lat, lon = extract_start_coordinates(activity)

    assert lat == 50.84
    assert lon == 4.35


def test_is_in_brussels_bbox():
    assert is_in_brussels(50.85, 4.36) is True
    assert is_in_brussels(50.7, 4.36) is False


def test_extract_polyline_points_from_nested_polyline_dto():
    details = {
        "geoPolylineDTO": {
            "polyline": "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
        }
    }

    points = extract_polyline_points(details)
    assert len(points) == 3


def test_extract_points_from_gpx_bytes():
    gpx = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="pytest" xmlns="http://www.topografix.com/GPX/1/1">
  <trk>
    <trkseg>
      <trkpt lat="50.8503" lon="4.3517"></trkpt>
      <trkpt lat="50.8510" lon="4.3520"></trkpt>
    </trkseg>
  </trk>
</gpx>
"""
    points = extract_points_from_gpx_bytes(gpx)
    assert points == [[50.8503, 4.3517], [50.851, 4.352]]


def test_parse_min_distance_default_and_validation():
    assert GpxActivities._parse_min_distance(None) == 20.0
    assert GpxActivities._parse_min_distance("") == 20.0
    assert GpxActivities._parse_min_distance("15.5") == 15.5


def test_parse_min_distance_negative_raises():
    try:
        GpxActivities._parse_min_distance("-1")
    except RuntimeError as e:
        assert "cannot be negative" in str(e)
    else:
        assert False, "Expected RuntimeError"


def test_format_duration():
    assert GpxActivities._format_duration(59) == "59s"
    assert GpxActivities._format_duration(60) == "1m"
    assert GpxActivities._format_duration(3661) == "1h 01m"


def test_format_elevation_gain():
    assert GpxActivities._format_elevation_gain(512.4) == "512 m elev"
    assert GpxActivities._format_elevation_gain(None) == "Unknown elev"
