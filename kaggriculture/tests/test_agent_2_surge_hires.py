import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from runner import load_agent, run_match


ROOT = pathlib.Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "agents_2.0.x/round37_9_surge_hires"
BASELINE = "variant:KAGG_LAND=2;KAGG_MAX_HANDS=14;KAGG_HANDS_PER_TILE=0.2"


def test_neglect_counts_dry_plants_and_unfed_animals():
    loaded = load_agent(str(CANDIDATE))
    policy = loaded.module._policy_agent.policy.baseline
    observation = {
        "player": 0,
        "farms": [
            {
                "tiles": [[
                    {"kind": "PLANT", "consecutive_unwatered": 1},
                    {"kind": "PASTURE", "animal": "COW", "consecutive_unfed": 1},
                    {"kind": "PLANT", "consecutive_unwatered": 0},
                ]]
            }
        ],
    }
    assert policy._neglect(observation) == 2


def test_surge_hires_finish_live_episode():
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=42)
    assert statuses == ["DONE", "DONE"]
