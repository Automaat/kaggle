import hashlib
import json
import math
import time
from dataclasses import dataclass, fields, is_dataclass

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_array

from .animal_ledger import ANIMAL_SPECS
from .market_ledger import ANIMAL_COSTS, DEFAULT_MARKET_PARAMS, PRODUCTS, market_price


LAST_DAY = 29
LAST_REFRESH_DAY = 28
DEFAULT_TERMINAL_STEP = 718
ANIMALS = tuple(ANIMAL_SPECS)
STRUCTURES = ("COOP", "PASTURE")
GOODS = ("WHEAT", "EGG", "MILK", "WOOL", "FERTILIZER")
SALE_PRODUCTS = GOODS
SCENARIOS = frozenset({"no-future-opponent-orders-v1"})


@dataclass(frozen=True, slots=True)
class ExistingAnimal:
    identifier: str
    position: tuple[int, int]
    animal: str
    placed_day: int
    yield_units: int
    consecutive_unfed: int
    fed_today: bool
    cared_today: bool
    fertilizer_available: bool
    pending_care_bonus: int

    def __post_init__(self):
        if type(self.identifier) is not str or not self.identifier:
            raise ValueError("animal identifier must be nonempty")
        if (
            type(self.position) is not tuple
            or len(self.position) != 2
            or any(type(value) is not int for value in self.position)
        ):
            raise TypeError("animal position must be an integer pair")
        if any(value < 0 or value >= 10 for value in self.position):
            raise ValueError("animal position must be on board")
        if type(self.animal) is not str or self.animal not in ANIMAL_SPECS:
            raise ValueError("unknown animal")
        counters = (
            self.placed_day,
            self.yield_units,
            self.consecutive_unfed,
            self.pending_care_bonus,
        )
        if any(type(value) is not int for value in counters):
            raise TypeError("animal counters must be integers")
        if self.placed_day < 0 or self.yield_units < 0 or self.pending_care_bonus < 0:
            raise ValueError("animal counters must be nonnegative")
        if self.yield_units > ANIMAL_SPECS[self.animal].max_held:
            raise ValueError("animal held yield exceeds its cap")
        if self.consecutive_unfed not in (0, 1):
            raise ValueError("placed animal must be alive")
        if self.pending_care_bonus > ANIMAL_SPECS[self.animal].first_yield_day:
            raise ValueError("pending care exceeds planning bound")
        flags = (self.fed_today, self.cared_today, self.fertilizer_available)
        if any(type(value) is not bool for value in flags):
            raise TypeError("animal flags must be booleans")


@dataclass(frozen=True, slots=True)
class AnimalTerminalValues:
    active_animals: tuple[float, ...]
    goods: tuple[float, ...]
    shed_animals: tuple[float, ...]
    empty_structures: tuple[float, ...]

    def __post_init__(self):
        for name, values, length in (
            ("active animal terminal values", self.active_animals, len(ANIMALS)),
            ("goods terminal values", self.goods, len(GOODS)),
            ("shed animal terminal values", self.shed_animals, len(ANIMALS)),
            ("structure terminal values", self.empty_structures, len(STRUCTURES)),
        ):
            _validate_float_vector(values, length, name)
            if any(value < 0 for value in values):
                raise ValueError(f"{name} must be nonnegative")


@dataclass(frozen=True, slots=True)
class AnimalOracleInput:
    source_step: int
    terminal_step: int
    cash: float
    cash_reserve: float
    goods: tuple[int, ...]
    shed_animals: tuple[int, ...]
    existing_animals: tuple[ExistingAnimal, ...]
    empty_structures: tuple[int, ...]
    animal_tile_capacity: tuple[int, ...]
    action_capacity: tuple[int, ...]
    shed_capacity: tuple[int, ...]
    fixed_shed_occupancy: tuple[int, ...]
    market_order_slots: tuple[int, ...]
    fixed_cash_flow: tuple[float, ...]
    base_inventory: tuple[tuple[int, ...], ...]
    wheat_buy_unit_limit: int
    placement_travel_actions: int
    feed_actions_per_unit: int
    return_actions: int
    sale_unit_limit: int
    max_new_animals: int
    allowed_animals: tuple[str, ...]
    fixed_slot_animals: tuple[str, ...]
    scenario: str
    min_new_animals: int = 0
    terminal_values: AnimalTerminalValues | None = None

    def __post_init__(self):
        integer_scalars = (
            self.source_step,
            self.terminal_step,
            self.placement_travel_actions,
            self.feed_actions_per_unit,
            self.return_actions,
            self.sale_unit_limit,
            self.wheat_buy_unit_limit,
            self.max_new_animals,
            self.min_new_animals,
        )
        if any(type(value) is not int for value in integer_scalars):
            raise TypeError("oracle integer settings must be integers")
        if self.source_step < 0 or self.source_step > DEFAULT_TERMINAL_STEP:
            raise ValueError("source step must be in 0..718")
        if self.terminal_step < self.source_step or self.terminal_step > 718:
            raise ValueError("terminal step must be in source_step..718")
        limits = (
            self.placement_travel_actions,
            self.return_actions,
            self.max_new_animals,
            self.min_new_animals,
        )
        if (
            any(value < 0 for value in limits)
            or self.sale_unit_limit < 1
            or self.wheat_buy_unit_limit < 1
        ):
            raise ValueError("oracle limits must be nonnegative")
        if self.min_new_animals > self.max_new_animals:
            raise ValueError("minimum animals exceed new animal slots")
        if self.feed_actions_per_unit < 1:
            raise ValueError("feed actions must be positive")
        if type(self.cash) not in (int, float) or isinstance(self.cash, bool):
            raise TypeError("cash must be numeric")
        if type(self.cash_reserve) not in (int, float) or isinstance(
            self.cash_reserve,
            bool,
        ):
            raise TypeError("cash reserve must be numeric")
        if not math.isfinite(self.cash) or not math.isfinite(self.cash_reserve):
            raise ValueError("cash values must be finite")
        if self.cash < 0 or self.cash_reserve < 0 or self.cash_reserve > self.cash:
            raise ValueError("cash reserve must fit current cash")
        if self.scenario not in SCENARIOS:
            raise ValueError("unknown market scenario")
        if self.terminal_values is not None and not isinstance(
            self.terminal_values,
            AnimalTerminalValues,
        ):
            raise TypeError("terminal values must be AnimalTerminalValues")
        _validate_int_vector(self.goods, len(GOODS), "goods")
        _validate_int_vector(self.shed_animals, len(ANIMALS), "shed animals")
        _validate_int_vector(self.empty_structures, len(STRUCTURES), "structures")
        if type(self.existing_animals) is not tuple:
            raise TypeError("existing animals must be a tuple")
        if any(not isinstance(value, ExistingAnimal) for value in self.existing_animals):
            raise TypeError("invalid existing animal")
        if len({value.identifier for value in self.existing_animals}) != len(
            self.existing_animals
        ):
            raise ValueError("existing animal identifiers must be unique")
        if len({value.position for value in self.existing_animals}) != len(
            self.existing_animals
        ):
            raise ValueError("existing animal positions must be unique")
        if any(value.placed_day > self.current_day for value in self.existing_animals):
            raise ValueError("existing animal cannot be from the future")
        if type(self.allowed_animals) is not tuple:
            raise TypeError("allowed animals must be a tuple")
        if len(set(self.allowed_animals)) != len(self.allowed_animals):
            raise ValueError("allowed animals must be unique")
        if any(value not in ANIMALS for value in self.allowed_animals):
            raise ValueError("unknown allowed animal")
        if type(self.fixed_slot_animals) is not tuple:
            raise TypeError("fixed slot animals must be a tuple")
        if self.fixed_slot_animals and len(self.fixed_slot_animals) != self.max_new_animals:
            raise ValueError("fixed slot animals must match new animal slots")
        if any(value not in self.allowed_animals for value in self.fixed_slot_animals):
            raise ValueError("fixed slot animal is not allowed")
        horizon = self.horizon_days
        for name, values in (
            ("animal tile capacity", self.animal_tile_capacity),
            ("action capacity", self.action_capacity),
            ("shed capacity", self.shed_capacity),
            ("fixed shed occupancy", self.fixed_shed_occupancy),
            ("market order slots", self.market_order_slots),
        ):
            _validate_int_vector(values, horizon, name)
        _validate_float_vector(self.fixed_cash_flow, horizon, "fixed cash flow")
        if type(self.base_inventory) is not tuple or len(self.base_inventory) != horizon:
            raise TypeError("base inventory must cover the horizon")
        for values in self.base_inventory:
            _validate_int_vector(values, len(SALE_PRODUCTS), "base inventory row")
        initial_tiles = len(self.existing_animals) + sum(self.empty_structures)
        if initial_tiles > self.animal_tile_capacity[0]:
            raise ValueError("existing animal tiles exceed capacity")
        initial_shed = sum(self.goods) + sum(self.shed_animals)
        if initial_shed + self.fixed_shed_occupancy[0] > self.shed_capacity[0]:
            raise ValueError("current shed inventory exceeds capacity")

    @property
    def current_day(self):
        return self.source_step // 24

    @property
    def horizon_days(self):
        return self.last_day - self.current_day + 1

    @property
    def last_day(self):
        return self.terminal_step // 24


