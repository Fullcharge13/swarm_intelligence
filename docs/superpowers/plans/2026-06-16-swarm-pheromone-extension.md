# Swarm Pheromone Extension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `swarm_intelligence` with a `PheromoneBoard`, a `scout` agent role, and Claude Code harness integration (a `/swarm` skill + a background `PostToolUse` hook) so the swarm learns from its own history and pre-loads context before the user invokes it.

**Architecture:** `PheromoneBoard` is a weighted, time-decaying key-value store (persisted to `.swarm/pheromones.json`). Scout agents probe the task space cheaply and deposit trail entries keyed by a stable MD5 goal hash. The `Orchestrator` reads the strongest trails and injects them as blackboard notes before assigning decomposer tasks, then reinforces or suppresses trails based on task outcomes. A background hook fires scouts passively; the `/swarm` Claude Code skill invokes the full scout-first pipeline on demand.

**Tech Stack:** Python 3.11+, `hashlib` (stdlib), `pytest`, `pytest-asyncio`, `unittest.mock` — no new dependencies.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `swarm/pheromone.py` | `PheromoneBoard` class + `goal_hash()` helper |
| Create | `swarm/hooks/__init__.py` | Empty package marker |
| Create | `swarm/hooks/scout_hook.py` | Standalone hook script invoked by Claude Code |
| Create | `.claude/settings.json` | PostToolUse hook registration |
| Create | `.claude/skills/swarm.md` | `/swarm` Claude Code skill |
| Create | `tests/test_pheromone.py` | Unit tests for `PheromoneBoard` |
| Create | `tests/test_integration.py` | End-to-end scout → forager trail test |
| Modify | `swarm/llm.py:47-62` | Add `scout` to `ROLE_COMPLEXITY` and `ROLE_MAX_TOKENS` |
| Modify | `swarm/agent.py:46-86` | Add `scout` to `ROLE_PROMPTS` |
| Modify | `swarm/agent.py:110-138` | Add `pheromone_board` param to `__init__` |
| Modify | `swarm/agent.py:165-170` | Route `scout` role in `run()` |
| Modify | `swarm/agent.py` | Add `_run_scout()` method |
| Modify | `swarm/orchestrator.py:62-83` | Add `pheromone_board` param to `__init__` |
| Modify | `swarm/orchestrator.py:151-161` | Call `_inject_trail_hints` in `_launch_agent_task` |
| Modify | `swarm/orchestrator.py:237-247` | Add trail reinforcement in `_run_agent` |
| Modify | `swarm/orchestrator.py:292-310` | Pass `pheromone_board` when creating agents |
| Modify | `swarm/orchestrator.py` | Add `_inject_trail_hints()` method |
| Modify | `swarm/__init__.py` | Export `PheromoneBoard` |

---

## Task 1: `PheromoneBoard` — core class and persistence

**Files:**
- Create: `swarm/pheromone.py`
- Create: `tests/test_pheromone.py`

- [ ] **Step 1.1 — Write failing tests**

Create `tests/test_pheromone.py`:

