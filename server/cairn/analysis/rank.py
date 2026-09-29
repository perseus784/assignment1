"""Notability scoring and de-duplication."""
from __future__ import annotations

import math
import re

from ..geo import haversine
from ..models import Place
from .classify import is_low_value
from .facts import interest

CATEGORY_PRIOR = {
    "geology": 0.8, "water": 0.6, "railway": 0.7, "mining": 0.7, "history": 0.6, "roads": 0.4,
    "town": 0.3, "nature": 0.4, "religion": 0.3, "culture": 0.2, "landmark": 0.0,
}


def score(place: Place) -> float:
    s = 1.0 * math.log1p(place.sitelinks)
    s += 0.6 * math.log1p(len(place.extract) / 250)
    s += 1.2 if place.heritage else 0.0
    s += 0.9 * interest(place.extract)
    s += CATEGORY_PRIOR.get(place.category, 0.0)
    if is_low_value(place):
        s -= 3.0
    place.score = round(s, 3)
    return place.score


_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {"the", "of", "and", "a", "at", "in", "on", "de", "la", "le", "national", "park", "colorado", "wyoming"}


def name_tokens(name: str) -> set[str]:
    return {t for t in _TOKEN.findall(name.lower()) if t not in _STOP}


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def dedupe(places: list[Place], near_m: float = 250) -> list[Place]:
    """Drop duplicates: same Wikidata item, or near-identical names close together.
    The higher-scoring record wins."""
    out: list[Place] = []
    for p in sorted(places, key=lambda p: -p.score):
        dup = False
        for q in out:
            if p.wikidata and p.wikidata == q.wikidata:
                dup = True
            elif haversine(p.latlon, q.latlon) <= near_m and jaccard(name_tokens(p.name), name_tokens(q.name)) >= 0.6:
                dup = True
            if dup:
                break
        if not dup:
            out.append(p)
    return out
