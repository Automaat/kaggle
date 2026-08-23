import copy
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
        original = module._protected_underfoot_tasks

        def capture(tasks, units, inventories):
            result = original(tasks, units, inventories)
            self._captured_tasks = tuple(tasks)
            return result

        module._protected_underfoot_tasks = capture
        self.module = module
        self._captured_tasks = None

    def decide(self, obs, strategy=None) -> BaselineDecision:
        if self.module is None:
            self.reset()
        self._captured_tasks = None
        if strategy is None:
            action = self.module.agent(obs)
        else:
            original = self.module._dynamic_plan
            targets = strategy.targets
            animals = frozenset(self.module.ANIMALS)

            def planned(
                tiles,
                day,
                inventory,
                shops,
                board_size=10,
                budget=None,
                seeds=None,
            ):
                plan = dict(
                    original(
                        tiles,
                        day,
                        inventory,
                        shops,
                        board_size,
                        budget,
                        seeds,
                    )
                )
                for x, y, crop in targets:
                    if plan.get((x, y)) not in animals:
                        plan[(x, y)] = crop
                return plan

            self.module._dynamic_plan = planned
            try:
                action = self.module.agent(obs)
            finally:
                self.module._dynamic_plan = original
        day = int(obs["day"])
        graph = TaskGraph.from_legacy(day, self._captured_tasks or ())
        return BaselineDecision(action, graph)

    def market_order_limit(self) -> int:
        if self.module is None:
            self.reset()
        return int(self.module.MAX_ORDERS)

    def remember_market(self, player: int, orders) -> None:
        if self.module is None:
            self.reset()
        state = self.module._opponent_state.get(player)
        previous = copy.deepcopy(state.get("orders")) if state is not None else None
        try:
            self.module._remember_market_orders(player, orders)
        except Exception:
            if state is not None:
                state["orders"] = previous
            raise

    def act(self, obs) -> dict:
        return self.decide(obs).action
