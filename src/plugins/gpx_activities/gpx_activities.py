from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt
import logging
import os
import random
import xml.etree.ElementTree as ET

from plugins.base_plugin.base_plugin import BasePlugin
from utils.app_utils import get_fonts, resolve_path
from utils.image_utils import take_screenshot_html

logger = logging.getLogger(__name__)


@dataclass
class Activity:
    title: str
    start_dt: datetime | None
    distance_km: float
    duration_seconds: int | None
    segments: list[list[list[float]]]


GPX_NS = {
    "gpx": "http://www.topografix.com/GPX/1/1"
}

def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if dt.tzinfo is None:
        # Treat naive timestamps as UTC for deterministic ordering.
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a))


def parse_gpx_activities(gpx_file: str) -> list[Activity]:
    try:
        tree = ET.parse(gpx_file)
    except Exception as exc:
        logger.exception("Failed to parse GPX file: %s", gpx_file)
        raise RuntimeError("Invalid GPX file.") from exc

    root = tree.getroot()
    tracks = root.findall("gpx:trk", GPX_NS)
    activities: list[Activity] = []

    for index, trk in enumerate(tracks, start=1):
        title = (trk.findtext("gpx:name", default="", namespaces=GPX_NS) or "").strip() or f"Activity {index}"
        trk_time = parse_iso_datetime(trk.findtext("gpx:time", namespaces=GPX_NS))

        segments: list[list[list[float]]] = []
        first_point_time: datetime | None = None
        last_point_time: datetime | None = None
        total_distance_km = 0.0

        for trkseg in trk.findall("gpx:trkseg", GPX_NS):
            segment_points: list[list[float]] = []
            prev_lat = None
            prev_lon = None

            for trkpt in trkseg.findall("gpx:trkpt", GPX_NS):
                lat_attr = trkpt.attrib.get("lat")
                lon_attr = trkpt.attrib.get("lon")
                if lat_attr is None or lon_attr is None:
                    continue

                lat = float(lat_attr)
                lon = float(lon_attr)
                segment_points.append([lat, lon])

                if prev_lat is not None and prev_lon is not None:
                    total_distance_km += haversine_distance_km(prev_lat, prev_lon, lat, lon)
                prev_lat, prev_lon = lat, lon

                track_point_time = parse_iso_datetime(trkpt.findtext("gpx:time", namespaces=GPX_NS))
                if track_point_time:
                    if first_point_time is None or track_point_time < first_point_time:
                        first_point_time = track_point_time
                    if last_point_time is None or track_point_time > last_point_time:
                        last_point_time = track_point_time

            if segment_points:
                segments.append(segment_points)

        if not segments:
            continue

        start_dt = trk_time or first_point_time
        duration_seconds = None
        if first_point_time and last_point_time and last_point_time >= first_point_time:
            duration_seconds = int((last_point_time - first_point_time).total_seconds())

        activities.append(
            Activity(
                title=title,
                start_dt=start_dt,
                distance_km=total_distance_km,
                duration_seconds=duration_seconds,
                segments=segments,
            )
        )

    def sort_key(activity: Activity) -> float:
        if not activity.start_dt:
            return float("-inf")
        return activity.start_dt.timestamp()

    activities.sort(key=sort_key, reverse=True)
    return activities


def parse_multiple_gpx_activities(gpx_files: list[str]) -> list[Activity]:
    activities: list[Activity] = []
    for gpx_file in gpx_files:
        activities.extend(parse_gpx_activities(gpx_file))

    activities.sort(key=lambda activity: activity.start_dt.timestamp() if activity.start_dt else float("-inf"), reverse=True)
    return activities


def random_trace_color() -> str:
    # Keep colors saturated and moderately dark for strong contrast on map tiles.
    hue = random.random()
    saturation = random.uniform(0.60, 0.90)
    value = random.uniform(0.45, 0.72)

    i = int(hue * 6.0)
    f = hue * 6.0 - i
    p = value * (1.0 - saturation)
    q = value * (1.0 - f * saturation)
    t = value * (1.0 - (1.0 - f) * saturation)
    i %= 6

    if i == 0:
        r, g, b = value, t, p
    elif i == 1:
        r, g, b = q, value, p
    elif i == 2:
        r, g, b = p, value, t
    elif i == 3:
        r, g, b = p, q, value
    elif i == 4:
        r, g, b = t, p, value
    else:
        r, g, b = value, p, q

    return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))


