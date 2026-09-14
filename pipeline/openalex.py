"""Minimal OpenAlex client: polite rate limiting, retries, on-disk cache.

OpenAlex meters usage. Without a key an IP gets about $0.10 of usage per day
(roughly 100 search calls); a free account's API key gets $1 per day. Single-
record lookups by id are free, so the monthly collector costs nothing; only the
one-time author matching spends budget. Set OPENALEX_API_KEY in the environment.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import BUILD_DIR, REPO_ROOT


def _load_dotenv() -> None:
    """Read KEY=value lines from a gitignored .env at the repo root, if present."""
    env = REPO_ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

BASE = "https://api.openalex.org"
CACHE_DIR = BUILD_DIR / "cache" / "openalex"
MAX_WAIT = 600               # never sleep longer than this on a 429; raise BudgetExhausted instead
MIN_INTERVAL = 0.4           # seconds between requests; OpenAlex 429s well below its nominal 10/s without mailto
_last_call = 0.0


class BudgetExhausted(RuntimeError):
    """The daily OpenAlex usage budget is spent; try again after midnight UTC."""


def get(path: str, params: dict | None = None, use_cache: bool = True) -> dict:
    """GET a JSON endpoint, e.g. get('/authors', {'search': 'Guang Tian'})."""
    global _last_call
    params = dict(params or {})
    # Cache key excludes credentials so cached responses survive key changes.
    cache_url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    api_key = os.environ.get("OPENALEX_API_KEY")
    if api_key:
        params["api_key"] = api_key
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"

    key = hashlib.sha1(cache_url.encode()).hexdigest()
    cache = CACHE_DIR / f"{key}.json"
    if use_cache and cache.exists():
        return json.loads(cache.read_text())

    for attempt in range(7):
        wait = MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "planning-citation-metrics/0.1"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data))
            return data
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 6:
                retry_after = e.headers.get("Retry-After") if e.headers else None
                delay = float(retry_after) if retry_after and retry_after.isdigit() else min(90, 3 * 2 ** attempt)
                if delay > MAX_WAIT:
                    # Daily budget exhausted: Retry-After points at midnight UTC.
                    # Fail now so callers can save partial results and resume tomorrow.
                    raise BudgetExhausted(f"OpenAlex budget exhausted; resets in {delay / 3600:.1f} h") from e
                print(f"  openalex {e.code}; waiting {delay:.0f}s", file=sys.stderr)
                time.sleep(delay)
                continue
            print(f"  openalex error {e.code} for {cache_url}", file=sys.stderr)
            raise
        except urllib.error.URLError:
            if attempt < 6:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError("unreachable")


def search_institutions(query: str, per_page: int = 10) -> list[dict]:
    return get("/institutions", {"search": query, "per-page": per_page}).get("results", [])


def search_authors(query: str, per_page: int = 25, filter: str | None = None) -> list[dict]:
    """Full-text author search (the expensive kind: ~$0.001 per call)."""
    params = {"search": query, "per-page": per_page}
    if filter:
        params["filter"] = filter
    return get("/authors", params).get("results", [])


def filter_authors(name: str, per_page: int = 25, extra_filter: str | None = None) -> list[dict]:
    """Name lookup via display_name.search filter (billed as list+filter, ~10x cheaper)."""
    flt = f"display_name.search:{name}"
    if extra_filter:
        flt += f",{extra_filter}"
    return get("/authors", {"filter": flt, "per-page": per_page}).get("results", [])


def get_author(author_id: str) -> dict:
    return get(f"/authors/{author_id}")


def short_id(openalex_url: str | None) -> str | None:
    """'https://openalex.org/A5023456789' -> 'A5023456789'."""
    return openalex_url.rsplit("/", 1)[-1] if openalex_url else None
