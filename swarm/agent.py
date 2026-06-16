"""SwarmAgent — the base class for every agent in the swarm.

Token optimisations applied here
---------------------------------
1. **Shared LLMClient** — the orchestrator injects a single ``LLMClient``
   instance shared by all agents of the same ``(role, complexity)`` pair
   so the Claude prompt cache is written once per tier, not per agent.

2. **Context summarisation** — ``_build_context`` truncates large dependency
   results through ``quick_summarize()`` (Haiku) before injecting them.
   The summary is written to the blackboard so it is computed only once
   no matter how many downstream agents consume the same dependency.

3. **Complexity tagging** — the decomposer sets ``complexity`` on every
   proposed subtask.  The orchestrator uses this to route the task to the
   correct model tier (Haiku / Sonnet / Opus).

4. **Batch helpers** — ``build_request`` / ``process_result`` let the
   orchestrator collect prompts without executing them, enabling the
   Message Batches API path (50 % cheaper).
"""

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

# Characters above this threshold trigger Haiku summarisation before the
# result is injected into a downstream agent's context.
SUMMARIZE_THRESHOLD: int = 3_000

# ---------------------------------------------------------------------------
# Role system prompts
# ---------------------------------------------------------------------------

ROLE_PROMPTS: dict[str, str] = {
    "decomposer": """\
You are a decomposer agent in a collaborative AI swarm.
Analyse the goal and break it into a list of concrete, independent subtasks.

Output ONLY valid JSON — no markdown fences, no extra text:
{
  "subtasks": [
    {
      "title": "<short title>",
      "description": "<what must be done>",
      "required_role": "<executor|reviewer|synthesizer|decomposer>",
      "complexity": "<simple|medium|complex>",
      "priority": <1-10>,
      "depends_on_titles": ["<title of other subtask if any>"]
    }
  ]
}

Complexity guide:
  simple  — lookup, formatting, short factual answer  → Haiku
  medium  — analysis, drafting, moderate reasoning    → Sonnet (default)
  complex — deep research, multi-step reasoning       → Opus
""",
    "executor": """\
You are an executor agent in a collaborative AI swarm.
You receive a specific task and must complete it thoroughly.
Write your result as clear, structured text.
If the task involves code, include working code with explanations.
""",
    "reviewer": """\
You are a reviewer agent in a collaborative AI swarm.
You will be given a task description and the result produced by another agent.
Identify issues, gaps, or improvements, then output a revised or annotated version.
""",
    "synthesizer": """\
You are a synthesizer agent in a collaborative AI swarm.
You receive multiple partial results and combine them into one coherent, well-structured response.
Eliminate redundancy and resolve any contradictions.
""",
}


