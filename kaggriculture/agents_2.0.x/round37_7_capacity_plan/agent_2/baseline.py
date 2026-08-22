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
        requested_land = int(os.environ.get("AGENT2_LAND", "2"))
        land = self._capacity_land(requested_land)
        herd_size = max(0, 14 - 2 * land)
        cows = (herd_size * 3 + 2) // 5
        sheep = herd_size - cows
        module.MAX_QUADRANTS = land
        module.MAX_HANDS = int(os.environ.get("AGENT2_MAX_HANDS", str(10 + 2 * land)))
        module.HANDS_PER_TILE = float(os.environ.get("AGENT2_HANDS_PER_TILE", "0.2"))
        module.HIRE_HOURS = int(os.environ.get("AGENT2_HIRE_HOURS", "4"))
        module.HERD_SPEC = os.environ.get("AGENT2_HERD_SPEC", f"COW:{cows},SHEEP:{sheep}")
        original = module._protected_underfoot_tasks

        def capture(tasks, units, inventories):
            result = original(tasks, units, inventories)
            self._captured_tasks = tuple(tasks)
            return result

        module._protected_underfoot_tasks = capture
        self.module = module
        self._captured_tasks = None

    @staticmethod
    def _capacity_land(requested_land):
        accepted = 0
        for land in range(1, requested_land + 1):
            herd_size = max(0, 14 - 2 * land)
            tiles = 25 * (land + 1)
            max_hands = 10 + 2 * land
            hands = min(max_hands, round(tiles * 0.2), 12)
            mandatory_work = tiles + 2 * herd_size
            if mandatory_work > (hands + 1) * 7.7:
                break
            accepted = land
        return accepted

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
