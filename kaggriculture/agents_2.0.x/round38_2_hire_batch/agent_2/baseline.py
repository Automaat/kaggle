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
        self._bundle_positions = {}
        self._specialists = set()
        self.reset()

    def reset(self) -> None:
        source = self.path.read_text()
        module = types.ModuleType(f"_agent_2_baseline_{id(self)}_{id(source)}")
        module.__file__ = str(self.path)
        exec(compile(source, str(self.path), "exec"), module.__dict__)
        module.MAX_QUADRANTS = int(os.environ.get("AGENT2_LAND", "2"))
        module.MAX_HANDS = int(os.environ.get("AGENT2_MAX_HANDS", "12"))
        module.HANDS_PER_TILE = float(os.environ.get("AGENT2_HANDS_PER_TILE", "0.2"))
        module.HIRES_PER_TURN = int(os.environ.get("AGENT2_HIRE_BATCH", "10"))
        default_radius = "1"
        default_bundles = "1"
        module.TRIP_RADIUS = int(os.environ.get("AGENT2_TRIP_RADIUS", default_radius))
        module.ZONE_PENALTY = int(os.environ.get("AGENT2_ZONE_PENALTY", str(module.ZONE_PENALTY)))
        module.FEEDER_UNITS = int(os.environ.get("AGENT2_FEEDER_UNITS", str(module.FEEDER_UNITS)))
        module.CARE_BEFORE_WATER = os.environ.get("AGENT2_CARE_BEFORE_WATER", "0") == "1"
        module.HERD_SPEC = os.environ.get("AGENT2_HERD_SPEC", module.HERD_SPEC)
        if "AGENT2_ROUTE_RL" in os.environ:
            module.ROUTE_RL = os.environ["AGENT2_ROUTE_RL"] == "1"
        if os.environ.get("AGENT2_HIRE_FIRST", "0") == "1":
            module.MAX_ORDERS = 100
        weights = list(module.ROUTE_RL_WEIGHTS)
        weights[2] = float(os.environ.get("AGENT2_DISTANCE_WEIGHT", str(weights[2])))
        weights[3] = float(os.environ.get("AGENT2_CONTINUATION_WEIGHT", str(weights[3])))
        weights[4] = float(os.environ.get("AGENT2_DENSITY_WEIGHT", str(weights[4])))
        module.ROUTE_RL_WEIGHTS = tuple(weights)
        original = module._protected_underfoot_tasks
        original_selector = module._route_rl_choice
        original_plan = module._dynamic_plan

        def capture(tasks, units, inventories):
            tasks[:] = [task for task in tasks if self._keep_task(module, task)]
            result = original(tasks, units, inventories)
            self._captured_tasks = tuple(tasks)
            self._select_specialists(tasks, units, inventories)
            return result

        def select(player, step, candidates, tasks, taken, unit_index, targets):
            bundle_mode = os.environ.get("AGENT2_TILE_BUNDLES", default_bundles)
            if bundle_mode != "0":
                position = self._bundle_positions.get((int(player), int(unit_index)))
                local = [candidate for candidate in candidates
                         if candidate[5] == 0
                         and (bundle_mode == "always" or position == (candidate[2], candidate[3]))]
                if local:
                    return min(local, key=lambda candidate: (candidate[1], candidate[0]))[0]
            specialist_count = int(os.environ.get("AGENT2_ANIMAL_SPECIALISTS", "0"))
            if specialist_count > 0 and self._specialists:
                animal = [candidate for candidate in candidates if self._is_animal_task(candidate)]
                crops = [candidate for candidate in candidates if not self._is_animal_task(candidate)]
                feed = [candidate for candidate in animal if candidate[4][0] in {"FEED!", "FEED"}]
                if int(unit_index) in self._specialists and feed:
                    return original_selector(player, step, feed, tasks, taken, unit_index, targets)
                if (os.environ.get("AGENT2_RESERVE_ANIMAL_TASKS", "0") == "1"
                        and int(unit_index) not in self._specialists and crops):
                    return original_selector(player, step, crops, tasks, taken, unit_index, targets)
            return original_selector(player, step, candidates, tasks, taken, unit_index, targets)

        def plan(tiles, day, inventory, shops, board_size=10, budget=None, seeds=None):
            result = original_plan(tiles, day, inventory, shops, board_size, budget, seeds)
            plant_cap = int(os.environ.get("AGENT2_PLANT_CAP", "0"))
            standing_plants = sum(
                isinstance(tile, dict) and tile.get("kind") == "PLANT"
                for _x, _y, tile in tiles
            )
            planned_crops = [position for position, crop in result.items() if crop in module.CROPS]
            plant_excess = max(0, standing_plants + len(planned_crops) - plant_cap) if plant_cap > 0 else 0
            if plant_excess:
                middle = board_size // 2
                ports = ((middle - 1, middle - 1), (middle, middle - 1),
                         (middle - 1, middle), (middle, middle))
                planned_crops.sort(
                    key=lambda position: min(
                        abs(position[0] - port[0]) + abs(position[1] - port[1]) for port in ports
                    ),
                    reverse=True,
                )
                for position in planned_crops[:plant_excess]:
                    result[position] = None
            cap = int(os.environ.get("AGENT2_STRAWBERRY_CAP", "0"))
            if cap <= 0:
                return result
            standing = sum(
                isinstance(tile, dict)
                and tile.get("kind") == "PLANT"
                and tile.get("crop") == "STRAWBERRY"
                for _x, _y, tile in tiles
            )
            planned = [position for position, crop in result.items() if crop == "STRAWBERRY"]
            excess = max(0, standing + len(planned) - cap)
            if excess == 0:
                return result
            ready = [crop for crop in module.CROPS
                     if crop != "STRAWBERRY" and day + module.LIFESPAN[crop] <= module.LAST_DAY]
            if not ready:
                return result
            projected = {
                crop: module._projected_inventory(inventory, shops, day, module.LIFESPAN[crop]).get(
                    crop, module.MARKET_I0
                )
                for crop in ready
            }
            for crop in result.values():
                if crop in projected:
                    projected[crop] += module._effective_yield(crop)
            middle = board_size // 2
            ports = ((middle - 1, middle - 1), (middle, middle - 1),
                     (middle - 1, middle), (middle, middle))
            planned.sort(
                key=lambda position: min(
                    abs(position[0] - port[0]) + abs(position[1] - port[1]) for port in ports
                ),
                reverse=True,
            )
            for position in planned[:excess]:
                crop = max(ready, key=lambda item: module._crop_value(item, projected, day))
                result[position] = crop
                projected[crop] += module._effective_yield(crop)
            return result

        module._protected_underfoot_tasks = capture
        module._route_rl_choice = select
        module._dynamic_plan = plan
        self.module = module
        self._captured_tasks = None
        self._observation = None
        self._bundle_positions = {}
        self._specialists = set()

    def _keep_task(self, module, task):
        _priority, x, y, operation_data = task
        operation, _item = operation_data
        if operation not in ("WATER", "FERTILIZE", "HARVEST"):
            return True
        obs = self._observation
        player = obs["player"]
        farm = obs["farms"][player]
        tile = farm["tiles"][y][x]
        if not isinstance(tile, dict) or tile.get("kind") != "PLANT":
            return True
        crop = tile.get("crop")
        if crop not in ("STRAWBERRY", "TOMATO") or not self._is_outer(farm, x, y):
            return True
        if operation == "FERTILIZE":
            return os.environ.get("AGENT2_OUTER_NO_FERTILIZER", "0") != "1"
        if operation == "WATER":
            return os.environ.get("AGENT2_OUTER_SURVIVAL_WATER", "0") != "1"
        if os.environ.get("AGENT2_OUTER_BATCH_HARVEST", "0") != "1":
            return True
        data = module.CROPS[crop]
        age = obs["day"] - tile["planted_day"]
        future = any(production > age for production in module.PRODUCTION_AGES[crop])
        return obs["day"] >= module.LAST_DAY or not future or tile.get("yield_units", 0) >= data["max_yield"]

    @staticmethod
    def _is_outer(farm, x, y):
        size = len(farm["tiles"])
        high = size // 2
        low = high - 1
        distance = min(abs(x - port_x) + abs(y - port_y)
                       for port_x in (low, high) for port_y in (low, high))
        threshold = int(os.environ.get("AGENT2_OUTER_DISTANCE", "3"))
        return distance >= threshold

    def decide(self, obs) -> BaselineDecision:
        if self.module is None:
            self.reset()
        self._captured_tasks = None
        self._observation = obs
        try:
            action = self.module.agent(obs)
            self._prioritize_hires(action)
            self._record_bundles(obs, action)
            day = int(obs["day"])
            graph = TaskGraph.from_legacy(day, self._captured_tasks or ())
            return BaselineDecision(action, graph)
        finally:
            self._observation = None

    def act(self, obs) -> dict:
        return self.decide(obs).action

    def _record_bundles(self, obs, action):
        player = int(obs["player"])
        farm = obs["farms"][player]
        units = [farm["farmer"], *farm.get("hands", [])]
        operations = [action.get("farmer"), *action.get("hands", [])]
        inactive = {"NORTH", "SOUTH", "EAST", "WEST", "PASS", "PICKUP", "DROP"}
        for unit_index, (position, operation) in enumerate(zip(units, operations)):
            key = (player, unit_index)
            if operation and operation[0] not in inactive:
                self._bundle_positions[key] = tuple(position)
            else:
                self._bundle_positions.pop(key, None)

    def _prioritize_hires(self, action):
        orders = list(action.get("market", []))
        if os.environ.get("AGENT2_HIRE_FIRST", "0") == "1" and any(
                order and order[0] == "BUY_LAND" for order in orders):
            sells = [order for order in orders if order and order[0] == "SELL"]
            hires = [order for order in orders if order and order[0] == "HIRE"]
            others = [order for order in orders if order and order[0] not in {"SELL", "HIRE"}]
            orders = sells + hires + others
        action["market"] = orders[:10]
        self.module._remember_market_orders(self._observation["player"], action["market"])

    def _select_specialists(self, tasks, units, inventories):
        count = int(os.environ.get("AGENT2_ANIMAL_SPECIALISTS", "0"))
        targets = [(x, y) for task in tasks if self._is_animal_task(task) for x, y in [(task[1], task[2])]]
        if count <= 0 or not targets:
            self._specialists = set()
            return
        ranked = sorted(
            range(len(units)),
            key=lambda index: (
                0 if index < len(inventories) and inventories[index].get("WHEAT", 0) > 0 else 1,
                min(abs(units[index][0] - x) + abs(units[index][1] - y) for x, y in targets),
                index,
            ),
        )
        self._specialists = set(ranked[:count])

    def _is_animal_task(self, task):
        if len(task) > 4:
            x, y, operation_data = task[2], task[3], task[4]
        else:
            _priority, x, y, operation_data = task
        operation = operation_data[0]
        if operation in {"FEED!", "FEED", "CARE", "COLLECT_FERTILIZER", "PLACE", "BUILD"}:
            return True
        if operation != "HARVEST":
            return False
        obs = self._observation
        tile = obs["farms"][obs["player"]]["tiles"][y][x]
        return isinstance(tile, dict) and "animal" in tile
