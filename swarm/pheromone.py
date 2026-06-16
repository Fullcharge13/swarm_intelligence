"""PheromoneBoard — weighted, time-decaying stigmergic trail store."""

from __future__ import annotations

import hashlib
import json
import time
import warnings
from pathlib import Path
from typing import Any


def goal_hash(goal: str) -> str:
    """Return a stable 8-char hex prefix for trail key namespacing."""
    return hashlib.md5(goal.encode()).hexdigest()[:8]


class PheromoneBoard:
    """In-process pheromone trail store for ant-colony-inspired task routing.

    Trail entries are keyed by namespaced strings (e.g. ``"scout/<hash>/title"``)
    and carry a weight, a timestamp, and arbitrary data.  Weights decay over
    time via :meth:`evaporate` and are reinforced via :meth:`deposit`.
    """

    def __init__(self) -> None:
        # Each value: {"weight": float, "timestamp": float, "data": Any}
        self._trails: dict[str, dict[str, Any]] = {}

    def deposit(self, key: str, weight: float, data: Any = None) -> None:
        """Add *weight* to the trail at *key*, creating it if absent."""
        if key in self._trails:
            self._trails[key]["weight"] += weight
            self._trails[key]["timestamp"] = time.time()
            if data is not None:
                self._trails[key]["data"] = data
        else:
            self._trails[key] = {
                "weight": weight,
                "timestamp": time.time(),
                "data": data,
            }

    def evaporate(self, decay_rate: float = 0.1) -> None:
        """Multiply all weights by ``(1 - decay_rate)`` and prune those below 0.01."""
        if not 0.0 < decay_rate < 1.0:
            raise ValueError(f"decay_rate must be in (0, 1), got {decay_rate}")
        to_delete = [
            key
            for key, entry in self._trails.items()
            if entry["weight"] * (1 - decay_rate) < 0.01
        ]
        for key in to_delete:
            del self._trails[key]
        for key in list(self._trails):
            self._trails[key]["weight"] *= 1 - decay_rate

    def get(self, key: str) -> dict[str, Any] | None:
        """Return the raw entry dict for *key*, or None if absent."""
        return self._trails.get(key)

    def strongest(self, prefix: str = "", n: int = 5) -> list[tuple[str, dict[str, Any]]]:
        """Return up to *n* entries whose keys start with *prefix*, sorted by weight desc."""
        matching = [(k, v) for k, v in self._trails.items() if k.startswith(prefix)]
        matching.sort(key=lambda x: x[1]["weight"], reverse=True)
        return matching[:n]

    def load(self, path: Path = Path(".swarm/pheromones.json")) -> None:
        """Load trails from *path*.  Missing file is silently ignored; corrupt file warns."""
        if not path.exists():
            return
        try:
            with open(path) as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError(f"Expected dict, got {type(data).__name__}")
            self._trails = data
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            warnings.warn(
                f"PheromoneBoard: failed to load {path}: {exc}. Starting fresh.",
                RuntimeWarning,
                stacklevel=2,
            )
            self._trails = {}

    def save(self, path: Path = Path(".swarm/pheromones.json")) -> None:
        """Persist trails to *path* as JSON.  Failure warns but does not raise."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(self._trails, f, indent=2)
            tmp.replace(path)
        except (OSError, TypeError) as exc:
            warnings.warn(
                f"PheromoneBoard: failed to save {path}: {exc}.",
                RuntimeWarning,
                stacklevel=2,
            )