```python
"""Unit tests for PheromoneBoard — no API calls."""

import json
import warnings
from pathlib import Path

import pytest

from swarm.pheromone import PheromoneBoard, goal_hash


class TestGoalHash:
    def test_same_string_gives_same_hash(self):
        assert goal_hash("Write a blog post") == goal_hash("Write a blog post")

    def test_different_strings_give_different_hashes(self):
        assert goal_hash("Task A") != goal_hash("Task B")

    def test_returns_eight_chars(self):
        assert len(goal_hash("anything")) == 8


class TestDeposit:
    def test_deposit_creates_entry(self):
        board = PheromoneBoard()
        board.deposit("scout/abc/research", 1.0, data={"title": "Research"})
        entry = board.get("scout/abc/research")
        assert entry is not None
        assert entry["weight"] == pytest.approx(1.0)
        assert entry["data"] == {"title": "Research"}

    def test_deposit_reinforces_existing_weight(self):
        board = PheromoneBoard()
        board.deposit("key", 1.0)
        board.deposit("key", 0.5)
        assert board.get("key")["weight"] == pytest.approx(1.5)

    def test_deposit_updates_data_on_reinforce(self):
        board = PheromoneBoard()
        board.deposit("key", 1.0, data={"v": 1})
        board.deposit("key", 0.5, data={"v": 2})
        assert board.get("key")["data"] == {"v": 2}


class TestEvaporate:
    def test_evaporate_reduces_weight(self):
        board = PheromoneBoard()
        board.deposit("key", 1.0)
        board.evaporate(decay_rate=0.1)
        assert board.get("key")["weight"] == pytest.approx(0.9)

    def test_evaporate_prunes_entries_below_threshold(self):
        board = PheromoneBoard()
        board.deposit("key", 0.005)
        board.evaporate(decay_rate=0.1)
        assert board.get("key") is None

    def test_evaporate_keeps_strong_entries(self):
        board = PheromoneBoard()
        board.deposit("key", 5.0)
        board.evaporate(decay_rate=0.1)
        assert board.get("key") is not None


class TestStrongest:
    def test_returns_top_n_sorted_by_weight(self):
        board = PheromoneBoard()
        board.deposit("scout/abc/a", 3.0)
        board.deposit("scout/abc/b", 1.0)
        board.deposit("scout/abc/c", 2.0)
        result = board.strongest("scout/abc/", n=2)
        assert len(result) == 2
        assert result[0][0] == "scout/abc/a"
        assert result[1][0] == "scout/abc/c"

    def test_prefix_filters_unrelated_keys(self):
        board = PheromoneBoard()
        board.deposit("scout/abc/task", 1.0)
        board.deposit("other/key", 5.0)
        result = board.strongest("scout/abc/")
        assert all(k.startswith("scout/abc/") for k, _ in result)

    def test_empty_board_returns_empty_list(self):
        board = PheromoneBoard()
        assert board.strongest("scout/") == []

    def test_n_limits_results(self):
        board = PheromoneBoard()
        for i in range(10):
            board.deposit(f"scout/abc/{i}", float(i))
        result = board.strongest("scout/abc/", n=3)
        assert len(result) == 3


class TestPersistence:
    def test_save_and_load_round_trip(self, tmp_path):
        board = PheromoneBoard()
        board.deposit("scout/abc/t1", 2.0, data={"title": "T1"})
        path = tmp_path / "pheromones.json"
        board.save(path)

        board2 = PheromoneBoard()
        board2.load(path)
        entry = board2.get("scout/abc/t1")
        assert entry is not None
        assert entry["weight"] == pytest.approx(2.0)
        assert entry["data"] == {"title": "T1"}

    def test_load_missing_file_gives_empty_board(self, tmp_path):
        board = PheromoneBoard()
        board.load(tmp_path / "nonexistent.json")
        assert board.strongest() == []

    def test_load_corrupt_file_warns_and_resets(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not valid json")
        board = PheromoneBoard()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            board.load(path)
        assert len(w) == 1
        assert issubclass(w[0].category, RuntimeWarning)
        assert board.strongest() == []

    def test_save_creates_parent_dirs(self, tmp_path):
        board = PheromoneBoard()
        board.deposit("k", 1.0)
        nested = tmp_path / "a" / "b" / "pheromones.json"
        board.save(nested)
        assert nested.exists()
```

- [ ] **Step 1.2 — Run tests to confirm they fail**

```
pytest tests/test_pheromone.py -v
```

Expected: `ModuleNotFoundError: No module named 'swarm.pheromone'`

- [ ] **Step 1.3 — Implement `swarm/pheromone.py`**

Create `swarm/pheromone.py`:

```python
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
            self._trails[key]["data"] = data
        else:
            self._trails[key] = {
                "weight": weight,
                "timestamp": time.time(),
                "data": data,
            }

    def evaporate(self, decay_rate: float = 0.1) -> None:
        """Multiply all weights by ``(1 - decay_rate)`` and prune those below 0.01."""
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
                self._trails = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
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
            with open(path, "w") as f:
                json.dump(self._trails, f, indent=2)
        except OSError as exc:
            warnings.warn(
                f"PheromoneBoard: failed to save {path}: {exc}.",
                RuntimeWarning,
                stacklevel=2,
            )
```

- [ ] **Step 1.4 — Run tests to confirm they pass**

```
pytest tests/test_pheromone.py -v
```

Expected: all tests pass.

- [ ] **Step 1.5 — Run full suite to confirm no regressions**

```
pytest -v
```

Expected: 35 existing tests + new pheromone tests all green.

- [ ] **Step 1.6 — Commit**

```bash
git add swarm/pheromone.py tests/test_pheromone.py
git commit -m "feat: add PheromoneBoard with weighted trails, evaporation, and persistence"
```

---

## Task 2: Scout agent role

**Files:**
- Modify: `swarm/llm.py` (lines 47-62)
- Modify: `swarm/agent.py` (ROLE_PROMPTS, `__init__`, `run`, add `_run_scout`)
- Modify: `tests/test_agent.py` (add `TestScoutRun` class)

- [ ] **Step 2.1 — Write failing tests**

Add this class to the bottom of `tests/test_agent.py`:

