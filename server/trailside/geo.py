"""Geodesy helpers. Coordinates are (lat, lon) in degrees; distances in meters."""
from __future__ import annotations

import math
from typing import Iterable, Sequence

EARTH_RADIUS = 6_371_008.8

LatLon = tuple[float, float]


def haversine(a: LatLon, b: LatLon) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS * math.asin(min(1.0, math.sqrt(h)))


def bearing(a: LatLon, b: LatLon) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    y = math.sin(lon2 - lon1) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def angle_diff(a: float, b: float) -> float:
    d = abs((a - b) % 360)
    return 360 - d if d > 180 else d


def destination(p: LatLon, brg: float, dist: float) -> LatLon:
    d = dist / EARTH_RADIUS
    t = math.radians(brg)
    lat1, lon1 = map(math.radians, p)
    lat2 = math.asin(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(t))
    lon2 = lon1 + math.atan2(math.sin(t) * math.sin(d) * math.cos(lat1), math.cos(d) - math.sin(lat1) * math.sin(lat2))
    return (math.degrees(lat2), (math.degrees(lon2) + 540) % 360 - 180)


class LocalProjection:
    """Equirectangular projection around an origin. Accurate to <0.5% within ~100 km,
    which is plenty for clustering, ordering and route projection."""

    def __init__(self, origin: LatLon):
        self.lat0, self.lon0 = origin
        self.kx = math.cos(math.radians(self.lat0)) * math.pi * EARTH_RADIUS / 180
        self.ky = math.pi * EARTH_RADIUS / 180

    def to_xy(self, p: LatLon) -> tuple[float, float]:
        return ((p[1] - self.lon0) * self.kx, (p[0] - self.lat0) * self.ky)

    def to_latlon(self, xy: tuple[float, float]) -> LatLon:
        return (xy[1] / self.ky + self.lat0, xy[0] / self.kx + self.lon0)


def polyline_length(points: Sequence[LatLon]) -> float:
    return sum(haversine(points[i - 1], points[i]) for i in range(1, len(points)))


def project_onto_polyline(p: LatLon, points: Sequence[LatLon]) -> dict:
    """Closest point on a route to `p`.

    Returns {"point", "along" (meters from route start), "offset" (meters from route),
    "heading" (route direction at that point), "side" ("left"/"right" of travel)}.
    """
    proj = LocalProjection(p)
    px, py = 0.0, 0.0
    best = None
    along = 0.0
    for i in range(1, len(points)):
        ax, ay = proj.to_xy(points[i - 1])
        bx, by = proj.to_xy(points[i])
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
        cx, cy = ax + t * dx, ay + t * dy
        dist = math.hypot(px - cx, py - cy)
        seg_len = math.sqrt(seg2)
        if best is None or dist < best["offset"]:
            cross = dx * (py - ay) - dy * (px - ax)  # >0: point is left of travel direction
            best = {
                "point": proj.to_latlon((cx, cy)),
                "along": along + t * seg_len,
                "offset": dist,
                "heading": bearing(points[i - 1], points[i]),
                "side": "left" if cross > 0 else "right",
            }
        along += seg_len
    if best is None:  # single-point route
        best = {"point": points[0], "along": 0.0, "offset": haversine(p, points[0]), "heading": 0.0, "side": "right"}
    return best


def point_along(points: Sequence[LatLon], dist: float) -> LatLon:
    remaining = max(0.0, dist)
    for i in range(1, len(points)):
        seg = haversine(points[i - 1], points[i])
        if remaining <= seg or i == len(points) - 1:
            t = 0 if seg == 0 else min(1.0, remaining / seg)
            a, b = points[i - 1], points[i]
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        remaining -= seg
    return points[0]


def bbox(points: Iterable[LatLon], pad_m: float = 0.0) -> dict:
    pts = list(points)
    south = min(p[0] for p in pts)
    north = max(p[0] for p in pts)
    west = min(p[1] for p in pts)
    east = max(p[1] for p in pts)
    if pad_m:
        dlat = pad_m / 111_320
        dlon = pad_m / (111_320 * max(0.1, math.cos(math.radians((north + south) / 2))))
        south, north, west, east = south - dlat, north + dlat, west - dlon, east + dlon
    return {"south": south, "west": west, "north": north, "east": east}


def hex_cover(center: LatLon, radius_m: float, cell_radius_m: float) -> list[LatLon]:
    """Centers of circles (radius `cell_radius_m`) on a hex lattice that together cover a
    disc of `radius_m`. Used to tile large areas into search queries that have a size cap."""
    if radius_m <= cell_radius_m:
        return [center]
    proj = LocalProjection(center)
    step = cell_radius_m * math.sqrt(3)  # hex spacing with full coverage
    out = []
    rows = int(radius_m / (step * math.sqrt(3) / 2)) + 1
    cols = int(radius_m / step) + 1
    for r in range(-rows, rows + 1):
        y = r * step * math.sqrt(3) / 2
        offset = step / 2 if r % 2 else 0.0
        for c in range(-cols - 1, cols + 2):
            x = c * step + offset
            if math.hypot(x, y) <= radius_m + cell_radius_m * 0.5:
                out.append(proj.to_latlon((x, y)))
    return out


def corridor_cover(route: Sequence[LatLon], width_m: float, cell_radius_m: float) -> list[LatLon]:
    """Search circles spaced along a route so a corridor of `width_m` on each side is covered."""
    length = polyline_length(route)
    if length == 0:
        return [route[0]]
    spacing = max(100.0, 2 * math.sqrt(max(cell_radius_m**2 - width_m**2, (cell_radius_m * 0.5) ** 2)))
    n = max(1, math.ceil(length / spacing))
    return [point_along(route, i * length / n) for i in range(n + 1)]


def decode_polyline(encoded: str, precision: int = 5) -> list[LatLon]:
    """Decode a Google/OSRM encoded polyline."""
    coords, index, lat, lon = [], 0, 0, 0
    factor = 10**precision
    while index < len(encoded):
        for is_lon in (False, True):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if is_lon:
                lon += delta
            else:
                lat += delta
        coords.append((lat / factor, lon / factor))
    return coords


def simplify(points: Sequence[LatLon], tolerance_m: float) -> list[LatLon]:
    """Douglas–Peucker simplification (keeps packs small without visibly changing the route)."""
    if len(points) < 3:
        return list(points)
    proj = LocalProjection(points[0])
    xy = [proj.to_xy(p) for p in points]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        s, e = stack.pop()
        ax, ay = xy[s]
        bx, by = xy[e]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy) or 1e-9
        idx, dmax = -1, 0.0
        for i in range(s + 1, e):
            px, py = xy[i]
            d = abs(dy * (px - ax) - dx * (py - ay)) / norm
            if d > dmax:
                idx, dmax = i, d
        if dmax > tolerance_m and idx > 0:
            keep[idx] = True
            stack += [(s, idx), (idx, e)]
    return [p for p, k in zip(points, keep) if k]
