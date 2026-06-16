# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```bash
# Copy and fill in your API key
cp .env.example .env

# Install (editable) with dev dependencies
pip install -e ".[dev]"
```

Requires Python ≥ 3.11 and a valid `ANTHROPIC_API_KEY`.

## Common Commands

```bash
# Run all tests (no API calls required)
pytest

# Run a single test file
pytest tests/test_task.py

# Run a specific test
pytest tests/test_agent.py::TestDecomposerRun::test_decomposer_proposes_subtasks

# Lint + format
ruff check .
ruff format .

# Type-check
mypy swarm/

# Run the example (requires API key)
python examples/decompose_and_run.py
```

## Architecture

The system is a **multi-agent swarm** where Claude-backed agents collectively decompose a goal, self-assign subtasks, and synthesize results.

### Core data flow

```
User goal
  │
  ▼
Orchestrator.run()
  │  seeds TaskGraph with a root Task(required_role="decomposer")
  ▼
dispatch loop  ──────────────────────────────────────────┐
  │  picks ready tasks from TaskGraph                    │
  │  assigns each to an idle SwarmAgent                  │
  ▼                                                       │
SwarmAgent.assign(task)                                   │
  │                                                       │
  ├─ role == "decomposer"                                 │
  │    └─ calls Claude → parses JSON subtask list        │
  │    └─ calls Orchestrator.register_subtasks()  ───────┘
  │         (adds children to TaskGraph, re-enters loop)
  │
  └─ any other role
       └─ calls Claude with task description + blackboard context
       └─ writes result to Blackboard at "task/<id>/result"
       └─ marks Task DONE
```

### Module responsibilities

| File | Purpose |
|------|---------|
| `swarm/task.py` | `Task` (Pydantic model) + `TaskGraph` (DAG scheduler). `TaskGraph.ready_tasks()` is the scheduling oracle. |
| `swarm/environment.py` | `Blackboard` — thread-safe key/value store; keys are namespaced strings like `"task/<id>/result"`. Acts as stigmergic memory between agents. |
| `swarm/messaging.py` | `MessageBus` — async pub/sub for direct agent↔agent messages. Topics follow `"agent/<id>"`. |
| `swarm/llm.py` | `LLMClient` — Anthropic SDK wrapper that applies prompt caching to all system prompts and tracks token usage. `make_client(role)` selects the right model. |
| `swarm/agent.py` | `SwarmAgent` — base agent class with role-specific `run()` logic. Roles: `decomposer`, `executor`, `reviewer`, `synthesizer`. Custom roles can be added via `ROLE_PROMPTS` dict or subclassing. |
| `swarm/orchestrator.py` | `Orchestrator` — async dispatch loop, agent pool management, token-usage reporting. |

### Token optimisations

| Optimisation | Where | Effect |
|---|---|---|
| **Shared LLMClient per `(role, complexity)`** | `llm.py:make_client`, `orchestrator.py:_shared_clients` | System prompt cached once per tier; all agents of the same role reuse the same Claude cache entry |
| **Tiered model routing** | `task.py:complexity`, `llm.py:COMPLEXITY_MODELS` | `simple→Haiku`, `medium→Sonnet`, `complex→Opus`; decomposer sets `complexity` on each subtask via JSON |
| **Role-based `max_tokens`** | `llm.py:ROLE_MAX_TOKENS` | Decomposer capped at 1 024 (JSON only); synthesizer up to 6 144 |
| **Context summarisation** | `agent.py:_build_context`, `llm.py:quick_summarize` | Results > 3 000 chars are condensed by Haiku; summary cached at `task/<id>/summary` — computed once, reused by all downstream agents |
| **Batch API path** | `orchestrator.py:_batch_dispatch`, `llm.py:batch_ask` | `SWARM_USE_BATCHING=true` submits independent leaf tasks as one Batch API call (50 % cheaper). Decomposers always go real-time. |

Token usage by tier is reported in the end-of-run table (including cache hit rate per tier).

### Key design decisions

**Decomposer output format**: The decomposer role instructs Claude to return *only* valid JSON matching the `{subtasks: [...]}` schema. `SwarmAgent._run_decomposer()` parses this and calls `orchestrator.register_subtasks()`. If JSON parsing fails, the task is marked `FAILED` immediately.

**Prompt caching**: Every `LLMClient` marks its system prompt with `cache_control: ephemeral`. Long-running swarms with many agents of the same role hit the cache heavily, reducing cost.

**Agent pool**: The orchestrator keeps idle agents per role in `self._idle`. Agents are reused across tasks; their `LLMClient` retains the same cached system prompt across calls.

**Depth guard**: `max_depth` (default 4) prevents runaway recursive decomposition. Tasks proposed beyond this depth are dropped with a warning.

**No external broker**: `MessageBus` and `Blackboard` are in-process. To distribute across processes, replace `Blackboard` with a Redis/shared-memory backend and `MessageBus` with NATS/Redis pub-sub — the agent API stays the same.

### Adding a new role

1. Add an entry to `ROLE_PROMPTS` in `swarm/agent.py`.
2. Override `run()` in a subclass if the role needs custom logic beyond the generic executor flow.
3. Use `required_role="<new_role>"` in task definitions so the orchestrator routes tasks to the right agents.

### Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | — | Required |
| `SWARM_MODEL` | `claude-sonnet-4-6` | Model for executor/reviewer/synthesizer agents |
| `SWARM_ORCHESTRATOR_MODEL` | `claude-opus-4-7` | Model for decomposer agents |
| `SWARM_MAX_AGENTS` | `8` | Max concurrent agent tasks |
| `SWARM_MAX_DEPTH` | `4` | Max task decomposition depth |
