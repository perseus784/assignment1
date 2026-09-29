"""Background build jobs with progress, de-duplicated by request."""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .config import settings
from .models import TourRequest
from .pipeline import build_tour, slugify

log = logging.getLogger(__name__)


@dataclass
class Job:
    id: str
    key: str
    status: str = "queued"  # queued | running | done | error
    progress: float = 0.0
    message: str = "Waiting to start"
    tour_id: Optional[str] = None
    error: Optional[str] = None
    created: float = field(default_factory=time.time)
    stats: dict = field(default_factory=dict)

    def public(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k != "key"}


class JobQueue:
    def __init__(self, workers: int = 2, tours_dir: Optional[Path] = None):
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tour-build")
        self.jobs: dict[str, Job] = {}
        self.by_key: dict[str, str] = {}
        self.lock = threading.Lock()
        self.tours_dir = tours_dir or settings.tours_dir

    def submit(self, req: TourRequest) -> Job:
        key = req.cache_key()
        with self.lock:
            existing = self.by_key.get(key)
            if existing and self.jobs[existing].status in ("queued", "running", "done"):
                return self.jobs[existing]
            done = self._already_built(key)
            job = Job(id=uuid.uuid4().hex[:12], key=key)
            self.jobs[job.id] = job
            self.by_key[key] = job.id
        if done:
            job.status, job.progress, job.message, job.tour_id = "done", 1.0, "Ready", done
            return job
        self.pool.submit(self._run, job, req)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def _already_built(self, key: str) -> Optional[str]:
        for manifest in self.tours_dir.glob(f"*-{key}/manifest.json"):
            return manifest.parent.name
        return None

    def _run(self, job: Job, req: TourRequest) -> None:
        job.status = "running"

        def progress(fraction: float, message: str) -> None:
            job.progress, job.message = round(fraction, 3), message

        try:
            result = build_tour(req, out_dir=self.tours_dir, progress=progress)
            job.tour_id, job.stats, job.status = result.tour_id, result.stats, "done"
        except LookupError as exc:
            job.status, job.error, job.message = "error", str(exc), str(exc)
        except Exception as exc:  # report, don't crash the worker
            log.exception("Tour build failed")
            job.status, job.error, job.message = "error", f"Build failed: {exc}", "Build failed"


def catalog(tours_dir: Optional[Path] = None) -> list[dict]:
    """Summaries of every built tour, newest first."""
    out = []
    for manifest_path in (tours_dir or settings.tours_dir).glob("*/manifest.json"):
        try:
            m = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        out.append(summary(m, f"tours/{m['id']}/manifest.json"))
    return sorted(out, key=lambda t: t.get("generatedAt", ""), reverse=True)


def summary(m: dict, manifest_url: str) -> dict:
    return {
        "id": m["id"],
        "name": m["name"],
        "region": m.get("region", ""),
        "description": m.get("description", ""),
        "center": m.get("center"),
        "bounds": m.get("bounds"),
        "stops": len(m.get("stops", [])),
        "routeLengthM": m.get("routeLengthM"),
        "totalBytes": m.get("totalBytes"),
        "version": m.get("version", 1),
        "generatedAt": m.get("generatedAt"),
        "generator": m.get("generator"),
        "manifest": manifest_url,
    }


__all__ = ["Job", "JobQueue", "catalog", "summary", "slugify"]
