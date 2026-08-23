import hashlib
import json
import math
from dataclasses import asdict, dataclass

from .market_ledger import PRODUCTS, SHOP_DEMAND, TOWN_CENTER_PRODUCTS


DEFAULT_TERMINAL_STEP = 718


@dataclass(frozen=True, slots=True)
class ShopForecastInput:
    source_step: int
    open_shops: tuple[str, ...]
    terminal_step: int = DEFAULT_TERMINAL_STEP
    turns_per_day: int = 24
    shop_unlock_interval_days: int = 3
    shop_sell_interval_steps: int = 4
    center_sell_interval_steps: int = 24
    max_shops: int = 8

    def __post_init__(self):
        values = (
            self.source_step,
            self.terminal_step,
            self.turns_per_day,
            self.shop_unlock_interval_days,
            self.shop_sell_interval_steps,
            self.center_sell_interval_steps,
            self.max_shops,
        )
        if any(type(value) is not int for value in values):
            raise TypeError("forecast settings must be integers")
        if self.source_step < 0 or self.source_step > DEFAULT_TERMINAL_STEP:
            raise ValueError("source step must be in 0..718")
        if self.terminal_step < self.source_step or self.terminal_step > 718:
            raise ValueError("terminal step must be in source_step..718")
        if min(values[2:]) < 1:
            raise ValueError("forecast intervals and limits must be positive")
        if self.max_shops != 8:
            raise ValueError("shop cap must equal the simulator limit")
        if type(self.open_shops) is not tuple:
            raise TypeError("open shops must be a tuple")
        if len(self.open_shops) > self.max_shops:
            raise ValueError("open shops exceed the shop cap")
        if any(shop not in SHOP_DEMAND for shop in self.open_shops):
            raise ValueError("unknown shop")

    @property
    def current_day(self):
        return self.source_step // self.turns_per_day

    @property
    def terminal_day(self):
        return self.terminal_step // self.turns_per_day


@dataclass(frozen=True, slots=True)
class ShopScenario:
    name: str
    probability: float
    next_shop: str | None
    drain_by_step: tuple[tuple[float, ...], ...]
    drain_by_day: tuple[tuple[float, ...], ...]
    total_drain: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ShopForecastResult:
    source_step: int
    terminal_step: int
    open_shop_signature: tuple[tuple[str, int], ...]
    next_daily_replan_step: int | None
    next_shop_replan_step: int | None
    action_end_step: int
    strategy_end_step: int
    investment_end_step: int
    scenarios: tuple[ShopScenario, ...]
    expected_drain_by_step: tuple[tuple[float, ...], ...]
    expected_drain_by_day: tuple[tuple[float, ...], ...]
    expected_total_drain: tuple[float, ...]
    input_hash: str


def shop_signature(shops):
    if type(shops) is not tuple:
        raise TypeError("shops must be a tuple")
    if any(shop not in SHOP_DEMAND for shop in shops):
        raise ValueError("unknown shop")
    return tuple((shop, shops.count(shop)) for shop in sorted(set(shops)))


def needs_shop_replan(previous_shops, current_shops):
    return shop_signature(previous_shops) != shop_signature(current_shops)


def _shop_vector(shop):
    demand = SHOP_DEMAND[shop]
    quantity = 2.0 if len(demand) == 1 else 1.0
    return tuple(quantity if item in demand else 0.0 for item in PRODUCTS)


def _expected_shop_vector():
    vectors = tuple(_shop_vector(shop) for shop in sorted(SHOP_DEMAND))
    return tuple(
        sum(vector[index] for vector in vectors) / len(vectors)
        for index in range(len(PRODUCTS))
    )


def _add(left, right):
    return tuple(a + b for a, b in zip(left, right))


def _scale(values, factor):
    return tuple(value * factor for value in values)


def _unlock_steps(data):
    remaining = data.max_shops - len(data.open_shops)
    next_day = (
        data.current_day // data.shop_unlock_interval_days + 1
    ) * data.shop_unlock_interval_days
    result = []
    while remaining > 0:
        step = next_day * data.turns_per_day
        if step > data.terminal_step:
            break
        result.append(step)
        remaining -= 1
        next_day += data.shop_unlock_interval_days
    return tuple(result)


