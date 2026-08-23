import hashlib
import json
import math
import time
from dataclasses import dataclass, fields, is_dataclass

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_array

from .crop_ledger import CROP_SPECS, scheduled_production_days
from .market_ledger import CROPS, DEFAULT_MARKET_PARAMS, market_price


LAST_DAY = 29
DEFAULT_TERMINAL_STEP = 718
SCENARIOS = frozenset({"no-future-opponent-orders-v1"})


@dataclass(frozen=True, slots=True)
class ExistingPlant:
    position: tuple[int, int]
    crop: str
    planted_day: int
    yield_units: int
    watered_today: bool
    consecutive_unwatered: int
    fertilized_until_day: int

    def __post_init__(self):
        if (
            type(self.position) is not tuple
            or len(self.position) != 2
            or any(type(value) is not int for value in self.position)
        ):
            raise TypeError("plant position must be an integer pair")
        if any(value < 0 or value >= 10 for value in self.position):
            raise ValueError("plant position must be on board")
        if type(self.crop) is not str or self.crop not in CROP_SPECS:
            raise ValueError("unknown crop")
        counters = (
            self.planted_day,
            self.yield_units,
            self.consecutive_unwatered,
            self.fertilized_until_day,
        )
        if any(type(value) is not int for value in counters):
            raise TypeError("plant counters must be integers")
        if self.planted_day < 0 or self.yield_units < 0:
            raise ValueError("plant counters must be nonnegative")
        if self.consecutive_unwatered < 0:
            raise ValueError("unwatered counter must be nonnegative")
        if type(self.watered_today) is not bool:
            raise TypeError("watered_today must be a boolean")


@dataclass(frozen=True, slots=True)
class CropTerminalValues:
    active_crops: tuple[float, ...]
    seeds: tuple[float, ...]
    goods: tuple[float, ...]
    fertilizer: float

    def __post_init__(self):
        _validate_float_vector(self.active_crops, len(CROPS), "active crop values")
        _validate_float_vector(self.seeds, len(CROPS), "seed values")
        _validate_float_vector(self.goods, len(CROPS), "goods values")
        values = (*self.active_crops, *self.seeds, *self.goods, self.fertilizer)
        if any(value < 0 for value in values):
            raise ValueError("terminal values must be nonnegative")


@dataclass(frozen=True, slots=True)
class OracleInput:
    source_step: int
    terminal_step: int
    cash: float
    cash_reserve: float
    seeds: tuple[int, ...]
    goods: tuple[int, ...]
    existing_plants: tuple[ExistingPlant, ...]
    tile_capacity: tuple[int, ...]
    action_capacity: tuple[int, ...]
    crop_storage_capacity: tuple[int, ...]
    wheat_demand: tuple[int, ...]
    fixed_cash_flow: tuple[float, ...]
    fertilizer_stock: int
    fertilizer_supply: tuple[int, ...]
    fertilizer_buy_price: tuple[float, ...]
    market_order_slots: tuple[int, ...]
    base_inventory: tuple[tuple[int, ...], ...]
    wheat_buy_price: tuple[float, ...]
    first_plant_day: int
    terminal_return_actions: int
    sale_unit_limit: int
    scenario: str
    terminal_values: CropTerminalValues | None = None

    def __post_init__(self):
        integer_scalars = (
            self.source_step,
            self.terminal_step,
            self.first_plant_day,
            self.terminal_return_actions,
            self.sale_unit_limit,
            self.fertilizer_stock,
        )
        if any(type(value) is not int for value in integer_scalars):
            raise TypeError("oracle integer settings must be integers")
        if self.source_step < 0 or self.source_step > DEFAULT_TERMINAL_STEP:
            raise ValueError("source step must be in 0..718")
        if self.terminal_step < self.source_step or self.terminal_step > 718:
            raise ValueError("terminal step must be in source_step..718")
        if self.first_plant_day < self.current_day or self.first_plant_day > self.last_day:
            raise ValueError("first plant day must be in the remaining horizon")
        if (
            self.terminal_return_actions < 0
            or self.sale_unit_limit < 1
            or self.fertilizer_stock < 0
        ):
            raise ValueError("terminal limits must be positive")
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
            CropTerminalValues,
        ):
            raise TypeError("terminal values must be CropTerminalValues")
        _validate_int_vector(self.seeds, len(CROPS), "seeds")
        _validate_int_vector(self.goods, len(CROPS), "goods")
        if type(self.existing_plants) is not tuple:
            raise TypeError("existing plants must be a tuple")
        if any(not isinstance(value, ExistingPlant) for value in self.existing_plants):
            raise TypeError("invalid existing plant")
        if len({value.position for value in self.existing_plants}) != len(
            self.existing_plants
        ):
            raise ValueError("existing plant positions must be unique")
        if any(value.planted_day > self.current_day for value in self.existing_plants):
            raise ValueError("existing plant cannot be from the future")
        horizon = self.horizon_days
        for name, values in (
            ("tile capacity", self.tile_capacity),
            ("action capacity", self.action_capacity),
            ("crop storage capacity", self.crop_storage_capacity),
            ("wheat demand", self.wheat_demand),
            ("fertilizer supply", self.fertilizer_supply),
            ("market order slots", self.market_order_slots),
        ):
            _validate_int_vector(values, horizon, name)
        _validate_float_vector(self.fixed_cash_flow, horizon, "fixed cash flow")
        _validate_float_vector(self.wheat_buy_price, horizon, "wheat buy price")
        _validate_float_vector(
            self.fertilizer_buy_price,
            horizon,
            "fertilizer buy price",
        )
        if any(value <= 0 for value in self.wheat_buy_price):
            raise ValueError("wheat buy prices must be positive")
        if any(value <= 0 for value in self.fertilizer_buy_price):
            raise ValueError("fertilizer buy prices must be positive")
        if type(self.base_inventory) is not tuple or len(self.base_inventory) != horizon:
            raise TypeError("base inventory must cover the horizon")
        for values in self.base_inventory:
            if type(values) is not tuple or len(values) != len(CROPS):
                raise TypeError("base inventory rows must cover crops")
            if any(type(value) is not int for value in values):
                raise TypeError("base inventory values must be integers")
        if sum(self.goods) > self.crop_storage_capacity[0]:
            raise ValueError("current goods exceed crop storage capacity")

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
class CropOption:
    identifier: str
    crop: str
    plant_day: int | None
    harvest_day: int
    sale_day: int
    yield_units: int
    active_days: tuple[int, ...]
    actions: tuple[int, ...]
    harvests: tuple[tuple[int, int], ...]
    fertilizer_days: tuple[int, ...]
    release_day: int | None
    existing_index: int | None


