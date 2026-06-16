"""Task model and task graph management.

A Task is the atomic unit of work in the swarm.  Tasks form a directed
acyclic graph (DAG): a parent task is not complete until all its children
are complete.  The orchestrator traverses this graph to schedule work.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"       # created, not yet assigned
    ASSIGNED = "assigned"     # given to an agent
    RUNNING = "running"       # agent is actively working
    DONE = "done"             # completed successfully
    FAILED = "failed"         # terminal failure
    BLOCKED = "blocked"       # waiting on dependencies


class TaskPriority(int, Enum):
    LOW = 1
    NORMAL = 5
    HIGH = 8
    CRITICAL = 10


class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    title: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    priority: int = 5  # plain int; use TaskPriority constants as named aliases

    # Graph relationships
    parent_id: str | None = None
    child_ids: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)  # IDs of tasks that must finish first

    # Assignment
    assigned_to: str | None = None  # agent ID

    # Results written back by the executing agent
    result: Any = None
    error: str | None = None

    # Metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    depth: int = 0  # nesting level; root = 0

    # Hints the orchestrator uses when assigning
    required_role: str | None = None   # e.g. "researcher", "coder", "reviewer"
    complexity: Literal["simple", "medium", "complex"] = "medium"  # drives model tier selection
    context: dict[str, Any] = Field(default_factory=dict)  # arbitrary extra data

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)

    def mark_done(self, result: Any) -> None:
        self.result = result
        self.status = TaskStatus.DONE
        self.touch()

    def mark_failed(self, error: str) -> None:
        self.error = error
        self.status = TaskStatus.FAILED
        self.touch()

    def is_terminal(self) -> bool:
        return self.status in (TaskStatus.DONE, TaskStatus.FAILED)

    def __repr__(self) -> str:
        return f"Task(id={self.id!r}, title={self.title!r}, status={self.status.value})"


class TaskGraph:
    """Maintains the full DAG of tasks and exposes scheduling queries."""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(self, task: Task) -> Task:
        self._tasks[task.id] = task
        if task.parent_id and task.parent_id in self._tasks:
            parent = self._tasks[task.parent_id]
            if task.id not in parent.child_ids:
                parent.child_ids.append(task.id)
        return task

    def get(self, task_id: str) -> Task:
        return self._tasks[task_id]

    def update(self, task: Task) -> None:
        task.touch()
        self._tasks[task.id] = task

    # ------------------------------------------------------------------
    # Scheduling queries
    # ------------------------------------------------------------------

    def ready_tasks(self) -> list[Task]:
        """Tasks that are PENDING with all dependencies met."""
        result = []
        for task in self._tasks.values():
            if task.status != TaskStatus.PENDING:
                continue
            if task.child_ids:
                # Parent tasks are scheduled after children finish
                continue
            # Deps dropped by max_depth are absent from the graph — fail fast (#4)
            missing = [d for d in task.depends_on if d not in self._tasks]
            if missing:
                task.mark_failed(f"Dependencies not in graph (dropped by max_depth): {missing}")
                continue
            if all(self._tasks[d].status == TaskStatus.DONE for d in task.depends_on):
                result.append(task)
        return sorted(result, key=lambda t: -t.priority)

    def propagate_failures(self) -> None:
        """Cascade FAILED status to PENDING tasks whose dependencies failed (#2).

        Runs until no new failures are produced so transitive chains resolve
        in a single call rather than one-step-per-dispatch-tick.
        """
        changed = True
        while changed:
            changed = False
            for task in self._tasks.values():
                if task.status != TaskStatus.PENDING:
                    continue
                failed_deps = [
                    d for d in task.depends_on
                    if d in self._tasks and self._tasks[d].status == TaskStatus.FAILED
                ]
                if failed_deps:
                    task.mark_failed(f"Upstream dependency failed: {failed_deps}")
                    changed = True

    def all_done(self) -> bool:
        return all(t.is_terminal() for t in self._tasks.values())

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for t in self._tasks.values():
            counts[t.status.value] = counts.get(t.status.value, 0) + 1
        return counts

    def __len__(self) -> int:
        return len(self._tasks)

    def values(self):  # type: ignore[override]
        return self._tasks.values()
