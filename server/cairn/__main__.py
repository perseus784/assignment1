"""Command line: build tours, render the sound library, or run the server.

    python -m cairn build --query "Zion National Park"
    python -m cairn build --from "Dallas, TX" --to "Denton, TX"
    python -m cairn build --cell 330_-970
    python -m cairn build --source fixture:yellowstone --query yellowstone \\
        --editorial yellowstone-geyser-country --out ../app/packs --id yellowstone-geyser-country
    python -m cairn sounds --out /tmp/sounds
    python -m cairn serve --port 8000
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path


def _endpoint(value):
    """'Dallas, TX' stays a name; '32.78,-96.80' becomes coordinates."""
    if value is None:
        return None
    parts = value.split(",")
    try:
        if len(parts) == 2:
            return (float(parts[0]), float(parts[1]))
    except ValueError:
        pass
    return value


def main() -> None:
    parser = argparse.ArgumentParser(prog="cairn")
    sub = parser.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build a tour pack")
    b.add_argument("--query", help="place name to geocode")
    b.add_argument("--center", help="lat,lon")
    b.add_argument("--radius", type=float, default=15_000, help="meters")
    b.add_argument("--route", type=Path, help="JSON file with [[lat, lon], ...]")
    b.add_argument("--from", dest="origin", help="start of a drive: place name or lat,lon")
    b.add_argument("--to", dest="destination", help="end of a drive: place name or lat,lon")
    b.add_argument("--cell", help="build the 'just drive' pack for one map cell, e.g. 330_-970")
    b.add_argument("--name")
    b.add_argument("--max-stops", type=int, default=14)
    b.add_argument("--source", help="fixture:<name> for demo data")
    b.add_argument("--editorial", help="editorial script set to prefer")
    b.add_argument("--writer", choices=["auto", "template", "claude"], default=None)
    b.add_argument("--out", type=Path, help="output directory (default: server/data/tours)")
    b.add_argument("--id", help="fixed tour id (default: name + request hash)")
    b.add_argument("--catalog", action="store_true", help="also write <out>/index.json listing every pack in --out")

    s = sub.add_parser("sounds", help="render the whole sound library to MP3s for auditioning")
    s.add_argument("--out", type=Path, required=True)

    v = sub.add_parser("serve", help="run the API + app server")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8000)

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.cmd == "build":
        from .models import TourRequest
        from .pipeline import build_tour
        from .writing.writer import get_writer

        req = TourRequest(
            query=args.query,
            center=tuple(map(float, args.center.split(","))) if args.center else None,
            radius_m=args.radius,
            route=[tuple(p) for p in json.loads(args.route.read_text())] if args.route else None,
            name=args.name,
            max_stops=args.max_stops,
            source=args.source,
            editorial=args.editorial,
            origin=_endpoint(args.origin),
            destination=_endpoint(args.destination),
            cell=args.cell,
        )
        result = build_tour(req, out_dir=args.out, writer=get_writer(args.writer), tour_id=args.id)
        print(json.dumps({"tour": result.tour_id, "path": str(result.path), **result.stats, "bytes": result.manifest["totalBytes"]}, indent=2))
        if args.catalog and args.out:
            from .jobs import catalog

            tours = catalog(args.out)
            for t in tours:  # relative to the index file, so it works from any static host
                t["manifest"] = f"{t['id']}/manifest.json"
            (args.out / "index.json").write_text(json.dumps({"tours": tours}, indent=1))
    elif args.cmd == "sounds":
        from .audio.library import LIBRARY
        from .audio.render import render_sound

        args.out.mkdir(parents=True, exist_ok=True)
        for name, sound in LIBRARY.items():
            data, dur = render_sound(name)
            (args.out / f"{sound.kind}-{name}.mp3").write_bytes(data)
            print(f"{sound.kind:5} {name:16} {dur:5.1f}s  {sound.description}")
    elif args.cmd == "serve":
        import uvicorn

        uvicorn.run("cairn.api:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
