"""Choose which places make the tour, in what order, and where each story triggers.

Two modes:

* Route mode (we know the road): project every place onto the route. A story occupies a
  stretch of road while it plays (duration × speed). Picking the best set of stories
  that don't talk over each other is *weighted interval scheduling*, solved exactly
  with dynamic programming. Triggers sit on the road at the point closest to the place,
  so a mountain 5 km away is announced when you have the best view of it, and we know
  whether it's on your left or right.

* Area mode (just a region): greedy selection with a spatial-diversity penalty (maximal
  marginal relevance) so stories spread out, then a short visiting order (nearest
  neighbour + 2-opt). The ordered stops are then routed on real roads when possible,
  and route mode refines the triggers.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import Optional, Sequence

from ..geo import LatLon, LocalProjection, haversine, project_onto_polyline
from ..models import Place, Stop

# How far from the road something can be and still be worth talking about.
VISIBILITY_M = {
    "geology": 2_500, "water": 2_000, "nature": 1_500, "railway": 1_200, "mining": 1_500, "roads": 800,
    "town": 1_500, "history": 1_500, "religion": 800, "culture": 800, "landmark": 1_000,
}
MOUNTAIN_VISIBILITY_M = 8_000

# Geofence radius by category (area mode, or when the trigger is at the place itself).
BASE_RADIUS_M = {
    "geology": 350, "water": 400, "nature": 500, "railway": 300, "mining": 300, "roads": 400,
    "town": 600, "history": 300, "religion": 200, "culture": 250, "landmark": 300,
}

WORDS_PER_SECOND = 2.5
LEAD_SECONDS = 20  # the app starts stories this far ahead at driving speed


def episode_seconds(place: Place) -> float:
    """Rough listening time: short scripts are ~60–90 words plus sound design."""
    words = min(95, max(45, len(place.extract.split()) // 3))
    return words / WORDS_PER_SECOND + 8


def visibility(place: Place) -> float:
    if "mountain" in place.themes[:2]:
        return MOUNTAIN_VISIBILITY_M
    return VISIBILITY_M.get(place.category, 1_000)


@dataclass
class RoutePlacement:
    place: Place
    along: float
    offset: float
    point: LatLon
    side: str
    start: float
    end: float


def place_on_route(places: Sequence[Place], route: Sequence[LatLon], speed_mps: float) -> list[RoutePlacement]:
    out = []
    for p in places:
        pr = project_onto_polyline(p.latlon, route)
        if pr["offset"] > visibility(p):
            continue
        lead = speed_mps * LEAD_SECONDS
        start = max(0.0, pr["along"] - lead)
        out.append(
            RoutePlacement(p, pr["along"], pr["offset"], pr["point"], pr["side"], start, start + episode_seconds(p) * speed_mps)
        )
    return out


def weighted_interval_schedule(items: list[RoutePlacement], gap_m: float = 0.0) -> list[RoutePlacement]:
    """Max-total-score subset of non-overlapping intervals. O(n log n)."""
    items = sorted(items, key=lambda r: r.end)
    ends = [r.end for r in items]
    n = len(items)
    best = [0.0] * (n + 1)
    take = [False] * (n + 1)
    prev = [0] * n
    for j, r in enumerate(items):
        prev[j] = bisect.bisect_right(ends, r.start - gap_m, 0, j)
        with_j = max(0.0, r.place.score) + best[prev[j]]
        if with_j > best[j]:
            best[j + 1], take[j + 1] = with_j, True
        else:
            best[j + 1] = best[j]
    chosen = []
    j = n
    while j > 0:
        if take[j]:
            chosen.append(items[j - 1])
            j = prev[j - 1]
        else:
            j -= 1
    return sorted(chosen, key=lambda r: r.along)


def quality_bar(places: Sequence[Place], floor: float = 1.2, fraction: float = 0.3) -> float:
    """Minimum score to be worth telling: an absolute floor, and a share of the best nearby."""
    top = max((p.score for p in places), default=0.0)
    return max(floor, fraction * top)


def slide_in(chosen: list[RoutePlacement], rejected: list[RoutePlacement], gap_m: float, slack_m: float = 1_200) -> list[RoutePlacement]:
    """Second pass after exact scheduling: a rejected story can still play *later* than its
    ideal start, right after the story that blocked it, as long as the place is still
    close (within `slack_m` of road). The app's queue plays them back to back."""
    timeline = sorted((r.start, r.end) for r in chosen)
    added = []
    for r in sorted(rejected, key=lambda r: -r.place.score):
        length = r.end - r.start
        t = r.start
        for s, e in timeline:  # earliest free slot at or after the ideal start
            if e + gap_m <= t:
                continue
            if s >= t + length + gap_m:
                break
            t = max(t, e + gap_m)
        if t - r.start <= slack_m:
            r.start, r.end = t, t + length
            timeline.append((r.start, r.end))
            timeline.sort()
            added.append(r)
    return sorted(chosen + added, key=lambda r: (r.along, r.start))