class SwarmAgent:
    """A single LLM-backed agent participating in the swarm.

    Parameters
    ----------
    role:
        One of the built-in role names, or ``"custom"``.
    system:
        Override system prompt (required when ``role="custom"``).
    blackboard:
        Shared environment instance.
    bus:
        Shared message bus instance.
    orchestrator:
        Back-reference to the orchestrator (set automatically on registration).
    llm:
        Pre-built shared ``LLMClient``.  When provided the ``system`` prompt
        must already be set on it.  When *None* a private client is created
        (useful in tests).
    """

    def __init__(
        self,
        role: str = "executor",
        complexity: str = "medium",
        system: str | None = None,
        blackboard: Blackboard | None = None,
        bus: MessageBus | None = None,
        orchestrator: "Orchestrator | None" = None,
        llm: LLMClient | None = None,
    ) -> None:
        self.id = f"{role}-{str(uuid.uuid4())[:6]}"
        self.role = role
        self.complexity = complexity  # stored so _return_agent can use the correct pool key (#5)
        self.blackboard = blackboard or Blackboard()
        self.bus = bus or MessageBus()
        self.orchestrator = orchestrator
        self._current_task: Task | None = None

        if llm is not None:
            # Injected shared client — system prompt already configured
            self.llm = llm
        else:
            # Fallback: private client (used in tests / standalone usage)
            self.llm = make_client(role=role)
            self.llm.system = system or ROLE_PROMPTS.get(role, "You are a helpful AI agent in a swarm.")
            self.llm.max_tokens = ROLE_MAX_TOKENS.get(role, 2_048)

        # Subscribe to direct messages addressed to this agent
        self.bus.subscribe(f"agent/{self.id}", self._handle_direct_message)

    # ------------------------------------------------------------------
    # Public API (called by Orchestrator)
    # ------------------------------------------------------------------

    async def assign(self, task: Task) -> None:
        """Accept a task and run it asynchronously."""
        self._current_task = task
        task.assigned_to = self.id
        task.status = TaskStatus.RUNNING
        task.touch()

        try:
            await self.run(task)
        except Exception as exc:  # noqa: BLE001
            task.mark_failed(str(exc))
            self.blackboard.write(
                f"task/{task.id}/error", str(exc), author=self.id, tags=["error"]
            )
        finally:
            self._current_task = None

    # ------------------------------------------------------------------
    # Role execution
    # ------------------------------------------------------------------

    async def run(self, task: Task) -> None:
        """Execute *task* using this agent's role logic."""
        if self.role == "decomposer":
            await self._run_decomposer(task)
        else:
            await self._run_generic(task)

    async def _run_generic(self, task: Task) -> None:
        context = self._build_context(task)
        prompt = f"# Task\n{task.title}\n\n{task.description}"
        if context:
            prompt += f"\n\n{context}"
        result = await asyncio.to_thread(self.llm.ask, prompt)  # #1: unblock event loop
        self.process_result(task, result)

    async def _run_decomposer(self, task: Task) -> None:
        prompt = (
            f"Goal: {task.title}\n\nDetails: {task.description}\n\n"
            "Decompose this goal into subtasks."
        )
        raw = await asyncio.to_thread(self.llm.ask, prompt)  # #1: unblock event loop

        try:
            data = json.loads(raw)
            subtasks_data = data.get("subtasks", [])
        except json.JSONDecodeError:
            task.mark_failed(f"Decomposer returned invalid JSON:\n{raw}")
            return

        if self.orchestrator:
            proposed = self.propose_subtasks(task, subtasks_data)
            await self.orchestrator.register_subtasks(task, proposed)

        task.mark_done({"subtask_count": len(subtasks_data)})

    # ------------------------------------------------------------------
    # Subtask proposal
    # ------------------------------------------------------------------

    def propose_subtasks(self, parent: Task, raw_subtasks: list[dict[str, Any]]) -> list[Task]:
        """Convert raw decomposer JSON into ``Task`` objects."""
        title_to_id: dict[str, str] = {}
        tasks: list[Task] = []

        for item in raw_subtasks:
            complexity = item.get("complexity", "medium")
            if complexity not in ("simple", "medium", "complex"):
                complexity = "medium"

            t = Task(
                title=item.get("title", "Untitled"),
                description=item.get("description", ""),
                required_role=item.get("required_role", "executor"),
                complexity=complexity,  # Opt. 3 — tiered model routing
                priority=min(max(int(item.get("priority", 5)), 1), 10),  # #3: plain int, no enum cast
                parent_id=parent.id,
                depth=parent.depth + 1,
            )
            title_to_id[t.title] = t.id
            tasks.append(t)

        # Resolve title-based dependencies to IDs
        for item, task in zip(raw_subtasks, tasks):
            for dep_title in item.get("depends_on_titles", []):
                dep_id = title_to_id.get(dep_title)
                if dep_id:
                    task.depends_on.append(dep_id)

        return tasks

    # ------------------------------------------------------------------
    # Opt. 2 — Context building with summarisation
    # ------------------------------------------------------------------

    def _build_context(self, task: Task) -> str:
        """Pull dependency results from the blackboard into a context string.

        Results longer than ``SUMMARIZE_THRESHOLD`` characters are condensed
        by the Haiku summariser.  Summaries are cached on the blackboard so
        the same condensation work is not repeated for shared dependencies.
        """
        lines: list[str] = []

        for dep_id in task.depends_on:
            result = self.blackboard.task_result(dep_id)
            if not result:
                continue

            result_str = str(result)
            if len(result_str) > SUMMARIZE_THRESHOLD:
                # Check cache first
                summary = self.blackboard.read(f"task/{dep_id}/summary")
                if summary is None:
                    summary = quick_summarize(result_str)
                    self.blackboard.write(
                        f"task/{dep_id}/summary",
                        summary,
                        author=self.id,
                        tags=["summary"],
                    )
                lines.append(f"## Summary of dependency {dep_id}\n{summary}")
            else:
                lines.append(f"## Result of dependency {dep_id}\n{result_str}")

        # Any notes on the blackboard matching the task id
        for key in self.blackboard.keys(prefix=f"note/{task.id}"):
            note = self.blackboard.read(key)
            if note:
                lines.append(f"## Note: {key}\n{note}")

        return "\n\n".join(lines)

    # ------------------------------------------------------------------
    # Opt. 4 — Batch API helpers
    # ------------------------------------------------------------------

    def build_request(self, task: Task) -> tuple[str, dict[str, Any]]:
        """Return ``(custom_id, api_params)`` without executing the call.

        The orchestrator uses this to collect all prompts for a dispatch
        round and submit them as a single Batch API request.
        """
        context = self._build_context(task)
        prompt = f"# Task\n{task.title}\n\n{task.description}"
        if context:
            prompt += f"\n\n{context}"

        system_block: list[dict[str, Any]] = (
            [{"type": "text", "text": self.llm.system, "cache_control": {"type": "ephemeral"}}]
            if self.llm.system
            else []
        )

        params: dict[str, Any] = {
            "model": self.llm.model,
            "max_tokens": self.llm.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_block:
            params["system"] = system_block

        return task.id, params

    def process_result(self, task: Task, result: str) -> None:
        """Write *result* to the blackboard and mark *task* done.

        Called by both the normal async path and the batch path.
        """
        self.blackboard.write_task_result(task.id, result, author=self.id)
        task.mark_done(result)

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    async def _handle_direct_message(self, msg: Message) -> None:
        """Default handler: log to blackboard.  Override for richer behaviour."""
        self.blackboard.write(
            f"message/{self.id}/{msg.timestamp.isoformat()}",
            msg.payload,
            author=msg.sender,
            tags=["message"],
        )

    async def send(self, to_agent_id: str, payload: Any, *, reply_to: str | None = None) -> None:
        await self.bus.publish(
            f"agent/{to_agent_id}", payload, sender=self.id, reply_to=reply_to
        )

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        status = f"on {self._current_task.id}" if self._current_task else "idle"
        return f"SwarmAgent(id={self.id!r}, role={self.role!r}, {status})"
