import math

from cairn.geo import (angle_diff, bearing, corridor_cover, decode_polyline, destination, haversine, hex_cover,
                           polyline_length, project_onto_polyline, simplify)

O = (44.5, -110.8)


def test_haversine_known_distance():
    assert abs(haversine((44.4605, -110.8281), (44.5251, -110.8382)) - 7230) < 100


def test_destination_roundtrip():
    p = destination(O, 45, 1000)
    assert abs(haversine(O, p) - 1000) < 0.5
    assert abs(bearing(O, p) - 45) < 0.1
    assert angle_diff(350, 10) == 20


def test_projection_side_and_along():
    route = [O, destination(O, 0, 2000)]  # driving north
    left = destination(destination(O, 0, 500), 270, 300)
    right = destination(destination(O, 0, 1500), 90, 300)
    pl, pr = project_onto_polyline(left, route), project_onto_polyline(right, route)
    assert pl["side"] == "left" and pr["side"] == "right"
    assert abs(pl["along"] - 500) < 5 and abs(pr["along"] - 1500) < 5
    assert abs(pl["offset"] - 300) < 3


def test_hex_cover_covers_disc():
    cells = hex_cover(O, 25_000, 10_000)
    assert len(cells) > 3
    for brg in range(0, 360, 20):
        for d in (0, 12_000, 24_000):
            p = destination(O, brg, d)
            assert min(haversine(p, c) for c in cells) <= 10_000


def test_corridor_cover_spacing():
    route = [O, destination(O, 90, 30_000)]
    cells = corridor_cover(route, 2_000, 10_000)
    gaps = [haversine(cells[i], cells[i + 1]) for i in range(len(cells) - 1)]
    assert max(gaps) <= 2 * math.sqrt(10_000**2 - 2_000**2) + 1


def test_decode_polyline_google_example():
    pts = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
    assert [(round(a, 3), round(b, 3)) for a, b in pts] == [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]


def test_simplify_keeps_shape():
    straight = [destination(O, 90, d) for d in range(0, 5000, 100)]
    kinked = straight + [destination(straight[-1], 0, 1000)]
    out = simplify(kinked, 10)
    assert len(out) == 3
    assert abs(polyline_length(out) - polyline_length(kinked)) < 5
