import json
from types import SimpleNamespace

from kaggriculture.tools.economics.run_whole_farm_game import _DailyProgress


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
    observation = {
        "farms": ({"money": 100.0}, {"money": 90.0}),
    }
    progress = _DailyProgress(path, "control-1.14", 3_980_000, 0)

    progress.record(0, 24, observation, states, source)
    progress.record(0, 48, observation, states, source)

    document = json.loads(path.read_text())
    assert document["schema"] == "whole-farm-progress-v1"
    assert len(document["records"]) == 1
    assert document["latest"]["completed_day"] == 0
    assert document["latest"]["planning_day"] == 1
    assert document["latest"]["source_step"] == 24
    assert document["latest"]["last_epoch"] == 4
    assert document["latest"]["fingerprint"] == "a" * 64
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
