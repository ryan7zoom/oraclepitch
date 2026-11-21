"""Small HTTP helper: one session, retries with backoff on 429/5xx/network errors."""

import logging
import time

import requests

logger = logging.getLogger("engine.data.http")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "*/*"})
    return s


def get(session, url: str, retries: int = 3, backoff: float = 2.0, timeout: int = 30):
    """GET with retries. Returns the response for any status other than
    429/5xx (so callers can handle 404 themselves); raises RuntimeError
    when every try failed."""
    last = None
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=timeout)
            if r.status_code not in (429, 500, 502, 503, 504):
                return r
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"GET failed after {retries} tries: {url} ({last})")