@dataclass(frozen=True, slots=True)
class CropDecision:
    crop: str
    plant_day: int | None
    harvest_day: int
    sale_day: int
    count: int
    yield_per_unit: int
    harvests: tuple[tuple[int, int], ...]
    fertilizer_days: tuple[int, ...]
    release_day: int | None
    existing_position: tuple[int, int] | None


@dataclass(frozen=True, slots=True)
class PurchaseDecision:
    item: str
    day: int
    quantity: int


@dataclass(frozen=True, slots=True)
class SaleDecision:
    crop: str
    day: int
    quantity: int
    revenue: float


@dataclass(frozen=True, slots=True)
class DayBalance:
    day: int
    cash: float
    seeds: tuple[int, ...]
    goods: tuple[int, ...]
    fertilizer: int


@dataclass(frozen=True, slots=True)
class OracleResult:
    success: bool
    status: int
    message: str
    mip_gap: float | None
    wall_seconds: float
    variable_count: int
    constraint_count: int
    terminal_cash: float | None
    incremental_crop_profit: float | None
    decisions: tuple[CropDecision, ...]
    purchases: tuple[PurchaseDecision, ...]
    sales: tuple[SaleDecision, ...]
    balances: tuple[DayBalance, ...]
    terminal_unsold_goods: tuple[int, ...] | None
    terminal_unsold_fertilizer: int | None
    scenario: str
    input_sha256: str
    terminal_value: float | None
    forecast_terminal_cash: float | None


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


def _terminal_feasible(data, harvest_day):
    harvest_step = max(data.source_step, harvest_day * 24)
    return harvest_step + data.terminal_return_actions <= data.terminal_step


def _actions(data, values):
    result = [0] * data.horizon_days
    for day, quantity in values.items():
        if data.current_day <= day <= data.last_day:
            result[day - data.current_day] += quantity
    return tuple(result)


def _new_one_time_options(data, crop):
    spec = CROP_SPECS[crop]
    result = []
    window_start = (spec.max_yield_day + 1) // 2
    for plant_day in range(data.first_plant_day, data.last_day + 1):
        for age in range(spec.first_yield_day, spec.max_yield_day + 1):
            harvest_day = plant_day + age
            if harvest_day > data.last_day or not _terminal_feasible(data, harvest_day):
                continue
            yield_units = min(
                spec.max_yield,
                1 + sum(window_start <= value <= age for value in range(age + 1)),
            )
            action_values = {day: 1 for day in range(plant_day, harvest_day + 1)}
            action_values[plant_day] += 1
            action_values[harvest_day] += 1 + data.terminal_return_actions
            result.append(
                CropOption(
                    f"new-{crop}-{plant_day}-{harvest_day}",
                    crop,
                    plant_day,
                    harvest_day,
                    harvest_day,
                    yield_units,
                    tuple(range(plant_day, harvest_day)),
                    _actions(data, action_values),
                    ((harvest_day, yield_units),),
                    (),
                    harvest_day,
                    None,
                )
            )
    return result


def _fertilizer_application_days(production_days, covered_until):
    refresh_days = tuple(day - 1 for day in production_days if day - 1 > covered_until)
    result = []
    covered = covered_until
    for day in refresh_days:
        if day <= covered:
            continue
        result.append(day)
        covered = day + 2
    return tuple(result)


