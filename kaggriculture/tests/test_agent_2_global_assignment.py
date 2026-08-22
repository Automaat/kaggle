import copy
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from runner import load_agent, run_match


ROOT = pathlib.Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "agents_2.0.x/round37_3_global_assignment"
BASELINE = "variant:KAGG_LAND=2;KAGG_MAX_HANDS=14;KAGG_HANDS_PER_TILE=0.2"
REPLAY = ROOT / "replays/main_vs_champion_42.json"


def _package_module(loaded, name):
    return next(module for module in loaded.package_modules if module.__name__ == name)


def _scheduler_types():
    loaded = load_agent(str(CANDIDATE))
    scheduler = _package_module(loaded, "agent_2.scheduler")
    tasks = _package_module(loaded, "agent_2.tasks")
    return scheduler.GlobalAssignmentScheduler, tasks.TaskGraph


class FakeModule:
    ZONE_PENALTY = 1
    UNDERFOOT = True
    _day_plans = {}

    @staticmethod
    def _task_key(priority, distance):
        if priority == 0:
            return 0, priority, distance
        return 1 if distance <= 2 else 2, priority, distance

    @staticmethod
    def _step_toward(source, target):
        if source[0] < target[0]:
            return ["EAST"]
        if source[0] > target[0]:
            return ["WEST"]
        return None

    @staticmethod
    def _act(operation, item):
        return [operation] if item is None else [operation, item]


def test_global_assignment_avoids_greedy_trap():
    scheduler_type, task_graph = _scheduler_types()
    graph = task_graph.from_legacy(
        0,
        [
            (1, 1, 0, ("FEED", None)),
            (1, 2, 0, ("FERTILIZE", None)),
        ],
    )
    scheduler = scheduler_type()
    scheduler.begin({"player": 0})
    scheduler.capture(
        graph,
        {},
        [(0, 0), (3, 0)],
        [{"WHEAT": 1, "FERTILIZER": 1}, {"WHEAT": 1}],
    )
    scheduler.record_selector(0)
    scheduler.record_selector(1)
    assignments = scheduler._assign(FakeModule)
    assert assignments[0].operation == "FERTILIZE"
    assert assignments[1].operation == "FEED"


def test_feed_is_assigned_only_to_wheat_carrier():
    scheduler_type, task_graph = _scheduler_types()
    graph = task_graph.from_legacy(0, [(0, 1, 0, ("FEED!", None))])
    scheduler = scheduler_type()
    scheduler.begin({"player": 0})
    scheduler.capture(graph, {}, [(0, 0), (2, 0)], [{}, {"WHEAT": 1}])
    scheduler.record_selector(0)
    scheduler.record_selector(1)
    assignments = scheduler._assign(FakeModule)
    assert set(assignments) == {1}


def test_protected_task_stays_with_owner():
    scheduler_type, task_graph = _scheduler_types()
    graph = task_graph.from_legacy(0, [(2, 1, 0, ("WATER", None))])
    scheduler = scheduler_type()
    scheduler.begin({"player": 0})
    scheduler.capture(graph, {0: 1}, [(1, 0), (3, 0)], [{}, {}])
    scheduler.record_selector(0)
    scheduler.record_selector(1)
    assignments = scheduler._assign(FakeModule)
    assert set(assignments) == {1}


def test_rewrite_keeps_market_orders():
    scheduler_type, task_graph = _scheduler_types()
    graph = task_graph.from_legacy(0, [(2, 0, 0, ("WATER", None))])
    scheduler = scheduler_type()
    scheduler.begin({"player": 0})
    scheduler.capture(graph, {}, [(0, 0)], [{}])
    scheduler.record_selector(0)
    action = {"farmer": ["PASS"], "hands": [], "market": [["SELL", "MELON", 3]]}
    rewritten = scheduler.rewrite(action, FakeModule)
    assert rewritten["farmer"] == ["WATER"]
    assert rewritten["market"] == action["market"]


def test_live_candidate_finishes_and_preserves_market_policy():
    candidate = load_agent(str(CANDIDATE))
    baseline = load_agent(BASELINE)
    replay = json.loads(REPLAY.read_text())
    for step in replay["steps"][:96]:
        observation = step[0]["observation"]
        candidate_action = candidate(copy.deepcopy(observation))
        baseline_action = baseline(copy.deepcopy(observation))
        assert candidate_action["market"] == baseline_action["market"]
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=42)
    assert statuses == ["DONE", "DONE"]
