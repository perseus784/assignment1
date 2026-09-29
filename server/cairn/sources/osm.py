"""OpenStreetMap services: Nominatim (place search) and OSRM (driving routes)."""
from __future__ import annotations

from typing import Optional

from ..geo import LatLon, decode_polyline
from .http import HttpClient


class Geocoder:
    def __init__(self, http: HttpClient, base_url: str):
        self.http = http
        self.base = base_url.rstrip("/")

    def geocode(self, query: str) -> Optional[dict]:
        data = self.http.get_json(f"{self.base}/search", {"q": query, "format": "jsonv2", "limit": 1}, ttl_s=30 * 86400)
        return parse_nominatim(data)


def parse_nominatim(data: list) -> Optional[dict]:
    if not data:
        return None
    hit = data[0]
    s, n, w, e = map(float, hit["boundingbox"])
    return {
        "name": hit.get("name") or hit["display_name"].split(",")[0],
        "display_name": hit["display_name"],
        "center": (float(hit["lat"]), float(hit["lon"])),
        "bounds": {"south": s, "north": n, "west": w, "east": e},
    }


class Router:
    def __init__(self, http: HttpClient, base_url: str):
        self.http = http
        self.base = base_url.rstrip("/")

    def route(self, waypoints: list[LatLon]) -> Optional[list[LatLon]]:
        details = self.route_details(waypoints)
        return details["points"] if details else None

    def route_details(self, waypoints: list[LatLon]) -> Optional[dict]:
        if len(waypoints) < 2:
            return None
        coords = ";".join(f"{lon:.5f},{lat:.5f}" for lat, lon in waypoints[:100])
        data = self.http.get_json(
            f"{self.base}/route/v1/driving/{coords}", {"overview": "full", "geometries": "polyline"}, ttl_s=30 * 86400
        )
        return parse_osrm_details(data)


def parse_osrm(data: dict) -> Optional[list[LatLon]]:
    details = parse_osrm_details(data)
    return details["points"] if details else None


def parse_osrm_details(data: dict) -> Optional[dict]:
    """Route geometry plus road distance (m) and typical driving time (s)."""
    if data.get("code") != "Ok" or not data.get("routes"):
        return None
    r = data["routes"][0]
    return {"points": decode_polyline(r["geometry"]), "distance": r.get("distance"), "duration": r.get("duration")}
