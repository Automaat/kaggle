import copy
import dataclasses
import json
import pathlib
import sys

import pytest


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from runner import load_agent


ROOT = pathlib.Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "agents_2.0.x/round37_1_task_graph"
BASELINE = ROOT / "agents_1.0.x/v1_14_0_central_herd.py"
REPLAY = ROOT / "replays/main_vs_champion_42.json"


def _observations(seat, limit=720):
    replay = json.loads(REPLAY.read_text())
    return [step[seat]["observation"] for step in replay["steps"][:limit]]


def _policy(loaded):
    return loaded.module._policy_agent.policy


def _task_types():
    loaded = load_agent(str(CANDIDATE))
    module = next(
        module for module in loaded.package_modules
        if module.__name__ == "agent_2.tasks"
    )
    return module.TaskGraph, module.TaskId


def test_task_graph_preserves_order_identity_and_ordinals():
    task_graph, task_id = _task_types()
    legacy = [
        (2, 3, 4, ("WATER", None)),
        (2, 3, 4, ("WATER", None)),
        (5, 7, 8, ("HARVEST", "MELON")),
    ]
    graph = task_graph.from_legacy(6, legacy)
    assert tuple(node.source_order for node in graph.nodes) == (0, 1, 2)
    assert tuple(node.identifier.ordinal for node in graph.nodes) == (0, 1, 0)
    identifier = task_id(6, 7, 8, "HARVEST", "MELON", 0)
    assert graph.find(identifier) == graph.nodes[2]


def test_task_graph_storage_is_immutable():
    task_graph, _task_id = _task_types()
    graph = task_graph.from_legacy(0, [(9, 1, 2, ("DIG", None))])
    assert isinstance(graph.nodes, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        graph.nodes = ()
    with pytest.raises(dataclasses.FrozenInstanceError):
        graph.nodes[0].priority = 0


def test_stage37_1_captures_tasks_without_changing_action():
    candidate = load_agent(str(CANDIDATE))
    baseline = load_agent(str(BASELINE))
    for observation in _observations(0, 96):
        candidate_input = copy.deepcopy(observation)
        baseline_input = copy.deepcopy(observation)
        assert candidate(candidate_input) == baseline(baseline_input)
        assert candidate_input == observation
        assert baseline_input == observation
        graph = _policy(candidate).state.task_graph
        assert graph.day == observation["day"]
        if graph.nodes:
            break
    else:
        raise AssertionError("replay did not produce tasks")


def test_duplicate_observation_preserves_matching_graph_and_action():
    candidate = load_agent(str(CANDIDATE))
    observation = _observations(0, 1)[0]
    first_action = candidate(copy.deepcopy(observation))
    first_graph = _policy(candidate).state.task_graph
    second_action = candidate(copy.deepcopy(observation))
    assert second_action == first_action
    assert _policy(candidate).state.task_graph is first_graph


def test_episode_reset_replaces_module_and_records_new_graph():
    candidate = load_agent(str(CANDIDATE))
    first, second = _observations(0, 2)
    candidate(copy.deepcopy(first))
    first_module = _policy(candidate).baseline.module
    first_graph = _policy(candidate).state.task_graph
    candidate(copy.deepcopy(second))
    candidate(copy.deepcopy(first))
    assert _policy(candidate).baseline.module is not first_module
    assert _policy(candidate).state.task_graph is not first_graph
    assert _policy(candidate).state.task_graph.day == first["day"]


def test_disabled_capture_returns_empty_graph_without_action_change(monkeypatch):
    monkeypatch.setenv("KAGG_PROTECT_UNDERFOOT", "0")
    candidate = load_agent(str(CANDIDATE))
    baseline = load_agent(str(BASELINE))
    observation = _observations(0, 1)[0]
    assert candidate(copy.deepcopy(observation)) == baseline(copy.deepcopy(observation))
    assert _policy(candidate).state.task_graph.nodes == ()
