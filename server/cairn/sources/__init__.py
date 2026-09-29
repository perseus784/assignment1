"""Knowledge sources. `LiveSource` talks to public APIs; `FixtureSource` replays saved data."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Protocol

from ..config import ROOT, settings
from ..geo import LatLon, haversine, project_onto_polyline
from ..models import Place


class Source(Protocol):
    name: str

    def places_near(self, center: LatLon, radius_m: float) -> list[Place]: ...
    def places_along(self, route: list[LatLon], corridor_m: float) -> list[Place]: ...
    def geocode(self, query: str) -> Optional[dict]: ...
    def route(self, waypoints: list[LatLon]) -> Optional[list[LatLon]]: ...
    def route_details(self, waypoints: list[LatLon]) -> Optional[dict]: ...


class LiveSource:
    name = "live"

    def __init__(self):
        from .http import HttpClient
        from .osm import Geocoder, Router
        from .wiki import WikiSource

        http = HttpClient()
        self.wiki = WikiSource(http, settings.wikipedia_lang)
        self.geocoder = Geocoder(http, settings.nominatim_url)
        self.router = Router(http, settings.osrm_url)

    def places_near(self, center, radius_m):
        return self.wiki.places_near(center, radius_m)

    def places_along(self, route, corridor_m):
        return self.wiki.places_along(route, corridor_m)

    def geocode(self, query):
        return self.geocoder.geocode(query)

    def route(self, waypoints):
        details = self.route_details(waypoints)
        return details["points"] if details else None

    def route_details(self, waypoints):
        try:
            return self.router.route_details(waypoints)
        except Exception:  # routing is a nice-to-have; fall back to straight lines
            return None


FIXTURES_DIR = ROOT / "fixtures"


class FixtureSource:
    """Serves places and routes recorded in server/fixtures/<name>.json.

    Used for demos and tests, and as a template for importing curated datasets
    (e.g. a park service's own POI list) without touching the rest of the pipeline.
    """

    def __init__(self, name: str, fixtures_dir: Path = FIXTURES_DIR):
        self.name = f"fixture:{name}"
        self.data = json.loads((fixtures_dir / f"{name}.json").read_text())
        self.places = [Place(**{**p, "source": p.get("source", "fixture")}) for p in self.data["places"]]

    def places_near(self, center, radius_m):
        return [p for p in self._copy() if haversine(center, p.latlon) <= radius_m]

    def places_along(self, route, corridor_m):
        return [p for p in self._copy() if project_onto_polyline(p.latlon, route)["offset"] <= corridor_m]

    def geocode(self, query):
        q = query.strip().lower()
        g = self.data.get("geocode")
        if g and q in [a.lower() for a in g.get("aliases", [])] + [g["name"].lower()]:
            return {**g, "center": tuple(g["center"])}
        # Small gazetteer of towns in the fixture, so "from A to B" works offline.
        for name, (lat, lon) in self.data.get("gazetteer", {}).items():
            if q == name.lower() or q.split(",")[0].strip() == name.lower():
                return {"name": name.title(), "center": (lat, lon), "region": g.get("region", "") if g else ""}
        return None

    def route(self, waypoints):
        details = self.route_details(waypoints)
        return details["points"] if details else None

    def route_details(self, waypoints):
        r = self.data.get("route")
        if not r:
            return None
        points = [tuple(p) for p in r]
        from ..geo import polyline_length

        length = polyline_length(points)
        speed = self.data.get("speed_mps", 15.0)
        return {"points": points, "distance": length, "duration": length / speed}

    def _copy(self) -> list[Place]:
        return [Place(**{k: v for k, v in p.__dict__.items()}) for p in self.places]


class AllFixturesSource:
    """Every demo fixture at once, so the app's "demo data" switch can search them by name."""

    name = "fixtures"

    def __init__(self):
        self.sources = [FixtureSource(n) for n in list_fixtures()]

    def places_near(self, center, radius_m):
        return [p for s in self.sources for p in s.places_near(center, radius_m)]

    def places_along(self, route, corridor_m):
        return [p for s in self.sources for p in s.places_along(route, corridor_m)]

    def geocode(self, query):
        return next((hit for s in self.sources if (hit := s.geocode(query))), None)

    def route(self, waypoints):
        details = self.route_details(waypoints)
        return details["points"] if details else None

    def route_details(self, waypoints):
        # Use the road of whichever fixture the tour is in.
        best = min(self.sources, key=lambda s: min((haversine(waypoints[0], p.latlon) for p in s.places), default=float("inf")))
        return best.route_details(waypoints)


def get_source(name: Optional[str]) -> Source:
    if name == "fixtures":
        return AllFixturesSource()
    if name and name.startswith("fixture:"):
        return FixtureSource(name.split(":", 1)[1])
    return LiveSource()


def list_fixtures() -> list[str]:
    return sorted(p.stem for p in FIXTURES_DIR.glob("*.json"))
