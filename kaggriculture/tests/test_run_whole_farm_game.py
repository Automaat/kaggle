import gzip
import json
import sys
from types import SimpleNamespace

import pytest

from kaggriculture.tools.economics import run_whole_farm_game as runner
from kaggriculture.tools.economics.run_whole_farm_game import (
    _DailyProgress,
    _argument_parser,
    _checkpoint_artifacts,
    _planning_horizon,
    _write_failure_result,
)


def test_daily_progress_writes_atomic_solve_checkpoint(tmp_path):
    path = tmp_path / "progress.json"
    trace = SimpleNamespace(epoch=4, fingerprint="a" * 64)
    ledger = SimpleNamespace(iterations=2, terminal_cash=4321.0)
    solve = SimpleNamespace(
        animal_result=SimpleNamespace(animals=("SHEEP",)),
        crop_result=SimpleNamespace(decisions=("CARROT", "STRAWBERRY")),
        ledger=ledger,
    )
    source = SimpleNamespace(
        traces=(trace,),
        backend=SimpleNamespace(last_solve=solve),
    )
    states = (
        SimpleNamespace(reward=100.0, status="ACTIVE"),
        SimpleNamespace(reward=90.0, status="ACTIVE"),
    )
    tiles = [[None for _ in range(10)] for _ in range(10)]
    tiles[0][0] = {"crop": "CARROT", "kind": "PLANT"}
    tiles[0][1] = {"animal": "SHEEP", "kind": "PASTURE"}
    tiles[0][2] = {"kind": "COOP"}
    observation = {
        "farms": (
            {
                "money": 100.0,
                "tiles": tiles,
                "unlocked_quadrants": ["NW", "NE"],
            },
            {"money": 90.0},
        ),
        "private": {
            "seeds": {"CARROT": 3},
            "shed": {"SHEEP": 2},
        },
    }
    horizon = _planning_horizon(5, True)
    progress = _DailyProgress(
        path,
        "control-1.14",
        3_980_000,
        0,
        horizon,
    )

    progress.record(0, 24, observation, states, source)
    progress.record(0, 48, observation, states, source)

    document = json.loads(path.read_text())
    assert document["schema"] == "whole-farm-progress-v1"
    assert document["planning_horizon"] == {
        "commit_days": 1,
        "exact_horizon_days": 5,
        "season_last_day": 29,
        "strategic_tail": True,
    }
    assert len(document["records"]) == 1
    assert document["latest"]["completed_day"] == 0
    assert document["latest"]["planning_day"] == 1
    assert document["latest"]["source_step"] == 24
    assert document["latest"]["last_epoch"] == 4
    assert document["latest"]["fingerprint"] == "a" * 64
    assert document["latest"]["candidate_farm"] == {
        "empty_structures": {"COOP": 1, "PASTURE": 0},
        "placed_animals": {"COW": 0, "GOOSE": 0, "SHEEP": 1},
        "seeds": {
            "CARROT": 3,
            "MELON": 0,
            "STRAWBERRY": 0,
            "TOMATO": 0,
            "WHEAT": 0,
        },
        "shed_animals": {"COW": 0, "GOOSE": 0, "SHEEP": 2},
        "standing_crops": {
            "CARROT": 1,
            "MELON": 0,
            "STRAWBERRY": 0,
            "TOMATO": 0,
            "WHEAT": 0,
        },
        "unlocked_quadrant_count": 2,
        "unlocked_quadrants": ["NW", "NE"],
    }
    assert document["latest"]["last_solve"] == {
        "animal_decisions": 1,
        "crop_decisions": 2,
        "iterations": 2,
        "terminal_cash": 4321.0,
    }
    assert document["latest"]["players"][0] == {
        "cash": 100.0,
        "reward": 100.0,
        "seat": 0,
        "status": "ACTIVE",
    }
    assert not (tmp_path / ".progress.json.tmp").exists()


def test_checkpoint_writes_official_html_and_replay_atomically(tmp_path):
    class Environment:
        def __init__(self):
            self.render_arguments = None
            self.info = {}

        def toJSON(self):
            return {
                "info": self.info,
                "steps": [[{"observation": {}}]],
            }

        def render(self, **arguments):
            self.render_arguments = arguments
            payload = json.dumps(self.toJSON(), sort_keys=True)
            return f"<html>{payload}</html>"

    environment = Environment()
    replay_path = tmp_path / "replay.json.gz"
    html_path = tmp_path / "replay.html"
    replay, metadata = _checkpoint_artifacts(
        environment,
        replay_path,
        html_path,
        "control-1.14",
        3_980_000,
        0,
        _planning_horizon(5, True),
    )

    with gzip.open(replay_path, "rt") as stream:
        written_replay = json.load(stream)
    assert written_replay == replay
    planning_horizon = {
        "commit_days": 1,
        "exact_horizon_days": 5,
        "season_last_day": 29,
        "strategic_tail": True,
    }
    assert replay["planning_horizon"] == planning_horizon
    assert replay["info"]["planning_horizon"] == planning_horizon
    html = html_path.read_text()
    assert '"exact_horizon_days": 5' in html
    assert '"strategic_tail": true' in html
    assert environment.render_arguments == {
        "controls": True,
        "height": 800,
        "mode": "html",
        "width": 1200,
    }
    assert metadata["replay"]["steps"] == 1
    assert metadata["html"]["bytes"] == len(html.encode())
    assert not (tmp_path / ".replay.json.gz.tmp").exists()
    assert not (tmp_path / ".replay.html.tmp").exists()


