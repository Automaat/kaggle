import copy
import json
import pathlib
import sys

import pytest


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from package_agent import build_archive
from runner import load_agent


ROOT = pathlib.Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "agents_2.0.x/round37_1_task_graph"
BASELINE = ROOT / "agents_1.0.x/v1_14_0_central_herd.py"
REPLAY = ROOT / "replays/main_vs_champion_42.json"


def _module(loaded, name):
    return next(module for module in loaded.package_modules if module.__name__ == name)


def _observations(seat, limit=720):
    replay = json.loads(REPLAY.read_text())
    return [step[seat]["observation"] for step in replay["steps"][:limit]]


class FixedStrategy:
    def __init__(self, value):
        self.value = value
        self.calls = []
        self.resets = 0

    def reset(self):
        self.resets += 1

    def prepare(self, world):
        self.calls.append(world)
        return self.value


def test_default_path_matches_frozen_replay():
    for seat in (0, 1):
        candidate = load_agent(str(CANDIDATE))
        baseline = load_agent(str(BASELINE))
        for observation in _observations(seat):
            candidate_input = copy.deepcopy(observation)
            baseline_input = copy.deepcopy(observation)
            assert candidate(candidate_input) == baseline(baseline_input)
            assert candidate_input == observation
            assert baseline_input == observation


def test_crop_wrapper_preserves_animals_and_restores_identity():
    loaded = load_agent(str(CANDIDATE))
    baseline_module = _module(loaded, "agent_2.baseline")
    strategy_module = _module(loaded, "agent_2.strategy")
    baseline = baseline_module.BaselinePolicy(BASELINE)
    original_result = {
        (0, 0): "COW",
        (1, 0): "MELON",
        (2, 0): "SHEEP",
        (3, 0): "GOOSE",
    }
    seen = {}

    def original(*args):
        seen["args"] = args
        return original_result

    def agent(obs):
        seen["plan"] = baseline.module._dynamic_plan(
            (),
            4,
            {"WHEAT": 3},
            ["BAKERY"],
            10,
            900,
            {"CARROT": 2},
        )
        return {"farmer": ["PASS"], "hands": [], "market": []}

    baseline.module._dynamic_plan = original
    baseline.module.agent = agent
    strategy = strategy_module.CropStrategy(
        (
            (0, 0, "WHEAT"),
            (1, 0, "STRAWBERRY"),
            (2, 0, None),
            (3, 0, "TOMATO"),
        )
    )
    decision = baseline.decide({"day": 4}, strategy)
    assert decision.action["farmer"] == ["PASS"]
    assert seen["args"] == (
        (),
        4,
        {"WHEAT": 3},
        ["BAKERY"],
        10,
        900,
        {"CARROT": 2},
    )
    assert seen["plan"] == {
        (0, 0): "COW",
        (1, 0): "STRAWBERRY",
        (2, 0): "SHEEP",
        (3, 0): "GOOSE",
    }
    assert original_result == {
        (0, 0): "COW",
        (1, 0): "MELON",
        (2, 0): "SHEEP",
        (3, 0): "GOOSE",
    }
    assert baseline.module._dynamic_plan is original


def test_crop_wrapper_restores_identity_after_failure():
    loaded = load_agent(str(CANDIDATE))
    baseline_module = _module(loaded, "agent_2.baseline")
    strategy_module = _module(loaded, "agent_2.strategy")
    baseline = baseline_module.BaselinePolicy(BASELINE)
    original = baseline.module._dynamic_plan
    calls = []

    def agent(obs):
        calls.append(obs)
        raise RuntimeError("failed")

    baseline.module.agent = agent
    strategy = strategy_module.CropStrategy(((0, 0, "WHEAT"),))
    with pytest.raises(RuntimeError, match="failed"):
        baseline.decide({"day": 0}, strategy)
    assert calls == [{"day": 0}]
    assert baseline.module._dynamic_plan is original


def test_strategy_validation_rejects_invalid_targets():
    loaded = load_agent(str(CANDIDATE))
    coordinator_module = _module(loaded, "agent_2.coordinator")
    domain_module = _module(loaded, "agent_2.domain")
    strategy_module = _module(loaded, "agent_2.strategy")
    values = {
        "farms": [
            {
                "tiles": [
                    [None, "LOCKED"],
                    [{"kind": "PLANT", "crop": "WHEAT"}, None],
                ]
            }
        ]
    }
    data = json.dumps(values)
    world = domain_module.World(0, 0, data, data, False, None)

    class Coordinate(int):
        pass

    invalid_targets = (
        ((True, 0, "WHEAT"),),
        ((0, False, "WHEAT"),),
        ((Coordinate(0), 0, "WHEAT"),),
        ((-1, 0, "WHEAT"),),
        ((2, 0, "WHEAT"),),
        ((0, 2, "WHEAT"),),
        ((0, 0, "RICE"),),
        ((0, 0, "WHEAT"), (0, 0, "CARROT")),
        ((1, 0, "WHEAT"),),
        ((0, 1, "WHEAT"),),
    )
    for targets in invalid_targets:
        strategy = strategy_module.CropStrategy(targets)
        with pytest.raises((TypeError, ValueError, IndexError, KeyError)):
            coordinator_module.Agent2Coordinator._validate_strategy(world, strategy)
    mutable = strategy_module.CropStrategy([(0, 0, None)])
    with pytest.raises(TypeError):
        coordinator_module.Agent2Coordinator._validate_strategy(world, mutable)


