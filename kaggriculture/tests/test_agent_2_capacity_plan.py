import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from runner import load_agent, run_match
from variants import _environment


ROOT = pathlib.Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "agents_2.0.x/round37_7_capacity_plan"


@pytest.mark.parametrize(
    ("requested", "land", "hands", "herd"),
    [
        (1, 1, 12, "COW:7,SHEEP:5"),
        (2, 2, 14, "COW:6,SHEEP:4"),
        (3, 2, 14, "COW:6,SHEEP:4"),
    ],
)
def test_capacity_plan_scales_herd_and_hands(requested, land, hands, herd):
    with _environment({"AGENT2_LAND": str(requested)}):
        loaded = load_agent(str(CANDIDATE))
    module = loaded.module._policy_agent.policy.baseline.module
    assert module.MAX_QUADRANTS == land
    assert module.MAX_HANDS == hands
    assert module.HERD_SPEC == herd


def test_capacity_plan_finishes_live_episode():
    baseline = "variant:KAGG_LAND=2;KAGG_MAX_HANDS=14;KAGG_HANDS_PER_TILE=0.2"
    _, _, statuses = run_match(str(CANDIDATE), baseline, seed=42)
    assert statuses == ["DONE", "DONE"]
