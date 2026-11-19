"""Collector registry. `config.yaml` -> `collector:` picks the active one."""

from __future__ import annotations

from lib import config as C

from .base import Collector


def get_collector(name: str | None = None) -> Collector:
    name = name or C.config()["collector"]
    if name == "fatihtahta":
        from .apify_fatihtahta import FatihtahtaCollector
        return FatihtahtaCollector()
    if name == "harshmaur":
        from .apify_harshmaur import HarshmaurCollector
        return HarshmaurCollector()
    raise ValueError(f"Unknown collector {name!r}. Options: fatihtahta, harshmaur.")
