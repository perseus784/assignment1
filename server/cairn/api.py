"""HTTP API for the app.

    GET  /v1/health
    GET  /v1/geocode?q=Yosemite
    GET  /v1/explore?lat=..&lon=..&radius=..        quick analysed preview (no audio)
    POST /v1/tours   {query | center+radius_m | route, ...}   → build job (202)
    GET  /v1/jobs/{id}                              build progress
    GET  /v1/tours                                  catalog of built tours
    GET  /v1/cells/{cell}                           "just drive" story pack for one map square
    GET  /tours/{id}/manifest.json (+ audio)        the tour pack itself

The same server also serves the app (../app) at /, so one process is a complete deployment.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from . import cells
from .config import ROOT, settings
from .jobs import JobQueue, catalog
from .models import TourRequest
from .pipeline import explore
from .sources import get_source, list_fixtures

APP_DIR = ROOT.parent / "app"


class TourBody(BaseModel):
    query: Optional[str] = Field(None, max_length=200, description="A place name to geocode, e.g. 'Zion National Park'")
    center: Optional[tuple[float, float]] = Field(None, description="[lat, lon]")
    radius_m: float = Field(15_000, ge=500, le=60_000)
    route: Optional[list[tuple[float, float]]] = Field(None, max_length=5000, description="[[lat, lon], ...] of the drive")
    origin: Optional[Union[str, tuple[float, float]]] = Field(None, description="Start of a drive: a place name or [lat, lon]")
    destination: Optional[Union[str, tuple[float, float]]] = Field(None, description="End of a drive: a place name or [lat, lon]")
    travel_speed_mps: Optional[float] = Field(None, ge=3, le=40, description="Typical speed; derived from the road when routing")
    name: Optional[str] = Field(None, max_length=120)
    max_stops: int = Field(14, ge=1, le=40)
    source: Optional[str] = Field(None, description="'fixture:<name>' for demo data; omit for live data")
    editorial: Optional[str] = None

    @model_validator(mode="after")
    def needs_location(self):
        has_drive = self.origin is not None and self.destination is not None
        if not (self.query or self.center or (self.route and len(self.route) >= 2) or has_drive):
            raise ValueError("Provide a query, a center, a route, or an origin and destination")
        if (self.origin is None) != (self.destination is None):
            raise ValueError("A drive needs both an origin and a destination")
        return self

    def to_request(self) -> TourRequest:
        return TourRequest(
            center=tuple(self.center) if self.center else None,
            radius_m=self.radius_m,
            route=[tuple(p) for p in self.route] if self.route else None,
            query=self.query,
            name=self.name,
            max_stops=self.max_stops,
            source=self.source,
            editorial=self.editorial,
            origin=self.origin,
            destination=self.destination,
            **({"travel_speed_mps": self.travel_speed_mps} if self.travel_speed_mps else {}),
        )


def create_app(tours_dir: Optional[Path] = None, serve_app: bool = True) -> FastAPI:
    tours_dir = tours_dir or settings.tours_dir
    tours_dir.mkdir(parents=True, exist_ok=True)
    queue = JobQueue(tours_dir=tours_dir)
    api = FastAPI(title="Cairn Engine", version="0.2.0")
    api.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])
    api.add_middleware(GZipMiddleware, minimum_size=2000)

    @api.get("/v1/health")
    def health():
        from .audio.render import get_tts
        from .writing.writer import get_writer

        tts = get_tts()
        return {"ok": True, "writer": get_writer().name, "tts": tts.name if tts else "device", "fixtures": list_fixtures()}

    @api.get("/v1/geocode")
    def geocode(q: str = Query(..., min_length=2, max_length=200), source: Optional[str] = None):
        hit = get_source(source).geocode(q)
        if not hit:
            raise HTTPException(404, f"No place found for “{q}”")
        return hit

    @api.get("/v1/explore")
    def explore_(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180), radius: float = Query(10_000, ge=500, le=40_000), source: Optional[str] = None):
        return {"places": explore(TourRequest(center=(lat, lon), radius_m=radius, source=source))}

    @api.post("/v1/tours", status_code=202)
    def create_tour(body: TourBody):
        return queue.submit(body.to_request()).public()

    @api.get("/v1/jobs/{job_id}")
    def job(job_id: str):
        j = queue.get(job_id)
        if not j:
            raise HTTPException(404, "Unknown job")
        return j.public()

    @api.get("/v1/tours")
    def tours():
        return {"tours": catalog(tours_dir)}

    @api.get("/v1/cells/{cell}")
    def cell(cell: str, source: Optional[str] = None):
        """Stories for one ~11 km map square. Built on first request, then cached for everyone.
        Poll until status is "ready" (or "empty")."""
        try:
            b = cells.bounds(cell)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        job = queue.submit(TourRequest(cell=cell, max_stops=8, source=source))
        base = {"cell": cell, "bounds": b}
        if job.status == "done":
            return {**base, "status": "ready", "manifest": f"tours/{job.tour_id}/manifest.json"}
        if job.empty:
            return {**base, "status": "empty"}
        if job.status == "error":
            raise HTTPException(502, job.error or "Build failed")
        return JSONResponse(status_code=202, content={**base, "status": "building", "progress": job.progress})

    api.mount("/tours", StaticFiles(directory=tours_dir), name="tours")
    if serve_app and APP_DIR.exists():
        api.mount("/", StaticFiles(directory=APP_DIR, html=True), name="app")
    return api


app = create_app()