def _drain_by_step(data, unlock_steps, next_shop):
    zero = (0.0,) * len(PRODUCTS)
    current = tuple(_shop_vector(shop) for shop in data.open_shops)
    selected = _shop_vector(next_shop) if next_shop is not None else zero
    expected = _expected_shop_vector()
    rows = []
    for step in range(data.source_step, data.terminal_step + 1):
        row = zero
        if step % data.shop_sell_interval_steps == 0:
            for vector in current:
                row = _add(row, vector)
            if unlock_steps and step >= unlock_steps[0]:
                row = _add(row, selected)
            later = sum(step >= unlock_step for unlock_step in unlock_steps[1:])
            row = _add(row, _scale(expected, later))
        if step % data.center_sell_interval_steps == 0:
            center = tuple(
                1.0 if item in TOWN_CENTER_PRODUCTS else 0.0
                for item in PRODUCTS
            )
            row = _add(row, center)
        rows.append(row)
    return tuple(rows)


def _drain_by_day(data, rows):
    result = []
    for day in range(data.current_day, data.terminal_day + 1):
        selected = [
            row
            for step, row in zip(
                range(data.source_step, data.terminal_step + 1), rows
            )
            if step // data.turns_per_day == day
        ]
        result.append(
            tuple(sum(row[index] for row in selected) for index in range(len(PRODUCTS)))
        )
    return tuple(result)


def _total(rows):
    return tuple(sum(row[index] for row in rows) for index in range(len(PRODUCTS)))


def _valid_drain_rows(rows, length):
    return (
        type(rows) is tuple
        and len(rows) == length
        and all(
            type(row) is tuple
            and len(row) == len(PRODUCTS)
            and all(
                type(value) in (int, float)
                and not isinstance(value, bool)
                and math.isfinite(value)
                and value >= 0
                for value in row
            )
            for row in rows
        )
    )


