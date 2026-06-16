# Swarm Pheromone Extension — Design Spec

**Date:** 2026-06-16
**Project:** `swarm_intelligence` extension
**Status:** Approved

---

## Overview

Extend `swarm_intelligence` with ant-colony-inspired scout agents and a pheromone trail system, integrated into Claude Code via a `/swarm` skill and background hooks.

Two ant colony behaviors are combined:
- **Stigmergy (pheromone trails):** agents leave weighted traces in a shared environment that attract or repel future agents
- **Scout + forager roles:** scout agents probe the task space first; forager agents (decomposers, executors) follow the strongest trails

The result is a swarm that learns from its own history and pre-loads context before the user explicitly invokes it.

---

## Goals

1. Add a `PheromoneBoard` to the swarm as an opt-in, weighted, time-decaying trail store
2. Add a `scout` agent role that probes task space cheaply (Haiku) and deposits trails
3. Create a `/swarm` Claude Code skill for on-demand invocation
4. Add a `PostToolUse` hook that runs scouts passively in the background
5. Keep all 35 existing tests green — pheromone board is additive, never required

---

## Architecture

```
Claude Code session
    │
    ├─ [hook: PostToolUse on Bash / executeCode]
    │       └─ background scout run → deposits pheromone trails
    │
    └─ [skill: /swarm <goal>]
            └─ Orchestrator.run() (scout-first mode)
                    │
                    ├─ Scout agents (Haiku) → probe task space
                    │       └─ PheromoneBoard.deposit(key, weight, data)
                    │
                    └─ Forager agents read strongest trails
                            └─ biased decomposition → execution → results
                            └─ success reinforces / failure evaporates trails
                            └─ PheromoneBoard.save() → .swarm/pheromones.json
```

### New files

| File | Purpose |
|------|---------|
| `swarm/pheromone.py` | `PheromoneBoard` — weighted, time-decaying trail store |
| `swarm/skills/swarm.md` | `/swarm` Claude Code skill definition |
| `swarm/hooks/scout_hook.py` | Script invoked by the PostToolUse hook |
| `tests/test_pheromone.py` | Unit tests for `PheromoneBoard` |
| `tests/test_integration.py` | End-to-end scout → forager trail flow (mocked LLM) |

### Existing files touched (minimally)

| File | Change |
|------|--------|
| `swarm/agent.py` | Add `scout` to `ROLE_PROMPTS`; add `_run_scout()` method |
| `swarm/orchestrator.py` | Accept optional `PheromoneBoard`; read trails before decomposition |
| `swarm/__init__.py` | Export `PheromoneBoard` |
| `.claude/settings.json` | Register PostToolUse hook |

---

## Components

### `PheromoneBoard` (`swarm/pheromone.py`)

Each trail entry: `{weight: float, timestamp: float, data: Any}`

Trail keys follow the existing Blackboard namespace convention:
`"scout/<goal_hash>/subtask_structure"`, `"scout/<goal_hash>/complexity"`

**API:**

```python
class PheromoneBoard:
    def deposit(self, key: str, weight: float, data: Any) -> None: ...
    def evaporate(self, decay_rate: float = 0.1) -> None: ...
    def strongest(self, prefix: str, n: int = 5) -> list[tuple[str, dict]]: ...
    def get(self, key: str) -> dict | None: ...
    def load(self, path: Path = Path(".swarm/pheromones.json")) -> None: ...
    def save(self, path: Path = Path(".swarm/pheromones.json")) -> None: ...
```

- `deposit`: adds a new trail or reinforces an existing one (weight addition)
- `evaporate`: multiplies all weights by `(1 - decay_rate)`, prunes entries below `0.01`
- `strongest`: returns top-N entries under a key prefix, sorted by weight descending
- `load/save`: JSON round-trip for cross-session persistence

### Scout role (`swarm/agent.py`)

New `_run_scout()` method on `SwarmAgent`. When `role == "scout"`:

- Sends a short Haiku call: *"Given this goal, list likely subtasks and their complexity. Reply in JSON only."*
- Deposits one trail per inferred subtask at `weight=1.0`
- Always uses `simple` model tier (Haiku) regardless of task `complexity` flag
- Marks task `DONE` — scout produces no user-visible result, only trails

### `/swarm` skill (`swarm/skills/swarm.md`)

