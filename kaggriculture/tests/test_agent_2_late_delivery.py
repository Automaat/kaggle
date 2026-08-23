import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from runner import load_agent, run_match
from variants import _environment


ROOT = pathlib.Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "agents_2.0.x/round42_3_late_delivery"


def _policy(values):
    with _environment(values):
        loaded = load_agent(str(CANDIDATE))
    return loaded.module._policy_agent.policy.baseline


def test_late_carrier_selects_nearest_safe_feed():
    policy = _policy({"AGENT2_LATE_DELIVERY": "1"})
    policy._observation = {"hour": 18}
    policy._wheat_carriers = {2}
    candidates = [
        (4, 2, 0, 0, ("WATER", None), 5, 5, None, (1, 0)),
        (5, 1, 4, 4, ("FEED", None), 4, 4, None, (1, 0)),
        (6, 1, 2, 2, ("FEED", None), 2, 2, None, (1, 0)),
    ]
    with _environment({"AGENT2_LATE_DELIVERY": "1"}):
        chosen = policy._late_delivery_choice(candidates, 2)
    assert chosen == 6


def test_late_delivery_does_not_add_travel():
    policy = _policy({"AGENT2_LATE_DELIVERY": "1"})
    policy._observation = {"hour": 23}
    policy._wheat_carriers = {2}
    candidates = [
        (4, 2, 0, 0, ("WATER", None), 0, 0, None, (1, 0)),
        (5, 1, 2, 2, ("FEED", None), 1, 1, None, (1, 0)),
    ]
    with _environment({"AGENT2_LATE_DELIVERY": "1"}):
        chosen = policy._late_delivery_choice(candidates, 2)
    assert chosen is None


def test_late_delivery_does_not_cross_safe_class():
    policy = _policy({"AGENT2_LATE_DELIVERY": "1"})
    policy._observation = {"hour": 23}
    policy._wheat_carriers = {2}
    candidates = [
        (4, 0, 0, 0, ("WATER!", None), 5, 5, None, (0, 0)),
        (5, 1, 2, 2, ("FEED", None), 1, 1, None, (1, 0)),
    ]
    with _environment({"AGENT2_LATE_DELIVERY": "1"}):
        chosen = policy._late_delivery_choice(candidates, 2)
    assert chosen is None


def test_late_delivery_finishes_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_DYNAMIC_HERD", "1")
    monkeypatch.setenv("AGENT2_LATE_DELIVERY", "1")
    _, _, statuses = run_match(str(CANDIDATE), "champion", seed=66)
    assert statuses == ["DONE", "DONE"]


def test_distributed_feed_finishes_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_DYNAMIC_HERD", "1")
    monkeypatch.setenv("AGENT2_DISTRIBUTE_FEED", "1")
    _, _, statuses = run_match(str(CANDIDATE), "champion", seed=67)
    assert statuses == ["DONE", "DONE"]