def _ongoing_schedule_options(
    data,
    crop,
    plant_day,
    initial_yield,
    watered_today,
    covered_until,
    existing_index,
):
    all_productions = scheduled_production_days(crop, plant_day, data.last_day)
    production_days = tuple(day for day in all_productions if day > data.current_day)
    candidates = tuple(
        ([data.current_day] if initial_yield > 0 else [])
        + [day for day in production_days if _terminal_feasible(data, day)]
    )
    if not candidates:
        return []
    applications = _fertilizer_application_days(production_days, covered_until)
    active_start = max(data.current_day, plant_day)
    result = {}
    for fertilizer_mask in range(1 << len(applications)):
        fertilizer_days = tuple(
            day
            for index, day in enumerate(applications)
            if fertilizer_mask & (1 << index)
        )
        production_yields = {
            day: 2
            if day - 1 <= covered_until
            or any(start <= day - 1 <= start + 2 for start in fertilizer_days)
            else 1
            for day in production_days
        }
        for harvest_mask in range(1, 1 << len(candidates)):
            selected_days = {
                day
                for index, day in enumerate(candidates)
                if harvest_mask & (1 << index)
            }
            held = initial_yield
            harvests = []
            for day in sorted({data.current_day, *production_days}):
                held = min(
                    CROP_SPECS[crop].max_yield,
                    held + production_yields.get(day, 0),
                )
                if day in selected_days and held:
                    harvests.append((day, held))
                    held = 0
            if not harvests:
                continue
            final_production_day = max(all_productions, default=data.current_day)
            release_day = (
                final_production_day + 1
                if harvests[-1][0] == final_production_day
                and held == 0
                and final_production_day < data.last_day
                else None
            )
            active_days = (
                tuple(range(plant_day, release_day or data.last_day + 1))
                if existing_index is None
                else ()
            )
            final_water_day = max(
                production_days,
                default=data.current_day,
            )
            water_stop = final_water_day if release_day is not None else final_water_day + 1
            action_values = {
                day: 1
                for day in range(
                    active_start,
                    water_stop,
                )
                if not (day == data.current_day and watered_today)
            }
            if existing_index is None:
                action_values[plant_day] = action_values.get(plant_day, 0) + 1
            for day in fertilizer_days:
                action_values[day] = action_values.get(day, 0) + 1
            for day, _quantity in harvests:
                action_values[day] = (
                    action_values.get(day, 0) + 1 + data.terminal_return_actions
                )
            if release_day is not None:
                action_values[release_day] = action_values.get(release_day, 0) + 1
            key = (tuple(harvests), fertilizer_days)
            identifier = (
                f"{'existing-' + str(existing_index) if existing_index is not None else 'new'}-"
                f"{crop}-{plant_day}-h"
                + "-".join(f"{day}x{quantity}" for day, quantity in harvests)
                + "-f"
                + "-".join(str(day) for day in fertilizer_days)
            )
            result[key] = CropOption(
                identifier,
                crop,
                None if existing_index is not None else plant_day,
                harvests[-1][0],
                harvests[-1][0],
                sum(quantity for _day, quantity in harvests),
                active_days,
                _actions(data, action_values),
                tuple(harvests),
                fertilizer_days,
                release_day,
                existing_index,
            )
    return list(result.values())


def _new_ongoing_options(data, crop):
    result = []
    for plant_day in range(data.first_plant_day, data.last_day + 1):
        result.extend(
            _ongoing_schedule_options(
                data,
                crop,
                plant_day,
                0,
                False,
                -1,
                None,
            )
        )
    return result


def _existing_one_time_options(data, existing_index, plant):
    spec = CROP_SPECS[plant.crop]
    first_day = max(data.current_day, plant.planted_day + spec.first_yield_day)
    final_growth_day = plant.planted_day + spec.max_yield_day
    last_day = min(data.last_day, max(data.current_day, final_growth_day + 1))
    window_start = (spec.max_yield_day + 1) // 2
    result = []
    for harvest_day in range(first_day, last_day + 1):
        if not _terminal_feasible(data, harvest_day):
            continue
        action_values = {}
        yield_units = plant.yield_units
        for day in range(data.current_day, harvest_day + 1):
            if day > final_growth_day:
                continue
            if day == data.current_day and plant.watered_today:
                continue
            action_values[day] = action_values.get(day, 0) + 1
            age = day - plant.planted_day
            if window_start <= age <= spec.max_yield_day:
                bonus = 2 if plant.fertilized_until_day >= day else 1
                yield_units = min(spec.max_yield, yield_units + bonus)
        action_values[harvest_day] = (
            action_values.get(harvest_day, 0)
            + 1
            + data.terminal_return_actions
        )
        result.append(
            CropOption(
                f"existing-{existing_index}-{plant.crop}-{harvest_day}",
                plant.crop,
                None,
                harvest_day,
                harvest_day,
                yield_units,
                (),
                _actions(data, action_values),
                ((harvest_day, yield_units),),
                (),
                harvest_day,
                existing_index,
            )
        )
    return result


def _existing_ongoing_options(data, existing_index, plant):
    return _ongoing_schedule_options(
        data,
        plant.crop,
        plant.planted_day,
        plant.yield_units,
        plant.watered_today,
        plant.fertilized_until_day,
        existing_index,
    )


def _pipeline_option(data, crop, plant_day, existing_index, watered_today):
    active_start = max(data.current_day, plant_day)
    action_values = {
        day: 1
        for day in range(active_start, data.last_day + 1)
        if not (day == data.current_day and watered_today)
    }
    if existing_index is None:
        action_values[plant_day] = action_values.get(plant_day, 0) + 1
    prefix = "new" if existing_index is None else f"existing-{existing_index}"
    return CropOption(
        f"{prefix}-{crop}-{plant_day}-pipeline-{data.last_day}",
        crop,
        plant_day if existing_index is None else None,
        data.last_day,
        data.last_day,
        0,
        tuple(range(plant_day, data.last_day + 1))
        if existing_index is None
        else (),
        _actions(data, action_values),
        (),
        (),
        None,
        existing_index,
    )


