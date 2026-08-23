import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from runner import load_agent, run_match


ROOT = pathlib.Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "agents_2.0.x/round38_2_hire_batch"
BASELINE = "variant:KAGG_LAND=2;KAGG_MAX_HANDS=14;KAGG_HANDS_PER_TILE=0.2"


def test_hire_batch_keeps_total_hands_at_twelve():
    loaded = load_agent(str(CANDIDATE))
    module = loaded.module._policy_agent.policy.baseline.module
    assert module.HIRES_PER_TURN == 10
    assert module.MAX_HANDS == 12
    assert module.TRIP_RADIUS == 1


def test_hire_batch_finishes_live_episode():
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=42)
    assert statuses == ["DONE", "DONE"]


def test_outer_policy_finishes_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_OUTER_NO_FERTILIZER", "1")
    monkeypatch.setenv("AGENT2_OUTER_SURVIVAL_WATER", "1")
    monkeypatch.setenv("AGENT2_OUTER_BATCH_HARVEST", "1")
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=43)
    assert statuses == ["DONE", "DONE"]


def test_tile_bundles_finish_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_TILE_BUNDLES", "1")
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=44)
    assert statuses == ["DONE", "DONE"]


def test_strawberry_cap_finishes_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_STRAWBERRY_CAP", "42")
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=45)
    assert statuses == ["DONE", "DONE"]


def test_animal_specialists_finish_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_ANIMAL_SPECIALISTS", "3")
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=46)
    assert statuses == ["DONE", "DONE"]