class GpxActivities(BasePlugin):
    def generate_image(self, settings, device_config):
        gpx_files = self._normalize_gpx_files(settings)
        if not gpx_files:
            raise RuntimeError("At least one GPX file is required.")

        for gpx_file in gpx_files:
            if not os.path.isfile(gpx_file):
                raise RuntimeError(f"Configured GPX file is missing: {os.path.basename(gpx_file)}")

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        activities = parse_multiple_gpx_activities(gpx_files)
        if not activities:
            raise RuntimeError("No valid tracks found in GPX files.")

        map_segments: list[list[list[float]]] = []
        map_traces: list[dict] = []
        rendered_activities = []

        for activity in activities:
            color = random_trace_color()
            map_segments.extend(activity.segments)
            map_traces.append(
                {
                    "color": color,
                    "segments": activity.segments,
                }
            )
            rendered_activities.append(
                {
                    "title": activity.title,
                    "start": self._format_activity_start(activity.start_dt),
                    "distance": f"{activity.distance_km:.1f} km",
                    "duration": self._format_duration(activity.duration_seconds),
                    "color": color,
                }
            )

        if not map_segments:
            raise RuntimeError("No track points found in GPX file.")

        all_points = [point for segment in map_segments for point in segment]
        min_lat = min(point[0] for point in all_points)
        max_lat = max(point[0] for point in all_points)
        min_lon = min(point[1] for point in all_points)
        max_lon = max(point[1] for point in all_points)

        template_params = {
            "style_sheets": [
                os.path.join(self.render_dir, "gpx_activities.css"),
            ],
            "font_faces": get_fonts(),
            "width": dimensions[0],
            "height": dimensions[1],
            "map_traces": map_traces,
            "activities": rendered_activities,
            "bounds": {
                "south": min_lat,
                "west": min_lon,
                "north": max_lat,
                "east": max_lon,
            },
            "static_dir": resolve_path("static"),
        }

        template = self.env.get_template("gpx_activities.html")
        rendered_html = template.render(template_params)
        image = take_screenshot_html(rendered_html, dimensions, timeout_ms=15000)

        if not image:
            raise RuntimeError("Failed to render GPX map. Check Chromium availability and network access.")

        return image

    def cleanup(self, settings):
        for gpx_file in self._normalize_gpx_files(settings):
            if gpx_file and os.path.exists(gpx_file):
                try:
                    os.remove(gpx_file)
                    logger.info("Deleted GPX file: %s", gpx_file)
                except Exception as exc:
                    logger.warning("Failed to delete GPX file %s: %s", gpx_file, exc)

    @staticmethod
    def _format_activity_start(start_dt: datetime | None) -> str:
        if not start_dt:
            return "Unknown start"

        local_dt = start_dt.astimezone()
        return local_dt.strftime("%Y-%m-%d %H:%M")

    @staticmethod
    def _format_duration(duration_seconds: int | None) -> str:
        if duration_seconds is None:
            return "Unknown duration"

        if duration_seconds < 60:
            return f"{duration_seconds}s"

        hours, remainder = divmod(duration_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        if hours > 0:
            if seconds > 0:
                return f"{hours}h {minutes:02d}m {seconds:02d}s"
            return f"{hours}h {minutes:02d}m"

        if seconds > 0:
            return f"{minutes}m {seconds:02d}s"
        return f"{minutes}m"

    @staticmethod
    def _normalize_gpx_files(settings) -> list[str]:
        # Preferred key for multi-upload mode.
        value = settings.get("gpxFiles[]")
        if value is None:
            # Backward compatibility with old single-file setting.
            value = settings.get("gpxFile")

        if value is None:
            return []
        if isinstance(value, list):
            return [path for path in value if path]
        return [value] if value else []
