"""Work out what kind of place something is and what it should sound like.

Signals are combined with weights: Wikidata "instance of" labels (strongest), the short
description, then the article text. Each theme maps to sounds in the audio library.
"""
from __future__ import annotations

import re
from collections import defaultdict

from ..models import Place

# theme -> keywords. Multi-word keywords are matched as phrases.
THEME_KEYWORDS: dict[str, list[str]] = {
    "geyser": ["geyser", "geothermal", "hydrothermal"],
    "hot_spring": ["hot spring", "thermal pool", "thermal spring", "hot pool", "terrace", "sinter"],
    "mudpot": ["mudpot", "mud pot", "fumarole", "paint pot", "mud volcano"],
    "waterfall": ["waterfall", "falls", "cascade", "cascades", "plunge"],
    "river": ["river", "creek", "stream", "rapids", "confluence", "canyon", "gorge", "brook"],
    "lake": ["lake", "pond", "reservoir", "tarn", "lagoon"],
    "ocean": ["ocean", "sea", "beach", "coast", "coastal", "cliff", "cliffs", "island", "cove", "surf"],
    "harbor": ["port", "harbor", "harbour", "dock", "wharf", "ferry", "lighthouse", "shipwreck", "fishing village"],
    "forest": ["forest", "woods", "woodland", "grove", "pine", "lodgepole", "redwood", "rainforest", "trees"],
    "birds": ["bird", "birds", "wetland", "marsh", "estuary", "wildlife", "sanctuary", "nesting"],
    "meadow": ["meadow", "prairie", "grassland", "valley", "steppe", "savanna", "pasture"],
    "mountain": ["mountain", "peak", "summit", "ridge", "alpine", "glacier", "continental divide", "mountain pass", "volcano", "caldera", "massif", "range"],
    "wind": ["desert", "mesa", "butte", "overlook", "viewpoint", "plateau", "dune", "badlands", "pass"],
    "cave": ["cave", "cavern", "grotto", "karst", "sinkhole"],
    "train": ["railway", "railroad", "train", "locomotive", "narrow gauge", "rail line", "railway station", "steam engine", "depot"],
    "mining": ["mine", "mines", "mining", "miner", "miners", "ore", "silver", "gold rush", "smelter", "prospector", "quarry", "boomtown", "lode", "tin", "coal"],
    "construction": ["toll road", "highway", "road", "bridge", "dam", "tunnel", "built", "construction", "engineering", "canal", "aqueduct", "byway"],
    "town": ["town", "city", "village", "settlement", "main street", "historic district", "saloon", "frontier", "ghost town", "county seat", "mining camp"],
    "horses": ["horse", "horses", "cavalry", "stagecoach", "wagon", "expedition", "pony express", "cowboy", "ranch", "trail", "fled"],
    "crowd": ["market", "festival", "plaza", "square", "fair", "bazaar", "stadium", "visitors"],
    "bells": ["church", "cathedral", "chapel", "monastery", "abbey", "mission", "basilica", "temple", "shrine", "mosque"],
    "campfire": ["campfire", "camp", "campsite", "encampment"],
    "city": ["downtown", "skyline", "metropolis", "megacity", "capital city", "traffic", "skyscraper"],
    "solemn": ["battle", "war", "massacre", "siege", "surrender", "memorial", "cemetery", "fort", "army", "tragedy", "disaster"],
}

# Which display category a theme belongs to (the strongest wins).
THEME_CATEGORY = {
    "geyser": "geology", "hot_spring": "geology", "mudpot": "geology", "cave": "geology", "mountain": "geology",
    "waterfall": "water", "river": "water", "lake": "water", "ocean": "water", "harbor": "water",
    "forest": "nature", "birds": "nature", "meadow": "nature", "wind": "nature",
    "train": "railway", "mining": "mining", "construction": "roads",
    "town": "town", "horses": "history", "crowd": "culture", "campfire": "history", "city": "town",
    "bells": "religion", "solemn": "history",
}

# Instance-of labels that almost never make a good roadside story.
LOW_VALUE_TYPES = {
    "school", "high school", "primary school", "elementary school", "company", "business", "enterprise",
    "shopping center", "shopping mall", "radio station", "television station", "hospital", "office building",
    "apartment building", "parking lot", "restaurant", "weather station", "census tract", "post office",
    "airport", "petrol station", "filling station", "supermarket", "sports venue", "stadium", "golf course",
    "housing estate", "residential area", "neighborhood", "street", "bus stop", "campground", "campsite",
    "visitor center", "picnic area", "rest area", "parking", "gift shop",
}

_WORD = re.compile(r"[a-z][a-z'-]+")


def _count(text: str, phrase: str) -> int:
    if " " in phrase:
        return text.count(phrase)
    return sum(1 for w in _WORD.findall(text) if w == phrase)


def theme_scores(place: Place) -> dict[str, float]:
    types = " | ".join(place.types).lower()
    desc = (place.description or "").lower()
    name = place.name.lower()
    body = (place.extract or "").lower()[:2500]
    scores: dict[str, float] = defaultdict(float)
    for theme, words in THEME_KEYWORDS.items():
        for w in words:
            scores[theme] += 3.0 * min(1, _count(types, w))
            scores[theme] += 2.5 * min(1, _count(name, w))
            scores[theme] += 2.0 * min(1, _count(desc, w))
            scores[theme] += 0.6 * min(3, _count(body, w))
    return {k: v for k, v in scores.items() if v > 0}


def classify(place: Place) -> Place:
    scores = theme_scores(place)
    # Geysers are hot springs too; mountain passes are mountains; etc. Small nudges keep
    # the soundscape coherent with the category.
    if scores.get("geyser"):
        scores["hot_spring"] = max(scores.get("hot_spring", 0), scores["geyser"] * 0.6)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    place.themes = [t for t, s in ranked if s >= 1.2][:4] or ["wind"]
    place.category = THEME_CATEGORY.get(place.themes[0], "landmark")
    if place.heritage and place.category in ("nature", "landmark"):
        place.category = "history"
    return place


def is_low_value(place: Place) -> bool:
    return any(t.lower() in LOW_VALUE_TYPES for t in place.types)


_YEAR = re.compile(r"\b(1[0-9]{3}|20[0-2][0-9])\b")


def era(place: Place) -> str | None:
    """Rough era for flavour ('1880s'), from inception or the earliest year mentioned."""
    years = [place.inception] if place.inception else [int(y) for y in _YEAR.findall(place.extract or "")]
    years = [y for y in years if y and 1000 <= y <= 2030]
    if not years:
        return None
    y = min(years)
    return f"{y // 10 * 10}s" if y >= 1700 else f"{y // 100 + 1}th century"