```python
class TestScoutRun:
    @pytest.mark.asyncio
    async def test_scout_deposits_trails_on_pheromone_board(self):
        from swarm.pheromone import PheromoneBoard, goal_hash
        board = PheromoneBoard()
        hints_json = json.dumps({
            "hints": [
                {"title": "Research topic", "complexity": "simple"},
                {"title": "Write draft", "complexity": "medium"},
            ]
        })
        llm = make_mock_llm(hints_json)
        agent = SwarmAgent(
            role="scout",
            blackboard=Blackboard(),
            bus=MessageBus(),
            llm=llm,
            pheromone_board=board,
        )
        task = Task(title="Write a blog post", description="...", required_role="scout")
        await agent.run(task)

        assert task.status == TaskStatus.DONE
        gh = goal_hash("Write a blog post")
        trails = board.strongest(f"scout/{gh}/")
        assert len(trails) == 2
        titles = [e["data"]["title"] for _, e in trails]
        assert "Research topic" in titles
        assert "Write draft" in titles

    @pytest.mark.asyncio
    async def test_scout_marks_done_with_hint_count(self):
        from swarm.pheromone import PheromoneBoard
        board = PheromoneBoard()
        hints_json = json.dumps({"hints": [{"title": "T", "complexity": "simple"}]})
        agent = SwarmAgent(
            role="scout",
            blackboard=Blackboard(),
            bus=MessageBus(),
            llm=make_mock_llm(hints_json),
            pheromone_board=board,
        )
        task = Task(title="Goal", description="...")
        await agent.run(task)
        assert task.status == TaskStatus.DONE
        assert task.result == {"hint_count": 1}

    @pytest.mark.asyncio
    async def test_scout_handles_invalid_json(self):
        agent = SwarmAgent(
            role="scout",
            blackboard=Blackboard(),
            bus=MessageBus(),
            llm=make_mock_llm("not json"),
        )
        task = Task(title="Goal", description="...")
        await agent.run(task)
        assert task.status == TaskStatus.FAILED

    @pytest.mark.asyncio
    async def test_scout_no_pheromone_board_still_runs(self):
        hints_json = json.dumps({"hints": [{"title": "T", "complexity": "simple"}]})
        agent = SwarmAgent(
            role="scout",
            blackboard=Blackboard(),
            bus=MessageBus(),
            llm=make_mock_llm(hints_json),
            pheromone_board=None,
        )
        task = Task(title="Goal", description="...")
        await agent.run(task)
        assert task.status == TaskStatus.DONE
```

- [ ] **Step 2.2 — Run tests to confirm they fail**

```
pytest tests/test_agent.py::TestScoutRun -v
```

Expected: `FAILED` — `SwarmAgent.__init__` has no `pheromone_board` param; `scout` not in `ROLE_PROMPTS`.

- [ ] **Step 2.3 — Add scout to `ROLE_COMPLEXITY` and `ROLE_MAX_TOKENS` in `swarm/llm.py`**

In `swarm/llm.py`, update `ROLE_COMPLEXITY` (around line 47) to add:

```python
ROLE_COMPLEXITY: dict[str, str] = {
    "decomposer":  "complex",
    "executor":    "medium",
    "reviewer":    "medium",
    "synthesizer": "complex",
    "_summarizer": "simple",
    "scout":       "simple",   # probing only — Haiku is sufficient
}
```

And update `ROLE_MAX_TOKENS` (around line 56) to add:

```python
ROLE_MAX_TOKENS: dict[str, int] = {
    "decomposer":  1_024,
    "executor":    2_048,
    "reviewer":    2_048,
    "synthesizer": 6_144,
    "_summarizer":   256,
    "scout":         512,   # JSON hints only
}
```

- [ ] **Step 2.4 — Add scout to `ROLE_PROMPTS` in `swarm/agent.py`**

In `swarm/agent.py`, add this entry to the `ROLE_PROMPTS` dict (after `"synthesizer"`):

```python
    "scout": """\
You are a scout agent in a collaborative AI swarm.
Analyse the goal and predict the likely subtasks WITHOUT executing them.
Output ONLY valid JSON — no markdown fences, no extra text:
{
  "hints": [
    {
      "title": "<short subtask title>",
      "complexity": "<simple|medium|complex>"
    }
  ]
}

Complexity guide:
  simple  — lookup, formatting, short factual answer
  medium  — analysis, drafting, moderate reasoning
  complex — deep research, multi-step reasoning
""",
```

- [ ] **Step 2.5 — Add `pheromone_board` param to `SwarmAgent.__init__`**

In `swarm/agent.py`, update the imports at the top to add TYPE_CHECKING import for PheromoneBoard:

```python
from __future__ import annotations

import asyncio
import json
import uuid
from typing import TYPE_CHECKING, Any

from .environment import Blackboard
from .llm import LLMClient, ROLE_MAX_TOKENS, make_client, quick_summarize
from .messaging import Message, MessageBus
from .task import Task, TaskPriority, TaskStatus

if TYPE_CHECKING:
    from .orchestrator import Orchestrator
    from .pheromone import PheromoneBoard
```

Then update `SwarmAgent.__init__` signature to add `pheromone_board`:

```python
    def __init__(
        self,
        role: str = "executor",
        complexity: str = "medium",
        system: str | None = None,
        blackboard: Blackboard | None = None,
        bus: MessageBus | None = None,
        orchestrator: "Orchestrator | None" = None,
        llm: LLMClient | None = None,
        pheromone_board: "PheromoneBoard | None" = None,
    ) -> None:
        self.id = f"{role}-{str(uuid.uuid4())[:6]}"
        self.role = role
        self.complexity = complexity
        self.blackboard = blackboard or Blackboard()
        self.bus = bus or MessageBus()
        self.orchestrator = orchestrator
        self.pheromone_board = pheromone_board
        self._current_task: Task | None = None

        if llm is not None:
            self.llm = llm
        else:
            self.llm = make_client(role=role)
            self.llm.system = system or ROLE_PROMPTS.get(role, "You are a helpful AI agent in a swarm.")
            self.llm.max_tokens = ROLE_MAX_TOKENS.get(role, 2_048)

        self.bus.subscribe(f"agent/{self.id}", self._handle_direct_message)
```