@dataclass(frozen=True, slots=True)
class AnimalDecision:
    identifier: str
    animal: str
    existing: bool
    placement_day: int


@dataclass(frozen=True, slots=True)
class StructureDecision:
    structure: str
    day: int
    quantity: int


@dataclass(frozen=True, slots=True)
class AnimalPurchase:
    item: str
    day: int
    quantity: int
    cost: float


@dataclass(frozen=True, slots=True)
class AnimalSale:
    item: str
    day: int
    quantity: int
    revenue: float


@dataclass(frozen=True, slots=True)
class AnimalService:
    identifier: str
    animal: str
    day: int
    active: bool
    feed_action: bool
    fed: bool
    care_action: bool
    cared: bool
    care_banked: int
    production: int
    overflow: int
    harvest_action: bool
    harvest_deferred: bool
    harvested: int
    held_end: int
    pending_care_end: int
    fertilizer_collected: int
    fertilizer_deferred: bool


@dataclass(frozen=True, slots=True)
class AnimalDayBalance:
    day: int
    cash: float
    goods: tuple[int, ...]
    shed_animals: tuple[int, ...]
    empty_structures: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class AnimalOracleResult:
    success: bool
    status: int
    message: str
    mip_gap: float | None
    wall_seconds: float
    variable_count: int
    constraint_count: int
    terminal_cash: float | None
    incremental_animal_profit: float | None
    animals: tuple[AnimalDecision, ...]
    structures: tuple[StructureDecision, ...]
    purchases: tuple[AnimalPurchase, ...]
    sales: tuple[AnimalSale, ...]
    services: tuple[AnimalService, ...]
    balances: tuple[AnimalDayBalance, ...]
    terminal_goods: tuple[int, ...] | None
    terminal_shed_animals: tuple[int, ...] | None
    scenario: str
    input_sha256: str
    terminal_value: float | None = None
    forecast_terminal_cash: float | None = None


def _validate_int_vector(values, length, name):
    if type(values) is not tuple or len(values) != length:
        raise TypeError(f"{name} must contain {length} values")
    if any(type(value) is not int for value in values):
        raise TypeError(f"{name} values must be integers")
    if any(value < 0 for value in values):
        raise ValueError(f"{name} values must be nonnegative")


def _validate_float_vector(values, length, name):
    if type(values) is not tuple or len(values) != length:
        raise TypeError(f"{name} must contain {length} values")
    for value in values:
        if type(value) not in (int, float) or isinstance(value, bool):
            raise TypeError(f"{name} values must be numeric")
        if not math.isfinite(value):
            raise ValueError(f"{name} values must be finite")


def _typed(value):
    if is_dataclass(value):
        return {field.name: _typed(getattr(value, field.name)) for field in fields(value)}
    if type(value) is tuple:
        return [_typed(item) for item in value]
    return value


def input_sha256(data):
    payload = _typed(data)
    if data.terminal_values is None:
        payload.pop("terminal_values")
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _production_due(animal, placed_day, refresh_day):
    spec = ANIMAL_SPECS[animal]
    age = refresh_day + 1 - placed_day - spec.first_yield_day
    return age >= 0 and age % spec.interval == 0


def _within_day_reachable(data, day, delay, actions):
    first_step = max(data.source_step, day * 24)
    final_step = first_step + delay + actions - 1
    return final_step <= min(data.terminal_step, day * 24 + 23)


def _day_end_reachable(data, day):
    return day * 24 + 23 <= data.terminal_step


def _refresh_reachable(data, day):
    return day <= LAST_REFRESH_DAY and _day_end_reachable(data, day)


def _same_day_animal_arrival_reachable(data, day):
    return _within_day_reachable(
        data,
        day,
        1,
        2 + data.placement_travel_actions,
    )


def _same_day_wheat_arrival_reachable(data, day):
    return _within_day_reachable(data, day, 1, data.feed_actions_per_unit)


class _Builder:
    def __init__(self):
        self.keys = []
        self.index = {}
        self.objective = []
        self.lower = []
        self.upper = []
        self.integrality = []
        self.rows = []
        self.row_lower = []
        self.row_upper = []

    def variable(self, key, objective=0.0, lower=0.0, upper=np.inf, integral=True):
        if key in self.index:
            raise ValueError("duplicate variable")
        index = len(self.keys)
        self.keys.append(key)
        self.index[key] = index
        self.objective.append(float(objective))
        self.lower.append(float(lower))
        self.upper.append(float(upper))
        self.integrality.append(1 if integral else 0)
        return index

    def constraint(self, values, lower=-np.inf, upper=np.inf):
        self.rows.append(dict(values))
        self.row_lower.append(float(lower))
        self.row_upper.append(float(upper))

    def arrays(self):
        rows = []
        columns = []
        values = []
        for row, coefficients in enumerate(self.rows):
            for column, value in coefficients.items():
                if value:
                    rows.append(row)
                    columns.append(column)
                    values.append(float(value))
        matrix = coo_array(
            (values, (rows, columns)),
            shape=(len(self.rows), len(self.keys)),
        ).tocsr()
        return (
            np.asarray(self.objective),
            np.asarray(self.integrality),
            Bounds(np.asarray(self.lower), np.asarray(self.upper)),
            LinearConstraint(
                matrix,
                np.asarray(self.row_lower),
                np.asarray(self.row_upper),
            ),
        )


def _add(values, variable, coefficient):
    values[variable] = values.get(variable, 0) + coefficient


def _marginal_prices(data):
    result = {}
    for day_index in range(data.horizon_days):
        day = data.current_day + day_index
        for item_index, item in enumerate(SALE_PRODUCTS):
            inventory = data.base_inventory[day_index][item_index]
            param_index = PRODUCTS.index(item)
            prices = tuple(
                market_price(
                    item,
                    inventory + unit,
                    DEFAULT_MARKET_PARAMS,
                )
                for unit in range(data.sale_unit_limit)
            )
            if any(left < right for left, right in zip(prices, prices[1:])):
                raise ValueError("marginal sale prices must be nonincreasing")
            if DEFAULT_MARKET_PARAMS[param_index].base <= 0:
                raise ValueError("invalid market base price")
            result[item, day] = prices
    return result


def _marginal_wheat_buy_prices(data):
    result = {}
    wheat_index = GOODS.index("WHEAT")
    for day_index in range(data.horizon_days):
        day = data.current_day + day_index
        inventory = data.base_inventory[day_index][wheat_index]
        prices = tuple(
            market_price(
                "WHEAT",
                inventory - unit - 1,
                DEFAULT_MARKET_PARAMS,
            )
            for unit in range(data.wheat_buy_unit_limit)
        )
        if any(left > right for left, right in zip(prices, prices[1:])):
            raise ValueError("marginal wheat buy prices must be nondecreasing")
        result[day] = prices
    return result


def _entities(data):
    result = []
    for index, existing in enumerate(data.existing_animals):
        result.append((f"existing:{existing.identifier}", existing.animal, index, None))
    for slot in range(data.max_new_animals):
        candidates = (
            (data.fixed_slot_animals[slot],)
            if data.fixed_slot_animals
            else data.allowed_animals
        )
        for animal in candidates:
            result.append((f"new:{slot}:{animal}", animal, None, slot))
    return tuple(result)


