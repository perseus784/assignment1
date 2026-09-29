"""Wikipedia + Wikidata: the main knowledge source, with worldwide coverage.

Pipeline: geosearch (which articles are near here?) → article details (intro text,
description, Wikidata id) → Wikidata facts (what kind of thing is it, how notable is it,
is it heritage-listed, when was it built?).
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from ..geo import LatLon, corridor_cover, hex_cover
from ..models import Place
from .http import HttpClient

GEOSEARCH_MAX_RADIUS = 10_000  # API cap
GEOSEARCH_MAX_LIMIT = 500

# Wikidata properties
P_INSTANCE_OF = "P31"
P_HERITAGE = "P1435"
P_INCEPTION = "P571"
P_OPENING = "P1619"


def _chunks(items: list, n: int) -> Iterable[list]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


class WikiSource:
    name = "wikipedia"

    def __init__(self, http: HttpClient, lang: str = "en"):
        self.http = http
        self.lang = lang
        self.api = f"https://{lang}.wikipedia.org/w/api.php"
        self.wd_api = "https://www.wikidata.org/w/api.php"

    # ---- discovery -------------------------------------------------------------------

    def places_near(self, center: LatLon, radius_m: float, limit: int = 400) -> list[Place]:
        cells = hex_cover(center, radius_m, GEOSEARCH_MAX_RADIUS)
        return self._discover(cells, GEOSEARCH_MAX_RADIUS if len(cells) > 1 else radius_m, limit)

    def places_along(self, route: list[LatLon], corridor_m: float, limit: int = 400) -> list[Place]:
        radius = min(GEOSEARCH_MAX_RADIUS, max(corridor_m * 1.5, 2_000))
        return self._discover(corridor_cover(route, corridor_m, radius), radius, limit)

    def _discover(self, cells: list[LatLon], radius: float, limit: int) -> list[Place]:
        hits: dict[int, dict] = {}
        for lat, lon in cells:
            data = self.http.get_json(
                self.api,
                {
                    "action": "query",
                    "list": "geosearch",
                    "gscoord": f"{lat:.5f}|{lon:.5f}",
                    "gsradius": int(min(radius, GEOSEARCH_MAX_RADIUS)),
                    "gslimit": GEOSEARCH_MAX_LIMIT,
                    "gsprimary": "primary",
                    "format": "json",
                    "formatversion": 2,
                },
            )
            for hit in parse_geosearch(data):
                hits.setdefault(hit["pageid"], hit)
        pages = list(hits.values())[:limit]
        places = self._details(pages)
        self._enrich_wikidata(places)
        return places

    # ---- article details ---------------------------------------------------------

    def _details(self, hits: list[dict]) -> list[Place]:
        places: list[Place] = []
        for batch in _chunks(hits, 20):  # extracts are limited to 20 pages per request
            data = self.http.get_json(
                self.api,
                {
                    "action": "query",
                    "pageids": "|".join(str(h["pageid"]) for h in batch),
                    "prop": "extracts|pageprops|description|info",
                    "exintro": 1,
                    "explaintext": 1,
                    "exlimit": 20,
                    "ppprop": "wikibase_item",
                    "inprop": "url",
                    "format": "json",
                    "formatversion": 2,
                },
            )
            places += parse_details(data, {h["pageid"]: h for h in batch}, self.lang)
        return places

    def _enrich_wikidata(self, places: list[Place]) -> None:
        by_qid = {p.wikidata: p for p in places if p.wikidata}
        type_ids: set[str] = set()
        claims_by_qid: dict[str, dict] = {}
        for batch in _chunks(list(by_qid), 50):
            data = self.http.get_json(
                self.wd_api,
                {"action": "wbgetentities", "ids": "|".join(batch), "props": "claims|sitelinks", "format": "json"},
            )
            for qid, entity in (data.get("entities") or {}).items():
                claims_by_qid[qid] = entity
                type_ids.update(claim_ids(entity, P_INSTANCE_OF))

        labels: dict[str, str] = {}
        for batch in _chunks(sorted(type_ids), 50):
            data = self.http.get_json(
                self.wd_api,
                {"action": "wbgetentities", "ids": "|".join(batch), "props": "labels", "languages": "en", "format": "json"},
            )
            for qid, entity in (data.get("entities") or {}).items():
                label = (entity.get("labels") or {}).get("en", {}).get("value")
                if label:
                    labels[qid] = label

        for qid, entity in claims_by_qid.items():
            apply_wikidata(by_qid[qid], entity, labels)


# ---- pure parsers (unit-tested against recorded API shapes) --------------------------


def parse_geosearch(data: dict) -> list[dict]:
    return [
        {"pageid": g["pageid"], "title": g["title"], "lat": g["lat"], "lon": g["lon"]}
        for g in (data.get("query") or {}).get("geosearch", [])
        if g.get("ns", 0) == 0
    ]


_PARENS = re.compile(r"\s*\([^()]*\)")


def clean_extract(text: str) -> str:
    """Strip pronunciation guides / parentheticals and collapse whitespace."""
    prev = None
    while prev != text:
        prev, text = text, _PARENS.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace(" ,", ",").replace(" .", ".")


def parse_details(data: dict, hits: dict[int, dict], lang: str) -> list[Place]:
    out = []
    for page in (data.get("query") or {}).get("pages", []):
        if page.get("missing") or "pageid" not in page:
            continue
        hit = hits.get(page["pageid"], {})
        extract = clean_extract(page.get("extract") or "")
        if len(extract) < 80:  # stubs rarely have a story in them
            continue
        qid = (page.get("pageprops") or {}).get("wikibase_item")
        title = page["title"]
        out.append(
            Place(
                id=f"wd:{qid}" if qid else f"wp:{lang}:{page['pageid']}",
                name=title,
                lat=hit.get("lat"),
                lon=hit.get("lon"),
                extract=extract,
                description=page.get("description") or "",
                wikidata=qid,
                url=page.get("fullurl") or f"https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}",
                source="wikipedia",
            )
        )
    return out


def claim_ids(entity: dict, prop: str) -> list[str]:
    ids = []
    for claim in (entity.get("claims") or {}).get(prop, []):
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(value, dict) and "id" in value:
            ids.append(value["id"])
    return ids


def claim_year(entity: dict, prop: str) -> Optional[int]:
    for claim in (entity.get("claims") or {}).get(prop, []):
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(value, dict) and isinstance(value.get("time"), str):
            m = re.match(r"([+-]\d+)-", value["time"])
            if m:
                return int(m.group(1))
    return None


def apply_wikidata(place: Place, entity: dict, labels: dict[str, str]) -> None:
    place.types = [labels[t] for t in claim_ids(entity, P_INSTANCE_OF) if t in labels]
    place.sitelinks = len(entity.get("sitelinks") or {})
    place.heritage = bool((entity.get("claims") or {}).get(P_HERITAGE))
    place.inception = claim_year(entity, P_INCEPTION) or claim_year(entity, P_OPENING)