def test_strategy_validation_uses_xy_coordinates_and_accepts_none():
    loaded = load_agent(str(CANDIDATE))
    coordinator_module = _module(loaded, "agent_2.coordinator")
    domain_module = _module(loaded, "agent_2.domain")
    strategy_module = _module(loaded, "agent_2.strategy")
    values = {"farms": [{"tiles": [["LOCKED", None], [None, "LOCKED"]]}]}
    data = json.dumps(values)
    world = domain_module.World(0, 0, data, data, False, None)
    strategy = strategy_module.CropStrategy(((0, 1, None),))
    coordinator_module.Agent2Coordinator._validate_strategy(world, strategy)


def test_prepare_failure_resets_only_strategy_and_calls_baseline_once():
    loaded = load_agent(str(CANDIDATE))
    baseline_module = _module(loaded, "agent_2.baseline")
    coordinator_module = _module(loaded, "agent_2.coordinator")
    domain_module = _module(loaded, "agent_2.domain")
    tasks_module = _module(loaded, "agent_2.tasks")

    class Baseline:
        def __init__(self):
            self.calls = []
            self.orders = []

        def decide(self, obs, strategy=None):
            self.calls.append((obs, strategy))
            return baseline_module.BaselineDecision(
                {"farmer": ["PASS"], "hands": [], "market": []},
                tasks_module.TaskGraph.empty(0),
            )

        def market_order_limit(self):
            return 10

        def remember_market(self, player, orders):
            self.orders.append((player, orders))

    class Economy:
        def reset(self):
            raise AssertionError("economy reset")

        def plan(self, world, orders):
            return orders

    class FailedStrategy(FixedStrategy):
        def prepare(self, world):
            self.calls.append(world)
            raise RuntimeError("failed")

    values = {"farms": [{"tiles": [[None]]}]}
    data = json.dumps(values)
    world = domain_module.World(0, 0, data, data, False, None)
    baseline = Baseline()
    strategy = FailedStrategy(None)
    coordinator = coordinator_module.Agent2Coordinator(baseline, Economy(), strategy)
    decision = coordinator.decide({"day": 0}, world)
    assert decision.action["farmer"] == ["PASS"]
    assert baseline.calls == [({"day": 0}, None)]
    assert len(strategy.calls) == 1
    assert strategy.resets == 1


def test_duplicate_and_episode_reset_manage_strategy():
    loaded = load_agent(str(CANDIDATE))
    adapter = _module(loaded, "agent_2.adapter")
    planner = FixedStrategy(None)
    candidate = adapter.create_agent(strategy_factory=lambda: planner)
    first, second = _observations(0, 2)
    first_action = candidate(copy.deepcopy(first))
    duplicate = candidate(copy.deepcopy(first))
    assert duplicate == first_action
    assert len(planner.calls) == 1
    candidate(copy.deepcopy(second))
    candidate(copy.deepcopy(first))
    assert len(planner.calls) == 3
    assert planner.resets == 1


def test_strategy_factory_creates_isolated_instances():
    loaded = load_agent(str(CANDIDATE))
    adapter = _module(loaded, "agent_2.adapter")
    planners = []

    def factory():
        planner = FixedStrategy(None)
        planners.append(planner)
        return planner

    first = adapter.create_agent(strategy_factory=factory)
    second = adapter.create_agent(strategy_factory=factory)
    first(copy.deepcopy(_observations(0, 1)[0]))
    second(copy.deepcopy(_observations(1, 1)[0]))
    assert len(planners) == 2
    assert planners[0] is not planners[1]
    assert len(planners[0].calls) == 1
    assert len(planners[1].calls) == 1


def test_fixed_planner_ignores_crop_strategy(monkeypatch):
    monkeypatch.setenv("KAGG_PLANNER", "fixed")
    loaded = load_agent(str(CANDIDATE))
    adapter = _module(loaded, "agent_2.adapter")
    strategy_module = _module(loaded, "agent_2.strategy")
    strategy = strategy_module.CropStrategy(((0, 0, "STRAWBERRY"),))
    candidate = adapter.create_agent(strategy_factory=lambda: FixedStrategy(strategy))
    frozen = load_agent(str(BASELINE))
    observation = _observations(0, 1)[0]
    assert candidate(copy.deepcopy(observation)) == frozen(copy.deepcopy(observation))


def test_packed_strategy_seam_matches_frozen_baseline(tmp_path):
    archive = tmp_path / "agent.tar.gz"
    build_archive(CANDIDATE, archive, source_commit="test")
    candidate = load_agent(str(archive))
    baseline = load_agent(str(BASELINE))
    observation = _observations(0, 1)[0]
    assert candidate(copy.deepcopy(observation)) == baseline(copy.deepcopy(observation))
