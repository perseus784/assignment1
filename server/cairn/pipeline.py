"""The tour-building pipeline: discover → analyse → plan → write → render → package.

Output is a self-contained directory in the Cairn Tour Format (see docs/tour-format.md):

    <tour-id>/manifest.json
    <tour-id>/sounds/<name>.mp3        ambience beds, effects, music (shared by all stops)
    <tour-id>/voice/<stop>-<n>.mp3     narration, when a neural TTS is configured
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .analysis import plan
from .analysis.classify import classify
from .analysis.rank import dedupe, score
from .audio.library import LIBRARY
from .audio.render import get_tts, render_sound, render_voice
from .config import settings
from .geo import bbox, haversine, polyline_length, simplify
from .models import Place, Script, Stop, TourRequest
from .sources import Source, get_source
from .writing.writer import Editorial, Writer, get_writer, intro_script, spoken_name

log = logging.getLogger(__name__)

FORMAT = "cairn.tour/2"
Progress = Callable[[float, str], None]


@dataclass
class BuildResult:
    tour_id: str
    path: Path
    manifest: dict
    stats: dict = field(default_factory=dict)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "tour"


def discover(req: TourRequest, source: Source, progress: Progress) -> tuple[list[Place], Optional[list], str, Optional[tuple]]:
    name = req.name
    center = req.center
    radius = req.radius_m
    if req.query and not (center or req.route):
        progress(0.05, f"Finding “{req.query}”")
        hit = source.geocode(req.query)
        if not hit:
            raise LookupError(f"Couldn't find a place called “{req.query}”")
        center = hit["center"]
        b = hit.get("bounds")
        if b:  # size the search to the place, within sane limits
            diag = haversine((b["south"], b["west"]), (b["north"], b["east"]))
            radius = min(40_000, max(5_000, diag / 2))
        name = name or hit["name"]
        region = hit.get("region") or ", ".join(hit.get("display_name", "").split(", ")[-2:][:2])
        req.region_hint = region  # type: ignore[attr-defined]

    progress(0.1, "Gathering places, history and geography")
    if req.route:
        places = source.places_along(req.route, corridor_m=plan.MOUNTAIN_VISIBILITY_M)
        return places, req.route, name or "Your drive", center
    if not center:
        raise ValueError("Give a center, a route or a place name")
    return source.places_near(center, radius), None, name or "Tour", center


def analyse(places: list[Place], progress: Progress) -> list[Place]:
    progress(0.25, f"Analysing {len(places)} places")
    for p in places:
        classify(p)
        score(p)
    return dedupe(places)


def make_stops(places: list[Place], route: Optional[list], req: TourRequest, center, source: Source, progress: Progress) -> tuple[list[Stop], Optional[list]]:
    progress(0.35, "Planning the route and story timing")
    if route:
        chosen = plan.select_along_route(places, route, req.max_stops, req.travel_speed_mps)
        return plan.build_stops_route(chosen, req.travel_speed_mps), route

    chosen = plan.select_in_area(places, req.max_stops)
    ordered = plan.order_stops(chosen, start=center)
    road = source.route([p.latlon for p in ordered]) if len(ordered) > 1 else None
    if road:
        # Now that we know the actual road, re-plan along it: triggers on the road,
        # left/right, and story timing that fits the drive.
        chosen = plan.select_along_route(places, road, req.max_stops, req.travel_speed_mps)
        if chosen:
            return plan.build_stops_route(chosen, req.travel_speed_mps), road
    return plan.build_stops_area(ordered), None


def write_scripts(stops: list[Stop], writer: Writer, editorial: Optional[Editorial], progress: Progress) -> None:
    for i, stop in enumerate(stops):
        progress(0.45 + 0.25 * i / max(1, len(stops)), f"Writing “{stop.place.name}”")
        stop.script = (editorial.script_for(stop.place) if editorial else None) or writer.write(stop)


class AssetWriter:
    """Writes audio into the tour directory, de-duplicated, and records the asset list."""

    def __init__(self, root: Path):
        self.root = root
        self.assets: dict[str, dict] = {}
        self.durations: dict[str, float] = {}

    def add(self, rel: str, data: bytes, duration: float) -> str:
        if rel not in self.assets:
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self.assets[rel] = {"path": rel, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "type": "audio/mpeg"}
            self.durations[rel] = duration
        return rel

    def sound(self, name: str) -> tuple[str, float]:
        rel = f"sounds/{name}.mp3"
        if rel not in self.assets:
            data, duration = render_sound(name)
            self.add(rel, data, duration)
        return rel, self.durations[rel]


def episode(script: Script, key: str, assets: AssetWriter, tts) -> dict:
    """Resolve a script into playable segments with asset paths."""
    out = []
    say_n = 0
    for seg in script.segments:
        d = seg.to_dict()
        if seg.type in ("bed", "sfx", "music"):
            d["audio"], d["duration"] = assets.sound(seg.sound)
            d["duration"] = round(d["duration"], 2)
            if seg.type == "bed":
                d["loop"] = True
        elif seg.type == "say" and tts is not None:
            data, dur = render_voice(tts, seg.text, seg.voice or "narrator")
            d["audio"] = assets.add(f"voice/{key}-{say_n}.mp3", data, dur)
            d["duration"] = round(dur, 2)
        if seg.type == "say":
            say_n += 1
        out.append(d)
    words = sum(len((s.text or "").split()) for s in script.segments if s.type == "say")
    est = words / plan.WORDS_PER_SECOND + sum(s.seconds or 0 for s in script.segments if s.type == "pause")
    return {"title": script.title, "writer": script.writer, "estimatedSeconds": round(est, 1), "transcript": script.spoken_text, "segments": out}


def build_tour(req: TourRequest, out_dir: Optional[Path] = None, progress: Optional[Progress] = None, writer: Optional[Writer] = None, tour_id: Optional[str] = None) -> BuildResult:
    progress = progress or (lambda f, m: log.info("%3d%% %s", f * 100, m))
    t0 = time.time()
    source = get_source(req.source)
    editorial = Editorial.load(req.editorial)
    writer = writer or get_writer()

    places, route, name, center = discover(req, source, progress)
    if editorial:
        name = editorial.meta().get("name", name)
    candidates = len(places)
    places = analyse(places, progress)
    if editorial:
        places = [p for p in places if not editorial.skips(p)]
    stops, route = make_stops(places, route, req, center, source, progress)
    if not stops:
        raise LookupError("Couldn't find enough interesting places here yet")

    write_scripts(stops, writer, editorial, progress)

    tour_id = tour_id or f"{slugify(name)}-{req.cache_key()}"
    out_dir = (out_dir or settings.tours_dir) / tour_id
    tmp = out_dir.with_name(out_dir.name + ".building")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)

    tts = get_tts()
    assets = AssetWriter(tmp)
    progress(0.72, "Designing sound and recording narration" if tts else "Designing sound")
    intro = (editorial.intro() if editorial else None) or intro_script(name, stops)
    manifest_stops = []
    for i, stop in enumerate(stops):
        progress(0.72 + 0.23 * i / len(stops), f"Producing “{stop.place.name}”")
        p = stop.place
        manifest_stops.append(
            {
                "id": slugify(p.name) + f"-{i}",
                "name": p.name,
                "order": stop.order,
                "category": p.category,
                "themes": p.themes,
                "summary": p.description,
                "place": {"lat": round(p.lat, 6), "lon": round(p.lon, 6)},
                "trigger": {"lat": round(stop.trigger[0], 6), "lon": round(stop.trigger[1], 6), "radius": stop.radius},
                "priority": stop.priority,
                "side": stop.side,
                "along": stop.along,
                "score": p.score,
                "sources": stop.script.sources,
                "episode": episode(stop.script, f"{i:02d}", assets, tts),
            }
        )
    ambient = [{"id": aid, "title": s.title, "episode": episode(s, f"amb-{aid}", assets, tts)} for aid, s in (editorial.ambient() if editorial else [])]
    pts_for_map = [s.trigger for s in stops] + (list(route) if route else [])
    basemap = offline_basemap(bbox(pts_for_map, pad_m=5000), tmp, assets, progress)
    intro_ep = episode(intro, "intro", assets, tts)
    outro_rel, _ = assets.sound("outro")

    pts = [s.trigger for s in stops] + [s.place.latlon for s in stops] + (list(route) if route else [])
    route_out = [[round(a, 5), round(b, 5)] for a, b in simplify(route, 15)] if route else None
    meta = editorial.meta() if editorial else {}
    if not meta.get("description"):
        top = [spoken_name(s.place.name) for s in sorted(stops, key=lambda s: -s.place.score)[:3]]
        length = f" across {polyline_length(route) / 1000:.0f} km of road" if route else ""
        meta["description"] = f"{len(stops)} stories{length}, including {', '.join(top[:-1])} and {top[-1]}." if len(top) > 1 else f"{len(stops)} stories."
    manifest = {
        "format": FORMAT,
        "id": tour_id,
        "version": 1,
        "name": name,
        "region": meta.get("region") or getattr(req, "region_hint", "") or "",
        "description": meta.get("description", ""),
        "note": meta.get("note"),
        "language": req.language,
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "generator": {"engine": "cairn-engine/0.2", "writer": writer.name, "tts": tts.name if tts else "device", "source": source.name},
        "center": list(plan.centroid([s.place.latlon for s in stops])),
        "bounds": bbox(pts, pad_m=3000),
        "routeLengthM": round(polyline_length(route)) if route else None,
        "route": route_out,
        "map": basemap,
        "voices": {
            "narrator": {"style": "calm documentary narrator", "device": {"pitch": 0.92, "rate": 0.97}},
            "storyteller": {"style": "warm storyteller", "device": {"pitch": 1.05, "rate": 1.0}},
        },
        "intro": {"id": "intro", **intro_ep},
        "outro": {"audio": outro_rel},
        "stops": manifest_stops,
        "ambient": ambient,
        "attribution": attribution(stops, source),
    }
    manifest["assets"] = sorted(assets.assets.values(), key=lambda a: a["path"])
    manifest["totalBytes"] = sum(a["bytes"] for a in manifest["assets"])
    (tmp / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))

    shutil.rmtree(out_dir, ignore_errors=True)
    tmp.rename(out_dir)
    progress(1.0, "Ready")
    stats = {"candidates": candidates, "afterDedupe": len(places), "stops": len(stops), "seconds": round(time.time() - t0, 1)}
    return BuildResult(tour_id, out_dir, manifest, stats)


def offline_basemap(box: dict, tmp: Path, assets: "AssetWriter", progress: Progress) -> Optional[dict]:
    """Cut an offline vector basemap for the tour area from a Protomaps planet file.

    Optional: needs CAIRN_PMTILES_SOURCE (a local path or URL of a .pmtiles build) and the
    `pmtiles` CLI (github.com/protomaps/go-pmtiles) on PATH. Without them the app shows live
    OpenFreeMap tiles when online and the route/stops on a plain canvas when offline.
    """
    import shutil as _sh
    import subprocess

    if not settings.pmtiles_source or not _sh.which("pmtiles"):
        return None
    progress(0.96, "Cutting the offline map")
    out = tmp / "map.pmtiles"
    bbox_arg = f"{box['west']:.5f},{box['south']:.5f},{box['east']:.5f},{box['north']:.5f}"
    try:
        subprocess.run(["pmtiles", "extract", settings.pmtiles_source, str(out), f"--bbox={bbox_arg}", "--maxzoom=14"],
                       check=True, capture_output=True, timeout=900)
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("Offline map extract failed: %s", exc)
        return None
    data = out.read_bytes()
    out.unlink()
    assets.add("map.pmtiles", data, 0.0)
    assets.assets["map.pmtiles"]["type"] = "application/vnd.pmtiles"
    return {"pmtiles": "map.pmtiles", "schema": "protomaps", "maxzoom": 14}


def attribution(stops: list[Stop], source: Source) -> list[str]:
    lines = []
    if any(s.place.source == "wikipedia" for s in stops):
        lines.append("Text adapted from Wikipedia articles, CC BY-SA 4.0. Place data from Wikidata (CC0).")
    if source.name == "live":
        lines.append("Geocoding and routing © OpenStreetMap contributors (ODbL).")
    if any(s.place.source.startswith("fixture") for s in stops):
        lines.append("Demo data written for Cairn; coordinates approximate.")
    lines.append("Sound design procedurally generated by the Cairn engine.")
    return lines


def explore(req: TourRequest) -> list[dict]:
    """Fast preview (no audio): the analysed candidates around a point, best first."""
    source = get_source(req.source)
    places, route, _, center = discover(req, source, lambda f, m: None)
    places = analyse(places, lambda f, m: None)
    places.sort(key=lambda p: -p.score)
    return [
        {"name": p.name, "lat": p.lat, "lon": p.lon, "category": p.category, "themes": p.themes, "score": p.score, "summary": p.description, "url": p.url}
        for p in places[:60]
        if p.score > 0 and not math.isnan(p.score)
    ]
