import json
import pathlib
import sys


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from runner import load_agent
from package_agent import build_archive


ROOT = pathlib.Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "agents_2.0.x/round39_8_milp_rollout"


def _module(loaded, name):
    return next(module for module in loaded.package_modules if module.__name__ == name)


def _world(domain, step=0, filled=()):
    tiles = [
        [None if x < 5 and y < 5 else "LOCKED" for x in range(10)]
        for y in range(10)
    ]
    for x, y in filled:
        tiles[y][x] = {"kind": "PLANT", "crop": "CARROT"}
    values = {"farms": [{"tiles": tiles}]}
    data = json.dumps(values)
    return domain.World(step, 0, data, data, False, None)


def test_milp_strategy_assigns_registered_initial_portfolio():
    loaded = load_agent(str(CANDIDATE))
    domain = _module(loaded, "agent_2.domain")
    strategy = _module(loaded, "agent_2.strategy")
    planner = strategy.MilpFirstDayStrategy()
    result = planner.prepare(_world(domain))
    crops = [crop for _x, _y, crop in result.targets]
    assert crops.count("CARROT") == 9
    assert crops.count("MELON") == 4
    assert len(result.targets) == 13
    positions = {(x, y) for x, y, _crop in result.targets}
    herd = set(
        sorted(
            ((x, y) for y in range(5) for x in range(5)),
            key=lambda position: (
                abs(position[0] - 4) + abs(position[1] - 4),
                position,
            ),
        )[:12]
    )
    assert not positions & herd


def test_milp_strategy_keeps_only_empty_targets_and_stops_after_day_zero():
    loaded = load_agent(str(CANDIDATE))
    domain = _module(loaded, "agent_2.domain")
    strategy = _module(loaded, "agent_2.strategy")
    planner = strategy.MilpFirstDayStrategy()
    first = planner.prepare(_world(domain))
    filled = tuple((x, y) for x, y, _crop in first.targets[:3])
    remaining = planner.prepare(_world(domain, step=1, filled=filled))
    assert len(remaining.targets) == 10
    assert planner.prepare(_world(domain, step=24)) is None
    planner.reset()
    assert len(planner.prepare(_world(domain)).targets) == 13


def test_milp_runtime_snapshot_does_not_import_scipy_or_oracle():
    sources = tuple(CANDIDATE.rglob("*.py"))
    assert sources
    text = "\n".join(path.read_text() for path in sources)
    assert "scipy" not in text.lower()
    assert "milp_oracle" not in text


def test_milp_runtime_snapshot_packs(tmp_path):
    archive = tmp_path / "agent.tar.gz"
    build_archive(CANDIDATE, archive, source_commit="test")
    candidate = load_agent(str(archive))
    assert callable(candidate)
