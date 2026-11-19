"""The ledger: every thread ID ever fetched or processed, with per-stage status.

Each stage is tracked separately so a crash mid-run never re-pays for work already done:
  fetched_at    raw post + comments saved to data/raw/
  images_done   image selection/description finished (even if no images were described)
  extracted_at  data/threads/<id>.json written
  book, chapter, secondary_chapter   where the thread was filed
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from . import config as C
from .store import read_json, write_json

STAGES = ("fetched_at", "images_done", "extracted_at", "chapter")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Ledger:
    def __init__(self, path=C.LEDGER):
        self.path = path
        self.data: dict = read_json(path, default={"threads": {}})
        self.data.setdefault("threads", {})

    @property
    def threads(self) -> dict:
        return self.data["threads"]

    def get(self, thread_id: str) -> dict:
        return self.threads.get(thread_id, {})

    def has(self, thread_id: str, stage: str = "fetched_at") -> bool:
        return bool(self.get(thread_id).get(stage))

    def missing(self, ids: Iterable[str], stage: str) -> list[str]:
        return [i for i in ids if not self.has(i, stage)]

    def mark(self, thread_id: str, **fields) -> None:
        entry = self.threads.setdefault(thread_id, {})
        entry.update(fields)

    def save(self) -> None:
        write_json(self.path, self.data)
