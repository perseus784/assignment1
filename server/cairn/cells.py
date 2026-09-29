"""The map-cell grid behind "just drive" mode.

The world is divided into 0.1° × 0.1° squares (about 11 km × 8–11 km). Each square gets a
small story pack, built once and cached for everyone. While you drive, the app fetches the
squares ahead of you, so it needs no plan and no destination, and anything already fetched
keeps working through a dead zone.

A cell id is the south-west corner in tenths of a degree: "330_-970" covers
latitude 33.0–33.1 and longitude −97.0 to −96.9 (Lewisville, Texas).
"""
from __future__ import annotations

import math
import re

from .geo import LatLon, haversine

SIZE_DEG = 0.1
_ID = re.compile(r"^(-?\d{1,3})_(-?\d{1,4})$")


def cell_id(lat: float, lon: float) -> str:
    return f"{math.floor(lat / SIZE_DEG + 1e-9)}_{math.floor(lon / SIZE_DEG + 1e-9)}"


def parse(cell: str) -> tuple[int, int]:
    m = _ID.match(cell)
    if not m:
        raise ValueError(f"Bad cell id {cell!r}; expected like '330_-970'")
    i, j = int(m.group(1)), int(m.group(2))
    if not (-900 <= i < 900 and -1800 <= j < 1800):
        raise ValueError(f"Cell {cell!r} is off the map")
    return i, j


def bounds(cell: str) -> dict:
    i, j = parse(cell)
    return {"south": i * SIZE_DEG, "north": (i + 1) * SIZE_DEG, "west": j * SIZE_DEG, "east": (j + 1) * SIZE_DEG}


def center(cell: str) -> LatLon:
    b = bounds(cell)
    return ((b["south"] + b["north"]) / 2, (b["west"] + b["east"]) / 2)


def search_radius(cell: str) -> float:
    """Radius of a circle around the center that covers the whole square."""
    b = bounds(cell)
    return haversine(center(cell), (b["south"], b["west"])) + 50


def contains(cell: str, p: LatLon) -> bool:
    b = bounds(cell)
    return b["south"] <= p[0] < b["north"] and b["west"] <= p[1] < b["east"]
