"""End-to-end integration tests: scout → pheromone → orchestrator → decomposer.

All LLM calls are mocked — no API key required.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

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
        board.deposit("scout/abc/weak", 0.005)

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
