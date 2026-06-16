"""Tests for the shared blackboard."""

from swarm.environment import Blackboard


class TestBlackboard:
    def test_write_and_read(self):
        b = Blackboard()
        b.write("foo", "bar", author="a1")
        assert b.read("foo") == "bar"

    def test_read_missing_key(self):
        b = Blackboard()
        assert b.read("nope") is None

    def test_history_preserved(self):
        b = Blackboard()
        b.write("k", 1, author="a")
        b.write("k", 2, author="b")
        h = b.history("k")
        assert len(h) == 2
        assert h[0].value == 1
        assert h[1].value == 2

    def test_latest_value_is_returned(self):
        b = Blackboard()
        b.write("k", "first", author="a")
        b.write("k", "second", author="b")
        assert b.read("k") == "second"

    def test_keys_prefix(self):
        b = Blackboard()
        b.write("task/1/result", "r1", author="a")
        b.write("task/2/result", "r2", author="a")
        b.write("note/misc", "n", author="a")
        task_keys = b.keys(prefix="task/")
        assert len(task_keys) == 2
        assert all(k.startswith("task/") for k in task_keys)

    def test_task_result_helpers(self):
        b = Blackboard()
        b.write_task_result("abc", "my result", author="agent-1")
        assert b.task_result("abc") == "my result"

    def test_snapshot(self):
        b = Blackboard()
        b.write("a", 1, author="x")
        b.write("b", 2, author="x")
        snap = b.snapshot()
        assert snap == {"a": 1, "b": 2}
