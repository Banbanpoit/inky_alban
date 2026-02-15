from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import os
import random
from typing import Any
import xml.etree.ElementTree as ET

from plugins.base_plugin.base_plugin import BasePlugin
from utils.app_utils import get_fonts, resolve_path
from utils.image_utils import take_screenshot_html

logger = logging.getLogger(__name__)

# Brussels-Capital Region bounding box
BRUSSELS_MIN_LAT = 50.796
BRUSSELS_MAX_LAT = 50.914
BRUSSELS_MIN_LON = 4.244
BRUSSELS_MAX_LON = 4.486
GPX_NS = {"gpx": "http://www.topografix.com/GPX/1/1"}


@dataclass
class Activity:
    title: str
    start_dt: datetime | None
    distance_km: float
    duration_seconds: int | None
    points: list[list[float]]


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


def parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None

    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def decode_polyline(encoded: str) -> list[list[float]]:
    """Decode Google encoded polyline to [lat, lon] pairs."""
    if not encoded:
        return []

    points = []
    lat = 0
    lon = 0
    index = 0

    while index < len(encoded):
        shift = 0
        result = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlat = ~(result >> 1) if (result & 1) else (result >> 1)
        lat += dlat

        shift = 0
        result = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlon = ~(result >> 1) if (result & 1) else (result >> 1)
        lon += dlon

        points.append([lat / 1e5, lon / 1e5])

    return points


def extract_start_coordinates(activity: dict[str, Any]) -> tuple[float | None, float | None]:
    candidates = [
        (activity.get("startLatitude"), activity.get("startLongitude")),
        (activity.get("beginLatitude"), activity.get("beginLongitude")),
        (activity.get("startLatitudeDegrees"), activity.get("startLongitudeDegrees")),
    ]

    summary = activity.get("summaryDTO") if isinstance(activity.get("summaryDTO"), dict) else {}
    candidates.append((summary.get("startLatitude"), summary.get("startLongitude")))

    for lat, lon in candidates:
        try:
            if lat is None or lon is None:
                continue
            return float(lat), float(lon)
        except (TypeError, ValueError):
            continue

    return None, None


def is_in_brussels(lat: float | None, lon: float | None) -> bool:
    if lat is None or lon is None:
        return False
    return BRUSSELS_MIN_LAT <= lat <= BRUSSELS_MAX_LAT and BRUSSELS_MIN_LON <= lon <= BRUSSELS_MAX_LON


def extract_polyline_points(details: dict[str, Any]) -> list[list[float]]:
    if not isinstance(details, dict):
        return []

    for key in ["geoPolylineDTO", "polylineDTO"]:
        value = details.get(key)
        if isinstance(value, dict):
            encoded = value.get("polyline") or value.get("encodedPolyline")
            if encoded:
                points = decode_polyline(encoded)
                if points:
                    return points

    encoded = details.get("polyline") or details.get("encodedPolyline")
    if isinstance(encoded, str) and encoded:
        points = decode_polyline(encoded)
        if points:
            return points

    # Fallback: chart data arrays if available.
    metric_desc = details.get("metricDescriptors")
    activity_detail_metrics = details.get("activityDetailMetrics")
    if isinstance(metric_desc, list) and isinstance(activity_detail_metrics, list):
        lat_idx = None
        lon_idx = None
        for idx, metric in enumerate(metric_desc):
            if not isinstance(metric, dict):
                continue
            key = metric.get("metricsIndex") or metric.get("key") or metric.get("displayKey")
            unit = str(metric.get("unit", "")).lower()
            text = str(key).lower()
            if lat_idx is None and ("latitude" in text or "lat" == text):
                lat_idx = idx
            if lon_idx is None and ("longitude" in text or "lon" == text or "lng" == text):
                lon_idx = idx
            if lat_idx is None and "degree" in unit and "lat" in text:
                lat_idx = idx
            if lon_idx is None and "degree" in unit and ("lon" in text or "lng" in text):
                lon_idx = idx

        if lat_idx is not None and lon_idx is not None:
            points: list[list[float]] = []
            for row in activity_detail_metrics:
                if not isinstance(row, dict):
                    continue
                metrics = row.get("metrics")
                if not isinstance(metrics, list):
                    continue
                if lat_idx >= len(metrics) or lon_idx >= len(metrics):
                    continue
                try:
                    lat = float(metrics[lat_idx])
                    lon = float(metrics[lon_idx])
                    points.append([lat, lon])
                except (TypeError, ValueError):
                    continue
            if points:
                return points

    return []


