"""Source adapters. Each module defines one or more `Adapter` subclasses; they register themselves.

An adapter answers one question deterministically: what does this source list right now?
It returns structured `Listing`s when the source publishes data, or `Snapshot`s of the pages it
watches when it does not. Raise on anything unexpected: a loud failure beats a silent empty result.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import ClassVar

from ..models import Result

REGISTRY: dict[str, type[Adapter]] = {}
PLAN = "_plan"  # cache key where chunks() leaves its plan for the chunks; runner.record never saves it
CHUNK_LEAST = 25  # spread() makes no chunk with fewer pages to fetch than this


class Adapter:
    name: ClassVar[str]  # stable id, used as the source key in the database
    title: ClassVar[str]  # human name for the digest
    url: ClassVar[str]  # page a human would open
    every_hours: ClassVar[float] = 4  # run cadence
    min_listings: ClassVar[int] = 0  # fewer results than this is treated as a broken adapter
    parallel: ClassVar[int] = 1  # how many of this source's chunks may run at once (each on a different worker)

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        if getattr(cls, "name", None):
            if cls.name in REGISTRY:
                raise ValueError(f"duplicate adapter {cls.name}")
            REGISTRY[cls.name] = cls

    def __init__(self, cache: dict | None = None):
        # Persistent per-source scratch space (json-serializable), saved after a successful run.
        # Lets big sources re-fetch only what changed (e.g. Reside re-reads a page only when its
        # sitemap lastmod moves). Adapters must work with an empty cache.
        self.cache: dict = cache if cache is not None else {}

    def fetch(self) -> Result:  # pragma: no cover - abstract
        raise NotImplementedError

    # Optional chunking, for sources big enough to spread across the worker fleet (Reside, SANEL).
    # A run plans chunks, workers fetch them independently (each with a copy of the cache), and the
    # run merges every chunk's Result before recording, so "gone" detection still sees the whole source.
    # The defaults make every adapter one chunk; override all three together. Paged sources (Reside, SANEL)
    # keep one top-level cache key per page, plan with spread() into self.cache[PLAN], and use fetch_serial().

    def chunks(self) -> list[str]:
        """Chunk keys for this run, decided from the cache and/or a cheap index fetch."""
        return [""]

    def fetch_chunk(self, key: str) -> Result:
        """Fetch one chunk; may update self.cache, which is returned to the run as this chunk's delta."""
        return self.fetch()

    def merge_cache(self, parts: list[dict]) -> dict:
        """Combine the caches the chunks returned (default: a key-wise update, later chunks win)."""
        out = dict(self.cache)
        for p in parts:
            out.update(p)
        return out

    def fetch_serial(self) -> Result:
        """Every chunk in turn on this instance: fetch() for a chunked adapter whose chunks write only their own
        top-level cache keys (so one instance ends with the cache the fleet's merge would produce)."""
        res = Result()
        for k in self.chunks():
            res.extend(self.fetch_chunk(k))
        return res


def spread(todo: list, keep: list, parallel: int) -> dict[str, dict]:
    """A paged source's plan: pages to fetch dealt round-robin into up to `parallel` chunks of at least CHUNK_LEAST
    each, and pages served from the cache dealt over the same chunks. Keys are 'i/n'."""
    n = max(1, min(parallel, len(todo) // CHUNK_LEAST))
    return {f"{i + 1}/{n}": {"fetch": todo[i::n], "keep": keep[i::n]} for i in range(n)}


def load_all() -> dict[str, type[Adapter]]:
    for m in pkgutil.iter_modules(__path__):
        if not m.name.startswith("_"):
            importlib.import_module(f"{__name__}.{m.name}")
    return REGISTRY