@pytest.mark.parametrize("value", ("0", "31"))
def test_cli_rejects_exact_horizon_outside_season(value):
    parser = _argument_parser()
    arguments = (
        "--output",
        "result.json",
        "--replay",
        "replay.json.gz",
        "--trace",
        "trace.json.gz",
        "--exact-horizon-days",
        value,
    )

    with pytest.raises(SystemExit):
        parser.parse_args(arguments)


def test_cli_accepts_five_day_strategic_horizon():
    arguments = _argument_parser().parse_args(
        (
            "--output",
            "result.json",
            "--replay",
            "replay.json.gz",
            "--trace",
            "trace.json.gz",
            "--exact-horizon-days",
            "5",
            "--strategic-tail",
        )
    )

    assert arguments.exact_horizon_days == 5
    assert arguments.strategic_tail is True


def test_strategic_tail_requires_exact_horizon():
    with pytest.raises(ValueError, match="requires an exact horizon"):
        _planning_horizon(strategic_tail=True)


def test_failure_result_preserves_config_and_partial_artifacts(tmp_path):
    output_path = tmp_path / "attempt.json"
    replay_path = tmp_path / "replay.json.gz"
    html_path = tmp_path / "replay.html"
    progress_path = tmp_path / "progress.json"
    with gzip.open(replay_path, "wt") as stream:
        json.dump({"steps": [[], [], []]}, stream)
    html_path.write_text("<html>partial</html>")
    progress_path.write_text(
        json.dumps(
            {
                "latest": {"completed_day": 1, "source_step": 48},
                "records": ({"source_step": 24}, {"source_step": 48}),
            }
        )
    )
    arguments = SimpleNamespace(
        arm="control-1.14",
        candidate_seat=0,
        exact_horizon_days=5,
        html=html_path,
        output=output_path,
        progress=progress_path,
        replay=replay_path,
        seed=3_980_000,
        strategic_tail=True,
        trace=tmp_path / "trace.json.gz",
    )

    result = _write_failure_result(arguments, RuntimeError("solve failed"))

    written = json.loads(output_path.read_text())
    assert written == result
    assert result["status"] == "failed"
    assert result["planning_horizon"] == {
        "commit_days": 1,
        "exact_horizon_days": 5,
        "season_last_day": 29,
        "strategic_tail": True,
    }
    assert result["error"] == {
        "text": "solve failed",
        "type": "RuntimeError",
    }
    assert result["partial_replay"]["steps"] == 3
    assert result["html"]["path"] == str(html_path)
    assert result["progress"]["records"] == 2
    assert result["source_step"] == 48
    assert result["last_checkpoint"] == {
        "completed_day": 1,
        "source_step": 48,
    }
    assert len(result["result_sha256"]) == 64
    assert not (tmp_path / ".attempt.json.tmp").exists()


def test_main_writes_failure_result_and_reraises(tmp_path, monkeypatch):
    output_path = tmp_path / "attempt.json"

    def fail(*args):
        raise RuntimeError("planner stopped")

    monkeypatch.setattr(runner, "run", fail)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_whole_farm_game.py",
            "--output",
            str(output_path),
            "--replay",
            str(tmp_path / "replay.json.gz"),
            "--trace",
            str(tmp_path / "trace.json.gz"),
            "--exact-horizon-days",
            "5",
            "--strategic-tail",
        ],
    )

    with pytest.raises(RuntimeError, match="planner stopped"):
        runner.main()

    result = json.loads(output_path.read_text())
    assert result["status"] == "failed"
    assert result["planning_horizon"]["exact_horizon_days"] == 5
    assert result["planning_horizon"]["strategic_tail"] is True
    assert not (tmp_path / ".attempt.json.tmp").exists()


def test_main_ignores_stale_artifacts_after_early_failure(tmp_path, monkeypatch):
    output_path = tmp_path / "attempt.json"
    replay_path = tmp_path / "replay.json.gz"
    html_path = tmp_path / "replay.html"
    progress_path = tmp_path / "progress.json"
    trace_path = tmp_path / "trace.json.gz"
    with gzip.open(replay_path, "wt") as stream:
        json.dump({"attempt_id": "old", "steps": [[]]}, stream)
    html_path.write_text("<html>old</html>")
    progress_path.write_text(
        json.dumps({"attempt_id": "old", "records": (), "latest": None})
    )
    with gzip.open(trace_path, "wt") as stream:
        json.dump({"attempt_id": "old", "decision_traces": ()}, stream)

    def fail(*args):
        raise RuntimeError("failed before artifacts")

    monkeypatch.setattr(runner, "run", fail)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_whole_farm_game.py",
            "--output",
            str(output_path),
            "--replay",
            str(replay_path),
            "--trace",
            str(trace_path),
            "--progress",
            str(progress_path),
            "--html",
            str(html_path),
            "--exact-horizon-days",
            "5",
            "--strategic-tail",
        ],
    )

    with pytest.raises(RuntimeError, match="failed before artifacts"):
        runner.main()

    result = json.loads(output_path.read_text())
    assert result["attempt_id"] != "old"
    assert "partial_replay" not in result
    assert "html" not in result
    assert "progress" not in result
    assert "decision_trace" not in result
    assert "last_checkpoint" not in result
