"""Orchestrator — the swarm's scheduler and coordinator.

Token optimisations applied here
---------------------------------
1. **Shared LLMClient per (role, complexity)** — ``_shared_clients`` holds
   one ``LLMClient`` per tier.  Every agent of the same role+tier reuses it,
   so the system prompt is cached once instead of once per agent.

2. **Complexity-aware agent pool** — the idle pool is now keyed by
   ``(role, complexity)`` so Haiku-backed and Sonnet-backed executor agents
   are kept in separate pools and never mixed.

3. **Batch dispatch path** — when ``use_batching=True`` (or env
   ``SWARM_USE_BATCHING=true``), the orchestrator collects all ready
   *non-decomposer* leaf tasks of the same model, submits them as one
   Batch API request (50 % cheaper), and distributes the results.

4. **Unified token reporting** — ``_print_summary`` reads from
   ``_shared_clients`` so the numbers cover *all* agents of each tier,
   not just the ones that happened to be idle at the end.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from rich.console import Console
from rich.table import Table

from .agent import SwarmAgent, ROLE_PROMPTS
from .environment import Blackboard
from .llm import LLMClient, COMPLEXITY_MODELS, ROLE_MAX_TOKENS, make_client
from .messaging import MessageBus
from .pheromone import PheromoneBoard, goal_hash
from .task import Task, TaskGraph, TaskStatus

_MAX_AGENTS    = int(os.getenv("SWARM_MAX_AGENTS",   "8"))
_MAX_DEPTH     = int(os.getenv("SWARM_MAX_DEPTH",    "4"))
_USE_BATCHING  = os.getenv("SWARM_USE_BATCHING", "false").lower() == "true"
# Minimum group size before the batch path is preferred over real-time
_BATCH_MIN     = int(os.getenv("SWARM_BATCH_MIN", "2"))

console = Console()


class Orchestrator:
    """Top-level coordinator for the agent swarm.

    Parameters
    ----------
    max_agents:
        Maximum number of concurrently running agent tasks.
    max_depth:
        Maximum task decomposition depth.
    use_batching:
        When True, independent leaf tasks are submitted via the Message
        Batches API (50 % cheaper, ~minutes latency instead of seconds).
        Defaults to ``SWARM_USE_BATCHING`` env var (False).
    """

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

        # Opt. 1 — shared LLMClient registry, keyed by (role, complexity)
        self._shared_clients: dict[tuple[str, str], LLMClient] = {}

        # Opt. 2 — idle pool keyed by (role, complexity)
        self._idle: dict[tuple[str, str], list[SwarmAgent]] = {}

        self._running_tasks: set[asyncio.Task[None]] = set()
        self._subtask_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, goal: str, description: str = "") -> Any:
        """Run the swarm on *goal* and return the final synthesised result."""
        console.rule(f"[bold cyan]Swarm starting — goal: {goal}")

        root = Task(
            title=goal,
            description=description or goal,
            required_role="decomposer",
            complexity="complex",
            depth=0,
        )
        self.graph.add(root)

        await self._dispatch_loop()

        final = self.blackboard.read("swarm/final_result")
        if final is None:
            done = [t for t in self.graph.values() if t.status == TaskStatus.DONE and not t.child_ids]
            if done:
                final = done[-1].result

        self._print_summary()
        return final

    # ------------------------------------------------------------------
    # Dispatch loop
    # ------------------------------------------------------------------

    async def _dispatch_loop(self) -> None:
        while not self.graph.all_done():
            self.graph.propagate_failures()  # #2: cascade failures before scheduling
            ready = self.graph.ready_tasks()
            available = self.max_agents - len(self._running_tasks)

            if not ready or available <= 0:
                if self._running_tasks:
                    done, _ = await asyncio.wait(
                        self._running_tasks, return_when=asyncio.FIRST_COMPLETED
                    )
                    for t in done:
                        if exc := t.exception():
                            console.log(f"[red]Agent task raised: {exc}")
                else:
                    await asyncio.sleep(0.05)
                continue

            schedulable = ready[:available]

            if self.use_batching:
                await self._batch_dispatch(schedulable)
            else:
                for task in schedulable:
                    self._launch_agent_task(task)

            if self._running_tasks:
                done, _ = await asyncio.wait(
                    self._running_tasks, return_when=asyncio.FIRST_COMPLETED
                )
                for t in done:
                    if exc := t.exception():
                        console.log(f"[red]Agent task raised: {exc}")

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

    # ------------------------------------------------------------------
    # Opt. 3 — Batch dispatch path
    # ------------------------------------------------------------------

    async def _batch_dispatch(self, tasks: list[Task]) -> None:
        """Route tasks: decomposers go real-time; others are batched by model."""
        realtime: list[Task] = []
        batchable: dict[str, list[tuple[Task, SwarmAgent]]] = {}

        for task in tasks:
            task.status = TaskStatus.ASSIGNED
            role = task.required_role or "executor"
            agent = self._get_or_create_agent(role, task.complexity)

            if role == "decomposer":
                realtime.append(task)
                self._return_agent(agent)  # will be re-fetched in _launch_agent_task
            else:
                batchable.setdefault(agent.llm.model, []).append((task, agent))

        # Dispatch decomposers real-time
        for task in realtime:
            self._launch_agent_task(task)

        # Dispatch batches
        batch_coros = []
        for model, pairs in batchable.items():
            if len(pairs) < _BATCH_MIN:
                # Too few to batch — fall back to real-time
                for task, agent in pairs:
                    at = asyncio.create_task(self._run_agent(agent, task))
                    self._running_tasks.add(at)
                    at.add_done_callback(self._running_tasks.discard)
            else:
                console.log(
                    f"[cyan]⚡ Batching {len(pairs)} tasks on {model}[/]"
                )
                batch_coros.append(self._run_batch(pairs))

        if batch_coros:
            await asyncio.gather(*batch_coros)

    async def _run_batch(self, pairs: list[tuple[Task, SwarmAgent]]) -> None:
        """Submit *pairs* as a Batch API request and distribute results."""
        requests = []
        task_agent: dict[str, tuple[Task, SwarmAgent]] = {}

        for task, agent in pairs:
            task.status = TaskStatus.RUNNING
            custom_id, params = agent.build_request(task)
            requests.append({"custom_id": custom_id, "params": params})
            task_agent[custom_id] = (task, agent)

        # Use the first agent's client (all share the same model)
        shared_client = pairs[0][1].llm
        results = await shared_client.batch_ask(requests)

        for custom_id, (task, agent) in task_agent.items():
            result = results.get(custom_id)
            if result is not None:
                agent.process_result(task, result)
                console.log(f"[green]✓[/] batch result for task [bold]{task.id}[/]")
            else:
                task.mark_failed("Batch request did not return a result")
                console.log(f"[red]✗[/] batch failure for task [bold]{task.id}[/]")
            self.graph.update(task)
            if task.parent_id:
                await self._maybe_complete_parent(task.parent_id)
            if self.pheromone_board is not None and task.required_role != "scout":
                current = task
                while current.parent_id:
                    try:
                        current = self.graph.get(current.parent_id)
                    except KeyError:
                        break
                gh = goal_hash(current.title)
                key = f"scout/{gh}/{task.title}"
                if task.status == TaskStatus.DONE:
                    self.pheromone_board.deposit(key, 0.5)
                elif task.status == TaskStatus.FAILED:
                    entry = self.pheromone_board.get(key)
                    if entry is not None:
                        entry["weight"] = entry["weight"] * 0.2
            self._return_agent(agent)

    # ------------------------------------------------------------------
    # Normal (real-time) agent execution
    # ------------------------------------------------------------------

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
                # Traverse to root task to get the session goal for the trail key
                current = task
                while current.parent_id:
                    try:
                        current = self.graph.get(current.parent_id)
                    except KeyError:
                        break
                gh = goal_hash(current.title)
                key = f"scout/{gh}/{task.title}"
                if task.status == TaskStatus.DONE:
                    self.pheromone_board.deposit(key, 0.5)
                elif task.status == TaskStatus.FAILED:
                    entry = self.pheromone_board.get(key)
                    if entry is not None:
                        entry["weight"] = entry["weight"] * 0.2
        finally:
            self._return_agent(agent)

    async def _maybe_complete_parent(self, parent_id: str) -> None:
        try:
            parent = self.graph.get(parent_id)
        except KeyError:
            return
        all_task_ids = {t.id for t in self.graph.values()}
        children = [self.graph.get(cid) for cid in parent.child_ids if cid in all_task_ids]
        if all(c.is_terminal() for c in children):
            if parent.status != TaskStatus.DONE:
                results = {c.title: c.result for c in children if c.status == TaskStatus.DONE}
                parent.mark_done(results)
                self.graph.update(parent)

    # ------------------------------------------------------------------
    # Subtask registration
    # ------------------------------------------------------------------

    async def register_subtasks(self, parent: Task, subtasks: list[Task]) -> None:
        async with self._subtask_lock:
            for task in subtasks:
                if task.depth <= self.max_depth:
                    self.graph.add(task)
                    console.log(
                        f"  [dim]+ subtask {task.id}: {task.title[:55]} "
                        f"[{task.complexity}][/]"
                    )
                else:
                    console.log(f"  [yellow]⚠ max depth, skipping: {task.title[:40]}[/]")

    # ------------------------------------------------------------------
    # Opt. 1 — Shared client + agent pool management
    # ------------------------------------------------------------------

    def _get_shared_client(self, role: str, complexity: str) -> LLMClient:
        """Return (creating if needed) the shared LLMClient for this tier."""
        key = (role, complexity)
        if key not in self._shared_clients:
            client = make_client(role=role, complexity=complexity)
            # Always set system prompt — avoids stale prompt from a prior Orchestrator (#6)
            client.system = ROLE_PROMPTS.get(role, "You are a helpful AI agent in a swarm.")
            self._shared_clients[key] = client
        return self._shared_clients[key]

    def _get_or_create_agent(self, role: str, complexity: str = "medium") -> SwarmAgent:
        # Decomposers always use "complex" tier regardless of task.complexity
        if role == "decomposer":
            complexity = "complex"

        key = (role, complexity)
        idle = self._idle.get(key, [])
        if idle:
            return idle.pop()

        shared_llm = self._get_shared_client(role, complexity)
        return SwarmAgent(
            role=role,
            complexity=complexity,  # #5: stored so _return_agent uses the correct pool key
            blackboard=self.blackboard,
            bus=self.bus,
            orchestrator=self,
            llm=shared_llm,
            pheromone_board=self.pheromone_board,
        )

    def _return_agent(self, agent: SwarmAgent) -> None:
        key = (agent.role, agent.complexity)  # #5: use actual complexity, not ROLE_COMPLEXITY default
        self._idle.setdefault(key, []).append(agent)

    # ------------------------------------------------------------------
    # Opt. 4 — Unified token reporting across all shared clients
    # ------------------------------------------------------------------

    def _print_summary(self) -> None:
        summary = self.graph.summary()
        table = Table(title="Swarm run summary", show_lines=True)
        table.add_column("Status")
        table.add_column("Count", justify="right")
        for status, count in summary.items():
            color = "green" if status == "done" else ("red" if status == "failed" else "yellow")
            table.add_row(f"[{color}]{status}[/]", str(count))
        table.add_row("Total", str(len(self.graph)))
        console.print(table)

        # Aggregate across ALL shared clients (not just idle agents)
        token_table = Table(title="Token usage by tier", show_lines=True)
        token_table.add_column("Role / tier")
        token_table.add_column("Model", style="dim")
        token_table.add_column("Input",       justify="right")
        token_table.add_column("Output",      justify="right")
        token_table.add_column("Cache reads", justify="right")
        token_table.add_column("Cache writes",justify="right")
        token_table.add_column("Hit rate",    justify="right")

        total_in = total_out = 0
        for (role, cplx), client in sorted(self._shared_clients.items()):
            u = client.usage_summary()
            if u["input_tokens"] == 0 and u["cache_read_tokens"] == 0:
                continue
            hit_pct = f"{client.cache_hit_rate():.0%}"
            token_table.add_row(
                f"{role} / {cplx}",
                client.model,
                f"{u['input_tokens']:,}",
                f"{u['output_tokens']:,}",
                f"{u['cache_read_tokens']:,}",
                f"{u['cache_write_tokens']:,}",
                hit_pct,
            )
            total_in  += u["input_tokens"]
            total_out += u["output_tokens"]

        if total_in:
            console.print(token_table)
            console.print(f"[dim]Total: {total_in:,} input / {total_out:,} output tokens[/]")
