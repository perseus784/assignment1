import json
import time

import pytest
from fastapi.testclient import TestClient

from cairn.api import create_app
from cairn.models import TourRequest
from cairn.pipeline import FORMAT, build_tour
from cairn.writing.writer import TemplateWriter


@pytest.fixture(scope="module")
def skyway(tmp_path_factory):
    out = tmp_path_factory.mktemp("tours")
    return build_tour(TourRequest(query="million dollar highway", source="fixture:san-juan-skyway"), out_dir=out, writer=TemplateWriter(), progress=lambda f, m: None)


def test_manifest_contract(skyway):
    m = json.loads((skyway.path / "manifest.json").read_text())
    assert m["format"] == FORMAT and m["generator"]["tts"] == "device"
    assert 5 <= len(m["stops"]) <= 14
    assert m["route"] and m["routeLengthM"] > 20_000
    names = [s["name"] for s in m["stops"]]
    assert "Silverton Visitor Center" not in names  # low-value filtered
    assert "Yankee Girl Mine" in names
    alongs = [s["along"] for s in m["stops"]]
    assert alongs == sorted(alongs)  # in driving order
    for s in m["stops"]:
        assert 120 <= s["trigger"]["radius"] <= 1500
        segs = s["episode"]["segments"]
        assert any(g["type"] == "say" for g in segs)
        for g in segs:
            if g["type"] in ("bed", "sfx", "music"):
                assert (skyway.path / g["audio"]).exists()
    assert {a["path"] for a in m["assets"]} == {p.relative_to(skyway.path).as_posix() for p in skyway.path.rglob("*.mp3")}
    assert m["totalBytes"] == sum(a["bytes"] for a in m["assets"])


def test_mining_and_rail_stories_get_matching_sound(skyway):
    by = {s["name"]: s for s in skyway.manifest["stops"]}
    beds = lambda n: [g["sound"] for g in by[n]["episode"]["segments"] if g["type"] == "bed"]  # noqa: E731
    assert beds("Yankee Girl Mine")[0] == "mine"
    rail = next(n for n in by if "Railroad" in n)
    assert beds(rail)[0] == "train"


def test_editorial_tour_uses_curated_scripts(tmp_path):
    res = build_tour(TourRequest(query="yellowstone", source="fixture:yellowstone", editorial="yellowstone-geyser-country"), out_dir=tmp_path, writer=TemplateWriter(), progress=lambda f, m: None)
    m = res.manifest
    assert m["name"] == "Yellowstone: Geyser Country"
    assert all(s["episode"]["writer"] == "editorial" for s in m["stops"])
    assert "Excelsior Geyser" not in [s["name"] for s in m["stops"]]
    assert len(m["ambient"]) == 5 and m["intro"]["writer"] == "editorial"


def test_api_build_poll_and_serve(tmp_path):
    client = TestClient(create_app(tours_dir=tmp_path, serve_app=False))
    assert client.get("/v1/health").json()["ok"]
    assert client.post("/v1/tours", json={}).status_code == 422
    job = client.post("/v1/tours", json={"query": "million dollar highway", "source": "fixture:san-juan-skyway", "max_stops": 5}).json()
    for _ in range(300):
        job = client.get(f"/v1/jobs/{job['id']}").json()
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert job["status"] == "done", job
    tours = client.get("/v1/tours").json()["tours"]
    assert tours[0]["id"] == job["tour_id"] and tours[0]["stops"] <= 5
    manifest = client.get(f"/tours/{job['tour_id']}/manifest.json").json()
    first_audio = manifest["assets"][0]["path"]
    assert client.get(f"/tours/{job['tour_id']}/{first_audio}").status_code == 200
    # Same request again is served from the cache instantly.
    again = client.post("/v1/tours", json={"query": "million dollar highway", "source": "fixture:san-juan-skyway", "max_stops": 5}).json()
    assert again["status"] == "done" and again["tour_id"] == job["tour_id"]


def test_api_unknown_place(tmp_path):
    client = TestClient(create_app(tours_dir=tmp_path, serve_app=False))
    assert client.get("/v1/geocode", params={"q": "Atlantis", "source": "fixture:yellowstone"}).status_code == 404
    places = client.get("/v1/explore", params={"lat": 44.46, "lon": -110.83, "radius": 3000, "source": "fixture:yellowstone"}).json()["places"]
    assert places[0]["name"] in ("Old Faithful", "Grand Prismatic Spring")


def test_every_trigger_is_reachable_from_the_road(skyway):
    """A car following the route must pass inside every stop's geofence."""
    from cairn.geo import project_onto_polyline

    route = [tuple(p) for p in skyway.manifest["route"]]
    for s in skyway.manifest["stops"]:
        t = s["trigger"]
        assert project_onto_polyline((t["lat"], t["lon"]), route)["offset"] < t["radius"] * 0.5, s["name"]