def extract_points_from_gpx_bytes(gpx_data: bytes) -> list[list[float]]:
    if not gpx_data:
        return []

    try:
        root = ET.fromstring(gpx_data)
    except ET.ParseError:
        return []

    points: list[list[float]] = []
    trkpts = root.findall(".//gpx:trkpt", GPX_NS)
    if not trkpts:
        # Fallback for non-namespaced GPX exports.
        trkpts = root.findall(".//trkpt")

    for trkpt in trkpts:
        lat_attr = trkpt.attrib.get("lat")
        lon_attr = trkpt.attrib.get("lon")
        if lat_attr is None or lon_attr is None:
            continue
        try:
            points.append([float(lat_attr), float(lon_attr)])
        except (TypeError, ValueError):
            continue
    return points


class GpxActivities(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params["style_settings"] = False
        return template_params

    def generate_image(self, settings, device_config):
        if settings.get("gpxFiles[]") or settings.get("gpxFile"):
            raise RuntimeError("This plugin now uses Garmin Connect directly. Re-save the plugin instance with Garmin credentials.")

        email_key = settings.get("garminEmailKey")
        password_key = settings.get("garminPasswordKey")
        if not email_key or not password_key:
            raise RuntimeError("Garmin credentials are required. Save email and password in plugin settings.")

        email = device_config.load_env_key(email_key)
        password = device_config.load_env_key(password_key)
        if not email or not password:
            raise RuntimeError("Garmin credentials are missing from .env. Re-save credentials in plugin settings.")

        min_distance_km = self._parse_min_distance(settings.get("minDistanceKm"))

        activities = self._fetch_filtered_activities(email, password, min_distance_km)
        if not activities:
            raise RuntimeError("No matching road_biking activities found in Brussels for the last 6 months with current distance filter.")

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        map_traces: list[dict[str, Any]] = []
        rendered_activities = []

        for activity in activities:
            color = random_trace_color()
            if len(activity.points) > 1:
                map_traces.append({"color": color, "segments": [activity.points]})
            rendered_activities.append(
                {
                    "title": activity.title,
                    "start": self._format_activity_start(activity.start_dt),
                    "distance": f"{activity.distance_km:.1f} km",
                    "duration": self._format_duration(activity.duration_seconds),
                    "color": color,
                }
            )

        points = [point for activity in activities for point in activity.points]
        if points:
            min_lat = min(point[0] for point in points)
            max_lat = max(point[0] for point in points)
            min_lon = min(point[1] for point in points)
            max_lon = max(point[1] for point in points)
        else:
            # Fallback to Brussels bbox when no polyline data is available.
            min_lat, max_lat = BRUSSELS_MIN_LAT, BRUSSELS_MAX_LAT
            min_lon, max_lon = BRUSSELS_MIN_LON, BRUSSELS_MAX_LON

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
        image = take_screenshot_html(rendered_html, dimensions, timeout_ms=20000)

        if not image:
            raise RuntimeError("Failed to render Garmin map view. Check Chromium availability and network access.")

        return image

    def cleanup(self, settings):
        # Credentials are persisted in .env and may be reused by other instances.
        return

    def _fetch_filtered_activities(self, email: str, password: str, min_distance_km: float) -> list[Activity]:
        try:
            from garminconnect import Garmin
            from garminconnect import GarminConnectAuthenticationError, GarminConnectConnectionError, GarminConnectTooManyRequestsError
        except Exception as exc:
            raise RuntimeError("Missing dependency: garminconnect. Install requirements and restart.") from exc

        end_date = datetime.now().date()
        start_date = (datetime.now() - timedelta(days=183)).date()

        try:
            api = Garmin(email=email, password=password, return_on_mfa=True)
            login_result = api.login()

            if isinstance(login_result, tuple) and login_result and str(login_result[0]).lower() == "needs_mfa":
                raise RuntimeError("Garmin account requires MFA/challenge. This plugin currently supports non-interactive login only.")

            raw_activities = api.get_activities_by_date(
                startdate=start_date.isoformat(),
                enddate=end_date.isoformat(),
                activitytype="cycling",
            )
        except RuntimeError:
            raise
        except GarminConnectAuthenticationError as exc:
            raise RuntimeError("Garmin authentication failed. Check credentials.") from exc
        except GarminConnectTooManyRequestsError as exc:
            raise RuntimeError("Garmin API rate limit reached. Please try again later.") from exc
        except GarminConnectConnectionError as exc:
            raise RuntimeError("Garmin connection failed. Please verify network connectivity.") from exc
        except Exception as exc:
            message = str(exc).lower()
            if "mfa" in message or "challenge" in message or "needs_mfa" in message:
                raise RuntimeError("Garmin account requires MFA/challenge. This plugin currently supports non-interactive login only.") from exc
            if "401" in message or "403" in message or "authentication" in message:
                raise RuntimeError("Garmin authentication failed. Check credentials.") from exc
            raise RuntimeError("Failed to fetch Garmin activities.") from exc

        filtered: list[Activity] = []

        for activity in raw_activities or []:
            activity_type = (activity.get("activityType") or {}).get("typeKey")
            if activity_type != "road_biking":
                continue

            lat, lon = extract_start_coordinates(activity)
            if not is_in_brussels(lat, lon):
                continue

            distance_m = activity.get("distance") or 0
            try:
                distance_km = float(distance_m) / 1000.0
            except (TypeError, ValueError):
                continue
            if distance_km < min_distance_km:
                continue

            activity_id = activity.get("activityId")
            points: list[list[float]] = []
            if activity_id:
                try:
                    # Prefer GPX export to maximize chance of getting full trace geometry.
                    gpx_bytes = api.download_activity(
                        str(activity_id),
                        dl_fmt=Garmin.ActivityDownloadFormat.GPX,
                    )
                    points = extract_points_from_gpx_bytes(gpx_bytes)
                except Exception:
                    logger.warning("Unable to download/parse GPX trace for activity %s, trying details endpoint", activity_id)

                if not points:
                    try:
                        details = api.get_activity_details(str(activity_id), maxpoly=4000)
                        points = extract_polyline_points(details)
                    except Exception:
                        logger.warning("Unable to fetch/parse route geometry from details for activity %s", activity_id)

            title = activity.get("activityName") or f"Road Ride {activity_id}"
            start_dt = parse_iso_datetime(activity.get("startTimeLocal") or activity.get("startTimeGMT"))

            duration_seconds: int | None = None
            try:
                duration_value = activity.get("duration")
                if duration_value is not None:
                    duration_seconds = int(float(duration_value))
            except (TypeError, ValueError):
                duration_seconds = None

            filtered.append(
                Activity(
                    title=title,
                    start_dt=start_dt,
                    distance_km=distance_km,
                    duration_seconds=duration_seconds,
                    points=points,
                )
            )

        filtered.sort(key=lambda a: a.start_dt.timestamp() if a.start_dt else float("-inf"), reverse=True)
        return filtered

    @staticmethod
    def _parse_min_distance(value: Any) -> float:
        if value is None or value == "":
            return 20.0
        try:
            distance = float(value)
        except (TypeError, ValueError):
            raise RuntimeError("Minimum distance must be a valid number in kilometers.")
        if distance < 0:
            raise RuntimeError("Minimum distance cannot be negative.")
        return distance

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
