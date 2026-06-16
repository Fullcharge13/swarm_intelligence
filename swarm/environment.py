"""Shared blackboard / environment.

The blackboard is the single source of truth agents read from and write to.
It acts as stigmergic memory: agents leave results here, and other agents
discover and build on them without direct coupling.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any


class Entry:
    __slots__ = ("key", "value", "author", "timestamp", "tags")

    def __init__(self, key: str, value: Any, author: str, tags: list[str] | None = None) -> None:
        self.key = key
        self.value = value
        self.author = author
        self.timestamp = datetime.now(timezone.utc)
        self.tags = tags or []

    def __repr__(self) -> str:
        return f"Entry(key={self.key!r}, author={self.author!r}, ts={self.timestamp.isoformat()})"


class Blackboard:
    """Thread-safe key-value store shared across all agents.

    Design rules:
    - Keys are namespaced strings, e.g. ``"task/<id>/result"``
    - Values are arbitrary Python objects (agents must handle their own
      serialization if persistence is added later)
    - History of writes is preserved so agents can observe change over time
    """

    def __init__(self) -> None:
        self._store: dict[str, list[Entry]] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def write(self, key: str, value: Any, *, author: str, tags: list[str] | None = None) -> Entry:
        entry = Entry(key, value, author, tags)
        with self._lock:
            self._store.setdefault(key, []).append(entry)
        return entry

    def read(self, key: str) -> Any | None:
        """Return the most recent value for *key*, or None if absent."""
        with self._lock:
            entries = self._store.get(key)
        return entries[-1].value if entries else None

    def read_entry(self, key: str) -> Entry | None:
        with self._lock:
            entries = self._store.get(key)
        return entries[-1] if entries else None

    def history(self, key: str) -> list[Entry]:
        with self._lock:
            return list(self._store.get(key, []))

    def keys(self, prefix: str = "") -> list[str]:
        with self._lock:
            return [k for k in self._store if k.startswith(prefix)]

    def snapshot(self) -> dict[str, Any]:
        """Return {key: latest_value} for all keys — useful for agent context."""
        with self._lock:
            return {k: v[-1].value for k, v in self._store.items()}

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def task_result(self, task_id: str) -> Any | None:
        return self.read(f"task/{task_id}/result")

    def write_task_result(self, task_id: str, value: Any, *, author: str) -> Entry:
        return self.write(f"task/{task_id}/result", value, author=author, tags=["result"])

    def write_note(self, key: str, note: str, *, author: str) -> Entry:
        return self.write(f"note/{key}", note, author=author, tags=["note"])
