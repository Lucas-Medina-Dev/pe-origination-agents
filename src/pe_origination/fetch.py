"""A polite HTTP fetcher: robots.txt, per-host rate limiting, retries, cache."""

from __future__ import annotations

import hashlib
import threading
import time
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

MAX_BYTES = 2_000_000
RETRY_STATUS = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class FetchResult:
    url: str  # final URL after redirects
    status: int
    text: str


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        return url
    if not urlparse(url).scheme:
        url = "https://" + url.lstrip("/")
    return url


def host_key(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


class Fetcher:
    def __init__(
        self,
        client: httpx.Client | None = None,
        user_agent: str = "pe-origination-agents/0.1",
        delay_seconds: float = 1.0,
        respect_robots: bool = True,
        cache_dir: str | Path | None = None,
        retries: int = 2,
        backoff_seconds: float = 1.0,
    ) -> None:
        self.client = client or httpx.Client(timeout=15.0, follow_redirects=True)
        self.user_agent = user_agent
        self.delay = delay_seconds
        self.respect_robots = respect_robots
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.retries = retries
        self.backoff = backoff_seconds
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last_hit: dict[str, float] = {}
        self._lock = threading.Lock()

    # ---------- robots.txt ----------
    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        if base not in self._robots:
            rp: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
            try:
                r = self.client.get(f"{base}/robots.txt", headers={"User-Agent": self.user_agent})
                if r.status_code >= 400:
                    rp = None  # no robots.txt: everything allowed
                else:
                    rp.parse(r.text.splitlines())
            except httpx.HTTPError:
                rp = None
            self._robots[base] = rp
        rp = self._robots[base]
        return True if rp is None else rp.can_fetch(self.user_agent, url)

    # ---------- rate limit ----------
    def _wait_turn(self, url: str) -> None:
        key = host_key(url)
        with self._lock:
            now = time.monotonic()
            wait = self._last_hit.get(key, 0) + self.delay - now
            self._last_hit[key] = max(now, now + wait)
        if wait > 0:
            time.sleep(wait)

    # ---------- cache ----------
    def _cache_path(self, url: str) -> Path | None:
        if not self.cache_dir:
            return None
        return self.cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".html")

    def get(self, url: str) -> FetchResult | None:
        """Return the page, or None if blocked by robots, not HTML or failing."""
        url = normalize_url(url)
        if not url or not self.allowed(url):
            return None
        cache = self._cache_path(url)
        if cache and cache.exists():
            return FetchResult(url, 200, cache.read_text(encoding="utf-8"))

        for attempt in range(self.retries + 1):
            self._wait_turn(url)
            try:
                r = self.client.get(url, headers={"User-Agent": self.user_agent, "Accept": "text/html"})
            except httpx.HTTPError:
                if attempt == self.retries:
                    return None
                time.sleep(self.backoff * 2**attempt)
                continue
            if r.status_code in RETRY_STATUS and attempt < self.retries:
                time.sleep(self.backoff * 2**attempt)
                continue
            if r.status_code >= 400:
                return None
            ctype = r.headers.get("content-type", "")
            if "html" not in ctype or len(r.content) > MAX_BYTES:
                return None
            if cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(r.text, encoding="utf-8")
            return FetchResult(str(r.url), r.status_code, r.text)
        return None
