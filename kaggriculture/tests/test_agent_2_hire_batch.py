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


def test_scaling_defaults_are_selected():
    loaded = load_agent(str(CANDIDATE))
    policy = loaded.module._policy_agent.policy.baseline
    assert policy.module.MAX_QUADRANTS == 2
    assert policy.module.MAX_HANDS == 12
    assert policy.module.HIRES_PER_TURN == 10


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


def test_expansion_hands_finish_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_EXPANSION_HANDS", "14")
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=47)
    assert statuses == ["DONE", "DONE"]


def test_sale_funded_feed_finishes_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_SALE_FUNDED_FEED", "1")
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=48)
    assert statuses == ["DONE", "DONE"]


def test_precare_finishes_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_PRECARE", "1")
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=49)
    assert statuses == ["DONE", "DONE"]


def test_crisis_feed_finishes_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_CRISIS_FEED", "1")
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=50)
    assert statuses == ["DONE", "DONE"]


def test_expansion_seed_batch_finishes_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_EXPANSION_SEED_BATCH", "4")
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=51)
    assert statuses == ["DONE", "DONE"]


def test_sale_funded_seeds_finish_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_SALE_FUNDED_SEEDS", "1")
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=52)
    assert statuses == ["DONE", "DONE"]


def test_production_feed_finishes_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_PRODUCTION_FEED", "1")
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=53)
    assert statuses == ["DONE", "DONE"]


def test_plant_cap_release_finishes_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_PLANT_CAP", "42")
    monkeypatch.setenv("AGENT2_PLANT_CAP_RELEASE_DAY", "20")
    monkeypatch.setenv("AGENT2_PLANT_CAP_RELEASE", "55")
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=54)
    assert statuses == ["DONE", "DONE"]


def test_plant_cap_ramp_finishes_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_PLANT_CAP_RAMP", "3")
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=55)
    assert statuses == ["DONE", "DONE"]


def test_skip_cap_weeds_finishes_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_SKIP_CAP_WEEDS", "1")
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=56)
    assert statuses == ["DONE", "DONE"]


def test_terminal_prune_finishes_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_TERMINAL_PRUNE", "1")
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=57)
    assert statuses == ["DONE", "DONE"]


def test_service_flow_layout_finishes_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_SERVICE_FLOW_LAYOUT", "1")
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=58)
    assert statuses == ["DONE", "DONE"]


def test_final_extra_crop_finishes_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_PLANT_CAP_FINAL_DAY", "24")
    monkeypatch.setenv("AGENT2_PLANT_CAP_FINAL", "63")
    monkeypatch.setenv("AGENT2_FINAL_EXTRA_CROP", "CARROT")
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=59)
    assert statuses == ["DONE", "DONE"]


def test_terminal_fertilize_finishes_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_PLANT_CAP_FINAL_DAY", "24")
    monkeypatch.setenv("AGENT2_PLANT_CAP_FINAL", "63")
    monkeypatch.setenv("AGENT2_FINAL_EXTRA_CROP", "WHEAT")
    monkeypatch.setenv("AGENT2_TERMINAL_FERTILIZE", "WHEAT")
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=60)
    assert statuses == ["DONE", "DONE"]


def test_late_hire_first_finishes_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_LATE_HIRE_FIRST_DAY", "24")
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=61)
    assert statuses == ["DONE", "DONE"]


def test_seed_buy_stop_finishes_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_SEED_BUY_STOP_HOUR", "20")
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=62)
    assert statuses == ["DONE", "DONE"]


def test_feed_action_ledger_finishes_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_FEED_ACTION_LEDGER", "1")
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=63)
    assert statuses == ["DONE", "DONE"]