def _build_model(data):
    builder = _Builder()
    days = tuple(range(data.current_day, data.last_day + 1))
    entities = _entities(data)
    terminal_values = data.terminal_values
    max_quantity = max(
        1,
        sum(data.goods)
        + sum(data.shed_animals)
        + len(data.existing_animals) * 120
        + data.max_new_animals * 120,
    )
    selected = {}
    placement = {}
    active = {}
    due = {}
    feed_action = {}
    fed = {}
    care_action = {}
    cared = {}
    care_banked = {}
    pending_start = {}
    pending_end = {}
    base_production = {}
    payout = {}
    wipe = {}
    held_start = {}
    held_end = {}
    harvest_on = {}
    harvest_deferred = {}
    harvest_quantity = {}
    deferred_harvest_quantity = {}
    overflow_on = {}
    overflow = {}
    collect = {}
    collect_deferred = {}
    animal_buy = {}
    animal_buy_on = {}
    animal_shed = {}
    structure_build = {}
    structure_balance = {}
    wheat_buy = {}
    wheat_buy_units = {}
    wheat_buy_on = {}
    goods_balance = {}
    sale_units = {}
    sale_quantity = {}
    sale_on = {}
    marginal_prices = _marginal_prices(data)
    marginal_wheat_buy_prices = _marginal_wheat_buy_prices(data)

    for identifier, animal, existing_index, slot in entities:
        care_limit = ANIMAL_SPECS[animal].first_yield_day
        fixed = 1 if existing_index is not None else None
        selected[identifier] = builder.variable(
            ("selected", identifier),
            lower=fixed if fixed is not None else 0,
            upper=fixed if fixed is not None else 1,
        )
        if slot is not None:
            for day in days:
                placement[identifier, day] = builder.variable(
                    ("placement", identifier, day),
                    upper=int(
                        _within_day_reachable(
                            data,
                            day,
                            0,
                            2 + data.placement_travel_actions,
                        )
                    ),
                )
            builder.constraint(
                {
                    placement[identifier, day]: 1
                    for day in days
                }
                | {selected[identifier]: -1},
                lower=0,
                upper=0,
            )
        for day in days:
            active[identifier, day] = builder.variable(
                ("active", identifier, day),
                objective=(
                    -terminal_values.active_animals[ANIMALS.index(animal)]
                    / data.horizon_days
                    if terminal_values is not None
                    else 0.0
                ),
                upper=1,
            )
            due[identifier, day] = builder.variable(
                ("due", identifier, day),
                upper=1,
            )
            feed_action[identifier, day] = builder.variable(
                ("feed_action", identifier, day),
                upper=1,
            )
            fed[identifier, day] = builder.variable(
                ("fed", identifier, day),
                upper=1,
            )
            care_action[identifier, day] = builder.variable(
                ("care_action", identifier, day),
                upper=1,
            )
            cared[identifier, day] = builder.variable(
                ("cared", identifier, day),
                upper=1,
            )
            care_banked[identifier, day] = builder.variable(
                ("care_banked", identifier, day),
                upper=1,
            )
            pending_start[identifier, day] = builder.variable(
                ("pending_start", identifier, day),
                upper=care_limit,
            )
            pending_end[identifier, day] = builder.variable(
                ("pending_end", identifier, day),
                upper=care_limit,
            )
            base_production[identifier, day] = builder.variable(
                ("base_production", identifier, day),
                upper=1,
            )
            payout[identifier, day] = builder.variable(
                ("payout", identifier, day),
                upper=care_limit,
            )
            wipe[identifier, day] = builder.variable(
                ("wipe", identifier, day),
                upper=care_limit,
            )
            held_start[identifier, day] = builder.variable(
                ("held_start", identifier, day),
                upper=ANIMAL_SPECS[animal].max_held,
            )
            held_end[identifier, day] = builder.variable(
                ("held_end", identifier, day),
                objective=(
                    -terminal_values.goods[
                        GOODS.index(ANIMAL_SPECS[animal].product)
                    ]
                    if terminal_values is not None and day == data.last_day
                    else 0.0
                ),
                upper=ANIMAL_SPECS[animal].max_held,
            )
            harvest_on[identifier, day] = builder.variable(
                ("harvest_on", identifier, day),
                upper=1,
            )
            harvest_deferred[identifier, day] = builder.variable(
                ("harvest_deferred", identifier, day),
                upper=1,
            )
            harvest_quantity[identifier, day] = builder.variable(
                ("harvest_quantity", identifier, day),
                upper=ANIMAL_SPECS[animal].max_held,
            )
            deferred_harvest_quantity[identifier, day] = builder.variable(
                ("deferred_harvest_quantity", identifier, day),
                upper=ANIMAL_SPECS[animal].max_held,
            )
            overflow_on[identifier, day] = builder.variable(
                ("overflow_on", identifier, day),
                upper=1,
            )
            overflow[identifier, day] = builder.variable(
                ("overflow", identifier, day),
                upper=care_limit + 1,
            )
            collect[identifier, day] = builder.variable(
                ("collect", identifier, day),
                upper=1,
            )
            collect_deferred[identifier, day] = builder.variable(
                ("collect_deferred", identifier, day),
                upper=1,
            )

    slot_identifiers = {}
    for identifier, animal, existing_index, slot in entities:
        if slot is not None:
            slot_identifiers.setdefault(slot, []).append(identifier)
    for slot in range(data.max_new_animals):
        builder.constraint(
            {
                selected[identifier]: 1
                for identifier in slot_identifiers[slot]
            },
            upper=1,
        )
    if data.min_new_animals:
        builder.constraint(
            {
                selected[identifier]: 1
                for identifier, _, existing_index, _ in entities
                if existing_index is None
            },
            lower=data.min_new_animals,
        )
    if data.fixed_slot_animals:
        for animal in ANIMALS:
            same_type = [
                slot
                for slot, candidate in enumerate(data.fixed_slot_animals)
                if candidate == animal
            ]
            for previous, current in zip(same_type, same_type[1:]):
                builder.constraint(
                    {
                        selected[f"new:{previous}:{animal}"]: 1,
                        selected[f"new:{current}:{animal}"]: -1,
                    },
                    lower=0,
                )
    else:
        for slot in range(data.max_new_animals - 1):
            current = {
                selected[f"new:{slot}:{animal}"]: 1
                for animal in data.allowed_animals
            }
            for animal in data.allowed_animals:
                current[selected[f"new:{slot + 1}:{animal}"]] = -1
            builder.constraint(current, lower=0)
            for animal_index in range(len(data.allowed_animals)):
                ordered = {}
                for animal in data.allowed_animals[: animal_index + 1]:
                    ordered[selected[f"new:{slot}:{animal}"]] = 1
                    ordered[selected[f"new:{slot + 1}:{animal}"]] = -1
                builder.constraint(ordered, lower=0)

    for animal in ANIMALS:
        for day in days:
            animal_buy[animal, day] = builder.variable(
                ("animal_buy", animal, day),
                objective=ANIMAL_COSTS[animal],
                upper=data.max_new_animals,
            )
            animal_buy_on[animal, day] = builder.variable(
                ("animal_buy_on", animal, day),
                upper=1,
            )
            animal_shed[animal, day] = builder.variable(
                ("animal_shed", animal, day),
                objective=(
                    -terminal_values.shed_animals[ANIMALS.index(animal)]
                    if terminal_values is not None and day == data.last_day
                    else 0.0
                ),
                upper=sum(data.shed_animals) + data.max_new_animals,
            )
    for structure in STRUCTURES:
        for day in days:
            structure_build[structure, day] = builder.variable(
                ("structure_build", structure, day),
                upper=data.max_new_animals,
            )
            structure_balance[structure, day] = builder.variable(
                ("structure_balance", structure, day),
                objective=(
                    -terminal_values.empty_structures[STRUCTURES.index(structure)]
                    if terminal_values is not None and day == data.last_day
                    else 0.0
                ),
                upper=sum(data.empty_structures) + data.max_new_animals,
            )
    for day in days:
        wheat_buy[day] = builder.variable(
            ("wheat_buy", day),
            upper=data.wheat_buy_unit_limit,
        )
        for unit, price in enumerate(marginal_wheat_buy_prices[day]):
            wheat_buy_units[day, unit] = builder.variable(
                ("wheat_buy_unit", day, unit),
                objective=price,
                upper=1,
                integral=False,
            )
        wheat_buy_on[day] = builder.variable(("wheat_buy_on", day), upper=1)
        for item in GOODS:
            goods_balance[item, day] = builder.variable(
                ("goods_balance", item, day),
                objective=(
                    -terminal_values.goods[GOODS.index(item)]
                    if terminal_values is not None and day == data.last_day
                    else 0.0
                ),
                upper=max_quantity,
            )
            sale_quantity[item, day] = builder.variable(
                ("sale_quantity", item, day),
                upper=data.sale_unit_limit,
            )
            sale_on[item, day] = builder.variable(("sale_on", item, day), upper=1)
            for unit, price in enumerate(marginal_prices[item, day]):
                sale_units[item, day, unit] = builder.variable(
                    ("sale", item, day, unit),
                    objective=-price,
                    upper=1,
                    integral=False,
                )

    for identifier, animal, existing_index, slot in entities:
        spec = ANIMAL_SPECS[animal]
        care_limit = spec.first_yield_day
        existing = (
            data.existing_animals[existing_index]
            if existing_index is not None
            else None
        )
        for day in days:
            if existing is not None:
                builder.constraint(
                    {active[identifier, day]: 1},
                    lower=1,
                    upper=1,
                )
                due_value = int(
                    _refresh_reachable(data, day)
                    and _production_due(animal, existing.placed_day, day)
                )
                builder.constraint(
                    {due[identifier, day]: 1},
                    lower=due_value,
                    upper=due_value,
                )
            else:
                active_values = {active[identifier, day]: 1}
                for placement_day in days:
                    if placement_day <= day:
                        _add(active_values, placement[identifier, placement_day], -1)
                builder.constraint(active_values, lower=0, upper=0)
                due_values = {due[identifier, day]: 1}
                if _refresh_reachable(data, day):
                    for placement_day in days:
                        if _production_due(animal, placement_day, day):
                            _add(due_values, placement[identifier, placement_day], -1)
                builder.constraint(due_values, lower=0, upper=0)

            builder.constraint(
                {feed_action[identifier, day]: 1, active[identifier, day]: -1},
                upper=0,
            )
            builder.constraint(
                {care_action[identifier, day]: 1, active[identifier, day]: -1},
                upper=0,
            )
            builder.constraint(
                {harvest_on[identifier, day]: 1, active[identifier, day]: -1},
                upper=0,
            )
            if not _within_day_reachable(
                data,
                day,
                0,
                data.feed_actions_per_unit,
            ):
                builder.constraint(
                    {feed_action[identifier, day]: 1},
                    upper=0,
                )
            if not _refresh_reachable(data, day):
                builder.constraint(
                    {
                        feed_action[identifier, day]: 1,
                        care_action[identifier, day]: 1,
                        fed[identifier, day]: 1,
                        cared[identifier, day]: 1,
                    },
                    lower=0,
                    upper=0,
                )
            elif day == data.current_day and existing is not None and existing.fed_today:
                builder.constraint(
                    {feed_action[identifier, day]: 1},
                    lower=0,
                    upper=0,
                )
                builder.constraint(
                    {fed[identifier, day]: 1},
                    lower=1,
                    upper=1,
                )
            else:
                builder.constraint(
                    {
                        fed[identifier, day]: 1,
                        feed_action[identifier, day]: -1,
                    },
                    lower=0,
                    upper=0,
                )
            if not _refresh_reachable(data, day):
                pass
            elif day == data.current_day and existing is not None and existing.cared_today:
                builder.constraint(
                    {care_action[identifier, day]: 1},
                    lower=0,
                    upper=0,
                )
                builder.constraint(
                    {cared[identifier, day]: 1},
                    lower=1,
                    upper=1,
                )
            else:
                builder.constraint(
                    {
                        cared[identifier, day]: 1,
                        care_action[identifier, day]: -1,
                    },
                    lower=0,
                    upper=0,
                )
            builder.constraint(
                {
                    care_banked[identifier, day]: 1,
                    cared[identifier, day]: -1,
                },
                upper=0,
            )
            builder.constraint(
                {
                    care_banked[identifier, day]: 1,
                    fed[identifier, day]: -1,
                },
                upper=0,
            )
            builder.constraint(
                {
                    care_banked[identifier, day]: 1,
                    cared[identifier, day]: -1,
                    fed[identifier, day]: -1,
                    active[identifier, day]: 1,
                },
                lower=0,
            )
            builder.constraint(
                {
                    base_production[identifier, day]: 1,
                    due[identifier, day]: -1,
                },
                upper=0,
            )
            builder.constraint(
                {
                    base_production[identifier, day]: 1,
                    fed[identifier, day]: -1,
                },
                upper=0,
            )
            builder.constraint(
                {
                    base_production[identifier, day]: 1,
                    due[identifier, day]: -1,
                    fed[identifier, day]: -1,
                },
                lower=-1,
            )
            builder.constraint(
                {
                    payout[identifier, day]: 1,
                    pending_start[identifier, day]: -1,
                },
                upper=0,
            )
            builder.constraint(
                {
                    payout[identifier, day]: 1,
                    due[identifier, day]: -care_limit,
                },
                upper=0,
            )
            builder.constraint(
                {
                    payout[identifier, day]: 1,
                    fed[identifier, day]: -care_limit,
                },
                upper=0,
            )
            builder.constraint(
                {
                    payout[identifier, day]: 1,
                    pending_start[identifier, day]: -1,
                    due[identifier, day]: -care_limit,
                    fed[identifier, day]: -care_limit,
                },
                lower=-2 * care_limit,
            )
            builder.constraint(
                {
                    wipe[identifier, day]: 1,
                    pending_start[identifier, day]: -1,
                },
                upper=0,
            )
            builder.constraint(
                {
                    wipe[identifier, day]: 1,
                    due[identifier, day]: -care_limit,
                },
                upper=0,
            )
            builder.constraint(
                {
                    wipe[identifier, day]: 1,
                    fed[identifier, day]: care_limit,
                },
                upper=care_limit,
            )
            builder.constraint(
                {
                    wipe[identifier, day]: 1,
                    pending_start[identifier, day]: -1,
                    due[identifier, day]: -care_limit,
                    fed[identifier, day]: care_limit,
                },
                lower=-care_limit,
            )
            builder.constraint(
                {
                    pending_end[identifier, day]: 1,
                    pending_start[identifier, day]: -1,
                    payout[identifier, day]: 1,
                    wipe[identifier, day]: 1,
                    care_banked[identifier, day]: -1,
                },
                lower=0,
                upper=0,
            )
            builder.constraint(
                {
                    harvest_quantity[identifier, day]: 1,
                    harvest_on[identifier, day]: -spec.max_held,
                },
                upper=0,
            )
            builder.constraint(
                {
                    harvest_quantity[identifier, day]: 1,
                    held_start[identifier, day]: -1,
                },
                upper=0,
            )
            builder.constraint(
                {
                    harvest_quantity[identifier, day]: 1,
                    held_start[identifier, day]: -1,
                    harvest_on[identifier, day]: -spec.max_held,
                },
                lower=-spec.max_held,
            )
            builder.constraint(
                {
                    harvest_deferred[identifier, day]: 1,
                    harvest_on[identifier, day]: -1,
                },
                upper=0,
            )
            builder.constraint(
                {
                    deferred_harvest_quantity[identifier, day]: 1,
                    harvest_quantity[identifier, day]: -1,
                },
                upper=0,
            )
            builder.constraint(
                {
                    deferred_harvest_quantity[identifier, day]: 1,
                    harvest_deferred[identifier, day]: -spec.max_held,
                },
                upper=0,
            )
            builder.constraint(
                {
                    deferred_harvest_quantity[identifier, day]: 1,
                    harvest_quantity[identifier, day]: -1,
                    harvest_deferred[identifier, day]: -spec.max_held,
                },
                lower=-spec.max_held,
            )
            total = {
                held_start[identifier, day]: 1,
                harvest_quantity[identifier, day]: -1,
                base_production[identifier, day]: 1,
                payout[identifier, day]: 1,
            }
            overflow_lower = dict(total)
            _add(overflow_lower, overflow[identifier, day], -1)
            builder.constraint(overflow_lower, upper=spec.max_held)
            builder.constraint(
                {
                    overflow[identifier, day]: 1,
                    overflow_on[identifier, day]: -(care_limit + 1),
                },
                upper=0,
            )
            overflow_upper = {
                overflow[identifier, day]: 1,
                overflow_on[identifier, day]: care_limit + 1,
            }
            for variable, coefficient in total.items():
                _add(overflow_upper, variable, -coefficient)
            builder.constraint(
                overflow_upper,
                upper=care_limit + 1 - spec.max_held,
            )
            total_trigger = dict(total)
            _add(
                total_trigger,
                overflow_on[identifier, day],
                -(care_limit + 1),
            )
            builder.constraint(total_trigger, upper=spec.max_held)
            held_values = {
                held_end[identifier, day]: 1,
                overflow[identifier, day]: 1,
            }
            for variable, coefficient in total.items():
                _add(held_values, variable, -coefficient)
            builder.constraint(held_values, lower=0, upper=0)

            if day == data.current_day:
                initial_held = existing.yield_units if existing is not None else 0
                initial_pending = (
                    existing.pending_care_bonus if existing is not None else 0
                )
                builder.constraint(
                    {held_start[identifier, day]: 1},
                    lower=initial_held,
                    upper=initial_held,
                )
                builder.constraint(
                    {pending_start[identifier, day]: 1},
                    lower=initial_pending,
                    upper=initial_pending,
                )
            else:
                builder.constraint(
                    {
                        held_start[identifier, day]: 1,
                        held_end[identifier, day - 1]: -1,
                    },
                    lower=0,
                    upper=0,
                )
                builder.constraint(
                    {
                        pending_start[identifier, day]: 1,
                        pending_end[identifier, day - 1]: -1,
                    },
                    lower=0,
                    upper=0,
                )
            builder.constraint(
                {
                    held_end[identifier, day]: 1,
                    active[identifier, day]: -spec.max_held,
                },
                upper=0,
            )
            builder.constraint(
                {
                    pending_end[identifier, day]: 1,
                    active[identifier, day]: -care_limit,
                },
                upper=0,
            )

            if existing is not None and day == data.current_day:
                eligible = int(existing.fertilizer_available)
                builder.constraint(
                    {collect[identifier, day]: 1},
                    upper=eligible,
                )
            elif day == data.current_day:
                builder.constraint({collect[identifier, day]: 1}, upper=0)
            else:
                builder.constraint(
                    {
                        collect[identifier, day]: 1,
                        active[identifier, day - 1]: -1,
                    },
                    upper=0,
                )
            builder.constraint(
                {
                    collect_deferred[identifier, day]: 1,
                    collect[identifier, day]: -1,
                },
                upper=0,
            )
            if day == data.last_day or not _day_end_reachable(data, day):
                builder.constraint(
                    {
                        harvest_deferred[identifier, day]: 1,
                        collect_deferred[identifier, day]: 1,
                    },
                    upper=0,
                )
            if not _within_day_reachable(data, day, 0, 1):
                builder.constraint(
                    {
                        harvest_on[identifier, day]: 1,
                        collect[identifier, day]: 1,
                    },
                    upper=0,
                )
            elif not _within_day_reachable(
                data,
                day,
                0,
                1 + data.return_actions,
            ):
                builder.constraint(
                    {
                        harvest_on[identifier, day]: 1,
                        harvest_deferred[identifier, day]: -1,
                    },
                    upper=0,
                )
                builder.constraint(
                    {
                        collect[identifier, day]: 1,
                        collect_deferred[identifier, day]: -1,
                    },
                    upper=0,
                )

        refresh_days = tuple(
            day for day in days if _refresh_reachable(data, day)
        )
        if (
            existing is not None
            and refresh_days
            and existing.consecutive_unfed == 1
            and not existing.fed_today
        ):
            builder.constraint(
                {fed[identifier, refresh_days[0]]: 1},
                lower=1,
            )
        for previous_day, day in zip(refresh_days, refresh_days[1:]):
            builder.constraint(
                {
                    fed[identifier, previous_day]: 1,
                    fed[identifier, day]: 1,
                    active[identifier, previous_day]: -1,
                    active[identifier, day]: -1,
                },
                lower=-1,
            )

    wheat_use = {
        wheat_buy[day]: 1
        for day in days
    }
    for identifier, animal, existing_index, slot in entities:
        for day in days:
            _add(wheat_use, feed_action[identifier, day], -1)
    builder.constraint(wheat_use, upper=0)

    for animal_index, animal in enumerate(ANIMALS):
        placement_total = {}
        for identifier, candidate_animal, existing_index, slot in entities:
            if slot is None or candidate_animal != animal:
                continue
            for day in days:
                _add(placement_total, placement[identifier, day], 1)
        buy_total = {
            animal_buy[animal, day]: 1
            for day in days
        }
        builder.constraint(
            buy_total | {variable: -value for variable, value in placement_total.items()},
            upper=0,
        )
        for day in days:
            balance_values = {
                animal_shed[animal, day]: 1,
                animal_buy[animal, day]: -1,
            }
            if day == data.current_day:
                balance_rhs = data.shed_animals[animal_index]
            else:
                balance_values[animal_shed[animal, day - 1]] = -1
                balance_rhs = 0
            for identifier, candidate_animal, existing_index, slot in entities:
                if slot is not None and candidate_animal == animal:
                    _add(balance_values, placement[identifier, day], 1)
            builder.constraint(balance_values, lower=balance_rhs, upper=balance_rhs)
            available_values = {}
            for identifier, candidate_animal, existing_index, slot in entities:
                if slot is not None and candidate_animal == animal:
                    _add(available_values, placement[identifier, day], 1)
            if day == data.current_day:
                if _same_day_animal_arrival_reachable(data, day):
                    available_values[animal_buy[animal, day]] = -1
                builder.constraint(
                    available_values,
                    upper=data.shed_animals[animal_index],
                )
            else:
                available_values[animal_shed[animal, day - 1]] = -1
                if _same_day_animal_arrival_reachable(data, day):
                    available_values[animal_buy[animal, day]] = -1
                builder.constraint(available_values, upper=0)
            builder.constraint(
                {
                    animal_buy[animal, day]: 1,
                    animal_buy_on[animal, day]: -data.max_new_animals,
                },
                upper=0,
            )
            builder.constraint(
                {
                    animal_buy[animal, day]: 1,
                    animal_buy_on[animal, day]: -1,
                },
                lower=0,
            )

    for structure_index, structure in enumerate(STRUCTURES):
        total_structure_use = {
            structure_build[structure, day]: 1
            for day in days
        }
        for identifier, animal, existing_index, slot in entities:
            if slot is not None and ANIMAL_SPECS[animal].structure == structure:
                for day in days:
                    _add(total_structure_use, placement[identifier, day], -1)
        builder.constraint(total_structure_use, upper=0)
        for day in days:
            values = {
                structure_balance[structure, day]: 1,
                structure_build[structure, day]: -1,
            }
            if day == data.current_day:
                rhs = data.empty_structures[structure_index]
            else:
                values[structure_balance[structure, day - 1]] = -1
                rhs = 0
            for identifier, animal, existing_index, slot in entities:
                if slot is not None and ANIMAL_SPECS[animal].structure == structure:
                    _add(values, placement[identifier, day], 1)
            builder.constraint(values, lower=rhs, upper=rhs)

    for day_index, day in enumerate(days):
        tile_values = {}
        for structure in STRUCTURES:
            for prior_day in days:
                if prior_day <= day:
                    _add(tile_values, structure_build[structure, prior_day], 1)
        builder.constraint(
            tile_values,
            upper=data.animal_tile_capacity[day_index]
            - len(data.existing_animals)
            - sum(data.empty_structures),
        )

        action_values = {}
        for structure in STRUCTURES:
            _add(action_values, structure_build[structure, day], 1)
        for identifier, animal, existing_index, slot in entities:
            if slot is not None:
                _add(
                    action_values,
                    placement[identifier, day],
                    2 + data.placement_travel_actions,
                )
            _add(
                action_values,
                feed_action[identifier, day],
                data.feed_actions_per_unit,
            )
            _add(action_values, care_action[identifier, day], 1)
            _add(
                action_values,
                harvest_on[identifier, day],
                1 + data.return_actions,
            )
            _add(
                action_values,
                harvest_deferred[identifier, day],
                -data.return_actions,
            )
            _add(
                action_values,
                collect[identifier, day],
                1 + data.return_actions,
            )
            _add(
                action_values,
                collect_deferred[identifier, day],
                -data.return_actions,
            )
        builder.constraint(action_values, upper=data.action_capacity[day_index])

        feed_availability = {
            feed_action[identifier, day]: 1
            for identifier, animal, existing_index, slot in entities
        }
        if day == data.current_day:
            if _same_day_wheat_arrival_reachable(data, day):
                feed_availability[wheat_buy[day]] = -1
            builder.constraint(feed_availability, upper=data.goods[0])
        else:
            feed_availability[goods_balance["WHEAT", day - 1]] = -1
            if _same_day_wheat_arrival_reachable(data, day):
                feed_availability[wheat_buy[day]] = -1
            builder.constraint(feed_availability, upper=0)

        for item_index, item in enumerate(GOODS):
            values = {goods_balance[item, day]: 1}
            if day == data.current_day:
                rhs = data.goods[item_index]
            else:
                values[goods_balance[item, day - 1]] = -1
                rhs = 0
            if item == "WHEAT":
                values[wheat_buy[day]] = -1
                for identifier, animal, existing_index, slot in entities:
                    _add(values, feed_action[identifier, day], 1)
            if item == "FERTILIZER":
                for identifier, animal, existing_index, slot in entities:
                    _add(values, collect[identifier, day], -1)
            for identifier, animal, existing_index, slot in entities:
                if ANIMAL_SPECS[animal].product == item:
                    _add(values, harvest_quantity[identifier, day], -1)
            _add(values, sale_quantity[item, day], 1)
            builder.constraint(values, lower=rhs, upper=rhs)
            available_for_sale = {sale_quantity[item, day]: 1}
            if day == data.current_day:
                sale_rhs = data.goods[item_index]
            else:
                available_for_sale[goods_balance[item, day - 1]] = -1
                sale_rhs = 0
            if item == "WHEAT":
                available_for_sale[wheat_buy[day]] = -1
                for identifier, animal, existing_index, slot in entities:
                    _add(available_for_sale, feed_action[identifier, day], 1)
            if item == "FERTILIZER":
                for identifier, animal, existing_index, slot in entities:
                    _add(available_for_sale, collect[identifier, day], -1)
                    _add(
                        available_for_sale,
                        collect_deferred[identifier, day],
                        1,
                    )
            for identifier, animal, existing_index, slot in entities:
                if ANIMAL_SPECS[animal].product == item:
                    _add(
                        available_for_sale,
                        harvest_quantity[identifier, day],
                        -1,
                    )
                    _add(
                        available_for_sale,
                        deferred_harvest_quantity[identifier, day],
                        1,
                    )
            builder.constraint(available_for_sale, upper=sale_rhs)

        shed_values = {
            goods_balance[item, day]: 1
            for item in GOODS
        }
        for animal in ANIMALS:
            shed_values[animal_shed[animal, day]] = 1
        builder.constraint(
            shed_values,
            upper=data.shed_capacity[day_index] - data.fixed_shed_occupancy[day_index],
        )

        builder.constraint(
            {
                wheat_buy[day]: 1,
                wheat_buy_on[day]: -max_quantity,
            },
            upper=0,
        )
        builder.constraint(
            {wheat_buy[day]: 1, wheat_buy_on[day]: -1},
            lower=0,
        )
        wheat_quantity_definition = {wheat_buy[day]: 1}
        for unit in range(data.wheat_buy_unit_limit):
            _add(wheat_quantity_definition, wheat_buy_units[day, unit], -1)
        builder.constraint(wheat_quantity_definition, lower=0, upper=0)
        for unit in range(data.wheat_buy_unit_limit - 1):
            builder.constraint(
                {
                    wheat_buy_units[day, unit]: 1,
                    wheat_buy_units[day, unit + 1]: -1,
                },
                lower=0,
            )
        order_values = {wheat_buy_on[day]: 1}
        for animal in ANIMALS:
            order_values[animal_buy_on[animal, day]] = 1
        for item in GOODS:
            selected_sales = {sale_quantity[item, day]: 1}
            selected_sales[sale_on[item, day]] = -data.sale_unit_limit
            builder.constraint(selected_sales, upper=0)
            builder.constraint(
                {sale_quantity[item, day]: 1, sale_on[item, day]: -1},
                lower=0,
            )
            quantity_definition = {sale_quantity[item, day]: 1}
            for unit in range(data.sale_unit_limit):
                _add(quantity_definition, sale_units[item, day, unit], -1)
            builder.constraint(quantity_definition, lower=0, upper=0)
            for unit in range(data.sale_unit_limit - 1):
                builder.constraint(
                    {
                        sale_units[item, day, unit]: 1,
                        sale_units[item, day, unit + 1]: -1,
                    },
                    lower=0,
                )
            order_values[sale_on[item, day]] = 1
        builder.constraint(order_values, upper=data.market_order_slots[day_index])

    cumulative_cash = {}
    fixed_cash = 0.0
    for day_index, day in enumerate(days):
        fixed_cash += data.fixed_cash_flow[day_index]
        for animal in ANIMALS:
            cumulative_cash[animal_buy[animal, day]] = ANIMAL_COSTS[animal]
        for unit, price in enumerate(marginal_wheat_buy_prices[day]):
            cumulative_cash[wheat_buy_units[day, unit]] = price
        for item in GOODS:
            for unit, price in enumerate(marginal_prices[item, day]):
                cumulative_cash[sale_units[item, day, unit]] = -price
        builder.constraint(
            cumulative_cash,
            upper=data.cash + fixed_cash - data.cash_reserve,
        )

    return {
        "builder": builder,
        "entities": entities,
        "selected": selected,
        "placement": placement,
        "active": active,
        "feed_action": feed_action,
        "fed": fed,
        "care_action": care_action,
        "cared": cared,
        "care_banked": care_banked,
        "base_production": base_production,
        "payout": payout,
        "overflow": overflow,
        "harvest_on": harvest_on,
        "harvest_deferred": harvest_deferred,
        "harvest_quantity": harvest_quantity,
        "deferred_harvest_quantity": deferred_harvest_quantity,
        "held_end": held_end,
        "pending_end": pending_end,
        "collect": collect,
        "collect_deferred": collect_deferred,
        "animal_buy": animal_buy,
        "animal_shed": animal_shed,
        "structure_build": structure_build,
        "structure_balance": structure_balance,
        "wheat_buy": wheat_buy,
        "wheat_buy_units": wheat_buy_units,
        "goods_balance": goods_balance,
        "sale_units": sale_units,
        "sale_quantity": sale_quantity,
        "marginal_prices": marginal_prices,
        "marginal_wheat_buy_prices": marginal_wheat_buy_prices,
    }


