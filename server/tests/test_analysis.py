import itertools

from trailside.analysis.classify import classify, era, is_low_value
from trailside.analysis.facts import pick_facts, split_sentences
from trailside.analysis.plan import (RoutePlacement, fit_radii, order_stops, select_in_area, slide_in,
                                     weighted_interval_schedule)
from trailside.analysis.rank import dedupe, score
from trailside.geo import destination, haversine
from trailside.models import Place

O = (44.5, -110.8)


def mk(name, lat=O[0], lon=O[1], **kw):
    return Place(id=f"t:{name}", name=name, lat=lat, lon=lon, **kw)


def test_classify_themes_and_categories():
    rail = classify(mk("Durango and Silverton Narrow Gauge Railroad", types=["heritage railway"], extract="Steam locomotives haul ore from the mines along the river."))
    assert rail.themes[0] == "train" and rail.category == "railway"
    geyser = classify(mk("Old Faithful", types=["cone geyser"], extract="A geyser that erupts often."))
    assert geyser.themes[0] == "geyser" and "hot_spring" in geyser.themes
    town = classify(mk("Ouray, Colorado", types=["city", "county seat"], extract="A mining camp founded in 1876."))
    assert town.themes[0] == "town"  # small US 'cities' are towns, not traffic
    assert era(town) == "1870s"
    assert is_low_value(mk("Camp", types=["campground"]))


def test_pick_facts_prefers_stories_and_keeps_order():
    text = ("Foo Creek is a stream located in Bar County. It is 12 km long. "
            "In 1877 about eight hundred people fled up this valley, pursued by the army. "
            "The creek is the largest tributary of the river and was named after a trapper.")
    facts = pick_facts(text, max_sentences=2)
    assert facts == split_sentences(text)[2:4]


def test_split_sentences_abbreviations():
    assert len(split_sentences("It is in the U.S. state of Utah. Mt. Zion is near St. George.")) == 2


def test_score_and_dedupe():
    good = mk("Grand Prismatic Spring", sitelinks=38, extract="It is the largest hot spring in the United States. " * 3)
    dup = mk("Grand Prismatic", lat=O[0] + 0.0005, sitelinks=2, extract="A spring.")
    junk = mk("Visitor Center", types=["visitor center"], sitelinks=1, extract="Restrooms and maps.")
    for p in (good, dup, junk):
        classify(p)
        score(p)
    assert good.score > junk.score and junk.score < 0
    names = [p.name for p in dedupe([dup, good, junk])]
    assert "Grand Prismatic Spring" in names and "Grand Prismatic" not in names


def _rp(name, start, end, s):
    p = mk(name)
    p.score = s
    return RoutePlacement(p, start, 0, O, "left", start, end)


def test_weighted_interval_schedule_is_optimal():
    import random
    rnd = random.Random(7)
    for _ in range(40):
        items = []
        for i in range(9):
            s = rnd.uniform(0, 5000)
            items.append(_rp(f"p{i}", s, s + rnd.uniform(200, 1500), rnd.uniform(0.5, 6)))
        chosen = weighted_interval_schedule(items)
        best = 0.0
        for k in range(len(items) + 1):
            for combo in itertools.combinations(items, k):
                srt = sorted(combo, key=lambda r: r.start)
                if all(srt[i].end <= srt[i + 1].start for i in range(len(srt) - 1)):
                    best = max(best, sum(r.place.score for r in combo))
        assert abs(sum(r.place.score for r in chosen) - best) < 1e-9
        srt = sorted(chosen, key=lambda r: r.start)
        assert all(srt[i].end <= srt[i + 1].start for i in range(len(srt) - 1))


def test_slide_in_queues_story_after_blocker_within_slack():
    a = _rp("a", 0, 800, 5)
    b = _rp("b", 100, 900, 4)  # overlaps a; can start at 800 (700 m late)
    c = _rp("c", 100, 900, 3)  # would have to wait until 1600 (1500 m late) → dropped
    out = slide_in([a], [b, c], gap_m=0, slack_m=1000)
    assert [r.place.name for r in out] == ["a", "b"]
    assert out[1].start == 800


def test_order_stops_two_opt_beats_crossing_path():
    pts = [destination(O, 90, d) for d in (0, 3000, 1000, 2000, 4000)]
    places = [mk(f"p{i}", *p) for i, p in enumerate(pts)]
    path = order_stops(places, start=O)
    total = sum(haversine(path[i].latlon, path[i + 1].latlon) for i in range(len(path) - 1))
    assert abs(total - 4000) < 5


def test_select_in_area_spreads_out():
    cluster = [mk(f"c{i}", *destination(O, i * 40, 50), sitelinks=0) for i in range(5)]
    far = mk("far", *destination(O, 0, 5000))
    for p in cluster + [far]:
        p.score = 5.0
    far.score = 4.0
    chosen = select_in_area(cluster + [far], 2)
    assert "far" in [p.name for p in chosen]


def test_fit_radii_never_overlap():
    triggers = [O, destination(O, 0, 300), destination(O, 0, 5000)]
    radii = fit_radii(triggers, [500, 500, 500])
    assert radii[0] + radii[1] <= 300 and radii[2] == 500
