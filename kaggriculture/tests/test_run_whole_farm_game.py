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