def _integer(value):
    return int(round(float(value)))


def _terminal_value(data, services, balance):
    values = data.terminal_values
    if values is None:
        return None
    total = 0.0
    for service in services:
        if not service.active:
            continue
        animal_index = ANIMALS.index(service.animal)
        total += values.active_animals[animal_index] / data.horizon_days
        if service.day == data.last_day:
            product_index = GOODS.index(ANIMAL_SPECS[service.animal].product)
            total += service.held_end * values.goods[product_index]
    total += sum(
        quantity * value for quantity, value in zip(balance.goods, values.goods)
    )
    total += sum(
        quantity * value
        for quantity, value in zip(balance.shed_animals, values.shed_animals)
    )
    total += sum(
        quantity * value
        for quantity, value in zip(
            balance.empty_structures,
            values.empty_structures,
        )
    )
    return total


def solve_animal_oracle(
    data,
    time_limit=120.0,
    mip_rel_gap=0.0,
    accept_feasible=False,
):
    if not isinstance(data, AnimalOracleInput):
        raise TypeError("data must be an AnimalOracleInput")
    if type(time_limit) not in (int, float) or time_limit <= 0:
        raise ValueError("time limit must be positive")
    if type(mip_rel_gap) not in (int, float) or not 0 <= mip_rel_gap < 1:
        raise ValueError("MIP gap must be in 0..1")
    if type(accept_feasible) is not bool:
        raise TypeError("feasible acceptance must be a boolean")
    built = _build_model(data)
    builder = built["builder"]
    objective, integrality, bounds, constraints = builder.arrays()
    started = time.perf_counter()
    solved = milp(
        objective,
        integrality=integrality,
        bounds=bounds,
        constraints=constraints,
        options={
            "time_limit": float(time_limit),
            "mip_rel_gap": float(mip_rel_gap),
            "presolve": True,
        },
    )
    wall_seconds = time.perf_counter() - started
    gap = getattr(solved, "mip_gap", None)
    if gap is not None and math.isfinite(float(gap)):
        gap = float(gap)
    else:
        gap = None
    success = bool(solved.x is not None and (solved.success or accept_feasible))
    if not success:
        return AnimalOracleResult(
            False,
            int(solved.status),
            str(solved.message),
            gap,
            wall_seconds,
            len(builder.keys),
            len(builder.rows),
            None,
            None,
            (),
            (),
            (),
            (),
            (),
            (),
            None,
            None,
            data.scenario,
            input_sha256(data),
        )
    values = solved.x
    days = tuple(range(data.current_day, data.last_day + 1))
    animals = []
    structures = []
    purchases = []
    sales = []
    services = []
    balances = []
    for identifier, animal, existing_index, slot in built["entities"]:
        if not _integer(values[built["selected"][identifier]]):
            continue
        if existing_index is not None:
            placement_day = data.existing_animals[existing_index].placed_day
        else:
            placement_day = next(
                day
                for day in days
                if _integer(values[built["placement"][identifier, day]])
            )
        animals.append(
            AnimalDecision(
                identifier,
                animal,
                existing_index is not None,
                placement_day,
            )
        )
        for day in days:
            if not _integer(values[built["active"][identifier, day]]):
                continue
            production = _integer(values[built["base_production"][identifier, day]])
            production += _integer(values[built["payout"][identifier, day]])
            services.append(
                AnimalService(
                    identifier,
                    animal,
                    day,
                    True,
                    bool(_integer(values[built["feed_action"][identifier, day]])),
                    bool(_integer(values[built["fed"][identifier, day]])),
                    bool(_integer(values[built["care_action"][identifier, day]])),
                    bool(_integer(values[built["cared"][identifier, day]])),
                    _integer(values[built["care_banked"][identifier, day]]),
                    production,
                    _integer(values[built["overflow"][identifier, day]]),
                    bool(_integer(values[built["harvest_on"][identifier, day]])),
                    bool(
                        _integer(
                            values[built["harvest_deferred"][identifier, day]]
                        )
                    ),
                    _integer(values[built["harvest_quantity"][identifier, day]]),
                    _integer(values[built["held_end"][identifier, day]]),
                    _integer(values[built["pending_end"][identifier, day]]),
                    _integer(values[built["collect"][identifier, day]]),
                    bool(
                        _integer(
                            values[built["collect_deferred"][identifier, day]]
                        )
                    ),
                )
            )
    for structure in STRUCTURES:
        for day in days:
            quantity = _integer(values[built["structure_build"][structure, day]])
            if quantity:
                structures.append(StructureDecision(structure, day, quantity))
    cumulative_cost = 0.0
    cumulative_revenue = 0.0
    cumulative_fixed = 0.0
    for day_index, day in enumerate(days):
        for animal in ANIMALS:
            quantity = _integer(values[built["animal_buy"][animal, day]])
            if quantity:
                cost = quantity * ANIMAL_COSTS[animal]
                purchases.append(AnimalPurchase(animal, day, quantity, cost))
                cumulative_cost += cost
        wheat = _integer(values[built["wheat_buy"][day]])
        if wheat:
            cost = sum(built["marginal_wheat_buy_prices"][day][:wheat])
            purchases.append(AnimalPurchase("WHEAT", day, wheat, cost))
            cumulative_cost += cost
        for item in GOODS:
            quantity = _integer(values[built["sale_quantity"][item, day]])
            if quantity:
                revenue = sum(
                    built["marginal_prices"][item, day][unit]
                    for unit in range(quantity)
                )
                sales.append(AnimalSale(item, day, quantity, revenue))
                cumulative_revenue += revenue
        cumulative_fixed += data.fixed_cash_flow[day_index]
        cash = data.cash + cumulative_fixed - cumulative_cost + cumulative_revenue
        balances.append(
            AnimalDayBalance(
                day,
                cash,
                tuple(
                    _integer(values[built["goods_balance"][item, day]])
                    for item in GOODS
                ),
                tuple(
                    _integer(values[built["animal_shed"][animal, day]])
                    for animal in ANIMALS
                ),
                tuple(
                    _integer(values[built["structure_balance"][structure, day]])
                    for structure in STRUCTURES
                ),
            )
        )
    terminal_cash = balances[-1].cash
    terminal_value = _terminal_value(data, services, balances[-1])
    forecast_terminal_cash = (
        terminal_cash + terminal_value if terminal_value is not None else None
    )
    return AnimalOracleResult(
        True,
        int(solved.status),
        str(solved.message),
        gap,
        wall_seconds,
        len(builder.keys),
        len(builder.rows),
        terminal_cash,
        terminal_cash - data.cash - sum(data.fixed_cash_flow),
        tuple(animals),
        tuple(structures),
        tuple(purchases),
        tuple(sales),
        tuple(services),
        tuple(balances),
        balances[-1].goods,
        balances[-1].shed_animals,
        data.scenario,
        input_sha256(data),
        terminal_value,
        forecast_terminal_cash,
    )


