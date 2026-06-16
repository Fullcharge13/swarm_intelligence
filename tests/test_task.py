"""Tests for task.py — no API calls required."""

import pytest

from swarm.task import Task, TaskGraph, TaskPriority, TaskStatus


def make_task(**kwargs) -> Task:
    defaults = {"title": "Test task", "description": "desc", "complexity": "medium"}
    return Task(**{**defaults, **kwargs})


class TestTask:
    def test_default_status(self):
        t = make_task()
        assert t.status == TaskStatus.PENDING

    def test_mark_done(self):
        t = make_task()
        t.mark_done("result")
        assert t.status == TaskStatus.DONE
        assert t.result == "result"

    def test_mark_failed(self):
        t = make_task()
        t.mark_failed("boom")
        assert t.status == TaskStatus.FAILED
        assert t.error == "boom"

    def test_is_terminal(self):
        t = make_task()
        assert not t.is_terminal()
        t.mark_done(None)
        assert t.is_terminal()


class TestTaskGraph:
    def test_add_and_get(self):
        g = TaskGraph()
        t = make_task()
        g.add(t)
        assert g.get(t.id) is t

    def test_ready_tasks_no_deps(self):
        g = TaskGraph()
        t1 = make_task(title="A")
        t2 = make_task(title="B")
        g.add(t1)
        g.add(t2)
        ready = g.ready_tasks()
        assert len(ready) == 2

    def test_ready_tasks_with_unsatisfied_dep(self):
        g = TaskGraph()
        t1 = make_task(title="A")
        t2 = make_task(title="B", depends_on=[t1.id])
        g.add(t1)
        g.add(t2)
        ready = g.ready_tasks()
        assert t1 in ready
        assert t2 not in ready

    def test_ready_tasks_dep_satisfied(self):
        g = TaskGraph()
        t1 = make_task(title="A")
        t2 = make_task(title="B", depends_on=[t1.id])
        g.add(t1)
        g.add(t2)
        t1.mark_done("done")
        g.update(t1)
        ready = g.ready_tasks()
        assert t2 in ready

    def test_parent_not_in_ready_while_children_exist(self):
        g = TaskGraph()
        parent = make_task(title="Parent")
        child = make_task(title="Child", parent_id=parent.id)
        g.add(parent)
        g.add(child)
        ready_ids = {t.id for t in g.ready_tasks()}
        assert parent.id not in ready_ids
        assert child.id in ready_ids

    def test_summary(self):
        g = TaskGraph()
        t1 = make_task()
        t2 = make_task()
        g.add(t1)
        g.add(t2)
        t1.mark_done("x")
        g.update(t1)
        s = g.summary()
        assert s["done"] == 1
        assert s["pending"] == 1

    def test_all_done(self):
        g = TaskGraph()
        t = make_task()
        g.add(t)
        assert not g.all_done()
        t.mark_done("x")
        g.update(t)
        assert g.all_done()

    def test_complexity_field_stored(self):
        t = make_task(complexity="simple")
        assert t.complexity == "simple"

    def test_complexity_defaults_to_medium(self):
        t = Task(title="t", description="d")
        assert t.complexity == "medium"

    def test_priority_ordering(self):
        g = TaskGraph()
        low = make_task(title="low", priority=TaskPriority.LOW)
        high = make_task(title="high", priority=TaskPriority.HIGH)
        g.add(low)
        g.add(high)
        ready = g.ready_tasks()
        assert ready[0].id == high.id
