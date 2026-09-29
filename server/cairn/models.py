"""Core data types that flow through the pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

LatLon = tuple[float, float]


@dataclass
class Place:
    """A candidate point of interest as discovered from a knowledge source."""

    id: str  # stable id, e.g. "wd:Q123" or "wp:en:Old_Faithful"
    name: str
    lat: float
    lon: float
    extract: str = ""  # encyclopedic text used for facts
    description: str = ""  # short one-line description ("geyser in Wyoming")
    types: list[str] = field(default_factory=list)  # instance-of labels, e.g. ["geyser"]
    sitelinks: int = 0  # number of Wikipedia language editions (a notability signal)
    heritage: bool = False  # has a heritage designation (NRHP, NHL, UNESCO, …)
    inception: Optional[int] = None  # founding/construction year if known
    wikidata: Optional[str] = None
    url: Optional[str] = None
    source: str = "unknown"
    license: str = "CC BY-SA 4.0"

    # Filled in by analysis
    category: str = "landmark"
    themes: list[str] = field(default_factory=list)  # sound themes, strongest first
    score: float = 0.0

    @property
    def latlon(self) -> LatLon:
        return (self.lat, self.lon)


@dataclass
class TourRequest:
    """What the app (or CLI) asks the engine for."""

    # One of: center+radius, a route (list of points), or a free-text place to geocode.
    center: Optional[LatLon] = None
    radius_m: float = 15_000
    route: Optional[list[LatLon]] = None
    query: Optional[str] = None
    name: Optional[str] = None
    max_stops: int = 14
    language: str = "en"
    travel_speed_mps: float = 15.0  # typical driving speed, drives spacing between stories
    editorial: Optional[str] = None  # id of hand-curated scripts to prefer
    source: Optional[str] = None  # force a source (e.g. "fixture:yellowstone") for demos/tests
    # A drive from A to B: place names or (lat, lon). The engine routes it on real roads.
    origin: Optional[object] = None
    destination: Optional[object] = None
    # A map cell for "just drive" mode (see cells.py); stories for one grid square.
    cell: Optional[str] = None

    def cache_key(self) -> str:
        import hashlib
        import json

        payload = {
            "c": [round(v, 3) for v in self.center] if self.center else None,
            "r": round(self.radius_m, -2),
            "route": [[round(a, 4), round(b, 4)] for a, b in self.route] if self.route else None,
            "q": (self.query or "").strip().lower() or None,
            "n": self.max_stops,
            "l": self.language,
            "e": self.editorial,
            "s": self.source,
            "o": _endpoint_key(self.origin),
            "d": _endpoint_key(self.destination),
            "cell": self.cell,
            "v": self.travel_speed_mps if self.route else None,
        }
        return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]


def _endpoint_key(v):
    if v is None:
        return None
    if isinstance(v, str):
        return v.strip().lower()
    return [round(float(x), 3) for x in v]


SegmentType = Literal["bed", "sfx", "say", "pause", "music"]


@dataclass
class Segment:
    """One step of an episode script. The app plays these in order.

    - bed:   start a looping ambience under everything that follows (replaces the previous bed)
    - sfx:   a one-shot sound effect (wait=False lets narration continue over it)
    - say:   a spoken line by `voice` ("narrator" or "storyteller")
    - pause: silence (beds keep playing)
    - music: a short musical sting ("intro", "outro", "transition")
    """

    type: SegmentType
    text: Optional[str] = None
    voice: Optional[str] = None
    sound: Optional[str] = None
    gain: float = 1.0
    seconds: Optional[float] = None
    wait: bool = True

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type}
        if self.type == "say":
            d.update(voice=self.voice or "narrator", text=self.text)
        elif self.type in ("bed", "sfx", "music"):
            d.update(sound=self.sound, gain=round(self.gain, 2))
            if self.type in ("sfx", "music"):
                d["wait"] = self.wait
        elif self.type == "pause":
            d["seconds"] = self.seconds or 0.6
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Segment":
        return cls(
            type=d["type"],
            text=d.get("text"),
            voice=d.get("voice"),
            sound=d.get("sound"),
            gain=float(d.get("gain", 1.0)),
            seconds=d.get("seconds"),
            wait=bool(d.get("wait", True)),
        )


@dataclass
class Script:
    title: str
    segments: list[Segment]
    sources: list[dict] = field(default_factory=list)
    writer: str = "template"

    @property
    def spoken_text(self) -> str:
        return " ".join(s.text or "" for s in self.segments if s.type == "say")


@dataclass
class Stop:
    """A place that made it into the tour, with where/when to trigger it."""

    place: Place
    order: int
    trigger: LatLon
    radius: float
    priority: int
    side: Optional[str] = None  # left/right of the road, when a route is known
    along: Optional[float] = None  # meters along the route
    reach: Optional[float] = None  # how far from the road it can be seen/announced (area & cell tours)
    script: Optional[Script] = None
