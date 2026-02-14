from src.plugins.gpx_activities.gpx_activities import parse_gpx_activities


def test_parse_gpx_activities_sorted_latest_first(tmp_path):
    gpx_content = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="pytest" xmlns="http://www.topografix.com/GPX/1/1">
  <trk>
    <name>Morning Run</name>
    <time>2026-02-10T06:00:00Z</time>
    <trkseg>
      <trkpt lat="48.8566" lon="2.3522"><time>2026-02-10T06:00:00Z</time></trkpt>
      <trkpt lat="48.8570" lon="2.3530"><time>2026-02-10T06:10:00Z</time></trkpt>
    </trkseg>
  </trk>
  <trk>
    <name>Evening Ride</name>
    <time>2026-02-11T18:00:00Z</time>
    <trkseg>
      <trkpt lat="48.8600" lon="2.3500"><time>2026-02-11T18:00:00Z</time></trkpt>
      <trkpt lat="48.8615" lon="2.3490"><time>2026-02-11T18:20:00Z</time></trkpt>
    </trkseg>
  </trk>
</gpx>
"""

    gpx_file = tmp_path / "activities.gpx"
    gpx_file.write_text(gpx_content)

    activities = parse_gpx_activities(str(gpx_file))

    assert len(activities) == 2
    assert activities[0].title == "Evening Ride"
    assert activities[1].title == "Morning Run"
    assert activities[0].distance_km > 0
    assert activities[1].distance_km > 0


def test_parse_gpx_activities_uses_first_point_time_when_track_time_missing(tmp_path):
    gpx_content = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="pytest" xmlns="http://www.topografix.com/GPX/1/1">
  <trk>
    <name>No Track Time</name>
    <trkseg>
      <trkpt lat="40.7128" lon="-74.0060"><time>2026-02-01T07:30:00Z</time></trkpt>
      <trkpt lat="40.7138" lon="-74.0050"><time>2026-02-01T07:50:00Z</time></trkpt>
    </trkseg>
  </trk>
</gpx>
"""

    gpx_file = tmp_path / "missing-track-time.gpx"
    gpx_file.write_text(gpx_content)

    activities = parse_gpx_activities(str(gpx_file))

    assert len(activities) == 1
    assert activities[0].start_dt is not None
    assert activities[0].start_dt.isoformat() == "2026-02-01T07:30:00+00:00"
