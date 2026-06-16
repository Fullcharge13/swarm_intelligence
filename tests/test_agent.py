"""Tests for SwarmAgent — uses a mock LLM client to avoid API calls."""

import json
from unittest.mock import MagicMock, patch

import pytest

from swarm.agent import SwarmAgent, SUMMARIZE_THRESHOLD
from swarm.environment import Blackboard
from swarm.llm import LLMClient, reset_registry
from swarm.messaging import MessageBus
from swarm.task import Task, TaskStatus


def make_mock_llm(response: str = "mock result") -> LLMClient:
    """Build a LLMClient whose ``ask`` is mocked."""
    llm = MagicMock(spec=LLMClient)
    llm.ask = MagicMock(return_value=response)
    llm.system = "mock system"
    llm.model = "claude-mock"
    llm.max_tokens = 2048
    return llm


def make_agent(role: str = "executor", response: str = "mock result") -> SwarmAgent:
    return SwarmAgent(
        role=role,
        blackboard=Blackboard(),
        bus=MessageBus(),
        llm=make_mock_llm(response),
    )


@pytest.fixture(autouse=True)
def clear_registry():
    reset_registry()
    yield
    reset_registry()


class TestSwarmAgentBasics:
    def test_id_includes_role(self):
        agent = make_agent("executor")
        assert agent.id.startswith("executor-")

    def test_injected_llm_is_used(self):
        llm = make_mock_llm("hello")
        agent = SwarmAgent(role="executor", llm=llm)
        assert agent.llm is llm

    def test_repr_idle(self):
        agent = make_agent()
        assert "idle" in repr(agent)


class TestExecutorRun:
    @pytest.mark.asyncio
    async def test_run_marks_done_and_writes_blackboard(self):
        agent = make_agent("executor", response="The answer is 42")
        task = Task(title="Answer question", description="What is 6×7?")
        await agent.run(task)

        assert task.status == TaskStatus.DONE
        assert task.result == "The answer is 42"
        assert agent.blackboard.task_result(task.id) == "The answer is 42"

    @pytest.mark.asyncio
    async def test_assign_catches_exceptions(self):
        llm = make_mock_llm()
        llm.ask = MagicMock(side_effect=RuntimeError("API down"))
        agent = SwarmAgent(role="executor", blackboard=Blackboard(), bus=MessageBus(), llm=llm)

        task = Task(title="Failing task", description="...")
        await agent.assign(task)

        assert task.status == TaskStatus.FAILED
        assert "API down" in (task.error or "")


class TestDecomposerRun:
    @pytest.mark.asyncio
    async def test_decomposer_proposes_subtasks_with_complexity(self):
        subtasks_json = json.dumps({
            "subtasks": [
                {
                    "title": "Research",
                    "description": "Research the topic",
                    "required_role": "executor",
                    "complexity": "simple",
                    "priority": 5,
                    "depends_on_titles": [],
                },
                {
                    "title": "Write draft",
                    "description": "Write the draft",
                    "required_role": "executor",
                    "complexity": "medium",
                    "priority": 5,
                    "depends_on_titles": ["Research"],
                },
            ]
        })
        agent = make_agent("decomposer", response=subtasks_json)

        registered: list[Task] = []

        async def mock_register(parent, subtasks):
            registered.extend(subtasks)

        mock_orch = MagicMock()
        mock_orch.register_subtasks = mock_register
        agent.orchestrator = mock_orch

        task = Task(title="Big goal", description="Do something big")
        await agent.run(task)

        assert task.status == TaskStatus.DONE
        assert len(registered) == 2

        research    = next(t for t in registered if t.title == "Research")
        write_draft = next(t for t in registered if t.title == "Write draft")

        # Opt. 3 — complexity should be preserved
        assert research.complexity == "simple"
        assert write_draft.complexity == "medium"

        # Dependency wiring
        assert research.id in write_draft.depends_on

    @pytest.mark.asyncio
    async def test_decomposer_defaults_invalid_complexity(self):
        """Invalid complexity values should fall back to 'medium'."""
        subtasks_json = json.dumps({
            "subtasks": [
                {
                    "title": "Task A",
                    "description": "Do A",
                    "required_role": "executor",
                    "complexity": "extreme",  # invalid
                    "priority": 5,
                    "depends_on_titles": [],
                }
            ]
        })
        agent = make_agent("decomposer", response=subtasks_json)

        registered: list[Task] = []

        async def mock_register(parent, subtasks):
            registered.extend(subtasks)

        mock_orch = MagicMock()
        mock_orch.register_subtasks = mock_register
        agent.orchestrator = mock_orch

        task = Task(title="Goal", description="...")
        await agent.run(task)

        assert registered[0].complexity == "medium"

    @pytest.mark.asyncio
    async def test_decomposer_handles_invalid_json(self):
        agent = make_agent("decomposer", response="oops not json")
        task = Task(title="Bad", description="...")
        await agent.run(task)
        assert task.status == TaskStatus.FAILED