- [ ] **Step 2.6 — Update `run()` and add `_run_scout()` in `swarm/agent.py`**

Update `run()` to add the scout branch:

```python
    async def run(self, task: Task) -> None:
        """Execute *task* using this agent's role logic."""
        if self.role == "decomposer":
            await self._run_decomposer(task)
        elif self.role == "scout":
            await self._run_scout(task)
        else:
            await self._run_generic(task)
```

Add `_run_scout()` method after `_run_decomposer()`:

```python
    async def _run_scout(self, task: Task) -> None:
        from .pheromone import goal_hash
        prompt = (
            f"Goal: {task.title}\n\nDetails: {task.description}\n\n"
            "Predict the likely subtasks for this goal."
        )
        raw = await asyncio.to_thread(self.llm.ask, prompt)

        try:
            data = json.loads(raw)
            hints = data.get("hints", [])
        except json.JSONDecodeError:
            task.mark_failed(f"Scout returned invalid JSON:\n{raw}")
            return

        if self.pheromone_board is not None:
            gh = goal_hash(task.title)
            for hint in hints:
                title = hint.get("title", "unknown")
                key = f"scout/{gh}/{title}"
                self.pheromone_board.deposit(key, 1.0, data=hint)

        task.mark_done({"hint_count": len(hints)})
```

- [ ] **Step 2.7 — Run tests to confirm they pass**

```
pytest tests/test_agent.py -v
```

Expected: all agent tests pass (existing 14 + new 4).

- [ ] **Step 2.8 — Run full suite**

```
pytest -v
```

Expected: all tests green.

- [ ] **Step 2.9 — Commit**

```bash
git add swarm/llm.py swarm/agent.py tests/test_agent.py
git commit -m "feat: add scout agent role with pheromone trail deposits"
```

---

## Task 3: Orchestrator pheromone integration

**Files:**
- Modify: `swarm/orchestrator.py`
- Create: `tests/test_orchestrator.py` (new file with pheromone-specific tests)

- [ ] **Step 3.1 — Write failing tests**

Create `tests/test_orchestrator.py`:

```python
"""Tests for Orchestrator pheromone integration — no API calls."""

import json
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

from swarm.environment import Blackboard
from swarm.llm import LLMClient, reset_registry
from swarm.orchestrator import Orchestrator
from swarm.pheromone import PheromoneBoard, goal_hash
from swarm.task import Task, TaskStatus


@pytest.fixture(autouse=True)
def clear_registry():
    reset_registry()
    yield
    reset_registry()


class TestOrchestratorPheromoneBoard:
    def test_accepts_pheromone_board_param(self):
        board = PheromoneBoard()
        orch = Orchestrator(pheromone_board=board)
        assert orch.pheromone_board is board

    def test_none_board_by_default(self):
        orch = Orchestrator()
        assert orch.pheromone_board is None


class TestInjectTrailHints:
    def test_injects_note_when_trails_exist(self):
        board = PheromoneBoard()
        goal = "Summarize quarterly results"
        gh = goal_hash(goal)
        board.deposit(f"scout/{gh}/Data gathering", 2.0, data={"title": "Data gathering", "complexity": "simple"})
        board.deposit(f"scout/{gh}/Analysis", 1.5, data={"title": "Analysis", "complexity": "medium"})

        orch = Orchestrator(pheromone_board=board)
        task = Task(title=goal, description=goal, required_role="decomposer", complexity="complex", depth=0)
        orch.graph.add(task)

        orch._inject_trail_hints(task)

        note_keys = orch.blackboard.keys(prefix=f"note/{task.id}")
        assert len(note_keys) == 1
        note_text = orch.blackboard.read(note_keys[0])
        assert "Data gathering" in note_text
        assert "Analysis" in note_text

    def test_no_injection_when_no_trails(self):
        board = PheromoneBoard()
        orch = Orchestrator(pheromone_board=board)
        task = Task(title="No trails goal", description="...", required_role="decomposer")
        orch.graph.add(task)

        orch._inject_trail_hints(task)

        note_keys = orch.blackboard.keys(prefix=f"note/{task.id}")
        assert len(note_keys) == 0

    def test_no_injection_when_board_is_none(self):
        orch = Orchestrator(pheromone_board=None)
        task = Task(title="Goal", description="...", required_role="decomposer")
        orch.graph.add(task)

        orch._inject_trail_hints(task)

        note_keys = orch.blackboard.keys(prefix=f"note/{task.id}")
        assert len(note_keys) == 0


class TestTrailReinforcement:
    @pytest.mark.asyncio
    async def test_done_task_reinforces_trail(self):
        board = PheromoneBoard()
        goal = "My goal"
        gh = goal_hash(goal)
        board.deposit(f"scout/{gh}/{goal}", 1.0)

        orch = Orchestrator(pheromone_board=board)

        mock_agent = MagicMock()
        mock_agent.role = "executor"
        mock_agent.complexity = "medium"

        task = Task(title=goal, description="...", required_role="executor")
        task.status = TaskStatus.DONE

        async def fake_assign(t):
            t.status = TaskStatus.DONE

        mock_agent.assign = fake_assign

        with patch.object(orch, "_get_or_create_agent", return_value=mock_agent), \
             patch.object(orch, "_return_agent"), \
             patch.object(orch, "_maybe_complete_parent", new_callable=AsyncMock):
            await orch._run_agent(mock_agent, task)

        entry = board.get(f"scout/{gh}/{goal}")
        assert entry is not None
        assert entry["weight"] == pytest.approx(1.5)

    @pytest.mark.asyncio
    async def test_failed_task_suppresses_trail(self):
        board = PheromoneBoard()
        goal = "Failing goal"
        gh = goal_hash(goal)
        board.deposit(f"scout/{gh}/{goal}", 1.0)

        orch = Orchestrator(pheromone_board=board)

        mock_agent = MagicMock()
        mock_agent.role = "executor"
        mock_agent.complexity = "medium"

        task = Task(title=goal, description="...", required_role="executor")

        async def fake_assign(t):
            t.status = TaskStatus.FAILED

        mock_agent.assign = fake_assign

        with patch.object(orch, "_get_or_create_agent", return_value=mock_agent), \
             patch.object(orch, "_return_agent"), \
             patch.object(orch, "_maybe_complete_parent", new_callable=AsyncMock):
            await orch._run_agent(mock_agent, task)

        entry = board.get(f"scout/{gh}/{goal}")
        assert entry is not None
        assert entry["weight"] == pytest.approx(0.2)
```

