"""A very small SearchApi client. Standard library only.

Every call goes to one endpoint with an `engine` parameter:

    GET https://www.searchapi.io/api/v1/search?engine=...&...

The client also keeps a list of the calls it made (engine, search id, seconds),
because a pause should be able to show exactly which searches it relied on.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

BASE_URL = "https://www.searchapi.io/api/v1/search"
KEY_REJECTED = "SearchApi rejected the API key (HTTP 401). Check SEARCHAPI_API_KEY."


class SearchApiError(RuntimeError):
    """The search could not be completed (network, auth, rate limit, server)."""


@dataclass
class Call:
    engine: str
    search_id: str | None
    seconds: float
    cached: bool = False


@dataclass
class SearchApi:
    """`cache_dir` makes repeat calls free: a response is saved to disk the
    first time and read back afterwards. Useful while tuning the rubric."""

    api_key: str | None = None
    cache_dir: Path | None = None
    timeout: float = 30.0
    calls: list[Call] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.api_key = self.api_key or os.environ.get("SEARCHAPI_API_KEY")
        if self.cache_dir is not None:
            self.cache_dir = Path(self.cache_dir)

    def search(self, engine: str, **params) -> dict:
        """Run one search. Returns the parsed JSON.

        A search that worked but found nothing comes back as HTTP 200 with an
        "error" key (for example "... didn't return any results."). That is a
        valid answer, not a failure, so it is returned as-is.
        """
        params = {k: v for k, v in params.items() if v is not None}
        cache_file = self._cache_file(engine, params)
        if cache_file is not None and cache_file.exists():
            data = json.loads(cache_file.read_text())
            self.calls.append(Call(engine, _search_id(data), 0.0, cached=True))
            return data

        if not self.api_key:
            raise SearchApiError(
                "No API key. Set SEARCHAPI_API_KEY (get one at https://www.searchapi.io/)."
            )
        url = BASE_URL + "?" + urllib.parse.urlencode({"engine": engine, **params})
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self.api_key}"}
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise SearchApiError(f"{engine}: {KEY_REJECTED}") from exc
            detail = " ".join(exc.read().decode("utf-8", "replace").split())[:200]
            raise SearchApiError(f"{engine}: HTTP {exc.code} {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise SearchApiError(f"{engine}: {exc}") from exc
        seconds = round(time.monotonic() - started, 2)

        self.calls.append(Call(engine, _search_id(data), seconds))
        if cache_file is not None:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(data))
        return data

    def _cache_file(self, engine: str, params: dict) -> Path | None:
        if self.cache_dir is None:
            return None
        key = json.dumps({"engine": engine, **params}, sort_keys=True)
        digest = hashlib.sha1(key.encode()).hexdigest()[:16]
        return self.cache_dir / f"{engine}-{digest}.json"


def _search_id(data: dict) -> str | None:
    return (data.get("search_metadata") or {}).get("id")
