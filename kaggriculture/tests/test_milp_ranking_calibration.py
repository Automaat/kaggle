import json
import importlib
import pathlib
import sys
from types import SimpleNamespace

import pytest


TOOLS = pathlib.Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
calibration = importlib.import_module("run_milp_ranking_calibration")


class Strategy:
    def __init__(self, targets):
        self.targets = targets


def _world(step=0, filled=()):
    tiles = [
        [None if x < 5 and y < 5 else "LOCKED" for x in range(10)]
        for y in range(10)
    ]
    for x, y in filled:
        tiles[y][x] = {"kind": "PLANT", "crop": "CARROT"}
    data = json.dumps({"farms": [{"tiles": tiles}]})
    return SimpleNamespace(step=step, player=0, data=data)


def test_portfolio_planner_assigns_exact_counts_outside_herd():
    counts = (3, 4, 0, 3, 3)
    planner = calibration.PortfolioPlanner(Strategy, counts)
    result = planner.prepare(_world())
    crops = tuple(crop for _x, _y, crop in result.targets)
    assert tuple(crops.count(crop) for crop in calibration.CROPS) == counts
    assert len({(x, y) for x, y, _crop in result.targets}) == 13


def test_portfolio_planner_returns_remaining_day_zero_targets_only():
    planner = calibration.PortfolioPlanner(Strategy, (0, 13, 0, 0, 0))
    first = planner.prepare(_world())
    filled = tuple((x, y) for x, y, _crop in first.targets[:4])
    second = planner.prepare(_world(step=1, filled=filled))
    assert len(second.targets) == 9
    assert planner.prepare(_world(step=24)) is None
    planner.reset()
    assert len(planner.prepare(_world()).targets) == 13


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ((1, 2, 3), (10, 20, 30), 1.0),
        ((1, 2, 3), (30, 20, 10), -1.0),
        ((1, 1, 3), (10, 10, 30), 1.0),
    ],
)
def test_spearman_handles_order_and_ties(left, right, expected):
    assert calibration.spearman(left, right) == pytest.approx(expected)


def test_spearman_returns_none_for_constant_vector():
    assert calibration.spearman((1, 1, 1), (1, 2, 3)) is None


def test_summary_calculates_execution_and_blocking_gates(monkeypatch):
    monkeypatch.setattr(calibration, "SEEDS", (1,))
    forecasts = {
        name: {
            "terminal_cash": float(index * 100),
            "success": True,
            "mip_gap": 0.0,
            "message": "optimal",
        }
        for index, (name, _counts) in enumerate(calibration.PORTFOLIOS)
    }
    games = []
    for seat in (0, 1):
        for index, (name, counts) in enumerate(calibration.PORTFOLIOS):
            requested = calibration._requested_counts(counts)
            games.append(
                {
                    "portfolio": name,
                    "seed": 1,
                    "candidate_seat": seat,
                    "candidate_reward": float(index * 1000),
                    "candidate_status": "DONE",
                    "champion_status": "DONE",
                    "requested_day_1_crops": requested,
                    "executed_day_1_crops": requested,
                }
            )
    result = calibration.summarize(forecasts, games)
    assert result["metrics"]["planned_versus_executed_ratio"] == 1.0
    assert result["metrics"]["mean_group_spearman"] == pytest.approx(1.0)
    assert result["metrics"]["mean_normalized_top_regret"] == 0.0
    assert not result["a2b_promoted"]
    assert not result["gates"]["intermediate_day_calibration_complete"]
