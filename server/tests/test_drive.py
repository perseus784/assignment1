"""From-A-to-B drives, highway pacing, and the "just drive" map cells."""
import time

import pytest
from fastapi.testclient import TestClient

from cairn import cells
from cairn.analysis import plan
from cairn.analysis.classify import classify
from cairn.analysis.rank import score
from cairn.api import create_app
from cairn.models import TourRequest
from cairn.pipeline import build_tour
from cairn.sources import FixtureSource
from cairn.writing.writer import TemplateWriter

quiet = lambda f, m: None  # noqa: E731


def test_cell_grid():
    assert cells.cell_id(33.046, -96.994) == "330_-970"  # Lewisville, TX
    assert cells.cell_id(-33.87, 151.21) == "-339_1512"  # Sydney: negative latitudes floor correctly
    b = cells.bounds("330_-970")
    assert (round(b["south"], 3), round(b["north"], 3), round(b["west"], 3), round(b["east"], 3)) == (33.0, 33.1, -97.0, -96.9)
    assert cells.contains("330_-970", (33.046, -96.994)) and not cells.contains("330_-970", (33.1, -96.994))
    assert 7_000 < cells.search_radius("330_-970") < 8_000
    for bad in ("abc", "330", "950_0", "0_1900"):
        with pytest.raises(ValueError):
            cells.parse(bad)


def test_drive_from_a_to_b(tmp_path):
    res = build_tour(TourRequest(origin="Silverton", destination="Ouray", source="fixture:san-juan-skyway"), out_dir=tmp_path, writer=TemplateWriter(), progress=quiet)
    m = res.manifest
    assert m["name"] == "Silverton to Ouray" and m["kind"] == "tour"
    assert m["route"] and m["travelSpeedMps"] == 13.0  # paced for the road, not the default
    assert "Yankee Girl Mine" in [s["name"] for s in m["stops"]]
    assert m["intro"]["transcript"].startswith("Welcome to Silverton to Ouray") or "Silverton to Ouray" in m["intro"]["transcript"]


def test_unknown_endpoint_is_a_clear_error(tmp_path):
    with pytest.raises(LookupError, match="Atlantis"):
        build_tour(TourRequest(origin="Silverton", destination="Atlantis", source="fixture:san-juan-skyway"), out_dir=tmp_path, writer=TemplateWriter(), progress=quiet)


def test_faster_roads_leave_room_for_fewer_stories():
    src = FixtureSource("san-juan-skyway")
    places = [score(classify(p)) and p for p in src.places]
    route = src.route([])
    slow = plan.select_along_route(places, route, 40, 8)
    fast = plan.select_along_route(places, route, 40, 30)
    assert len(fast) <= len(slow)
    # At highway speed, each story's stretch of road is proportionally longer.
    assert all(abs((r.end - r.start) - plan.episode_seconds(r.place) * 30) < 1 for r in fast)


def test_cell_pack(tmp_path):
    cell = cells.cell_id(44.4605, -110.8281)  # Old Faithful
    res = build_tour(TourRequest(cell=cell, max_stops=8, source="fixture:yellowstone"), out_dir=tmp_path, writer=TemplateWriter(), progress=quiet)
    m = res.manifest
    assert m["kind"] == "cell" and m["cell"] == cell and m["intro"] is None and m["route"] is None
    assert m["name"].startswith("Around ")
    assert 1 <= len(m["stops"]) <= 8
    for s in m["stops"]:
        assert s["id"].startswith(cell + "-")
        assert cells.contains(cell, (s["place"]["lat"], s["place"]["lon"]))
        assert 0 < s["trigger"]["reach"] <= plan.MAX_REACH_M


def test_cell_endpoint_builds_once_and_remembers_empty_cells(tmp_path):
    client = TestClient(create_app(tours_dir=tmp_path, serve_app=False))
    assert client.get("/v1/cells/nonsense").status_code == 400

    cell = cells.cell_id(44.4605, -110.8281)
    first = client.get(f"/v1/cells/{cell}", params={"source": "fixture:yellowstone"})
    assert first.status_code in (200, 202)
    for _ in range(300):
        r = client.get(f"/v1/cells/{cell}", params={"source": "fixture:yellowstone"}).json()
        if r["status"] != "building":
            break
        time.sleep(0.1)
    assert r["status"] == "ready"
    manifest = client.get("/" + r["manifest"]).json()
    assert manifest["kind"] == "cell"
    assert client.get("/v1/tours").json()["tours"] == []  # cells aren't listed as tours

    ocean = "0_-300"  # mid-Atlantic
    for _ in range(300):
        r = client.get(f"/v1/cells/{ocean}", params={"source": "fixture:yellowstone"}).json()
        if r["status"] != "building":
            break
        time.sleep(0.1)
    assert r["status"] == "empty"
    # A fresh server remembers it too (no rebuild).
    again = TestClient(create_app(tours_dir=tmp_path, serve_app=False)).get(f"/v1/cells/{ocean}", params={"source": "fixture:yellowstone"})
    assert again.status_code == 200 and again.json()["status"] == "empty"


def test_api_drive_validation(tmp_path):
    client = TestClient(create_app(tours_dir=tmp_path, serve_app=False))
    assert client.post("/v1/tours", json={"origin": "Silverton"}).status_code == 422
    job = client.post("/v1/tours", json={"origin": "Silverton", "destination": [38.0228, -107.6714], "source": "fixture:san-juan-skyway"}).json()
    for _ in range(300):
        job = client.get(f"/v1/jobs/{job['id']}").json()
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert job["status"] == "done", job
    assert client.get("/v1/tours").json()["tours"][0]["name"] == "Silverton to your destination"