- [ ] **Step 3.2 — Run tests to confirm they fail**

```
pytest tests/test_orchestrator.py -v
```

Expected: `FAILED` — `Orchestrator.__init__` has no `pheromone_board` param; `_inject_trail_hints` doesn't exist.

- [ ] **Step 3.3 — Update `swarm/orchestrator.py`**

Add `PheromoneBoard` to imports at the top of `swarm/orchestrator.py`:

```python
from .pheromone import PheromoneBoard, goal_hash
```

Update `Orchestrator.__init__` to accept and store `pheromone_board`:

```python
    def __init__(
        self,
        max_agents:      int  = _MAX_AGENTS,
        max_depth:       int  = _MAX_DEPTH,
        use_batching:    bool = _USE_BATCHING,
        pheromone_board: PheromoneBoard | None = None,
    ) -> None:
        self.max_agents      = max_agents
        self.max_depth       = max_depth
        self.use_batching    = use_batching
        self.pheromone_board = pheromone_board

        self.blackboard = Blackboard()
        self.bus        = MessageBus()
        self.graph      = TaskGraph()

        self._shared_clients: dict[tuple[str, str], LLMClient] = {}
        self._idle:           dict[tuple[str, str], list[SwarmAgent]] = {}
        self._running_tasks:  set[asyncio.Task[None]] = set()
        self._subtask_lock = asyncio.Lock()
```

Update `_launch_agent_task` to call `_inject_trail_hints` for decomposer tasks. Replace the existing method:

```python
    def _launch_agent_task(self, task: Task) -> None:
        """Assign *task* to an agent and schedule the asyncio task."""
        task.status = TaskStatus.ASSIGNED
        if task.required_role == "decomposer":
            self._inject_trail_hints(task)
        agent = self._get_or_create_agent(task.required_role or "executor", task.complexity)
        at = asyncio.create_task(self._run_agent(agent, task))
        self._running_tasks.add(at)
        at.add_done_callback(self._running_tasks.discard)
        console.log(
            f"[green]▶[/] {agent.id} → task [bold]{task.id}[/] "
            f"({task.title[:55]}) [{agent.llm.model.split('-')[1]}]"
        )
```

Add `_inject_trail_hints` method (place after `_launch_agent_task`):

```python
    def _inject_trail_hints(self, task: Task) -> None:
        """Write strongest pheromone trails as a blackboard note for the decomposer.

        The decomposer's ``_build_context`` already reads ``note/<task_id>/*``
        keys, so this requires no changes to agent logic.
        """
        if self.pheromone_board is None:
            return
        gh = goal_hash(task.title)
        trails = self.pheromone_board.strongest(f"scout/{gh}/", n=5)
        if not trails:
            return
        lines = []
        for _, entry in trails:
            data = entry.get("data")
            if isinstance(data, dict):
                title = data.get("title", "unknown")
                complexity = data.get("complexity", "?")
                lines.append(f"- {title} [{complexity}]")
        if lines:
            hint_text = "Scout trail hints (consider these as likely subtasks):\n" + "\n".join(lines)
            self.blackboard.write_note(task.id, hint_text, author="pheromone_board")
```

Update `_run_agent` to add trail reinforcement. Replace the existing method:

