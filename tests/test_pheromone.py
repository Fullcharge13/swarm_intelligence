"""Unit tests for PheromoneBoard — no API calls."""

import json
import warnings
from pathlib import Path

import pytest

from swarm.pheromone import PheromoneBoard, goal_hash


class TestGoalHash:
    def test_same_string_gives_same_hash(self):
        assert goal_hash("Write a blog post") == goal_hash("Write a blog post")

    def test_different_strings_give_different_hashes(self):
        assert goal_hash("Task A") != goal_hash("Task B")

    def test_returns_eight_chars(self):
        assert len(goal_hash("anything")) == 8


class TestDeposit:
    def test_deposit_creates_entry(self):
        board = PheromoneBoard()
        board.deposit("scout/abc/research", 1.0, data={"title": "Research"})
        entry = board.get("scout/abc/research")
        assert entry is not None
        assert entry["weight"] == pytest.approx(1.0)
        assert entry["data"] == {"title": "Research"}

    def test_deposit_reinforces_existing_weight(self):
        board = PheromoneBoard()
        board.deposit("key", 1.0)
        board.deposit("key", 0.5)
        assert board.get("key")["weight"] == pytest.approx(1.5)

    def test_deposit_updates_data_on_reinforce(self):
        board = PheromoneBoard()
        board.deposit("key", 1.0, data={"v": 1})
        board.deposit("key", 0.5, data={"v": 2})
        assert board.get("key")["data"] == {"v": 2}


class TestEvaporate:
    def test_evaporate_reduces_weight(self):
        board = PheromoneBoard()
        board.deposit("key", 1.0)
        board.evaporate(decay_rate=0.1)
        assert board.get("key")["weight"] == pytest.approx(0.9)

    def test_evaporate_prunes_entries_below_threshold(self):
        board = PheromoneBoard()
        board.deposit("key", 0.005)
        board.evaporate(decay_rate=0.1)
        assert board.get("key") is None

    def test_evaporate_keeps_strong_entries(self):
        board = PheromoneBoard()
        board.deposit("key", 5.0)
        board.evaporate(decay_rate=0.1)
        assert board.get("key") is not None


class TestStrongest:
    def test_returns_top_n_sorted_by_weight(self):
        board = PheromoneBoard()
        board.deposit("scout/abc/a", 3.0)
        board.deposit("scout/abc/b", 1.0)
        board.deposit("scout/abc/c", 2.0)
        result = board.strongest("scout/abc/", n=2)
        assert len(result) == 2
        assert result[0][0] == "scout/abc/a"
        assert result[1][0] == "scout/abc/c"

    def test_prefix_filters_unrelated_keys(self):
        board = PheromoneBoard()
        board.deposit("scout/abc/task", 1.0)
        board.deposit("other/key", 5.0)
        result = board.strongest("scout/abc/")
        assert all(k.startswith("scout/abc/") for k, _ in result)

    def test_empty_board_returns_empty_list(self):
        board = PheromoneBoard()
        assert board.strongest("scout/") == []

    def test_n_limits_results(self):
        board = PheromoneBoard()
        for i in range(10):
            board.deposit(f"scout/abc/{i}", float(i))
        result = board.strongest("scout/abc/", n=3)
        assert len(result) == 3


class TestPersistence:
    def test_save_and_load_round_trip(self, tmp_path):
        board = PheromoneBoard()
        board.deposit("scout/abc/t1", 2.0, data={"title": "T1"})
        path = tmp_path / "pheromones.json"
        board.save(path)

        board2 = PheromoneBoard()
        board2.load(path)
        entry = board2.get("scout/abc/t1")
        assert entry is not None
        assert entry["weight"] == pytest.approx(2.0)
        assert entry["data"] == {"title": "T1"}

    def test_load_missing_file_gives_empty_board(self, tmp_path):
        board = PheromoneBoard()
        board.load(tmp_path / "nonexistent.json")
        assert board.strongest() == []

    def test_load_corrupt_file_warns_and_resets(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not valid json")
        board = PheromoneBoard()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            board.load(path)
        assert len(w) == 1
        assert issubclass(w[0].category, RuntimeWarning)
        assert board.strongest() == []

    def test_save_creates_parent_dirs(self, tmp_path):
        board = PheromoneBoard()
        board.deposit("k", 1.0)
        nested = tmp_path / "a" / "b" / "pheromones.json"
        board.save(nested)
        assert nested.exists()