def _input_hash(data):
    payload = json.dumps(
        asdict(data),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def forecast_shops(data):
    if not isinstance(data, ShopForecastInput):
        raise TypeError("data must be ShopForecastInput")
    unlock_steps = _unlock_steps(data)
    if unlock_steps:
        names = tuple(sorted(SHOP_DEMAND))
        probability = 1.0 / len(names)
        choices = tuple((shop, probability) for shop in names)
    else:
        choices = ((None, 1.0),)
    scenarios = []
    for next_shop, probability in choices:
        rows = _drain_by_step(data, unlock_steps, next_shop)
        label = "deterministic" if next_shop is None else f"next-{next_shop.lower()}"
        scenarios.append(
            ShopScenario(
                label,
                probability,
                next_shop,
                rows,
                _drain_by_day(data, rows),
                _total(rows),
            )
        )
    expected_rows = tuple(
        tuple(
            sum(
                scenario.probability * scenario.drain_by_step[row_index][item_index]
                for scenario in scenarios
            )
            for item_index in range(len(PRODUCTS))
        )
        for row_index in range(data.terminal_step - data.source_step + 1)
    )
    next_daily = (data.current_day + 1) * data.turns_per_day
    if next_daily > data.terminal_step:
        next_daily = None
    next_shop = unlock_steps[0] if unlock_steps else None
    action_end = min(
        data.terminal_step,
        (data.current_day + 1) * data.turns_per_day - 1,
    )
    strategy_end = data.terminal_step if next_shop is None else next_shop - 1
    return ShopForecastResult(
        data.source_step,
        data.terminal_step,
        shop_signature(data.open_shops),
        next_daily,
        next_shop,
        action_end,
        strategy_end,
        data.terminal_step,
        tuple(scenarios),
        expected_rows,
        _drain_by_day(data, expected_rows),
        _total(expected_rows),
        _input_hash(data),
    )


def inventory_before_steps(initial_inventory, drain_by_step):
    if type(initial_inventory) is not tuple or len(initial_inventory) != len(PRODUCTS):
        raise TypeError("initial inventory must match products")
    if any(
        type(value) not in (int, float)
        or isinstance(value, bool)
        or not math.isfinite(value)
        for value in initial_inventory
    ):
        raise ValueError("initial inventory must be finite")
    current = tuple(float(value) for value in initial_inventory)
    result = []
    for row in drain_by_step:
        if type(row) is not tuple or len(row) != len(PRODUCTS):
            raise TypeError("drain rows must match products")
        if any(
            type(value) not in (int, float)
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value < 0
            for value in row
        ):
            raise ValueError("drain values must be finite and nonnegative")
        result.append(current)
        current = tuple(value - drain for value, drain in zip(current, row))
    return tuple(result)


def verify_forecast(data, result):
    errors = []
    if not isinstance(result, ShopForecastResult):
        return ("result has wrong type",)
    expected_steps = data.terminal_step - data.source_step + 1
    expected_days = data.terminal_day - data.current_day + 1
    unlock_steps = _unlock_steps(data)
    expected_daily = (data.current_day + 1) * data.turns_per_day
    if expected_daily > data.terminal_step:
        expected_daily = None
    expected_shop = unlock_steps[0] if unlock_steps else None
    expected_action_end = min(
        data.terminal_step,
        (data.current_day + 1) * data.turns_per_day - 1,
    )
    expected_strategy_end = (
        data.terminal_step if expected_shop is None else expected_shop - 1
    )
    if result.source_step != data.source_step:
        errors.append("source step mismatch")
    if result.terminal_step != data.terminal_step:
        errors.append("terminal step mismatch")
    if result.next_daily_replan_step != expected_daily:
        errors.append("daily replan boundary mismatch")
    if result.next_shop_replan_step != expected_shop:
        errors.append("shop replan boundary mismatch")
    if result.action_end_step != expected_action_end:
        errors.append("action horizon mismatch")
    if result.strategy_end_step != expected_strategy_end:
        errors.append("strategy horizon mismatch")
    expected_next_shops = (
        tuple(sorted(SHOP_DEMAND)) if unlock_steps else (None,)
    )
    if type(result.scenarios) is not tuple:
        errors.append("scenarios must be a tuple")
        scenarios = ()
    else:
        scenarios = result.scenarios
    probabilities_valid = all(
        type(scenario.probability) in (int, float)
        and not isinstance(scenario.probability, bool)
        and math.isfinite(scenario.probability)
        and scenario.probability > 0
        for scenario in scenarios
    )
    if not probabilities_valid:
        errors.append("scenario probabilities are invalid")
    elif not math.isclose(
        sum(scenario.probability for scenario in scenarios),
        1.0,
        abs_tol=1e-12,
    ):
        errors.append("scenario probability does not sum to one")
    if tuple(scenario.next_shop for scenario in scenarios) != expected_next_shops:
        errors.append("scenario branches mismatch")
    expected_probability = 1.0 / len(expected_next_shops)
    if probabilities_valid and any(
        not math.isclose(
            scenario.probability,
            expected_probability,
            abs_tol=1e-12,
        )
        for scenario in scenarios
    ):
        errors.append("scenario probabilities are not uniform")
    expected_names = tuple(
        "deterministic" if shop is None else f"next-{shop.lower()}"
        for shop in expected_next_shops
    )
    if tuple(scenario.name for scenario in scenarios) != expected_names:
        errors.append("scenario names mismatch")
    expected_rows_valid = _valid_drain_rows(
        result.expected_drain_by_step,
        expected_steps,
    )
    if not expected_rows_valid:
        errors.append("expected step demand is invalid")
    if len(result.expected_drain_by_day) != expected_days:
        errors.append("expected day horizon has wrong length")
    if result.input_hash != _input_hash(data):
        errors.append("input hash mismatch")
    if result.open_shop_signature != shop_signature(data.open_shops):
        errors.append("open shop signature mismatch")
    if result.investment_end_step != data.terminal_step:
        errors.append("investment horizon mismatch")
    for scenario in scenarios:
        rows_valid = _valid_drain_rows(scenario.drain_by_step, expected_steps)
        if not rows_valid:
            errors.append(f"{scenario.name} step demand is invalid")
        if len(scenario.drain_by_day) != expected_days:
            errors.append(f"{scenario.name} day horizon has wrong length")
        if rows_valid:
            if (
                type(scenario.next_shop) is str
                and scenario.next_shop in SHOP_DEMAND
            ) or (
                scenario.next_shop is None and not unlock_steps
            ):
                expected_rows = _drain_by_step(
                    data,
                    unlock_steps,
                    scenario.next_shop,
                )
                if scenario.drain_by_step != expected_rows:
                    errors.append(f"{scenario.name} step demand mismatch")
            if scenario.drain_by_day != _drain_by_day(data, scenario.drain_by_step):
                errors.append(f"{scenario.name} daily demand mismatch")
            if scenario.total_drain != _total(scenario.drain_by_step):
                errors.append(f"{scenario.name} total demand mismatch")
    if expected_rows_valid:
        if result.expected_total_drain != _total(result.expected_drain_by_step):
            errors.append("expected total demand mismatch")
        if result.expected_drain_by_day != _drain_by_day(
            data, result.expected_drain_by_step
        ):
            errors.append("expected daily demand mismatch")
        if all(
            _valid_drain_rows(scenario.drain_by_step, expected_steps)
            for scenario in scenarios
        ):
            weighted = tuple(
                tuple(
                    sum(
                        scenario.probability
                        * scenario.drain_by_step[row_index][item_index]
                        for scenario in scenarios
                    )
                    for item_index in range(len(PRODUCTS))
                )
                for row_index in range(expected_steps)
            )
            if result.expected_drain_by_step != weighted:
                errors.append("probability-weighted demand mismatch")
    return tuple(errors)