```python
    async def _run_agent(self, agent: SwarmAgent, task: Task) -> None:
        try:
            await agent.assign(task)
            self.graph.update(task)
            icon  = "✓" if task.status == TaskStatus.DONE else "✗"
            color = "green" if task.status == TaskStatus.DONE else "red"
            console.log(f"[{color}]{icon}[/] {agent.id} finished task [bold]{task.id}[/]")
            if task.parent_id:
                await self._maybe_complete_parent(task.parent_id)
            # Reinforce or suppress pheromone trail based on outcome
            if self.pheromone_board is not None and task.required_role != "scout":
                gh = goal_hash(task.title)
                key = f"scout/{gh}/{task.title}"
                if task.status == TaskStatus.DONE:
                    self.pheromone_board.deposit(key, 0.5)
                elif task.status == TaskStatus.FAILED:
                    entry = self.pheromone_board.get(key)
                    if entry is not None:
                        entry["weight"] = entry["weight"] * 0.2
        finally:
            self._return_agent(agent)
```

Update `_get_or_create_agent` to pass `pheromone_board` when creating agents. Replace the existing method:

```python
    def _get_or_create_agent(self, role: str, complexity: str = "medium") -> SwarmAgent:
        if role == "decomposer":
            complexity = "complex"

        key = (role, complexity)
        idle = self._idle.get(key, [])
        if idle:
            return idle.pop()

        shared_llm = self._get_shared_client(role, complexity)
        return SwarmAgent(
            role=role,
            complexity=complexity,
            blackboard=self.blackboard,
            bus=self.bus,
            orchestrator=self,
            llm=shared_llm,
            pheromone_board=self.pheromone_board,
        )
```

- [ ] **Step 3.4 — Run tests to confirm they pass**

```
pytest tests/test_orchestrator.py -v
```

Expected: all orchestrator pheromone tests pass.

- [ ] **Step 3.5 — Run full suite**

```
pytest -v
```

Expected: all tests green.

- [ ] **Step 3.6 — Commit**

```bash
git add swarm/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: orchestrator accepts PheromoneBoard, injects trail hints, reinforces on completion"
```

---

## Task 4: Export, hook script, settings, and skill

**Files:**
- Modify: `swarm/__init__.py`
- Create: `swarm/hooks/__init__.py`
- Create: `swarm/hooks/scout_hook.py`
- Create: `.claude/settings.json`
- Create: `.claude/skills/swarm.md`

- [ ] **Step 4.1 — Export `PheromoneBoard` from `swarm/__init__.py`**

Replace `swarm/__init__.py` with:

```python
"""swarm_intelligence — LLM-powered multi-agent swarm."""

from .agent import SwarmAgent
from .environment import Blackboard
from .messaging import MessageBus
from .orchestrator import Orchestrator
from .pheromone import PheromoneBoard
from .task import Task, TaskGraph, TaskPriority, TaskStatus

__all__ = [
    "SwarmAgent",
    "Blackboard",
    "MessageBus",
    "Orchestrator",
    "PheromoneBoard",
    "Task",
    "TaskGraph",
    "TaskPriority",
    "TaskStatus",
]
```

- [ ] **Step 4.2 — Create `swarm/hooks/__init__.py`**

Create `swarm/hooks/__init__.py` as an empty file:

```python
```

- [ ] **Step 4.3 — Create `swarm/hooks/scout_hook.py`**

Create `swarm/hooks/scout_hook.py`:

```python
#!/usr/bin/env python3
"""Background scout hook — fires after Bash / executeCode tool calls.

Reads SWARM_SESSION_GOAL from the environment and runs a single scout
agent pass to deposit pheromone trails into .swarm/pheromones.json.

Usage (invoked by Claude Code PostToolUse hook):
    python swarm/hooks/scout_hook.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Make the package importable when run as a script from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from swarm.agent import SwarmAgent, ROLE_PROMPTS
from swarm.environment import Blackboard
from swarm.llm import ROLE_MAX_TOKENS, make_client, reset_registry
from swarm.messaging import MessageBus
from swarm.pheromone import PheromoneBoard
from swarm.task import Task


async def run_scout(goal: str, board: PheromoneBoard) -> None:
    reset_registry()
    llm = make_client(role="scout", complexity="simple")
    if not llm.system:
        llm.system = ROLE_PROMPTS["scout"]
        llm.max_tokens = ROLE_MAX_TOKENS.get("scout", 512)

    agent = SwarmAgent(
        role="scout",
        complexity="simple",
        blackboard=Blackboard(),
        bus=MessageBus(),
        llm=llm,
        pheromone_board=board,
    )

    task = Task(
        title=goal,
        description=goal,
        required_role="scout",
        complexity="simple",
        depth=0,
    )
    await agent.assign(task)


async def main() -> None:
    goal = os.getenv("SWARM_SESSION_GOAL", "").strip()
    if not goal:
        return

    board = PheromoneBoard()
    board.load()
    board.evaporate(decay_rate=0.05)

    await run_scout(goal, board)

    board.save()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4.4 — Create `.claude/settings.json`**

Create `.claude/settings.json` in `swarm_intelligence/`:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python swarm/hooks/scout_hook.py"
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 4.5 — Create `.claude/skills/swarm.md`**

Create directory `.claude/skills/` then create `.claude/skills/swarm.md`:

```markdown
---
name: swarm
description: Run the swarm_intelligence pipeline on a goal. Invokes a scout pass then full decompose→execute→review→synthesize flow. Use when the user wants to delegate a complex goal to the multi-agent swarm.
---

