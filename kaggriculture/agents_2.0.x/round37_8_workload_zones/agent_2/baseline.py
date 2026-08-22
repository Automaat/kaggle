import os
import types
from dataclasses import dataclass
from pathlib import Path

from .tasks import TaskGraph


BASELINE_NAME = "v1_14_0_central_herd.py"


@dataclass(frozen=True, slots=True)
class BaselineDecision:
    action: dict
    task_graph: TaskGraph


def resolve_baseline_path() -> Path:
    current = Path(__file__).resolve()
    for root in current.parents:
        for relative in (("frozen", BASELINE_NAME), ("agents_1.0.x", BASELINE_NAME)):
            candidate = root.joinpath(*relative)
            if candidate.is_file():
                return candidate
    raise FileNotFoundError(BASELINE_NAME)


class BaselinePolicy:
    def __init__(self, path=None):
        self.path = Path(path).resolve() if path is not None else resolve_baseline_path()
        self.module = None
        self._captured_tasks = None
        self.reset()

    def reset(self) -> None:
        source = self.path.read_text()
        module = types.ModuleType(f"_agent_2_baseline_{id(self)}_{id(source)}")
        module.__file__ = str(self.path)
        exec(compile(source, str(self.path), "exec"), module.__dict__)
        module.MAX_QUADRANTS = int(os.environ.get("AGENT2_LAND", "2"))
        module.MAX_HANDS = int(os.environ.get("AGENT2_MAX_HANDS", "14"))
        module.HANDS_PER_TILE = float(os.environ.get("AGENT2_HANDS_PER_TILE", "0.2"))
        original = module._protected_underfoot_tasks

        def workload_plan(player, day, step, tiles, units, board_size):
            state = module._day_plans.get(player)
            if (
                state is not None
                and step >= state["step"]
                and state["day"] == day
                and state["units"] == units
            ):
                state["step"] = step
                return state["zones"]
            working = [
                (x, y, 3 if isinstance(tile, dict) and "animal" in tile else 1)
                for x, y, tile in tiles
                if isinstance(tile, dict)
                and (tile.get("kind") == "PLANT" or "animal" in tile)
            ]
            positions = module._snake_order([(x, y) for x, y, _weight in working], board_size)
            weights = {(x, y): weight for x, y, weight in working}
            total = sum(weights.values())
            zones = {}
            cumulative = 0.0
            for x, y in positions:
                weight = weights[(x, y)]
                zone = int((cumulative + weight / 2) * units / max(1, total))
                zones[(x, y)] = min(units - 1, zone)
                cumulative += weight
            module._day_plans[player] = {
                "day": day,
                "units": units,
                "zones": zones,
                "step": step,
            }
            return zones

        def capture(tasks, units, inventories):
            result = original(tasks, units, inventories)
            self._captured_tasks = tuple(tasks)
            return result

        module._protected_underfoot_tasks = capture
        module._cluster_plan = workload_plan
        self.module = module
        self._captured_tasks = None

    def decide(self, obs) -> BaselineDecision:
        if self.module is None:
            self.reset()
        self._captured_tasks = None
        action = self.module.agent(obs)
        day = int(obs["day"])
        graph = TaskGraph.from_legacy(day, self._captured_tasks or ())
        return BaselineDecision(action, graph)

    def act(self, obs) -> dict:
        return self.decide(obs).action
