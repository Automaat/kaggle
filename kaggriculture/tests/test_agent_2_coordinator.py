import copy
import json
import pathlib
import sys


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from runner import load_agent
from package_agent import build_archive


ROOT = pathlib.Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "agents_2.0.x/round37_1_task_graph"
BASELINE = ROOT / "agents_1.0.x/v1_14_0_central_herd.py"
REPLAY = ROOT / "replays/main_vs_champion_42.json"


def _observations(seat, limit=720):
    replay = json.loads(REPLAY.read_text())
    return [step[seat]["observation"] for step in replay["steps"][:limit]]


def _module(loaded, name):
    return next(module for module in loaded.package_modules if module.__name__ == name)


def _custom_agent(factory):
    loaded = load_agent(str(CANDIDATE))
    adapter = _module(loaded, "agent_2.adapter")
    return loaded, adapter.create_agent(economy_factory=factory)


class FixedEconomy:
    def __init__(self, orders):
        self.orders = orders
        self.calls = []
        self.resets = 0

    def reset(self):
        self.resets += 1

    def plan(self, world, frozen_orders):
        self.calls.append((world, frozen_orders))
        return self.orders


def test_default_coordinator_matches_frozen_replay():
    for seat in (0, 1):
        candidate = load_agent(str(CANDIDATE))
        baseline = load_agent(str(BASELINE))
        for observation in _observations(seat):
            candidate_input = copy.deepcopy(observation)
            baseline_input = copy.deepcopy(observation)
            assert candidate(candidate_input) == baseline(baseline_input)
            assert candidate_input == observation
            assert baseline_input == observation


def test_custom_economy_changes_only_market():
    planner = FixedEconomy((("HIRE",),))
    loaded, candidate = _custom_agent(lambda: planner)
    adapter = _module(loaded, "agent_2.adapter")
    frozen = adapter.create_agent()
    observation = _observations(0, 1)[0]
    candidate_action = candidate(copy.deepcopy(observation))
    frozen_action = frozen(copy.deepcopy(observation))
    assert candidate_action["market"] == [["HIRE"]]
    assert candidate_action["farmer"] == frozen_action["farmer"]
    assert candidate_action["hands"] == frozen_action["hands"]
    assert candidate.policy.state.task_graph == frozen.policy.state.task_graph
    assert isinstance(planner.calls[0][1], tuple)
    assert all(isinstance(order, tuple) for order in planner.calls[0][1])


def test_market_orders_are_copied_truncated_and_remembered():
    planner = FixedEconomy(tuple(("HIRE",) for _ in range(12)))
    _loaded, candidate = _custom_agent(lambda: planner)
    observation = _observations(0, 1)[0]
    action = candidate(copy.deepcopy(observation))
    remembered = candidate.policy.baseline.module._opponent_state[0]["orders"]
    assert action["market"] == [["HIRE"]] * 10
    assert remembered == action["market"]
    action["market"][0].append("MUTATED")
    assert remembered == [["HIRE"]] * 10


def test_invalid_or_failed_economy_uses_frozen_orders():
    for result in (None, (("UNKNOWN",),), (("SELL", "WHEAT", 0),)):
        planner = FixedEconomy(result)
        _loaded, candidate = _custom_agent(lambda: planner)
        baseline = load_agent(str(BASELINE))
        observation = _observations(0, 1)[0]
        assert candidate(copy.deepcopy(observation)) == baseline(copy.deepcopy(observation))
        assert planner.resets == 1

    class FailedEconomy(FixedEconomy):
        def plan(self, world, frozen_orders):
            self.calls.append((world, frozen_orders))
            raise RuntimeError("failed")

    planner = FailedEconomy(())
    _loaded, candidate = _custom_agent(lambda: planner)
    baseline = load_agent(str(BASELINE))
    observation = _observations(0, 1)[0]
    assert candidate(copy.deepcopy(observation)) == baseline(copy.deepcopy(observation))
    assert planner.resets == 1


def test_duplicate_does_not_call_economy_again():
    planner = FixedEconomy((("HIRE",),))
    _loaded, candidate = _custom_agent(lambda: planner)
    observation = _observations(0, 1)[0]
    first = candidate(copy.deepcopy(observation))
    first["market"].append(["MUTATED"])
    duplicate = candidate(copy.deepcopy(observation))
    assert duplicate["market"] == [["HIRE"]]
    assert len(planner.calls) == 1


