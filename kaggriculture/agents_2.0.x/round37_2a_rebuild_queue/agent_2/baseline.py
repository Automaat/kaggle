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
        self._captured_graph = None
        self._scheduler = None
        self._current_day = None
        self.reset()

    def reset(self) -> None:
        source = self.path.read_text()
        module = types.ModuleType(f"_agent_2_baseline_{id(self)}_{id(source)}")
        module.__file__ = str(self.path)
        exec(compile(source, str(self.path), "exec"), module.__dict__)
        original = module._protected_underfoot_tasks
        original_selector = module._route_rl_choice

        def capture(tasks, units, inventories):
            result = original(tasks, units, inventories)
            self._captured_tasks = tuple(tasks)
            self._captured_graph = TaskGraph.from_legacy(
                self._current_day,
                self._captured_tasks,
            )
            if self._scheduler is not None:
                self._scheduler.capture(
                    self._captured_graph,
                    result,
                    units,
                    inventories,
                )
            return result

        def select(player, step, candidates, tasks, taken, unit_index, targets):
            chosen = original_selector(
                player,
                step,
                candidates,
                tasks,
                taken,
                unit_index,
                targets,
            )
            if self._scheduler is not None:
                self._scheduler.record_selector(
                    unit_index,
                    chosen,
                    candidates,
                    taken,
                    targets,
                )
            return chosen

        module._protected_underfoot_tasks = capture
        module._route_rl_choice = select
        self.module = module
        self._captured_tasks = None
        self._captured_graph = None
        self._scheduler = None
        self._current_day = None

    def decide(self, obs, scheduler=None) -> BaselineDecision:
        if self.module is None:
            self.reset()
        self._captured_tasks = None
        self._captured_graph = None
        self._scheduler = scheduler
        day = int(obs["day"])
        self._current_day = day
        if scheduler is not None:
            scheduler.begin(obs)
        try:
            action = self.module.agent(obs)
            graph = self._captured_graph or TaskGraph.empty(day)
            if scheduler is not None:
                scheduler.finish(graph, self.module)
            return BaselineDecision(action, graph)
        except BaseException:
            if scheduler is not None:
                scheduler.abort()
            raise
        finally:
            self._scheduler = None
            self._current_day = None

    def act(self, obs) -> dict:
        return self.decide(obs).action