def _pipeline_options(data):
    if data.terminal_values is None or data.last_day >= LAST_DAY:
        return ()
    result = []
    for crop in CROPS:
        spec = CROP_SPECS[crop]
        for plant_day in range(data.first_plant_day, data.last_day + 1):
            if plant_day + spec.first_yield_day <= LAST_DAY:
                result.append(
                    _pipeline_option(data, crop, plant_day, None, False)
                )
    for index, plant in enumerate(data.existing_plants):
        result.append(
            _pipeline_option(
                data,
                plant.crop,
                plant.planted_day,
                index,
                plant.watered_today,
            )
        )
    return tuple(result)


def _active_crop_terminal_value(data, option):
    if data.terminal_values is None or option.release_day is not None:
        return 0.0
    plant_day = option.plant_day
    if option.existing_index is not None:
        plant_day = data.existing_plants[option.existing_index].planted_day
    progress_days = max(0, data.last_day - plant_day + 1)
    maturity_days = max(1, CROP_SPECS[option.crop].first_yield_day)
    progress = min(1.0, progress_days / maturity_days)
    return data.terminal_values.active_crops[CROPS.index(option.crop)] * progress


def generate_crop_options(data):
    if not isinstance(data, OracleInput):
        raise TypeError("data must be an OracleInput")
    options = []
    for crop in CROPS:
        if CROP_SPECS[crop].ongoing:
            options.extend(_new_ongoing_options(data, crop))
        else:
            options.extend(_new_one_time_options(data, crop))
    for index, plant in enumerate(data.existing_plants):
        if CROP_SPECS[plant.crop].ongoing:
            options.extend(_existing_ongoing_options(data, index, plant))
        else:
            options.extend(_existing_one_time_options(data, index, plant))
    options.extend(_pipeline_options(data))
    return tuple(options)


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
        row_values = []
        columns = []
        data = []
        for row, values in enumerate(self.rows):
            for column, value in values.items():
                if value:
                    row_values.append(row)
                    columns.append(column)
                    data.append(float(value))
        matrix = coo_array(
            (data, (row_values, columns)),
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


def _marginal_prices(data):
    result = {}
    for day_index in range(data.horizon_days):
        day = data.current_day + day_index
        for crop_index, crop in enumerate(CROPS):
            base = data.base_inventory[day_index][crop_index]
            values = tuple(
                market_price(crop, base + unit, DEFAULT_MARKET_PARAMS)
                for unit in range(data.sale_unit_limit)
            )
            if any(left < right for left, right in zip(values, values[1:])):
                raise ValueError("marginal sale prices must be nonincreasing")
            result[(crop, day)] = values
    return result


def _validate_first_day_counts(first_day_crop_counts):
    if first_day_crop_counts is None:
        return None
    if type(first_day_crop_counts) is not tuple or len(first_day_crop_counts) != len(
        CROPS
    ):
        raise TypeError("first-day crop counts must cover crops")
    if any(type(value) is not int for value in first_day_crop_counts):
        raise TypeError("first-day crop counts must be integers")
    if any(value < 0 for value in first_day_crop_counts):
        raise ValueError("first-day crop counts must be nonnegative")
    return first_day_crop_counts


def _build_model(data, options, first_day_crop_counts=None):
    first_day_crop_counts = _validate_first_day_counts(first_day_crop_counts)
    builder = _Builder()
    max_tiles = max(data.tile_capacity, default=0)
    releasable_existing = {
        option.existing_index
        for option in options
        if option.existing_index is not None and option.release_day is not None
    }
    new_tile_limit = max_tiles + len(releasable_existing)
    quantity_limit = max(
        1,
        (sum(data.tile_capacity) + new_tile_limit) * 6
        + sum(data.goods)
        + sum(data.seeds)
        + sum(data.wheat_demand)
        + data.fertilizer_stock
        + sum(data.fertilizer_supply),
    )
    new_options = [option for option in options if option.existing_index is None]
    existing_options = [option for option in options if option.existing_index is not None]
    option_vars = {}
    for option in new_options:
        option_vars[option.identifier] = builder.variable(
            ("option", option.identifier),
            objective=-_active_crop_terminal_value(data, option),
            upper=new_tile_limit,
        )
    for option in existing_options:
        option_vars[option.identifier] = builder.variable(
            ("existing", option.identifier),
            objective=-_active_crop_terminal_value(data, option),
            upper=1,
        )
    seed_buy = {}
    seed_on = {}
    goods_balance = {}
    seed_balance = {}
    wheat_buy = {}
    wheat_on = {}
    fertilizer_buy = {}
    fertilizer_on = {}
    fertilizer_balance = {}
    sale_units = {}
    sale_on = {}
    marginal_prices = _marginal_prices(data)
    for day_index in range(data.horizon_days):
        day = data.current_day + day_index
        for crop_index, crop in enumerate(CROPS):
            seed_terminal_value = 0.0
            goods_terminal_value = 0.0
            if data.terminal_values is not None and day == data.last_day:
                seed_terminal_value = data.terminal_values.seeds[crop_index]
                goods_terminal_value = data.terminal_values.goods[crop_index]
            seed_buy[crop, day] = builder.variable(
                ("seed_buy", crop, day),
                objective=CROP_SPECS[crop].seed,
                upper=quantity_limit,
            )
            seed_on[crop, day] = builder.variable(
                ("seed_on", crop, day),
                upper=1,
            )
            seed_balance[crop, day] = builder.variable(
                ("seed_balance", crop, day),
                objective=-seed_terminal_value,
                upper=quantity_limit,
            )
            goods_balance[crop, day] = builder.variable(
                ("goods_balance", crop, day),
                objective=-goods_terminal_value,
                upper=quantity_limit * 6,
            )
            sale_on[crop, day] = builder.variable(
                ("sale_on", crop, day),
                upper=1,
            )
            for unit, price in enumerate(marginal_prices[crop, day]):
                sale_units[crop, day, unit] = builder.variable(
                    ("sale", crop, day, unit),
                    objective=-price,
                    upper=1,
                )
        wheat_buy[day] = builder.variable(
            ("wheat_buy", day),
            objective=data.wheat_buy_price[day_index],
            upper=quantity_limit,
        )
        wheat_on[day] = builder.variable(("wheat_on", day), upper=1)
        fertilizer_buy[day] = builder.variable(
            ("fertilizer_buy", day),
            objective=data.fertilizer_buy_price[day_index],
            upper=quantity_limit,
        )
        fertilizer_on[day] = builder.variable(("fertilizer_on", day), upper=1)
        fertilizer_terminal_value = 0.0
        if data.terminal_values is not None and day == data.last_day:
            fertilizer_terminal_value = data.terminal_values.fertilizer
        fertilizer_balance[day] = builder.variable(
            ("fertilizer_balance", day),
            objective=-fertilizer_terminal_value,
            upper=quantity_limit,
        )
    for existing_index in range(len(data.existing_plants)):
        values = {
            option_vars[option.identifier]: 1
            for option in existing_options
            if option.existing_index == existing_index
        }
        if values:
            builder.constraint(values, upper=1)
    if first_day_crop_counts is not None:
        for crop, count in zip(CROPS, first_day_crop_counts):
            values = {
                option_vars[option.identifier]: 1
                for option in new_options
                if option.crop == crop and option.plant_day == data.first_plant_day
            }
            builder.constraint(values, lower=count, upper=count)
    builder.constraint(
        {wheat_buy[day]: 1 for day in range(data.current_day, data.last_day + 1)},
        upper=sum(data.wheat_demand),
    )
    for day_index in range(data.horizon_days):
        day = data.current_day + day_index
        occupancy = {}
        action_values = {}
        for option in options:
            variable = option_vars[option.identifier]
            if option.existing_index is None and day in option.active_days:
                occupancy[variable] = occupancy.get(variable, 0) + 1
            if (
                option.existing_index is not None
                and option.release_day is not None
                and option.release_day <= day
            ):
                occupancy[variable] = occupancy.get(variable, 0) - 1
            action_count = option.actions[day_index]
            if action_count:
                action_values[variable] = action_count
        builder.constraint(occupancy, upper=data.tile_capacity[day_index])
        builder.constraint(action_values, upper=data.action_capacity[day_index])
        storage_values = {goods_balance[crop, day]: 1 for crop in CROPS}
        storage_values[fertilizer_balance[day]] = 1
        builder.constraint(
            storage_values,
            upper=data.crop_storage_capacity[day_index],
        )
        order_values = {wheat_on[day]: 1, fertilizer_on[day]: 1}
        for crop in CROPS:
            order_values[seed_on[crop, day]] = 1
            order_values[sale_on[crop, day]] = 1
            buy = seed_buy[crop, day]
            active = seed_on[crop, day]
            builder.constraint({buy: 1, active: -quantity_limit}, upper=0)
            builder.constraint({buy: 1, active: -1}, lower=0)
            sales = {
                sale_units[crop, day, unit]: 1
                for unit in range(data.sale_unit_limit)
            }
            sales[active := sale_on[crop, day]] = -data.sale_unit_limit
            builder.constraint(sales, upper=0)
            lower_sales = {
                sale_units[crop, day, unit]: 1
                for unit in range(data.sale_unit_limit)
            }
            lower_sales[active] = -1
            builder.constraint(lower_sales, lower=0)
        builder.constraint(
            {
                wheat_buy[day]: 1,
                wheat_on[day]: -quantity_limit,
            },
            upper=0,
        )
        builder.constraint(
            {wheat_buy[day]: 1, wheat_on[day]: -1},
            lower=0,
        )
        builder.constraint(
            {
                fertilizer_buy[day]: 1,
                fertilizer_on[day]: -quantity_limit,
            },
            upper=0,
        )
        builder.constraint(
            {fertilizer_buy[day]: 1, fertilizer_on[day]: -1},
            lower=0,
        )
        builder.constraint(order_values, upper=data.market_order_slots[day_index])
        fertilizer_values = {
            fertilizer_balance[day]: 1,
            fertilizer_buy[day]: -1,
        }
        if day_index:
            fertilizer_values[fertilizer_balance[day - 1]] = -1
            fertilizer_rhs = data.fertilizer_supply[day_index]
        else:
            fertilizer_rhs = (
                data.fertilizer_stock + data.fertilizer_supply[day_index]
            )
        for option in options:
            if day in option.fertilizer_days:
                variable = option_vars[option.identifier]
                fertilizer_values[variable] = (
                    fertilizer_values.get(variable, 0) + 1
                )
        builder.constraint(
            fertilizer_values,
            lower=fertilizer_rhs,
            upper=fertilizer_rhs,
        )
    for crop_index, crop in enumerate(CROPS):
        for day_index in range(data.horizon_days):
            day = data.current_day + day_index
            seed_values = {
                seed_balance[crop, day]: 1,
                seed_buy[crop, day]: -1,
            }
            if day_index:
                seed_values[seed_balance[crop, day - 1]] = -1
                seed_rhs = 0
            else:
                seed_rhs = data.seeds[crop_index]
            for option in new_options:
                if option.crop == crop and option.plant_day == day:
                    variable = option_vars[option.identifier]
                    seed_values[variable] = seed_values.get(variable, 0) + 1
            builder.constraint(seed_values, lower=seed_rhs, upper=seed_rhs)
            goods_values = {goods_balance[crop, day]: 1}
            if day_index:
                goods_values[goods_balance[crop, day - 1]] = -1
                goods_rhs = -(
                    data.wheat_demand[day_index] if crop == "WHEAT" else 0
                )
            else:
                goods_rhs = data.goods[crop_index] - (
                    data.wheat_demand[day_index] if crop == "WHEAT" else 0
                )
            if crop == "WHEAT":
                goods_values[wheat_buy[day]] = -1
            for option in options:
                if option.crop != crop:
                    continue
                produced = sum(
                    quantity
                    for harvest_day, quantity in option.harvests
                    if harvest_day == day
                )
                if produced:
                    variable = option_vars[option.identifier]
                    goods_values[variable] = (
                        goods_values.get(variable, 0) - produced
                    )
            for unit in range(data.sale_unit_limit):
                goods_values[sale_units[crop, day, unit]] = 1
            builder.constraint(goods_values, lower=goods_rhs, upper=goods_rhs)
    cumulative_cash = {}
    fixed_cash = 0.0
    for day_index in range(data.horizon_days):
        day = data.current_day + day_index
        fixed_cash += data.fixed_cash_flow[day_index]
        for crop in CROPS:
            cumulative_cash[seed_buy[crop, day]] = CROP_SPECS[crop].seed
            for unit, price in enumerate(marginal_prices[crop, day]):
                cumulative_cash[sale_units[crop, day, unit]] = -price
        cumulative_cash[wheat_buy[day]] = data.wheat_buy_price[day_index]
        cumulative_cash[fertilizer_buy[day]] = data.fertilizer_buy_price[day_index]
        builder.constraint(
            cumulative_cash,
            upper=data.cash + fixed_cash - data.cash_reserve,
        )
    return (
        builder,
        option_vars,
        seed_buy,
        wheat_buy,
        fertilizer_buy,
        sale_units,
        seed_balance,
        goods_balance,
        fertilizer_balance,
        marginal_prices,
    )


def _integer(value):
    return int(round(float(value)))


def solve_oracle(
    data,
    time_limit=120.0,
    mip_rel_gap=0.0,
    first_day_crop_counts=None,
    accept_feasible=False,
):
    if not isinstance(data, OracleInput):
        raise TypeError("data must be an OracleInput")
    if type(time_limit) not in (int, float) or time_limit <= 0:
        raise ValueError("time limit must be positive")
    if type(mip_rel_gap) not in (int, float) or not 0 <= mip_rel_gap < 1:
        raise ValueError("MIP gap must be in 0..1")
    if type(accept_feasible) is not bool:
        raise TypeError("feasible acceptance must be a boolean")
    first_day_crop_counts = _validate_first_day_counts(first_day_crop_counts)
    options = generate_crop_options(data)
    built = _build_model(data, options, first_day_crop_counts)
    (
        builder,
        option_vars,
        seed_buy,
        wheat_buy,
        fertilizer_buy,
        sale_units,
        seed_balance,
        goods_balance,
        fertilizer_balance,
        marginal_prices,
    ) = built
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
    success = bool(solved.x is not None and (solved.success or accept_feasible))
    gap = getattr(solved, "mip_gap", None)
    if gap is not None and math.isfinite(float(gap)):
        gap = float(gap)
    else:
        gap = None
    if not success:
        return OracleResult(
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
            None,
            None,
            data.scenario,
            input_sha256(data),
            None,
            None,
        )
    values = solved.x
    decisions = []
    for option in options:
        count = _integer(values[option_vars[option.identifier]])
        if count <= 0:
            continue
        position = None
        if option.existing_index is not None:
            position = data.existing_plants[option.existing_index].position
        decisions.append(
            CropDecision(
                option.crop,
                option.plant_day,
                option.harvest_day,
                option.sale_day,
                count,
                option.yield_units,
                option.harvests,
                option.fertilizer_days,
                option.release_day,
                position,
            )
        )
    purchases = []
    sales = []
    balances = []
    cumulative_cost = 0.0
    cumulative_revenue = 0.0
    cumulative_fixed = 0.0
    fertilizer = data.fertilizer_stock
    for day_index in range(data.horizon_days):
        day = data.current_day + day_index
        for crop in CROPS:
            quantity = _integer(values[seed_buy[crop, day]])
            if quantity:
                purchases.append(PurchaseDecision(f"{crop}_SEED", day, quantity))
                cumulative_cost += quantity * CROP_SPECS[crop].seed
        wheat_quantity = _integer(values[wheat_buy[day]])
        if wheat_quantity:
            purchases.append(PurchaseDecision("WHEAT", day, wheat_quantity))
            cumulative_cost += wheat_quantity * data.wheat_buy_price[day_index]
        fertilizer_quantity = _integer(values[fertilizer_buy[day]])
        if fertilizer_quantity:
            purchases.append(
                PurchaseDecision("FERTILIZER", day, fertilizer_quantity)
            )
            cumulative_cost += (
                fertilizer_quantity * data.fertilizer_buy_price[day_index]
            )
        for crop in CROPS:
            selected = [
                unit
                for unit in range(data.sale_unit_limit)
                if _integer(values[sale_units[crop, day, unit]])
            ]
            if selected:
                revenue = sum(marginal_prices[crop, day][: len(selected)])
                cumulative_revenue += revenue
                sales.append(SaleDecision(crop, day, len(selected), revenue))
        cumulative_fixed += data.fixed_cash_flow[day_index]
        fertilizer = _integer(values[fertilizer_balance[day]])
        cash = data.cash + cumulative_fixed - cumulative_cost + cumulative_revenue
        balances.append(
            DayBalance(
                day,
                cash,
                tuple(
                    _integer(values[seed_balance[crop, day]]) for crop in CROPS
                ),
                tuple(
                    _integer(values[goods_balance[crop, day]]) for crop in CROPS
                ),
                fertilizer,
            )
        )
    terminal_cash = balances[-1].cash
    incremental = terminal_cash - data.cash - sum(data.fixed_cash_flow)
    terminal_value = None
    if data.terminal_values is not None:
        terminal_value = sum(
            _integer(values[option_vars[option.identifier]])
            * _active_crop_terminal_value(data, option)
            for option in options
            if option.release_day is None
        )
        terminal_value += sum(
            quantity * value
            for quantity, value in zip(
                balances[-1].seeds,
                data.terminal_values.seeds,
            )
        )
        terminal_value += sum(
            quantity * value
            for quantity, value in zip(
                balances[-1].goods,
                data.terminal_values.goods,
            )
        )
        terminal_value += balances[-1].fertilizer * data.terminal_values.fertilizer
    return OracleResult(
        True,
        int(solved.status),
        str(solved.message),
        gap,
        wall_seconds,
        len(builder.keys),
        len(builder.rows),
        terminal_cash,
        incremental,
        tuple(decisions),
        tuple(purchases),
        tuple(sales),
        tuple(balances),
        balances[-1].goods,
        balances[-1].fertilizer,
        data.scenario,
        input_sha256(data),
        terminal_value,
        terminal_cash + terminal_value if terminal_value is not None else None,
    )


def verify_result(data, result, first_day_crop_counts=None):
    if not isinstance(data, OracleInput):
        raise TypeError("data must be an OracleInput")
    if not isinstance(result, OracleResult):
        raise TypeError("result must be an OracleResult")
    first_day_crop_counts = _validate_first_day_counts(first_day_crop_counts)
    errors = []
    if result.input_sha256 != input_sha256(data):
        errors.append("input hash mismatch")
    if result.scenario != data.scenario:
        errors.append("scenario mismatch")
    if not result.success:
        return tuple(errors)
    options = generate_crop_options(data)
    option_keys = {}
    for option in options:
        position = None
        if option.existing_index is not None:
            position = data.existing_plants[option.existing_index].position
        key = (
            option.crop,
            option.plant_day,
            option.harvest_day,
            option.sale_day,
            option.yield_units,
            option.harvests,
            option.fertilizer_days,
            option.release_day,
            position,
        )
        option_keys[key] = option
    selected = []
    for decision in result.decisions:
        key = (
            decision.crop,
            decision.plant_day,
            decision.harvest_day,
            decision.sale_day,
            decision.yield_per_unit,
            decision.harvests,
            decision.fertilizer_days,
            decision.release_day,
            decision.existing_position,
        )
        option = option_keys.get(key)
        if option is None:
            errors.append("unknown crop decision")
            continue
        if type(decision.count) is not int or decision.count <= 0:
            errors.append("invalid crop decision count")
            continue
        selected.append((option, decision.count))
    if first_day_crop_counts is not None:
        actual = tuple(
            sum(
                count
                for option, count in selected
                if option.crop == crop
                and option.plant_day == data.first_plant_day
                and option.existing_index is None
            )
            for crop in CROPS
        )
        if actual != first_day_crop_counts:
            errors.append("first-day crop counts mismatch")
    existing_counts = {index: 0 for index in range(len(data.existing_plants))}
    seed_buys = {}
    wheat_buys = {}
    fertilizer_buys = {}
    for purchase in result.purchases:
        if purchase.day < data.current_day or purchase.day > data.last_day:
            errors.append("purchase day outside horizon")
            continue
        if type(purchase.quantity) is not int or purchase.quantity <= 0:
            errors.append("invalid purchase quantity")
            continue
        if purchase.item == "WHEAT":
            wheat_buys[purchase.day] = (
                wheat_buys.get(purchase.day, 0) + purchase.quantity
            )
        elif purchase.item == "FERTILIZER":
            fertilizer_buys[purchase.day] = (
                fertilizer_buys.get(purchase.day, 0) + purchase.quantity
            )
        elif purchase.item.endswith("_SEED"):
            crop = purchase.item.removesuffix("_SEED")
            if crop not in CROPS:
                errors.append("unknown seed purchase")
                continue
            seed_buys[crop, purchase.day] = (
                seed_buys.get((crop, purchase.day), 0) + purchase.quantity
            )
        else:
            errors.append("unknown purchase item")
    sales = {}
    sale_revenue = {}
    prices = _marginal_prices(data)
    for sale in result.sales:
        if sale.crop not in CROPS:
            errors.append("unknown sale crop")
            continue
        if sale.day < data.current_day or sale.day > data.last_day:
            errors.append("sale day outside horizon")
            continue
        if type(sale.quantity) is not int or not 0 < sale.quantity <= data.sale_unit_limit:
            errors.append("invalid sale quantity")
            continue
        key = (sale.crop, sale.day)
        sales[key] = sales.get(key, 0) + sale.quantity
        expected_revenue = sum(prices[key][: sale.quantity])
        sale_revenue[key] = sale_revenue.get(key, 0.0) + sale.revenue
        if not math.isclose(sale.revenue, expected_revenue, abs_tol=1e-7):
            errors.append("sale revenue mismatch")
    seed_balance = list(data.seeds)
    goods_balance = list(data.goods)
    fertilizer_balance = data.fertilizer_stock
    cash = data.cash
    calculated_balances = []
    for day_index in range(data.horizon_days):
        day = data.current_day + day_index
        occupancy = 0
        released_tiles = 0
        actions = 0
        for option, count in selected:
            if option.existing_index is None and day in option.active_days:
                occupancy += count
            if (
                option.existing_index is not None
                and option.release_day is not None
                and option.release_day <= day
            ):
                released_tiles += count
            actions += option.actions[day_index] * count
            if option.existing_index is not None:
                existing_counts[option.existing_index] += count if day_index == 0 else 0
            if option.plant_day == day:
                seed_balance[CROPS.index(option.crop)] -= count
            produced = sum(
                quantity
                for harvest_day, quantity in option.harvests
                if harvest_day == day
            )
            goods_balance[CROPS.index(option.crop)] += produced * count
            if day in option.fertilizer_days:
                fertilizer_balance -= count
        if occupancy - released_tiles > data.tile_capacity[day_index]:
            errors.append("tile capacity exceeded")
        if actions > data.action_capacity[day_index]:
            errors.append("action capacity exceeded")
        orders = 0
        for crop_index, crop in enumerate(CROPS):
            bought = seed_buys.get((crop, day), 0)
            if bought:
                orders += 1
                seed_balance[crop_index] += bought
                cash -= bought * CROP_SPECS[crop].seed
        wheat = wheat_buys.get(day, 0)
        if wheat:
            orders += 1
            goods_balance[CROPS.index("WHEAT")] += wheat
            cash -= wheat * data.wheat_buy_price[day_index]
        bought_fertilizer = fertilizer_buys.get(day, 0)
        if bought_fertilizer:
            orders += 1
            fertilizer_balance += bought_fertilizer
            cash -= bought_fertilizer * data.fertilizer_buy_price[day_index]
        fertilizer_balance += data.fertilizer_supply[day_index]
        goods_balance[CROPS.index("WHEAT")] -= data.wheat_demand[day_index]
        for crop_index, crop in enumerate(CROPS):
            quantity = sales.get((crop, day), 0)
            if quantity:
                orders += 1
                goods_balance[crop_index] -= quantity
                cash += sale_revenue[crop, day]
        cash += data.fixed_cash_flow[day_index]
        if orders > data.market_order_slots[day_index]:
            errors.append("market order capacity exceeded")
        if any(value < 0 for value in seed_balance):
            errors.append("negative seed balance")
        if any(value < 0 for value in goods_balance):
            errors.append("negative goods balance")
        if fertilizer_balance < 0:
            errors.append("negative fertilizer balance")
        if (
            sum(goods_balance) + fertilizer_balance
            > data.crop_storage_capacity[day_index]
        ):
            errors.append("crop storage capacity exceeded")
        if cash + 1e-7 < data.cash_reserve:
            errors.append("cash reserve violated")
        calculated_balances.append(
            DayBalance(
                day,
                cash,
                tuple(seed_balance),
                tuple(goods_balance),
                fertilizer_balance,
            )
        )
    if any(value > 1 for value in existing_counts.values()):
        errors.append("existing plant selected more than once")
    if tuple(calculated_balances) != result.balances:
        errors.append("reported balances mismatch")
    if result.terminal_unsold_goods != tuple(goods_balance):
        errors.append("terminal goods mismatch")
    if result.terminal_unsold_fertilizer != fertilizer_balance:
        errors.append("terminal fertilizer mismatch")
    if result.terminal_cash is None or not math.isclose(
        result.terminal_cash,
        cash,
        abs_tol=1e-7,
    ):
        errors.append("terminal cash mismatch")
    expected_profit = cash - data.cash - sum(data.fixed_cash_flow)
    if result.incremental_crop_profit is None or not math.isclose(
        result.incremental_crop_profit,
        expected_profit,
        abs_tol=1e-7,
    ):
        errors.append("incremental profit mismatch")
    terminal_value = None
    if data.terminal_values is not None:
        terminal_value = sum(
            count * _active_crop_terminal_value(data, option)
            for option, count in selected
            if option.release_day is None
        )
        terminal_value += sum(
            quantity * value
            for quantity, value in zip(seed_balance, data.terminal_values.seeds)
        )
        terminal_value += sum(
            quantity * value
            for quantity, value in zip(goods_balance, data.terminal_values.goods)
        )
        terminal_value += fertilizer_balance * data.terminal_values.fertilizer
    if terminal_value is None:
        if result.terminal_value is not None:
            errors.append("unexpected terminal value")
        if result.forecast_terminal_cash is not None:
            errors.append("unexpected forecast terminal cash")
    else:
        if result.terminal_value is None or not math.isclose(
            result.terminal_value,
            terminal_value,
            abs_tol=1e-7,
        ):
            errors.append("terminal value mismatch")
        if result.forecast_terminal_cash is None or not math.isclose(
            result.forecast_terminal_cash,
            cash + terminal_value,
            abs_tol=1e-7,
        ):
            errors.append("forecast terminal cash mismatch")
    return tuple(errors)
