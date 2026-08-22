import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from runner import load_agent, run_match


ROOT = pathlib.Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "agents_2.0.x/round37_4_daily_tour"
BASELINE = "variant:KAGG_LAND=2;KAGG_MAX_HANDS=14;KAGG_HANDS_PER_TILE=0.2"


def _package_module(loaded, name):
    return next(module for module in loaded.package_modules if module.__name__ == name)


def test_route_index_is_boustrophedon():
    loaded = load_agent(str(CANDIDATE))
    module = _package_module(loaded, "agent_2.scheduler")
    scheduler = module.DailyTourScheduler()
    assert [scheduler._route_index(x, 0) for x in range(3)] == [0, 1, 2]
    assert [scheduler._route_index(x, 1) for x in range(3)] == [19, 18, 17]


def test_live_daily_tour_finishes():
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=42)
    assert statuses == ["DONE", "DONE"]
