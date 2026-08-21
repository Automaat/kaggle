import copy
import json
import pathlib
import sys

import pytest


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from runner import load_agent, run_match


ROOT = pathlib.Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "agents_2.0.x/round37_2b2_sticky_goal"
BASELINE = ROOT / "agents_1.0.x/v1_14_0_central_herd.py"
OPPONENT = ROOT / "agents_1.0.x/v1_13_0_rl_routing.py"
REPLAY = ROOT / "replays/main_vs_champion_42.json"
MOVES = {"NORTH", "SOUTH", "EAST", "WEST"}


def _observations(seat, limit=720):
    replay = json.loads(REPLAY.read_text())
    return [step[seat]["observation"] for step in replay["steps"][:limit]]


def _policy(loaded):
    return loaded.module._policy_agent.policy


def _package_module(loaded, name):
    return next(module for module in loaded.package_modules if module.__name__ == name)


def _candidate(index, priority, x, safe_class):
    return (index, priority, x, 0, ("DIG", None), x, x, None, safe_class)


def _ready_scheduler():
    loaded = load_agent(str(CANDIDATE))
    scheduler_module = _package_module(loaded, "agent_2.scheduler")
    tasks_module = _package_module(loaded, "agent_2.tasks")
    graph = tasks_module.TaskGraph.from_legacy(
        0,
        [(5, 1, 0, ("DIG", None)), (5, 2, 0, ("DIG", None))],
    )
    route = scheduler_module.UnitRoute(0, graph.nodes[0].identifier, ())
    scheduler = scheduler_module.StickyGoalScheduler()
    scheduler.plan = scheduler_module.TurnPlan(0, 0, (route,), 1, 1, (1,), 0, 1)
    observation = _observations(0, 1)[0]
    scheduler.begin(observation)
    scheduler.capture(graph, {}, [[0, 0]], [{}])
    return loaded, scheduler, graph


def test_same_nonzero_priority_and_safe_class_uses_persisted_goal():
    _loaded, scheduler, _graph = _ready_scheduler()
    candidates = (
        _candidate(0, 5, 1, (1, 0)),
        _candidate(1, 5, 2, (1, 0)),
    )
    assert scheduler.choose(0, candidates, (), 1) == 0


@pytest.mark.parametrize(
    ("persisted_priority", "original_priority", "persisted_safe", "original_safe", "training"),
    (
        (0, 0, (0, 0), (0, 0), False),
        (5, 4, (1, 0), (1, 0), False),
        (5, 5, (2, 0), (1, 0), False),
        (5, 5, (1, 0), (1, 0), True),
    ),
)
def test_priority_safety_and_training_return_original(
    persisted_priority,
    original_priority,
    persisted_safe,
    original_safe,
    training,
):
    _loaded, scheduler, _graph = _ready_scheduler()
    candidates = (
        _candidate(0, persisted_priority, 1, persisted_safe),
        _candidate(1, original_priority, 2, original_safe),
    )
    assert scheduler.choose(0, candidates, (), 1, training) == 1


def test_priority_pause_preserves_route_for_later_resume():
    loaded, scheduler, graph = _ready_scheduler()
    paused = (
        _candidate(0, 5, 1, (1, 0)),
        _candidate(1, 4, 2, (1, 0)),
    )
    assert scheduler.choose(0, paused, (), 1) == 1
    scheduler.record_selector(0, 1, paused, (), {})
    plan = scheduler.finish(graph, _policy(loaded).baseline.module)
    assert plan.routes[0].head == graph.nodes[0].identifier
    next_observation = copy.deepcopy(_observations(0, 2)[1])
    next_observation["day"] = 0
    scheduler.begin(next_observation)
    scheduler.capture(graph, {}, [[0, 0]], [{}])
    resumed = (
        _candidate(0, 5, 1, (1, 0)),
        _candidate(1, 5, 2, (1, 0)),
    )
    assert scheduler.choose(0, resumed, (), 1) == 0


def test_unavailable_goal_uses_frozen_choice_without_rebuild():
    loaded, scheduler, graph = _ready_scheduler()
    candidates = (_candidate(1, 5, 2, (1, 0)),)
    assert scheduler.choose(0, candidates, (), 1) == 1
    scheduler.record_selector(0, 1, candidates, (), {})
    plan = scheduler.finish(graph, _policy(loaded).baseline.module)
    assert scheduler.rebuild_count == 0
    assert scheduler.diagnostics.unavailable_fallbacks == 1
    assert plan.routes[0].head == graph.nodes[0].identifier


def test_abort_preserves_committed_plan_and_diagnostics():
    candidate = load_agent(str(CANDIDATE))
    first, second = _observations(0, 2)
    candidate(copy.deepcopy(first))
    policy = _policy(candidate)
    plan = policy.scheduler.plan
    diagnostics = policy.scheduler.diagnostics

    def fail(_obs):
        raise RuntimeError("failed baseline")

    policy.baseline.module.agent = fail
    with pytest.raises(RuntimeError, match="failed baseline"):
        candidate(copy.deepcopy(second))
    assert policy.scheduler.plan is plan
    assert policy.scheduler.diagnostics == diagnostics


def test_duplicate_and_reset_keep_transaction_boundaries():
    candidate = load_agent(str(CANDIDATE))
    first, second = _observations(0, 2)
    action = candidate(copy.deepcopy(first))
    policy = _policy(candidate)
    plan = policy.scheduler.plan
    enrollments = policy.scheduler.enrollment_count
    assert candidate(copy.deepcopy(first)) == action
    assert policy.scheduler.plan is plan
    assert policy.scheduler.enrollment_count == enrollments
    candidate(copy.deepcopy(second))
    candidate(copy.deepcopy(first))
    assert policy.scheduler.plan is not plan
    assert policy.scheduler.plan.day == 0


def test_replay_shadow_preserves_market_and_changes_only_unit_choices():
    candidate = load_agent(str(CANDIDATE))
    baseline = load_agent(str(BASELINE))
    changed = 0
    for observation in _observations(0, 240):
        candidate_action = candidate(copy.deepcopy(observation))
        baseline_action = baseline(copy.deepcopy(observation))
        assert candidate_action["market"] == baseline_action["market"]
        changed += candidate_action != baseline_action
    assert changed >= 5


def test_live_episode_uses_persistent_routes_across_walking_turns():
    candidate = load_agent(str(CANDIDATE))
    longest_walk_run = 0
    current_walk_run = 0
    previous_persisted = 0

    def observed(obs):
        nonlocal current_walk_run, longest_walk_run, previous_persisted
        action = candidate(obs)
        persisted = _policy(candidate).scheduler.diagnostics.persisted_selections
        operations = [action["farmer"], *action["hands"]]
        if persisted > previous_persisted and any(op[0] in MOVES for op in operations):
            current_walk_run += 1
            longest_walk_run = max(longest_walk_run, current_walk_run)
        else:
            current_walk_run = 0
        previous_persisted = persisted
        return action

    _environment, _rewards, statuses = run_match(observed, str(OPPONENT), seed=42)
    scheduler = _policy(candidate).scheduler
    assert statuses == ["DONE", "DONE"]
    assert scheduler.diagnostics.persisted_selections >= 25
    assert scheduler.rebuild_count == 0
    assert longest_walk_run >= 2