class TestContextSummarization:
    @pytest.mark.asyncio
    async def test_short_result_not_summarized(self):
        agent = make_agent("executor")
        blackboard = agent.blackboard

        dep = Task(title="Dep", description="dep")
        dep.mark_done("Short result")
        blackboard.write_task_result(dep.id, "Short result", author="dep-agent")

        task = Task(title="Main", description="...", depends_on=[dep.id])
        ctx = agent._build_context(task)

        assert "Short result" in ctx
        # No summary entry should be created
        assert blackboard.read(f"task/{dep.id}/summary") is None

    @pytest.mark.asyncio
    async def test_long_result_is_summarized(self):
        agent = make_agent("executor")
        blackboard = agent.blackboard

        long_result = "x " * (SUMMARIZE_THRESHOLD // 2 + 10)
        dep = Task(title="Dep", description="dep")
        blackboard.write_task_result(dep.id, long_result, author="dep-agent")

        # Patch quick_summarize to avoid real API call
        with patch("swarm.agent.quick_summarize", return_value="Short summary.") as mock_qs:
            task = Task(title="Main", description="...", depends_on=[dep.id])
            ctx = agent._build_context(task)

        mock_qs.assert_called_once()
        assert "Short summary." in ctx

    @pytest.mark.asyncio
    async def test_summary_cached_on_blackboard(self):
        """Second call should use cached summary, not re-call quick_summarize."""
        agent = make_agent("executor")
        blackboard = agent.blackboard

        long_result = "y " * (SUMMARIZE_THRESHOLD // 2 + 10)
        dep_id = "dep-cached"
        blackboard.write(f"task/{dep_id}/result", long_result, author="x")

        with patch("swarm.agent.quick_summarize", return_value="Cached summary.") as mock_qs:
            task1 = Task(title="T1", description="...", depends_on=[dep_id])
            agent._build_context(task1)
            task2 = Task(title="T2", description="...", depends_on=[dep_id])
            agent._build_context(task2)

        # summarise should only be called once — second time uses the cached value
        mock_qs.assert_called_once()


class TestBatchHelpers:
    def test_build_request_returns_correct_structure(self):
        agent = make_agent("executor")
        task = Task(title="Batch task", description="Do it.")
        custom_id, params = agent.build_request(task)

        assert custom_id == task.id
        assert params["model"] == agent.llm.model
        assert params["max_tokens"] == agent.llm.max_tokens
        assert any(m["role"] == "user" for m in params["messages"])

    def test_process_result_writes_blackboard_and_marks_done(self):
        agent = make_agent("executor")
        task = Task(title="T", description="...")
        agent.process_result(task, "batch result")

        assert task.status == TaskStatus.DONE
        assert agent.blackboard.task_result(task.id) == "batch result"


class TestMessaging:
    @pytest.mark.asyncio
    async def test_direct_message_stored_on_blackboard(self):
        agent = make_agent("executor")
        from swarm.messaging import Message
        from datetime import datetime, timezone

        msg = Message(
            topic=f"agent/{agent.id}",
            payload="hello",
            sender="other-agent",
            timestamp=datetime.now(timezone.utc),
        )
        await agent._handle_direct_message(msg)

        keys = agent.blackboard.keys(prefix="message/")
        assert len(keys) == 1


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
