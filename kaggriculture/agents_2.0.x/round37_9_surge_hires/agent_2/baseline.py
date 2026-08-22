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
        self._surge_days = {}
        self.reset()

    def reset(self) -> None:
        source = self.path.read_text()
        module = types.ModuleType(f"_agent_2_baseline_{id(self)}_{id(source)}")
        module.__file__ = str(self.path)
        exec(compile(source, str(self.path), "exec"), module.__dict__)
        module.MAX_QUADRANTS = int(os.environ.get("AGENT2_LAND", "2"))
        module.MAX_HANDS = 12
        module.HANDS_PER_TILE = float(os.environ.get("AGENT2_HANDS_PER_TILE", "0.2"))
        module.HIRE_HOURS = 5
        original = module._protected_underfoot_tasks

        def capture(tasks, units, inventories):
            result = original(tasks, units, inventories)
            self._captured_tasks = tuple(tasks)
            return result

        module._protected_underfoot_tasks = capture
        self.module = module
        self._captured_tasks = None
        self._surge_days = {}

    @staticmethod
    def _neglect(obs):
        farm = obs["farms"][obs["player"]]
        return sum(
            tile.get("consecutive_unwatered", 0) > 0
            or tile.get("consecutive_unfed", 0) > 0
            for row in farm["tiles"]
            for tile in row
            if isinstance(tile, dict)
        )

    def decide(self, obs) -> BaselineDecision:
        if self.module is None:
            self.reset()
        self._captured_tasks = None
        player = int(obs["player"])
        day = int(obs["day"])
        if player not in self._surge_days or self._surge_days[player][0] != day:
            threshold = int(os.environ.get("AGENT2_SURGE_THRESHOLD", "5"))
            self._surge_days[player] = (day, self._neglect(obs) >= threshold)
        self.module.MAX_HANDS = 14 if self._surge_days[player][1] else 12
        action = self.module.agent(obs)
        graph = TaskGraph.from_legacy(day, self._captured_tasks or ())
        return BaselineDecision(action, graph)

    def act(self, obs) -> dict:
        return self.decide(obs).action
