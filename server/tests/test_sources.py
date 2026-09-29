from cairn.models import Place
from cairn.sources import FixtureSource
from cairn.sources.osm import parse_nominatim, parse_osrm
from cairn.sources.wiki import apply_wikidata, clean_extract, parse_details, parse_geosearch

# Response shapes recorded from the public APIs (trimmed).
GEOSEARCH = {"batchcomplete": True, "query": {"geosearch": [
    {"pageid": 22647, "ns": 0, "title": "Old Faithful", "lat": 44.46046, "lon": -110.82815, "dist": 12.3, "primary": True},
    {"pageid": 999, "ns": 2, "title": "User:Someone", "lat": 44.46, "lon": -110.83, "dist": 20, "primary": True},
]}}
DETAILS = {"batchcomplete": True, "query": {"pages": [
    {"pageid": 22647, "ns": 0, "title": "Old Faithful", "fullurl": "https://en.wikipedia.org/wiki/Old_Faithful",
     "description": "Geyser in Yellowstone National Park, Wyoming, US", "pageprops": {"wikibase_item": "Q1142"},
     "extract": "Old Faithful (/ˈoʊld ˈfeɪθfəl/) is a cone geyser in Yellowstone National Park in Wyoming, United States. It was named in 1870 during the Washburn–Langford–Doane Expedition and was the first geyser in the park to be named."},
    {"pageid": 5, "ns": 0, "title": "Stub", "extract": "Too short."},
]}}
ENTITY = {"claims": {
    "P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q83471"}}}}],
    "P1435": [{"mainsnak": {"datavalue": {"value": {"id": "Q19558910"}}}}],
    "P571": [{"mainsnak": {"datavalue": {"value": {"time": "+1870-00-00T00:00:00Z"}}}}]},
    "sitelinks": {"enwiki": {}, "dewiki": {}, "frwiki": {}}}


def test_parse_geosearch_skips_non_articles():
    hits = parse_geosearch(GEOSEARCH)
    assert [h["title"] for h in hits] == ["Old Faithful"]


def test_parse_details_and_wikidata():
    places = parse_details(DETAILS, {h["pageid"]: h for h in parse_geosearch(GEOSEARCH)}, "en")
    assert len(places) == 1  # stub dropped
    p = places[0]
    assert p.id == "wd:Q1142" and p.lat == 44.46046
    assert "/ˈ" not in p.extract and p.extract.startswith("Old Faithful is a cone geyser")
    apply_wikidata(p, ENTITY, {"Q83471": "geyser"})
    assert p.types == ["geyser"] and p.sitelinks == 3 and p.heritage and p.inception == 1870


def test_clean_extract_nested_parentheses():
    assert clean_extract("Ouray (/jʊˈreɪ/ (listen)) is a town.") == "Ouray is a town."


def test_parse_nominatim_and_osrm():
    hit = parse_nominatim([{"lat": "37.2", "lon": "-112.9", "display_name": "Zion National Park, Utah", "name": "Zion National Park",
                            "boundingbox": ["37.1", "37.5", "-113.2", "-112.8"]}])
    assert hit["center"] == (37.2, -112.9) and hit["bounds"]["north"] == 37.5
    assert parse_nominatim([]) is None
    route = parse_osrm({"code": "Ok", "routes": [{"geometry": "_p~iF~ps|U_ulLnnqC"}]})
    assert len(route) == 2
    assert parse_osrm({"code": "NoRoute"}) is None


def test_fixture_source_geocode_and_filters():
    src = FixtureSource("yellowstone")
    hit = src.geocode("Yellowstone")
    assert hit and hit["center"] == (44.53, -110.83)
    near = src.places_near((44.4605, -110.8281), 1500)
    assert {"Old Faithful", "Old Faithful Inn"} <= {p.name for p in near}
    assert all(isinstance(p, Place) for p in near)