def verify_result(data, result):
    if not isinstance(data, AnimalOracleInput):
        raise TypeError("data must be an AnimalOracleInput")
    if not isinstance(result, AnimalOracleResult):
        raise TypeError("result must be an AnimalOracleResult")
    errors = []
    if result.input_sha256 != input_sha256(data):
        errors.append("input hash mismatch")
    if result.scenario != data.scenario:
        errors.append("scenario mismatch")
    if not result.success:
        return tuple(errors)
    days = tuple(range(data.current_day, data.last_day + 1))
    purchases_by_day = {(item, day): 0 for item in ANIMALS + ("WHEAT",) for day in days}
    purchase_cost = {day: 0.0 for day in days}
    for purchase in result.purchases:
        purchases_by_day[purchase.item, purchase.day] += purchase.quantity
        expected_cost = (
            sum(_marginal_wheat_buy_prices(data)[purchase.day][: purchase.quantity])
            if purchase.item == "WHEAT"
            else purchase.quantity * ANIMAL_COSTS[purchase.item]
        )
        if purchase.cost != expected_cost:
            errors.append("purchase cost mismatch")
        purchase_cost[purchase.day] += purchase.cost
    sales_by_day = {(item, day): 0 for item in GOODS for day in days}
    sale_revenue = {day: 0.0 for day in days}
    marginal = _marginal_prices(data)
    for sale in result.sales:
        sales_by_day[sale.item, sale.day] += sale.quantity
        expected = sum(marginal[sale.item, sale.day][: sale.quantity])
        if sale.revenue != expected:
            errors.append("sale revenue mismatch")
        sale_revenue[sale.day] += sale.revenue
    services = {(service.identifier, service.day): service for service in result.services}
    existing_by_identifier = {
        f"existing:{animal.identifier}": animal for animal in data.existing_animals
    }
    decision_identifiers = [animal.identifier for animal in result.animals]
    if len(set(decision_identifiers)) != len(decision_identifiers):
        errors.append("duplicate animal decision")
    reported_existing = {
        animal.identifier for animal in result.animals if animal.existing
    }
    if reported_existing != set(existing_by_identifier):
        errors.append("existing animal set mismatch")
    candidate_animals = {
        identifier: animal
        for identifier, animal, existing_index, slot in _entities(data)
        if slot is not None
    }
    for decision in result.animals:
        if not decision.existing and candidate_animals.get(decision.identifier) != decision.animal:
            errors.append("new animal candidate mismatch")
    animal_placements = {(animal, day): 0 for animal in ANIMALS for day in days}
    harvests = {(item, day): 0 for item in GOODS for day in days}
    direct_harvests = {(item, day): 0 for item in GOODS for day in days}
    collections = {day: 0 for day in days}
    direct_collections = {day: 0 for day in days}
    feeds = {day: 0 for day in days}
    actions = {day: 0 for day in days}
    for decision in result.animals:
        if decision.existing:
            existing = existing_by_identifier.get(decision.identifier)
            if existing is None or existing.animal != decision.animal:
                errors.append("existing animal mismatch")
                continue
            if decision.placement_day != existing.placed_day:
                errors.append("existing placement day mismatch")
            held = existing.yield_units
            pending = existing.pending_care_bonus
            missed = existing.consecutive_unfed
        else:
            existing = None
            held = 0
            pending = 0
            missed = 0
            if decision.placement_day not in days:
                errors.append("placement day outside horizon")
                continue
            animal_placements[decision.animal, decision.placement_day] += 1
            actions[decision.placement_day] += 2 + data.placement_travel_actions
        for day in days:
            active = decision.existing or day >= decision.placement_day
            service = services.get((decision.identifier, day))
            if not active:
                if service is not None:
                    errors.append("service before placement")
                continue
            if service is None:
                errors.append("missing active service")
                continue
            if not _refresh_reachable(data, day):
                fed = False
                cared = False
                eligible_fertilizer = (
                    existing.fertilizer_available
                    if day == data.current_day and existing is not None
                    else day > data.current_day
                )
            elif day == data.current_day and existing is not None:
                fed = existing.fed_today or service.feed_action
                cared = existing.cared_today or service.care_action
                eligible_fertilizer = existing.fertilizer_available
                if existing.fed_today and service.feed_action:
                    errors.append("duplicate current feed")
                if existing.cared_today and service.care_action:
                    errors.append("duplicate current care")
            else:
                fed = service.feed_action
                cared = service.care_action
                eligible_fertilizer = day > data.current_day
            if service.fed != fed or service.cared != cared:
                errors.append("effective service mismatch")
            if not _refresh_reachable(data, day) and (
                service.feed_action or service.care_action
            ):
                errors.append("service after final refresh")
            if service.harvest_action:
                if service.harvested != held:
                    errors.append("harvest did not empty held product")
                held = 0
                actions[day] += 1
                if service.harvest_deferred:
                    if day == data.last_day or not _day_end_reachable(data, day):
                        errors.append("terminal harvest cannot be deferred")
                else:
                    actions[day] += data.return_actions
            elif service.harvested:
                errors.append("harvest quantity lacks action")
            elif service.harvest_deferred:
                errors.append("deferred harvest lacks action")
            product = ANIMAL_SPECS[decision.animal].product
            harvests[product, day] += service.harvested
            if not service.harvest_deferred:
                direct_harvests[product, day] += service.harvested
            if service.fertilizer_collected:
                if not eligible_fertilizer:
                    errors.append("fertilizer collected before available")
                collections[day] += service.fertilizer_collected
                actions[day] += service.fertilizer_collected
                if service.fertilizer_deferred:
                    if day == data.last_day or not _day_end_reachable(data, day):
                        errors.append("terminal fertilizer cannot be deferred")
                else:
                    direct_collections[day] += service.fertilizer_collected
                    actions[day] += data.return_actions * service.fertilizer_collected
            elif service.fertilizer_deferred:
                errors.append("deferred fertilizer lacks collection")
            if service.feed_action:
                feeds[day] += 1
                actions[day] += data.feed_actions_per_unit
                if not _within_day_reachable(
                    data,
                    day,
                    0,
                    data.feed_actions_per_unit,
                ):
                    errors.append("feed action exceeds day boundary")
            if service.care_action:
                actions[day] += 1
            if _refresh_reachable(data, day):
                if fed:
                    missed = 0
                else:
                    missed += 1
                if missed >= 2:
                    errors.append("selected animal escapes")
                due = _production_due(decision.animal, decision.placement_day, day)
                production = 0
                if due:
                    if fed:
                        production = 1 + pending
                    pending = 0
                banked = int(cared and fed)
                pending += banked
                total = held + production
                cap = ANIMAL_SPECS[decision.animal].max_held
                overflow = max(0, total - cap)
                held = min(cap, total)
            else:
                production = 0
                overflow = 0
                banked = 0
            if (
                service.care_banked != banked
                or service.production != production
                or service.overflow != overflow
                or service.held_end != held
                or service.pending_care_end != pending
            ):
                errors.append("animal state mismatch")
    if set(services) != {
        (decision.identifier, day)
        for decision in result.animals
        for day in days
        if decision.existing or day >= decision.placement_day
    }:
        errors.append("service key mismatch")
    builds = {(structure, day): 0 for structure in STRUCTURES for day in days}
    for build in result.structures:
        builds[build.structure, build.day] += build.quantity
        actions[build.day] += build.quantity
    animal_shed = list(data.shed_animals)
    structures = list(data.empty_structures)
    goods = list(data.goods)
    cash = data.cash
    previous_balance = None
    for day_index, day in enumerate(days):
        for structure_index, structure in enumerate(STRUCTURES):
            structures[structure_index] += builds[structure, day]
        for animal_index, animal in enumerate(ANIMALS):
            placements = animal_placements[animal, day]
            same_day_arrival = purchases_by_day[animal, day]
            if not _same_day_animal_arrival_reachable(data, day):
                same_day_arrival = 0
            if placements > animal_shed[animal_index] + same_day_arrival:
                errors.append("animal purchase latency mismatch")
            animal_shed[animal_index] += purchases_by_day[animal, day]
            animal_shed[animal_index] -= placements
            structure = ANIMAL_SPECS[animal].structure
            structure_index = STRUCTURES.index(structure)
            if placements > structures[structure_index]:
                errors.append("structure balance mismatch")
            structures[structure_index] -= placements
        occupied_tiles = len(data.existing_animals) + sum(data.empty_structures)
        occupied_tiles += sum(
            builds[structure, built_day]
            for structure in STRUCTURES
            for built_day in days
            if built_day <= day
        )
        if occupied_tiles > data.animal_tile_capacity[day_index]:
            errors.append("animal tile capacity exceeded")
        wheat_available = goods[0]
        if _same_day_wheat_arrival_reachable(data, day):
            wheat_available += purchases_by_day["WHEAT", day]
        if feeds[day] > wheat_available:
            errors.append("same-day wheat purchase used for feed")
        goods_start = tuple(goods)
        for item_index, item in enumerate(GOODS):
            available_for_sale = goods_start[item_index]
            available_for_sale += direct_harvests[item, day]
            if item == "FERTILIZER":
                available_for_sale += direct_collections[day]
            if item == "WHEAT":
                available_for_sale += purchases_by_day["WHEAT", day] - feeds[day]
            if sales_by_day[item, day] > available_for_sale:
                errors.append("same-day sale uses deferred goods")
        for item_index, item in enumerate(GOODS):
            goods[item_index] += harvests[item, day]
            if item == "FERTILIZER":
                goods[item_index] += collections[day]
            if item == "WHEAT":
                goods[item_index] += purchases_by_day["WHEAT", day] - feeds[day]
            goods[item_index] -= sales_by_day[item, day]
            if goods[item_index] < 0:
                errors.append("negative goods balance")
        active_orders = sum(
            purchases_by_day[animal, day] > 0 for animal in ANIMALS
        )
        active_orders += int(purchases_by_day["WHEAT", day] > 0)
        active_orders += sum(sales_by_day[item, day] > 0 for item in GOODS)
        if active_orders > data.market_order_slots[day_index]:
            errors.append("market order capacity exceeded")
        if actions[day] > data.action_capacity[day_index]:
            errors.append("action capacity exceeded")
        if (
            sum(goods)
            + sum(animal_shed)
            + data.fixed_shed_occupancy[day_index]
            > data.shed_capacity[day_index]
        ):
            errors.append("shed capacity exceeded")
        cash += data.fixed_cash_flow[day_index]
        cash -= purchase_cost[day]
        cash += sale_revenue[day]
        if cash < data.cash_reserve:
            errors.append("cash reserve violated")
        balance = result.balances[day_index]
        if (
            balance.day != day
            or balance.cash != cash
            or balance.goods != tuple(goods)
            or balance.shed_animals != tuple(animal_shed)
            or balance.empty_structures != tuple(structures)
        ):
            errors.append("reported daily balance mismatch")
        previous_balance = balance
    if previous_balance is None or result.terminal_cash != previous_balance.cash:
        errors.append("terminal cash mismatch")
    if result.terminal_goods != tuple(goods):
        errors.append("terminal goods mismatch")
    if result.terminal_shed_animals != tuple(animal_shed):
        errors.append("terminal animal inventory mismatch")
    terminal_value = _terminal_value(data, result.services, result.balances[-1])
    if terminal_value is None:
        if result.terminal_value is not None:
            errors.append("unexpected terminal value")
        if result.forecast_terminal_cash is not None:
            errors.append("unexpected forecast terminal cash")
    else:
        if result.terminal_value is None or not math.isclose(
            result.terminal_value,
            terminal_value,
        ):
            errors.append("terminal value mismatch")
        forecast_terminal_cash = result.terminal_cash + terminal_value
        if result.forecast_terminal_cash is None or not math.isclose(
            result.forecast_terminal_cash,
            forecast_terminal_cash,
        ):
            errors.append("forecast terminal cash mismatch")
    return tuple(errors)
