import math
import os
import types
from collections import Counter
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
        self._wheat_carriers = set()
        self._normal_max_hands = 12
        self._expansion_day = None
        self._normal_seed_batch = 1
        self._seed_expansion_day = None
        self._sale_seed_proceeds = 0
        self._dynamic_herd = []
        self._dynamic_herd_day = None
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
        module.SEED_BUY_STOP_HOUR = int(os.environ.get(
            "AGENT2_SEED_BUY_STOP_HOUR", str(module.SEED_BUY_STOP_HOUR)
        ))
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
        original_animal_tasks = module._animal_tasks
        original_sell_orders = module._sell_orders
        original_seed_orders = module._seed_orders
        original_fertilize_pays = module._fertilize_pays

        def capture(tasks, units, inventories):
            tasks[:] = [task for task in tasks if self._keep_task(module, task)]
            result = original(tasks, units, inventories)
            self._captured_tasks = tuple(tasks)
            self._wheat_carriers = {
                index for index, inventory in enumerate(inventories)
                if inventory.get("WHEAT", 0) > 0
            }
            self._select_specialists(tasks, units, inventories)
            return result

        def select(player, step, candidates, tasks, taken, unit_index, targets):
            def finish(choice):
                return self._production_feed_choice(module, candidates, choice)

            if (os.environ.get("AGENT2_CRISIS_FEED", "0") == "1"
                    and int(unit_index) in self._wheat_carriers):
                feed = [candidate for candidate in candidates if candidate[4][0] == "FEED!"]
                if feed:
                    return finish(original_selector(
                        player, step, feed, tasks, taken, unit_index, targets
                    ))
            bundle_mode = os.environ.get("AGENT2_TILE_BUNDLES", default_bundles)
            if bundle_mode != "0":
                position = self._bundle_positions.get((int(player), int(unit_index)))
                local = [candidate for candidate in candidates
                         if candidate[5] == 0
                         and (bundle_mode == "always" or position == (candidate[2], candidate[3]))]
                if local:
                    return finish(min(local, key=lambda candidate: (candidate[1], candidate[0]))[0])
            specialist_count = int(os.environ.get("AGENT2_ANIMAL_SPECIALISTS", "0"))
            if specialist_count > 0 and self._specialists:
                animal = [candidate for candidate in candidates if self._is_animal_task(candidate)]
                crops = [candidate for candidate in candidates if not self._is_animal_task(candidate)]
                feed = [candidate for candidate in animal if candidate[4][0] in {"FEED!", "FEED"}]
                if int(unit_index) in self._specialists and feed:
                    return finish(original_selector(
                        player, step, feed, tasks, taken, unit_index, targets
                    ))
                if (os.environ.get("AGENT2_RESERVE_ANIMAL_TASKS", "0") == "1"
                        and int(unit_index) not in self._specialists and crops):
                    return finish(original_selector(
                        player, step, crops, tasks, taken, unit_index, targets
                    ))
            return finish(original_selector(
                player, step, candidates, tasks, taken, unit_index, targets
            ))

        def plan(tiles, day, inventory, shops, board_size=10, budget=None, seeds=None):
            result = original_plan(tiles, day, inventory, shops, board_size, budget, seeds)
            plant_cap = self._plant_cap(day)
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
            final_crop = os.environ.get("AGENT2_FINAL_EXTRA_CROP", "")
            final_day = int(os.environ.get("AGENT2_PLANT_CAP_FINAL_DAY", "99"))
            if final_crop in module.CROPS and day >= final_day:
                base_cap = int(os.environ.get("AGENT2_FINAL_BASE_CAP", "48"))
                positions = [position for position, crop in result.items() if crop in module.CROPS]
                extra = max(0, standing_plants + len(positions) - base_cap)
                middle = board_size // 2
                ports = ((middle - 1, middle - 1), (middle, middle - 1),
                         (middle - 1, middle), (middle, middle))
                positions.sort(
                    key=lambda position: min(
                        abs(position[0] - port[0]) + abs(position[1] - port[1]) for port in ports
                    ),
                    reverse=True,
                )
                for position in positions[:extra]:
                    result[position] = final_crop
            if os.environ.get("AGENT2_SERVICE_FLOW_LAYOUT", "0") == "1":
                middle = board_size // 2
                ports = ((middle - 1, middle - 1), (middle, middle - 1),
                         (middle - 1, middle), (middle, middle))
                positions = [position for position, crop in result.items() if crop in module.CROPS]
                crops = [result[position] for position in positions]
                order = os.environ.get(
                    "AGENT2_SERVICE_FLOW_ORDER",
                    "CARROT,WHEAT,TOMATO,MELON,STRAWBERRY",
                ).split(",")
                rank = {crop: index for index, crop in enumerate(order)}
                positions.sort(key=lambda position: (
                    min(abs(position[0] - port[0]) + abs(position[1] - port[1]) for port in ports),
                    position,
                ))
                crops.sort(key=lambda crop: (rank.get(crop, len(rank)), crop))
                for position, crop in zip(positions, crops):
                    result[position] = crop
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

        def animal_tasks(tile, day):
            result = original_animal_tasks(tile, day)
            care_animals = set(filter(None, os.environ.get("AGENT2_CARE_ANIMALS", "").split(",")))
            if care_animals and tile["animal"] not in care_animals:
                result = [job for job in result if job[0] != "CARE"]
            if os.environ.get("AGENT2_PRECARE", "1") != "1":
                return result
            feed_pays = not module.FEED_DEADLINE or any(
                production >= day + 1 for production in module._animal_production_days(tile)
            )
            urgency = int(os.environ.get("AGENT2_PRECARE_URGENCY", "1"))
            precare_animals = set(filter(
                None, os.environ.get("AGENT2_PRECARE_ANIMALS", "").split(",")
            ))
            if (not tile["cared_today"] and day < module.LAST_DAY and feed_pays
                    and tile.get("consecutive_unfed", 0) >= urgency
                    and (not precare_animals or tile["animal"] in precare_animals)):
                care = ("CARE", None)
                if care not in result:
                    result.append(care)
            return result

        def sell_orders(*args, **kwargs):
            result = original_sell_orders(*args, **kwargs)
            inventory = args[1]
            self._sale_seed_proceeds = sum(
                module._sale_revenue(item, inventory.get(item, module.MARKET_I0), count)
                for operation, item, count in result if operation == "SELL"
            )
            return result

        def seed_orders(wanted, money, hour=0):
            if os.environ.get("AGENT2_SALE_FUNDED_SEEDS", "0") == "1":
                share = float(os.environ.get("AGENT2_SALE_SEED_SHARE", "1"))
                money += self._sale_seed_proceeds * share
            return original_seed_orders(wanted, money, hour)

        def fertilize_pays(tile, crop, age, day):
            if original_fertilize_pays(tile, crop, age, day):
                return True
            final_day = int(os.environ.get("AGENT2_PLANT_CAP_FINAL_DAY", "99"))
            crops = set(filter(
                None, os.environ.get("AGENT2_TERMINAL_FERTILIZE", "").split(",")
            ))
            return crop in crops and tile.get("planted_day", -1) >= final_day and age == 1

        module._protected_underfoot_tasks = capture
        module._route_rl_choice = select
        module._dynamic_plan = plan
        module._animal_tasks = animal_tasks
        module._sell_orders = sell_orders
        module._seed_orders = seed_orders
        module._fertilize_pays = fertilize_pays
        self.module = module
        self._captured_tasks = None
        self._observation = None
        self._bundle_positions = {}
        self._specialists = set()
        self._wheat_carriers = set()
        self._normal_max_hands = module.MAX_HANDS
        self._expansion_day = None
        self._normal_seed_batch = module.SEED_BUY_BATCH
        self._seed_expansion_day = None
        self._sale_seed_proceeds = 0
        self._dynamic_herd = []
        self._dynamic_herd_day = None

    def _keep_task(self, module, task):
        _priority, x, y, operation_data = task
        operation, _item = operation_data
        obs = self._observation
        if os.environ.get("AGENT2_TERMINAL_PRUNE", "0") == "1":
            day = obs["day"]
            if day >= module.LAST_DAY and operation in {
                    "WATER!", "WATER", "FERTILIZE", "CARE", "DIG"}:
                return False
            if day >= module.LAST_DAY - 1 and operation == "CARE":
                return False
            if operation == "DIG" and not any(
                    day + module.LIFESPAN[crop] <= module.LAST_DAY for crop in module.CROPS):
                return False
        if operation == "DIG" and os.environ.get("AGENT2_SKIP_CAP_WEEDS", "0") == "1":
            farm = obs["farms"][obs["player"]]
            standing = sum(
                isinstance(tile, dict) and tile.get("kind") == "PLANT"
                for row in farm["tiles"] for tile in row
            )
            return standing < self._plant_cap(obs["day"])
        if operation not in ("WATER", "FERTILIZE", "HARVEST"):
            return True
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
        self._sale_seed_proceeds = 0
        self._observation = obs
        try:
            self._set_expansion_settings(obs)
            self._set_dynamic_herd(obs)
            action = self.module.agent(obs)
            self._hold_animal_cash_for_land(obs, action)
            self._add_sale_funded_feed(obs, action)
            self._prioritize_hires(action)
            self._record_bundles(obs, action)
            day = int(obs["day"])
            graph = TaskGraph.from_legacy(day, self._captured_tasks or ())
            return BaselineDecision(action, graph)
        finally:
            self._observation = None

    def act(self, obs) -> dict:
        return self.decide(obs).action

    def _hold_animal_cash_for_land(self, obs, action):
        if os.environ.get("AGENT2_LAND_PRIORITY", "0") != "1":
            return
        orders = list(action.get("market", []))
        if any(order and order[0] == "BUY_LAND" for order in orders):
            return
        animal_orders = [order for order in orders if order and order[0] == "BUY_ANIMAL"]
        if not animal_orders:
            return
        farm = obs["farms"][obs["player"]]
        bought = len(farm.get("unlocked_quadrants", ["NW"])) - 1
        if bought >= min(self.module.MAX_QUADRANTS, len(self.module.LAND_PRICES)):
            return
        last_day = int(os.environ.get("AGENT2_LAND_PRIORITY_LAST_DAY", "12"))
        if int(obs["day"]) > last_day:
            return
        tiles = list(self.module._my_tiles(farm))
        empty = sum(tile is None for _x, _y, tile in tiles)
        quadrant = (len(farm["tiles"]) // 2) ** 2
        needed = self.module.LAND_PRICES[bought] + (empty + quadrant) * self.module.SEED_RESERVE
        shortfall = needed - farm["money"]
        gap = int(os.environ.get("AGENT2_LAND_PRIORITY_GAP", "600"))
        animal_spend = sum(
            self.module.ANIMALS[order[1]]["cost"] * order[2]
            for order in animal_orders
        )
        if 0 < shortfall <= gap and animal_spend >= shortfall:
            action["market"] = [
                order for order in orders if not order or order[0] != "BUY_ANIMAL"
            ]

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
        late_day = int(os.environ.get("AGENT2_LATE_HIRE_FIRST_DAY", "99"))
        if self._observation["day"] >= late_day:
            sells = [order for order in orders if order and order[0] == "SELL"]
            feed = [order for order in orders
                    if order and order[:2] == ["BUY_PRODUCT", "WHEAT"]]
            hires = [order for order in orders if order and order[0] == "HIRE"]
            others = [order for order in orders
                      if order and order[0] not in {"SELL", "HIRE"}
                      and order[:2] != ["BUY_PRODUCT", "WHEAT"]]
            orders = sells + feed + hires + others
        if os.environ.get("AGENT2_HIRE_FIRST", "0") == "1" and any(
                order and order[0] == "BUY_LAND" for order in orders):
            sells = [order for order in orders if order and order[0] == "SELL"]
            hires = [order for order in orders if order and order[0] == "HIRE"]
            others = [order for order in orders if order and order[0] not in {"SELL", "HIRE"}]
            orders = sells + hires + others
        action["market"] = orders[:10]
        self.module._remember_market_orders(self._observation["player"], action["market"])

    def _add_sale_funded_feed(self, obs, action):
        if os.environ.get("AGENT2_SALE_FUNDED_FEED", "1") != "1" or obs["day"] >= self.module.LAST_DAY:
            return
        farm = obs["farms"][obs["player"]]
        animals = [tile for row in farm["tiles"] for tile in row
                   if isinstance(tile, dict) and "animal" in tile]
        urgent = sum(tile.get("consecutive_unfed", 0) >= 1 for tile in animals)
        urgency = int(os.environ.get("AGENT2_SALE_FEED_URGENCY", "1"))
        orders = list(action.get("market", []))
        sells = [order for order in orders if order and order[0] == "SELL"]
        if urgent < urgency or not sells:
            return
        inventories = obs["private"].get("inventories", [])
        stock = obs["private"].get("shed", {}).get("WHEAT", 0)
        stock += sum(inventory.get("WHEAT", 0) for inventory in inventories)
        if os.environ.get("AGENT2_FEED_ACTION_LEDGER", "0") == "1":
            operations = [action.get("farmer"), *action.get("hands", [])]
            stock -= sum(operation and operation[0] == "FEED" for operation in operations)
        existing = sum(order[2] for order in orders
                       if order and order[:2] == ["BUY_PRODUCT", "WHEAT"])
        feed_days = int(os.environ.get("AGENT2_SALE_FEED_DAYS", str(self.module.FEED_DAYS)))
        short = len(animals) * feed_days - stock - existing
        if short <= 0:
            return
        prices = obs["market"]["prices"]
        proceeds = sum(order[2] * prices.get(order[1], 0) for order in sells)
        wheat_price = max(1, prices.get("WHEAT", 25))
        affordable = int((farm["money"] + proceeds * 0.5) // wheat_price)
        take = min(short, affordable)
        if take <= 0:
            return
        non_sells = [order for order in orders if not order or order[0] != "SELL"]
        action["market"] = sells + [["BUY_PRODUCT", "WHEAT", take]] + non_sells

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

    def _production_feed_choice(self, module, candidates, chosen):
        if os.environ.get("AGENT2_PRODUCTION_FEED", "0") != "1":
            return chosen
        selected = next((candidate for candidate in candidates if candidate[0] == chosen), None)
        if selected is None or selected[4][0] not in {"FEED", "FEED!"}:
            return chosen
        obs = self._observation
        farm = obs["farms"][obs["player"]]
        eligible = [
            candidate for candidate in candidates
            if candidate[4][0] in {"FEED", "FEED!"}
            and candidate[1] == selected[1]
            and candidate[-1] == selected[-1]
        ]

        def value(candidate):
            tile = farm["tiles"][candidate[3]][candidate[2]]
            produces = obs["day"] + 1 in module._animal_production_days(tile)
            product = module.ANIMALS[tile["animal"]]["product"]
            price = obs["market"]["prices"].get(product, 0)
            return produces, tile.get("pending_care_bonus", 0) * price, price, -candidate[6]

        best = max(eligible, key=value, default=selected)
        return best[0] if value(best) > value(selected) else chosen

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

    @staticmethod
    def _plant_cap(day):
        plant_cap = int(os.environ.get("AGENT2_PLANT_CAP", "42"))
        ramp = int(os.environ.get("AGENT2_PLANT_CAP_RAMP", "0"))
        if ramp > 0:
            start = int(os.environ.get("AGENT2_PLANT_CAP_RAMP_DAY", "18"))
            target = int(os.environ.get("AGENT2_PLANT_CAP_RAMP_TARGET", "63"))
            growth = max(0, day - start + 1) * ramp
            return min(target, plant_cap + growth)
        release_day = int(os.environ.get("AGENT2_PLANT_CAP_RELEASE_DAY", "18"))
        if plant_cap > 0 and day >= release_day:
            plant_cap = int(os.environ.get("AGENT2_PLANT_CAP_RELEASE", "48"))
        final_day = int(os.environ.get("AGENT2_PLANT_CAP_FINAL_DAY", "99"))
        if plant_cap > 0 and day >= final_day:
            plant_cap = int(os.environ.get("AGENT2_PLANT_CAP_FINAL", "0"))
        return plant_cap

    def _set_dynamic_herd(self, obs):
        if os.environ.get("AGENT2_DYNAMIC_HERD", "0") != "1":
            return
        module = self.module
        module._market_params = obs["market"].get("params")
        day = int(obs["day"])
        release_cap = int(os.environ.get("AGENT2_PLANT_CAP_RELEASE", "48"))
        work_budget = float(os.environ.get("AGENT2_DAILY_WORK_BUDGET", "84"))
        work_per_animal = float(os.environ.get("AGENT2_ANIMAL_WORK", "3"))
        work_limit = max(0, int((work_budget - release_cap) // work_per_animal))
        herd_limit = min(int(os.environ.get("AGENT2_DYNAMIC_HERD_MAX", "18")), work_limit)
        self._dynamic_herd = self._owned_animals(module, obs)
        additions = int(os.environ.get("AGENT2_DYNAMIC_HERD_ADD_PER_DAY", "99"))
        minimum = min(int(os.environ.get("AGENT2_DYNAMIC_HERD_MIN", "12")), herd_limit)
        threshold = float(os.environ.get("AGENT2_ANIMAL_MARGIN", "0"))
        allowed = [
            animal for animal in os.environ.get(
                "AGENT2_DYNAMIC_ANIMALS", "COW,SHEEP,GOOSE"
            ).split(",")
            if animal in module.ANIMALS
        ]
        for _index in range(additions):
            if len(self._dynamic_herd) >= herd_limit or not allowed:
                break
            counts = Counter(self._dynamic_herd)
            margins = {
                animal: self._animal_margin(module, obs, animal, counts)
                for animal in allowed
            }
            goose_threshold = float(os.environ.get("AGENT2_GOOSE_MARGIN", "0"))
            viable = [
                animal for animal in allowed
                if math.isfinite(margins[animal])
                and (animal != "GOOSE" or margins[animal] > goose_threshold)
            ]
            if not viable:
                break
            animal = max(viable, key=margins.get)
            if len(self._dynamic_herd) >= minimum and margins[animal] <= threshold:
                break
            self._dynamic_herd.append(animal)
        self._dynamic_herd_day = day
        counts = Counter(self._dynamic_herd)
        module.HERD_SPEC = ",".join(
            f"{animal}:{counts[animal]}" for animal in module.ANIMALS if counts[animal]
        )

    @staticmethod
    def _owned_animals(module, obs):
        farm = obs["farms"][obs["player"]]
        owned = [
            tile["animal"]
            for row in farm["tiles"]
            for tile in row
            if isinstance(tile, dict) and tile.get("animal") in module.ANIMALS
        ]
        private = obs["private"]
        stores = [private["shed"], *private["inventories"]]
        for store in stores:
            for animal in module.ANIMALS:
                owned.extend([animal] * int(store.get(animal, 0)))
        return owned

    def _animal_margin(self, module, obs, animal, counts):
        day = int(obs["day"])
        placed_day = day + int(os.environ.get("AGENT2_ANIMAL_PLACEMENT_DAYS", "2"))
        data = module.ANIMALS[animal]
        productions = list(range(
            placed_day + data["first_yield_day"],
            module.LAST_DAY + 1,
            data["interval"],
        ))
        if not productions:
            return float("-inf")
        last_production = productions[-1]
        service_days = max(0, last_production - placed_day)
        units = len(productions) + service_days
        horizon = max(1, last_production - day)
        market = obs["market"]
        shops = obs["town"]["unlocked_shops"]
        projected = module._projected_inventory(market["inventory"], shops, day, horizon)
        product = data["product"]
        realization = float(os.environ.get(
            f"AGENT2_{animal}_REALIZATION", {"GOOSE": "0.35", "COW": "0.8", "SHEEP": "0.65"}[animal]
        ))
        units = max(1, round(units * realization))
        product_inventory = projected.get(product, module.MARKET_I0) + counts[animal] * units
        product_revenue = module._sale_revenue(product, product_inventory, units)
        herd_size = sum(counts.values())
        fertilizer_inventory = projected.get("FERTILIZER", module.MARKET_I0)
        fertilizer_units = max(1, round(
            service_days * float(os.environ.get("AGENT2_FERTILIZER_REALIZATION", "0.4"))
        ))
        fertilizer_inventory += herd_size * fertilizer_units
        fertilizer_revenue = module._sale_revenue(
            "FERTILIZER", fertilizer_inventory, fertilizer_units,
        )
        wheat_price = max(1, market["prices"].get("WHEAT", 25))
        feed_cost = wheat_price * service_days
        primitive_work = service_days * 3 + len(productions) + 3
        work_price = float(os.environ.get("AGENT2_ANIMAL_WORK_PRICE", "15"))
        return (
            product_revenue
            + fertilizer_revenue
            - data["cost"]
            - feed_cost
            - primitive_work * work_price
        )

    def _set_expansion_settings(self, obs):
        surge_hands = int(os.environ.get("AGENT2_EXPANSION_HANDS", "0"))
        self.module.MAX_HANDS = self._normal_max_hands
        farm = obs["farms"][obs["player"]]
        empty_tiles = sum(tile is None for row in farm["tiles"] for tile in row)
        if surge_hands > self._normal_max_hands and self.module._land_orders(
                farm, obs["day"], empty_tiles):
            self._expansion_day = obs["day"]
        duration = int(os.environ.get("AGENT2_EXPANSION_HAND_DAYS", "2"))
        if self._expansion_day is not None and 0 <= obs["day"] - self._expansion_day < duration:
            self.module.MAX_HANDS = surge_hands
        self.module.SEED_BUY_BATCH = self._normal_seed_batch
        seed_batch = int(os.environ.get("AGENT2_EXPANSION_SEED_BATCH", "0"))
        if seed_batch <= self._normal_seed_batch:
            return
        if len(farm.get("unlocked_quadrants", ["NW"])) >= 3 and self._seed_expansion_day is None:
            self._seed_expansion_day = obs["day"]
        seed_days = int(os.environ.get("AGENT2_EXPANSION_SEED_DAYS", "2"))
        if (self._seed_expansion_day is not None
                and 0 <= obs["day"] - self._seed_expansion_day < seed_days):
            self.module.SEED_BUY_BATCH = seed_batch
