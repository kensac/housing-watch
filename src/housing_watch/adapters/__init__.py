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


class Adapter:
    name: ClassVar[str]  # stable id, used as the source key in the database
    title: ClassVar[str]  # human name for the digest
    url: ClassVar[str]  # page a human would open
    every_hours: ClassVar[float] = 24  # run cadence
    min_listings: ClassVar[int] = 0  # fewer results than this is treated as a broken adapter

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


def load_all() -> dict[str, type[Adapter]]:
    for m in pkgutil.iter_modules(__path__):
        if not m.name.startswith("_"):
            importlib.import_module(f"{__name__}.{m.name}")
    return REGISTRY
