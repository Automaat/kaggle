import math
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
        route_weights = os.environ.get("AGENT2_ROUTE_RL_WEIGHTS")
        if route_weights:
            module.ROUTE_RL_WEIGHTS = tuple(float(value) for value in route_weights.split(","))
        original = module._protected_underfoot_tasks
        original_features = module._route_rl_features

        def capture(tasks, units, inventories):
            result = original(tasks, units, inventories)
            self._captured_tasks = tuple(tasks)
            return result

        def features(candidate, tasks, taken, unit_index, targets):
            base = original_features(candidate, tasks, taken, unit_index, targets)
            return (*base, *self._scale_features(module, candidate, tasks, unit_index))

        def choose(player, step, candidates, tasks, taken, unit_index, targets):
            minimum = min(candidate[1] for candidate in candidates)
            eligible = [candidate for candidate in candidates if candidate[1] <= minimum + 2]
            rows = [features(candidate[:-1], tasks, taken, unit_index, targets) for candidate in eligible]
            scores = [
                sum(weight * value for weight, value in zip(module.ROUTE_RL_WEIGHTS, row))
                for row in rows
            ]
            if not module.ROUTE_RL_TRAIN:
                return eligible[max(range(len(scores)), key=scores.__getitem__)][0]
            state = module._route_rl_state(player, step)
            ceiling = max(scores)
            temperature = max(module.ROUTE_RL_TEMPERATURE, 1e-6)
            masses = [math.exp((score - ceiling) / temperature) for score in scores]
            total = sum(masses)
            probabilities = [mass / total for mass in masses]
            draw = module._route_rl_rng[player].random()
            selected = len(probabilities) - 1
            cumulative = 0.0
            for index, probability in enumerate(probabilities):
                cumulative += probability
                if draw <= cumulative:
                    selected = index
                    break
            expected = [
                sum(probability * row[index] for probability, row in zip(probabilities, rows))
                for index in range(len(module.ROUTE_RL_WEIGHTS))
            ]
            for index, value in enumerate(rows[selected]):
                state["gradient"][index] += value - expected[index]
            state["choices"] += 1
            state["entropy"] -= sum(
                probability * math.log(max(probability, 1e-12)) for probability in probabilities
            )
            return eligible[selected][0]

        module._protected_underfoot_tasks = capture
        module._route_rl_features = features
        module._route_rl_choice = choose
        if len(module.ROUTE_RL_WEIGHTS) == 19:
            module.ROUTE_RL_WEIGHTS = (*module.ROUTE_RL_WEIGHTS, *([0.0] * 7))
        self.module = module
        self._captured_tasks = None
        self._observation = None

    def _scale_features(self, module, candidate, tasks, unit_index):
        _task_index, priority, x, y, task_data, raw_distance, _distance, _zone = candidate
        operation, item = task_data
        obs = self._observation
        tile = obs["farms"][obs["player"]]["tiles"][y][x]
        market = obs["market"]["inventory"]
        value = self._task_value(module, operation, item, tile, market)
        urgent = 1.0 if priority == 0 else 0.0
        hour = float(obs["hour"]) / 23.0
        unlocked = sum(
            tile_value != "LOCKED"
            for row in obs["farms"][obs["player"]]["tiles"]
            for tile_value in row
        )
        urgent_share = sum(task[0] == 0 for task in tasks) / max(1, len(tasks))
        farm = obs["farms"][obs["player"]]
        unit_count = 1 + len(farm.get("hands", []))
        pressure = min(2.0, len(tasks) / unit_count)
        inventories = obs["private"].get("inventories", [])
        carried = inventories[unit_index] if unit_index < len(inventories) else {}
        compatible = self._compatible(operation, item, carried)
        return (
            math.log1p(max(0.0, value)) / 10.0,
            -float(priority),
            hour * (urgent + value / max(1.0, value + 500.0)),
            -float(raw_distance) * unlocked / 100.0,
            urgent_share * (urgent + value / max(1.0, value + 500.0)),
            pressure * (urgent + value / max(1.0, value + 500.0)),
            compatible,
        )

    @staticmethod
    def _task_value(module, operation, item, tile, market):
        if not isinstance(tile, dict):
            return 0.0
        if "animal" in tile:
            product = module.ANIMALS[tile["animal"]]["product"]
            price = module.market_price(product, market.get(product, module.MARKET_I0))
            if operation == "HARVEST":
                return tile.get("yield_units", 0) * price
            if operation in ("FEED", "FEED!", "CARE"):
                return price
        if tile.get("kind") == "PLANT":
            crop = tile["crop"]
            price = module.market_price(crop, market.get(crop, module.MARKET_I0))
            if operation == "HARVEST":
                return tile.get("yield_units", 0) * price
            if operation in ("WATER", "WATER!", "FERTILIZE"):
                return price
        if operation == "PLACE" and item in module.ANIMALS:
            product = module.ANIMALS[item]["product"]
            return module.market_price(product, market.get(product, module.MARKET_I0))
        return 0.0

    @staticmethod
    def _compatible(operation, item, carried):
        if operation in ("FEED", "FEED!"):
            return float(carried.get("WHEAT", 0) > 0)
        if operation == "FERTILIZE":
            return float(carried.get("FERTILIZER", 0) > 0)
        if operation == "PLACE":
            return float(carried.get(item, 0) > 0)
        return 0.0

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
