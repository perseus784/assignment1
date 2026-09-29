"""Polite HTTP client: identifies itself, rate-limits per host, and caches responses on disk.

Public APIs like Wikipedia, Nominatim and OSRM ask clients to send a descriptive
User-Agent and to keep request rates low. Caching also makes rebuilding a tour cheap.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode, urlparse

import httpx

from ..config import settings


class HttpClient:
    def __init__(self, cache_dir: Optional[Path] = None, min_interval: Optional[dict[str, float]] = None):
        self.cache_dir = cache_dir or settings.data_dir / "http-cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.min_interval = {"nominatim.openstreetmap.org": 1.1, **(min_interval or {})}
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self._client = httpx.Client(
            timeout=30,
            headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
            follow_redirects=True,
        )

    def get_json(self, url: str, params: Optional[dict[str, Any]] = None, ttl_s: float = 7 * 86400) -> Any:
        full = f"{url}?{urlencode(sorted((params or {}).items()), doseq=True)}" if params else url
        key = hashlib.sha1(full.encode()).hexdigest()
        path = self.cache_dir / key[:2] / f"{key}.json"
        if path.exists() and time.time() - path.stat().st_mtime < ttl_s:
            return json.loads(path.read_text())

        self._throttle(urlparse(url).hostname or "")
        for attempt in range(3):
            try:
                res = self._client.get(url, params=params)
                if res.status_code in (429, 503) and attempt < 2:
                    time.sleep(float(res.headers.get("retry-after", 2 * (attempt + 1))))
                    continue
                res.raise_for_status()
                data = res.json()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data))
                return data
            except httpx.TransportError:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"GET {url} failed")

    def _throttle(self, host: str) -> None:
        gap = self.min_interval.get(host, 0.2)
        with self._lock:
            wait = self._last.get(host, 0) + gap - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.monotonic()