# /swarm — Swarm Intelligence Skill

Use this skill when the user types `/swarm <goal>` to run the full ant-colony-inspired swarm pipeline.

## Steps

1. **Load and age existing pheromone trails**

   Run this Python snippet (or use the Bash tool):

   ```python
   from swarm.pheromone import PheromoneBoard
   board = PheromoneBoard()
   board.load()
   board.evaporate(decay_rate=0.1)
   ```

2. **Run scout pass** (fast, Haiku — pre-loads trail hints)

   ```python
   import asyncio
   from swarm.agent import SwarmAgent, ROLE_PROMPTS
   from swarm.environment import Blackboard
   from swarm.llm import ROLE_MAX_TOKENS, make_client
   from swarm.messaging import MessageBus
   from swarm.task import Task

   llm = make_client(role="scout", complexity="simple")
   llm.system = ROLE_PROMPTS["scout"]
   llm.max_tokens = ROLE_MAX_TOKENS.get("scout", 512)

   scout = SwarmAgent(
       role="scout", complexity="simple",
       blackboard=Blackboard(), bus=MessageBus(),
       llm=llm, pheromone_board=board,
   )
   scout_task = Task(title=goal, description=goal, required_role="scout", complexity="simple")
   asyncio.run(scout.assign(scout_task))
   ```

3. **Run full swarm pipeline**

   ```python
   import asyncio
   from swarm import Orchestrator

   orchestrator = Orchestrator(pheromone_board=board)
   result = asyncio.run(orchestrator.run(goal=goal))
   print(result)
   ```

4. **Save updated trails**

   ```python
   board.save()
   ```

5. **Show trail summary** — report to the user:
   - The final synthesised result
   - Top 5 pheromone trails and their weights (call `board.strongest(n=5)`)
   - Any trails that reinforced or evaporated during the run

## Setting the session goal for background hooks

To enable passive background scouting (via the PostToolUse hook), set the
`SWARM_SESSION_GOAL` environment variable before starting Claude Code:

```bash
export SWARM_SESSION_GOAL="my project goal here"
```