def test_episode_reset_resets_baseline_and_economy():
    planner = FixedEconomy((("HIRE",),))
    _loaded, candidate = _custom_agent(lambda: planner)
    first, second = _observations(0, 2)
    candidate(copy.deepcopy(first))
    initial_module = candidate.policy.baseline.module
    candidate(copy.deepcopy(second))
    candidate(copy.deepcopy(first))
    assert candidate.policy.baseline.module is not initial_module
    assert planner.resets == 1
    assert len(planner.calls) == 3


def test_factory_creates_isolated_economy_planners():
    planners = []

    def factory():
        planner = FixedEconomy((("HIRE",),))
        planners.append(planner)
        return planner

    loaded = load_agent(str(CANDIDATE))
    adapter = _module(loaded, "agent_2.adapter")
    first = adapter.create_agent(economy_factory=factory)
    second = adapter.create_agent(economy_factory=factory)
    first(copy.deepcopy(_observations(0, 1)[0]))
    second(copy.deepcopy(_observations(1, 1)[0]))
    assert len(planners) == 2
    assert planners[0] is not planners[1]
    assert len(planners[0].calls) == 1
    assert len(planners[1].calls) == 1


def test_next_observation_sees_final_previous_orders():
    class SequencedEconomy(FixedEconomy):
        def plan(self, world, frozen_orders):
            self.calls.append((world, frozen_orders))
            if len(self.calls) == 1:
                return (("SELL", "WHEAT", 1),)
            return (("BUY_PRODUCT", "WHEAT", 1),)

    planner = SequencedEconomy(())
    _loaded, candidate = _custom_agent(lambda: planner)
    module = candidate.policy.baseline.module
    original = module._update_opponent_stock
    seen = []

    def capture(obs, player):
        state = module._opponent_state.get(player)
        seen.append(copy.deepcopy(state.get("orders")) if state else None)
        return original(obs, player)

    module._update_opponent_stock = capture
    first, second = _observations(0, 2)
    candidate(copy.deepcopy(first))
    candidate(copy.deepcopy(second))
    assert seen == [None, [["SELL", "WHEAT", 1]]]
    assert module._opponent_state[0]["orders"] == [["BUY_PRODUCT", "WHEAT", 1]]


def test_synchronization_failure_restores_frozen_action():
    loaded = load_agent(str(CANDIDATE))
    coordinator_module = _module(loaded, "agent_2.coordinator")
    baseline_module = _module(loaded, "agent_2.baseline")
    domain_module = _module(loaded, "agent_2.domain")
    tasks_module = _module(loaded, "agent_2.tasks")

    class FailingBaseline:
        def __init__(self):
            self.calls = []

        def decide(self, obs):
            return baseline_module.BaselineDecision(
                {"farmer": ["PASS"], "hands": [], "market": [["BUY_LAND"]]},
                tasks_module.TaskGraph.empty(0),
            )

        def market_order_limit(self):
            return 10

        def remember_market(self, player, orders):
            self.calls.append(copy.deepcopy(orders))
            if len(self.calls) == 1:
                raise RuntimeError("failed")

    baseline = FailingBaseline()
    planner = FixedEconomy((("HIRE",),))
    coordinator = coordinator_module.Agent2Coordinator(baseline, planner)
    world = domain_module.World(0, 0, "id", "{}", False, None)
    decision = coordinator.decide({}, world)
    assert decision.action == {
        "farmer": ["PASS"],
        "hands": [],
        "market": [["BUY_LAND"]],
    }
    assert baseline.calls == [[["HIRE"]], [["BUY_LAND"]]]
    assert planner.resets == 1


def test_packed_coordinator_matches_frozen_baseline(tmp_path):
    archive = tmp_path / "agent.tar.gz"
    manifest = build_archive(CANDIDATE, archive, source_commit="test")
    candidate = load_agent(str(archive))
    baseline = load_agent(str(BASELINE))
    observation = _observations(0, 1)[0]
    assert candidate(copy.deepcopy(observation)) == baseline(copy.deepcopy(observation))
    assert manifest["baseline_sha256"] == (
        "86951703eac27253938500eac664650c1e927d1b86b26ed84be008f24739d699"
    )
