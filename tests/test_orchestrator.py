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

        async def fake_assign(t):
            t.status = TaskStatus.DONE

        mock_agent.assign = fake_assign

        with patch.object(orch, "_return_agent"), \
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

        with patch.object(orch, "_return_agent"), \
             patch.object(orch, "_maybe_complete_parent", new_callable=AsyncMock):
            await orch._run_agent(mock_agent, task)

        entry = board.get(f"scout/{gh}/{goal}")
        assert entry is not None
        assert entry["weight"] == pytest.approx(0.2)