The hook will then run scouts automatically after each Bash command.
```

- [ ] **Step 4.6 — Verify import works**

```
python -c "from swarm import PheromoneBoard; print('OK')"
```

Expected: `OK`

- [ ] **Step 4.7 — Run full suite**

```
pytest -v
```

Expected: all tests green.

- [ ] **Step 4.8 — Commit**

```bash
git add swarm/__init__.py swarm/hooks/__init__.py swarm/hooks/scout_hook.py .claude/settings.json .claude/skills/swarm.md
git commit -m "feat: export PheromoneBoard, add scout_hook.py, Claude Code hook and /swarm skill"
```

---

## Task 5: Integration tests

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 5.1 — Write integration tests**

Create `tests/test_integration.py`:

```python
"""End-to-end integration tests: scout → pheromone → orchestrator → decomposer.

All LLM calls are mocked — no API key required.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from swarm.agent import SwarmAgent
from swarm.environment import Blackboard
from swarm.llm import LLMClient, reset_registry
from swarm.messaging import MessageBus
from swarm.orchestrator import Orchestrator
from swarm.pheromone import PheromoneBoard, goal_hash
from swarm.task import Task, TaskStatus


def make_mock_llm(response: str) -> LLMClient:
    llm = MagicMock(spec=LLMClient)
    llm.ask = MagicMock(return_value=response)
    llm.system = "mock system"
    llm.model = "claude-mock"
    llm.max_tokens = 512
    return llm


@pytest.fixture(autouse=True)
def clear_registry():
    reset_registry()
    yield
    reset_registry()


class TestScoutDepositsToBoard:
    @pytest.mark.asyncio
    async def test_full_scout_deposit_pipeline(self):
        """Scout agent deposits trails that are then readable via PheromoneBoard."""
        board = PheromoneBoard()
        goal = "Build a REST API"
        hints_json = json.dumps({
            "hints": [
                {"title": "Design endpoints", "complexity": "medium"},
                {"title": "Implement handlers", "complexity": "complex"},
                {"title": "Write tests", "complexity": "simple"},
            ]
        })

        agent = SwarmAgent(
            role="scout",
            complexity="simple",
            blackboard=Blackboard(),
            bus=MessageBus(),
            llm=make_mock_llm(hints_json),
            pheromone_board=board,
        )

        task = Task(title=goal, description=goal, required_role="scout", complexity="simple")
        await agent.assign(task)

        assert task.status == TaskStatus.DONE
        gh = goal_hash(goal)
        trails = board.strongest(f"scout/{gh}/")
        assert len(trails) == 3
        titles = {e["data"]["title"] for _, e in trails}
        assert "Design endpoints" in titles
        assert "Implement handlers" in titles
        assert "Write tests" in titles


class TestTrailsFlowToDecomposer:
    def test_orchestrator_injects_trails_as_blackboard_note(self):
        """Trails deposited by scout appear as a note the decomposer can read."""
        board = PheromoneBoard()
        goal = "Refactor authentication module"
        gh = goal_hash(goal)
        board.deposit(f"scout/{gh}/Extract token logic", 3.0, data={"title": "Extract token logic", "complexity": "medium"})
        board.deposit(f"scout/{gh}/Add unit tests", 2.0, data={"title": "Add unit tests", "complexity": "simple"})

        orch = Orchestrator(pheromone_board=board)
        decomposer_task = Task(
            title=goal,
            description=goal,
            required_role="decomposer",
            complexity="complex",
            depth=0,
        )
        orch.graph.add(decomposer_task)

        orch._inject_trail_hints(decomposer_task)

        note_keys = orch.blackboard.keys(prefix=f"note/{decomposer_task.id}")
        assert len(note_keys) == 1
        note = orch.blackboard.read(note_keys[0])
        assert "Extract token logic" in note
        assert "Add unit tests" in note
        assert "medium" in note
        assert "simple" in note


class TestPheromoneEvaporation:
    def test_trails_evaporate_across_runs(self):
        """After multiple evaporate calls, weak trails disappear."""
        board = PheromoneBoard()
        board.deposit("scout/abc/strong", 10.0)
        board.deposit("scout/abc/weak", 0.05)

        for _ in range(5):
            board.evaporate(decay_rate=0.1)

        assert board.get("scout/abc/strong") is not None
        assert board.get("scout/abc/weak") is None

    def test_reinforcement_counteracts_evaporation(self):
        """Successful tasks that reinforce a trail prevent it from evaporating."""
        board = PheromoneBoard()
        gh = goal_hash("Goal")
        key = f"scout/{gh}/Stable task"
        board.deposit(key, 0.1)

        # Reinforce three times, then evaporate once
        board.deposit(key, 0.5)
        board.deposit(key, 0.5)
        board.deposit(key, 0.5)
        board.evaporate(decay_rate=0.1)

        assert board.get(key) is not None
        assert board.get(key)["weight"] > 0.01


class TestNoBoardFallback:
    def test_orchestrator_without_board_runs_normally(self):
        """An Orchestrator with no PheromoneBoard behaves exactly as before."""
        orch = Orchestrator(pheromone_board=None)
        assert orch.pheromone_board is None

        task = Task(title="Goal", description="...", required_role="decomposer")
        orch.graph.add(task)

        # _inject_trail_hints should be a no-op
        orch._inject_trail_hints(task)
        assert orch.blackboard.keys(prefix=f"note/{task.id}") == []
```

- [ ] **Step 5.2 — Run integration tests**

```
pytest tests/test_integration.py -v
```

Expected: all integration tests pass.

- [ ] **Step 5.3 — Run full suite**

```
pytest -v
```

Expected: 35 original tests + ~35 new tests all green. Zero failures.

- [ ] **Step 5.4 — Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add integration tests for scout→pheromone→decomposer pipeline"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task that implements it |
|-----------------|------------------------|
| `PheromoneBoard` with deposit/evaporate/strongest/get/load/save | Task 1 |
| `scout` role in ROLE_PROMPTS, ROLE_COMPLEXITY, ROLE_MAX_TOKENS | Task 2 |
| `_run_scout()` deposits trails by stable goal hash | Task 2 |
| `pheromone_board` field on `SwarmAgent` | Task 2 |
| `Orchestrator` accepts `pheromone_board` param | Task 3 |
| `_inject_trail_hints()` writes note before decomposer task | Task 3 |
| Trail reinforcement on DONE (+0.5) / suppression on FAILED (×0.2) | Task 3 |
| `pheromone_board` passed to agents via `_get_or_create_agent` | Task 3 |
| Export `PheromoneBoard` from `swarm/__init__.py` | Task 4 |
| `scout_hook.py` background script | Task 4 |
| `.claude/settings.json` PostToolUse hook registration | Task 4 |
| `/swarm` Claude Code skill | Task 4 |
| All 35 existing tests remain green | Verified in each task's final step |
| Error handling: missing/corrupt JSON file warns, does not raise | Task 1 (tested) |
| Error handling: scout failure is non-fatal | Task 2 (tested) |
| Error handling: no board → no-op | Task 3 (tested) |

**Placeholder scan:** No TBDs, TODOs, or vague steps found.

**Type consistency:**
- `PheromoneBoard.deposit(key, weight, data)` → used consistently in `_run_scout` and `_run_agent`
- `goal_hash(str) -> str` → imported and called identically in `agent.py`, `orchestrator.py`, and tests
- `board.strongest(prefix, n)` → returns `list[tuple[str, dict]]` — consumed correctly in `_inject_trail_hints` and tests
- `pheromone_board: PheromoneBoard | None` → typed consistently across `SwarmAgent.__init__` and `Orchestrator.__init__`
