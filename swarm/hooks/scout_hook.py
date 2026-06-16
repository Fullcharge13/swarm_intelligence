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