def select_along_route(places: Sequence[Place], route: Sequence[LatLon], max_stops: int, speed_mps: float) -> list[RoutePlacement]:
    bar = quality_bar(places)
    placed = [r for r in place_on_route(places, route, speed_mps) if r.place.score >= bar]
    gap = speed_mps * 3
    chosen = weighted_interval_schedule(placed, gap_m=gap)
    taken = {id(r) for r in chosen}
    chosen = slide_in(chosen, [r for r in placed if id(r) not in taken], gap)
    if len(chosen) > max_stops:
        keep = {id(r) for r in sorted(chosen, key=lambda r: -r.place.score)[:max_stops]}
        chosen = [r for r in chosen if id(r) in keep]
    return chosen


def select_in_area(places: Sequence[Place], max_stops: int, sigma_m: float = 900, min_gap_m: float = 250, lam: float = 1.5) -> list[Place]:
    """Maximal marginal relevance: good places, but not all bunched together."""
    bar = quality_bar(places)
    pool = [p for p in places if p.score >= bar]
    chosen: list[Place] = []
    while pool and len(chosen) < max_stops:
        def adjusted(p: Place) -> float:
            if not chosen:
                return p.score
            nearest = min(haversine(p.latlon, q.latlon) for q in chosen)
            if nearest < min_gap_m:
                return -math.inf
            return p.score - lam * math.exp(-nearest / sigma_m)

        best = max(pool, key=adjusted)
        if adjusted(best) == -math.inf:
            break
        chosen.append(best)
        pool.remove(best)
    return chosen


def order_stops(places: list[Place], start: Optional[LatLon] = None) -> list[Place]:
    """Open-path TSP heuristic: nearest neighbour, then 2-opt until no improvement."""
    if len(places) <= 2:
        return list(places)
    first = min(places, key=lambda p: haversine(start, p.latlon)) if start else places[0]
    rest = [p for p in places if p is not first]
    path = [first]
    while rest:
        nxt = min(rest, key=lambda p: haversine(path[-1].latlon, p.latlon))
        path.append(nxt)
        rest.remove(nxt)

    d = lambda a, b: haversine(a.latlon, b.latlon)  # noqa: E731
    improved = True
    while improved:
        improved = False
        for i in range(1, len(path) - 1):
            for k in range(i + 1, len(path)):
                a, b = path[i - 1], path[i]
                c = path[k]
                nxt = path[k + 1] if k + 1 < len(path) else None
                before = d(a, b) + (d(c, nxt) if nxt else 0)
                after = d(a, c) + (d(b, nxt) if nxt else 0)
                if after + 1e-6 < before:
                    path[i : k + 1] = reversed(path[i : k + 1])
                    improved = True
    return path


def fit_radii(triggers: list[LatLon], wanted: list[float], min_r: float = 120) -> list[float]:
    """Shrink geofences so neighbouring ones don't overlap (≤ 45% of the gap)."""
    out = []
    for i, (t, r) in enumerate(zip(triggers, wanted)):
        nearest = min((haversine(t, u) for j, u in enumerate(triggers) if j != i), default=math.inf)
        out.append(round(max(min_r, min(r, 0.45 * nearest))))
    return out


def priorities(places: Sequence[Place]) -> list[int]:
    ranked = sorted(p.score for p in places)
    if not ranked:
        return []
    hi = ranked[int(len(ranked) * 0.67)] if len(ranked) > 2 else ranked[-1]
    lo = ranked[int(len(ranked) * 0.33)] if len(ranked) > 2 else ranked[0]
    return [3 if p.score >= hi else 2 if p.score >= lo else 1 for p in places]


def build_stops_route(chosen: list[RoutePlacement], speed_mps: float) -> list[Stop]:
    triggers, wanted = [], []
    for r in chosen:
        # Always trigger on the road itself: a geofence around an off-road point can shrink
        # (to avoid its neighbours) until the road no longer passes through it.
        triggers.append(r.point)
        wanted.append(max(BASE_RADIUS_M.get(r.place.category, 300), speed_mps * 8))
    radii = fit_radii(triggers, wanted)
    prio = priorities([r.place for r in chosen])
    return [
        Stop(place=r.place, order=i, trigger=t, radius=rad, priority=pr, side=r.side if r.offset >= 60 else None, along=round(r.along))
        for i, (r, t, rad, pr) in enumerate(zip(chosen, triggers, radii, prio))
    ]


def build_stops_area(ordered: list[Place]) -> list[Stop]:
    triggers = [p.latlon for p in ordered]
    radii = fit_radii(triggers, [BASE_RADIUS_M.get(p.category, 300) for p in ordered])
    prio = priorities(ordered)
    return [Stop(place=p, order=i, trigger=p.latlon, radius=r, priority=pr) for i, (p, r, pr) in enumerate(zip(ordered, radii, prio))]


def centroid(points: Sequence[LatLon]) -> LatLon:
    proj = LocalProjection(points[0])
    xs, ys = zip(*(proj.to_xy(p) for p in points))
    return proj.to_latlon((sum(xs) / len(xs), sum(ys) / len(ys)))