Claude Code skill that executes the full scout-first pipeline:

1. `PheromoneBoard.load()` + `evaporate(decay_rate=0.1)`
2. Scout pass (fast, Haiku) deposits fresh trails
3. `Orchestrator.run()` — full pipeline:
   - Decomposer reads `strongest("scout/<hash>/")` and uses trails to bias subtask JSON
   - Executors run subtasks (Sonnet/Opus per complexity tier)
   - Reviewer annotates; Synthesizer produces final output
4. On subtask `DONE` → `deposit(weight += 0.5)` — reinforce successful paths
5. On subtask `FAILED` → `deposit(weight *= 0.2)` — suppress failed paths
6. `PheromoneBoard.save()`
7. Prints results + pheromone trail summary

### Hook (`swarm/hooks/scout_hook.py` + `.claude/settings.json`)

`PostToolUse` hook fires after any `Bash` or `mcp__ide__executeCode` tool call.

- Reads `SWARM_SESSION_GOAL` env var — if unset, no-ops silently
- `PheromoneBoard.load()` + `evaporate(decay_rate=0.05)` (gentle passive decay)
- Runs `Orchestrator.run(goal, max_depth=1, roles=["scout"])` — scouts only, no execution
- `PheromoneBoard.save()`

Trails accumulate passively so the board is pre-loaded when the user invokes `/swarm`.

---

## Data Flow

### Passive flow (background hook)

```
Claude Code runs Bash/executeCode tool
    │
    └─ PostToolUse hook → scout_hook.py <session_goal>
            ├─ PheromoneBoard.load()
            ├─ evaporate(decay_rate=0.05)
            ├─ Orchestrator.run(max_depth=1, roles=["scout"])
            │       └─ Scout → Haiku → JSON hints → deposit trails
            └─ PheromoneBoard.save()
```

### Active flow (`/swarm <goal>`)

```
User: /swarm "refactor the orchestrator module"
    │
    └─ skill: swarm.md
            ├─ PheromoneBoard.load() + evaporate(decay_rate=0.1)
            ├─ Scout pass → deposits fresh trails
            ├─ Orchestrator.run() — full pipeline
            │       ├─ Decomposer reads strongest() → biased subtask list
            │       ├─ Executors run subtasks
            │       ├─ Reviewer annotates
            │       └─ Synthesizer → final output
            ├─ Reinforce DONE subtasks / suppress FAILED subtasks
            └─ PheromoneBoard.save()
```

**Key invariant:** `Orchestrator` never *requires* `PheromoneBoard`. If no trails exist, the decomposer runs exactly as today. Pheromone bias is additive, not structural.

---

## Error Handling

| Scenario | Behavior |
|----------|----------|
| `.swarm/pheromones.json` missing | `load()` returns empty board silently |
| JSON file corrupt | `warnings.warn`, reset to empty board, continue |
| Scout agent fails (LLM error / timeout) | Non-fatal — log warning, skip deposit, full run proceeds unbiased |
| Hook fires, `SWARM_SESSION_GOAL` unset | Silent no-op |
| All trails evaporate to zero | Empty board — decomposer runs unbiased |
| `save()` fails (permissions) | `warnings.warn`, continue — degraded experience, not a crash |

No new exception types. All errors surface as `warnings.warn` or log lines, matching the existing `batch_ask()` pattern in `llm.py`.

---

## Testing

Follows the existing project pattern: pytest, no API calls, all LLM calls mocked.

| Test file | Coverage |
|-----------|----------|
| `tests/test_pheromone.py` | `deposit`, `evaporate`, `strongest`, `load/save` round-trip, corrupt file recovery |
| `tests/test_agent.py` (extended) | Scout role: correct trail keys, `simple` model tier, task marked DONE |
| `tests/test_orchestrator.py` (extended) | Board passed in → decomposer gets trail hints; `None` board → existing behavior unchanged |
| `tests/test_integration.py` (new) | Scout deposits → forager reads `strongest()` → bias flows end-to-end (mocked LLM) |

Target: 35 existing tests all green + ~15 new tests.

---

## Out of Scope (Phase 2)

- Full ACO scheduler replacement (`TaskGraph.ready_tasks()` rewrite)
- Cross-process / Redis-backed `PheromoneBoard`
- Hook-as-pheromone event architecture (Approach 3)
- Pheromone visualization / dashboard
