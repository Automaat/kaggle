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
        self._observation = None
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

        def capture(tasks, units, inventories):
            if os.environ.get("AGENT2_WATER_COHORTS", "1") == "1":
                tasks[:] = [task for task in tasks if self._keep_task(module, task)]
            result = original(tasks, units, inventories)
            self._captured_tasks = tuple(tasks)
            return result

        module._protected_underfoot_tasks = capture
        self.module = module
        self._captured_tasks = None
        self._observation = None

    def _keep_task(self, module, task):
        _priority, x, y, operation_data = task
        operation, _item = operation_data
        if operation != "WATER":
            return True
        obs = self._observation
        player = obs["player"]
        tile = obs["farms"][player]["tiles"][y][x]
        if not isinstance(tile, dict) or tile.get("kind") != "PLANT":
            return True
        crop = tile["crop"]
        data = module.CROPS[crop]
        age = obs["day"] - tile["planted_day"]
        if data["ongoing"]:
            if age + 1 in module.PRODUCTION_AGES[crop]:
                return True
        else:
            start = (data["max_yield_day"] + 1) // 2
            if start <= age <= data["max_yield_day"] and tile.get("yield_units", 0) < data["max_yield"]:
                return True
        period = max(2, int(os.environ.get("AGENT2_WATER_PERIOD", "2")))
        return (x + y + obs["day"]) % period != period - 1

    def decide(self, obs) -> BaselineDecision:
        if self.module is None:
            self.reset()
        self._captured_tasks = None
        self._observation = obs
        try:
            action = self.module.agent(obs)
            day = int(obs["day"])
            graph = TaskGraph.from_legacy(day, self._captured_tasks or ())
            return BaselineDecision(action, graph)
        finally:
            self._observation = None

    def act(self, obs) -> dict:
        return self.decide(obs).action
