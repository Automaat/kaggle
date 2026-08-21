import copy
import json
import pathlib
import sys

import pytest


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from runner import load_agent


ROOT = pathlib.Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "agents_2.0.x/round37_2a_rebuild_queue"
BASELINE = ROOT / "agents_1.0.x/v1_14_0_central_herd.py"
REPLAY = ROOT / "replays/main_vs_champion_42.json"


def _observations(seat, limit=720):
    replay = json.loads(REPLAY.read_text())
    return [step[seat]["observation"] for step in replay["steps"][:limit]]


def _policy(loaded):
    return loaded.module._policy_agent.policy


def _package_module(loaded, name):
    return next(module for module in loaded.package_modules if module.__name__ == name)


def test_rebuilt_queues_preserve_actions_and_account_for_tasks():
    candidate = load_agent(str(CANDIDATE))
    baseline = load_agent(str(BASELINE))
    saw_suffix = False
    for count, observation in enumerate(_observations(0, 240), 1):
        assert candidate(copy.deepcopy(observation)) == baseline(copy.deepcopy(observation))
        policy = _policy(candidate)
        plan = policy.scheduler.plan
        graph = policy.state.task_graph
        goals = [goal for route in plan.routes for goal in route.goals]
        assert plan.step == observation["step"]
        assert plan.day == observation["day"]
        assert plan.selector_calls == len(plan.routes)
        assert plan.queued_tasks == len(goals)
        assert len(goals) == len(set(goals))
        assert plan.queued_tasks + len(plan.unassigned_indices) == len(graph.nodes)
        assert policy.scheduler.rebuild_count == count
        saw_suffix = saw_suffix or any(route.suffix for route in plan.routes)
    assert saw_suffix


def test_duplicate_keeps_plan_without_rebuild():
    candidate = load_agent(str(CANDIDATE))
    observation = _observations(0, 1)[0]
    action = candidate(copy.deepcopy(observation))
    policy = _policy(candidate)
    plan = policy.scheduler.plan
    rebuilds = policy.scheduler.rebuild_count
    assert candidate(copy.deepcopy(observation)) == action
    assert policy.scheduler.plan is plan
    assert policy.scheduler.rebuild_count == rebuilds


def test_episode_reset_clears_scheduler_before_new_plan():
    candidate = load_agent(str(CANDIDATE))
    first, second = _observations(0, 2)
    candidate(copy.deepcopy(first))
    candidate(copy.deepcopy(second))
    assert _policy(candidate).scheduler.rebuild_count == 2
    candidate(copy.deepcopy(first))
    assert _policy(candidate).scheduler.rebuild_count == 1
    assert _policy(candidate).scheduler.plan.step == first["step"]


@pytest.mark.parametrize(
    ("name", "value"),
    (("KAGG_ROUTE_RL", "0"), ("KAGG_PROTECT_UNDERFOOT", "0")),
)
def test_disabled_capture_hooks_keep_actions_and_empty_routes(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    candidate = load_agent(str(CANDIDATE))
    baseline = load_agent(str(BASELINE))
    saw_tasks = False
    for observation in _observations(0, 96):
        assert candidate(copy.deepcopy(observation)) == baseline(copy.deepcopy(observation))
        policy = _policy(candidate)
        saw_tasks = saw_tasks or bool(policy.state.task_graph.nodes)
        assert policy.scheduler.plan.routes == ()
    if name == "KAGG_ROUTE_RL":
        assert saw_tasks
    else:
        assert not saw_tasks


def test_training_selector_runs_once_per_record(monkeypatch):
    monkeypatch.setenv("KAGG_ROUTE_RL_TRAIN", "1")
    monkeypatch.setenv("KAGG_ROUTE_RL_SEED", "271828")
    candidate = load_agent(str(CANDIDATE))
    baseline = load_agent(str(BASELINE))
    recorded = 0
    for observation in _observations(0, 120):
        assert candidate(copy.deepcopy(observation)) == baseline(copy.deepcopy(observation))
        recorded += _policy(candidate).scheduler.plan.selector_calls
    module = _policy(candidate).baseline.module
    choices = (module._route_rl_stats.get(0) or {}).get("choices", 0)
    assert choices == recorded


def test_scheduler_aborts_partial_plan_on_baseline_error():
    candidate = load_agent(str(CANDIDATE))
    first, second = _observations(0, 2)
    candidate(copy.deepcopy(first))
    policy = _policy(candidate)

    def fail(_obs):
        raise RuntimeError("failed baseline")

    policy.baseline.module.agent = fail
    with pytest.raises(RuntimeError, match="failed baseline"):
        candidate(copy.deepcopy(second))
    assert policy.scheduler.plan is None


def test_suffix_uses_route_tail_and_stable_tie_order():
    candidate = load_agent(str(CANDIDATE))
    policy = _policy(candidate)
    scheduler_module = _package_module(candidate, "agent_2.scheduler")
    tasks_module = _package_module(candidate, "agent_2.tasks")
    scheduler = scheduler_module.RebuildQueueScheduler()
    observation = _observations(0, 1)[0]
    scheduler.begin(observation)
    graph = tasks_module.TaskGraph.from_legacy(
        0,
        [
            (9, 0, 0, ("DIG", None)),
            (9, 1, 0, ("DIG", None)),
            (9, 1, 1, ("DIG", None)),
        ],
    )
    scheduler.capture(graph, {}, [[9, 9]], [{}])
    scheduler.record_selector(0, 0, (), (), {})
    plan = scheduler.finish(graph, policy.baseline.module)
    assert tuple(goal.x for goal in plan.routes[0].goals) == (0, 1, 1)
    assert tuple(goal.y for goal in plan.routes[0].goals) == (0, 0, 1)
