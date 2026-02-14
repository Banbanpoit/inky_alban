from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
import logging
import os
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
    point_count: int
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
        total_distance_km = 0.0
        total_points = 0

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
                total_points += 1

                if prev_lat is not None and prev_lon is not None:
                    total_distance_km += haversine_distance_km(prev_lat, prev_lon, lat, lon)
                prev_lat, prev_lon = lat, lon

                if first_point_time is None:
                    first_point_time = parse_iso_datetime(trkpt.findtext("gpx:time", namespaces=GPX_NS))

            if segment_points:
                segments.append(segment_points)

        if not segments:
            continue

        start_dt = trk_time or first_point_time
        activities.append(
            Activity(
                title=title,
                start_dt=start_dt,
                distance_km=total_distance_km,
                point_count=total_points,
                segments=segments,
            )
        )

    def sort_key(activity: Activity) -> float:
        if not activity.start_dt:
            return float("-inf")
        return activity.start_dt.timestamp()

    activities.sort(key=sort_key, reverse=True)
    return activities


class GpxActivities(BasePlugin):
    def generate_image(self, settings, device_config):
        gpx_file = settings.get("gpxFile")
        if not gpx_file:
            raise RuntimeError("GPX file is required.")

        if not os.path.isfile(gpx_file):
            raise RuntimeError("Configured GPX file is missing.")

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        activities = parse_gpx_activities(gpx_file)
        if not activities:
            raise RuntimeError("No valid tracks found in GPX file.")

        map_segments: list[list[list[float]]] = []
        for activity in activities:
            map_segments.extend(activity.segments)

        if not map_segments:
            raise RuntimeError("No track points found in GPX file.")

        all_points = [point for segment in map_segments for point in segment]
        min_lat = min(point[0] for point in all_points)
        max_lat = max(point[0] for point in all_points)
        min_lon = min(point[1] for point in all_points)
        max_lon = max(point[1] for point in all_points)

        rendered_activities = []
        for activity in activities:
            rendered_activities.append(
                {
                    "title": activity.title,
                    "start": self._format_activity_start(activity.start_dt),
                    "distance": f"{activity.distance_km:.1f} km",
                    "point_count": activity.point_count,
                }
            )

        template_params = {
            "style_sheets": [
                os.path.join(self.render_dir, "gpx_activities.css"),
            ],
            "font_faces": get_fonts(),
            "width": dimensions[0],
            "height": dimensions[1],
            "map_segments": map_segments,
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
        gpx_file = settings.get("gpxFile")
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
