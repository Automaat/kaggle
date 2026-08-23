import hashlib
import json
import math
import time
from dataclasses import dataclass, fields, is_dataclass, replace

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_array

from .market_ledger import (
    CROPS,
    LAND_PRICES,
    PRODUCTS,
    SHED_ITEMS,
    MarketConfig,
    MarketState,
    PlayerAccount,
    apply_market_phase,
)


TILES_PER_QUADRANT = 25
LAST_PROCESSED_STEP = 718
TURNS_PER_DAY = 24
MODES = frozenset({"baseline", "land-only", "hire-only", "combined"})
SCENARIOS = frozenset({"registered-executor-capacity-v1", "unit-test-v1"})


@dataclass(frozen=True, slots=True)
class OptimizerInput:
    source_step: int
    terminal_step: int
    cash: float
    cash_reserve: float
    unlocked_quadrants: int
    hands_today: int
    hires_today: int
    max_hands_per_day: int
    hire_multiplier: int
    fixed_cash_flow: tuple[float, ...]
    market_order_slots: tuple[int, ...]
    existing_work: tuple[int, ...]
    land_work_per_quadrant: tuple[int, ...]
    base_work_capacity: tuple[int, ...]
    executor_work_capacity: tuple[int, ...]
    terminal_value_per_work: tuple[float, ...]
    scenario: str

    def __post_init__(self):
        integer_values = (
            self.source_step,
            self.terminal_step,
            self.unlocked_quadrants,
            self.hands_today,
            self.hires_today,
            self.max_hands_per_day,
            self.hire_multiplier,
        )
        if any(type(value) is not int for value in integer_values):
            raise TypeError("optimizer integer settings must be integers")
        if self.source_step < 0 or self.source_step > LAST_PROCESSED_STEP:
            raise ValueError("source step must be in 0..718")
        if (
            self.terminal_step < self.source_step
            or self.terminal_step > LAST_PROCESSED_STEP
        ):
            raise ValueError("terminal step must be in source_step..718")
        if self.unlocked_quadrants < 1 or self.unlocked_quadrants > 4:
            raise ValueError("unlocked quadrants must be in 1..4")
        counters = (
            self.hands_today,
            self.hires_today,
            self.max_hands_per_day,
            self.hire_multiplier,
        )
        if any(value < 0 for value in counters):
            raise ValueError("hand settings must be nonnegative")
        if self.hands_today != self.hires_today:
            raise ValueError("current hands must match hires_today")
        if self.hands_today > self.max_hands_per_day:
            raise ValueError("current hands exceed daily limit")
        for name, value in (
            ("cash", self.cash),
            ("cash reserve", self.cash_reserve),
        ):
            if type(value) not in (int, float) or isinstance(value, bool):
                raise TypeError(f"{name} must be numeric")
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.cash < 0 or self.cash_reserve < 0:
            raise ValueError("cash values must be nonnegative")
        if self.cash_reserve > self.cash:
            raise ValueError("cash reserve must fit current cash")
        if self.scenario not in SCENARIOS:
            raise ValueError("unknown optimizer scenario")
        horizon = self.horizon_steps
        _validate_float_vector(
            self.fixed_cash_flow,
            horizon,
            "fixed cash flow",
            allow_negative=True,
        )
        _validate_int_vector(
            self.market_order_slots,
            horizon,
            "market order slots",
            upper=10,
        )
        _validate_int_vector(self.existing_work, horizon, "existing work")
        _validate_int_vector(
            self.land_work_per_quadrant,
            horizon,
            "land work per quadrant",
            upper=TILES_PER_QUADRANT,
        )
        _validate_int_vector(
            self.base_work_capacity,
            horizon,
            "base work capacity",
        )
        _validate_int_vector(
            self.executor_work_capacity,
            horizon,
            "executor work capacity",
            upper=self.max_hands_per_day + 1,
        )
        _validate_float_vector(
            self.terminal_value_per_work,
            horizon,
            "terminal value per work",
            allow_negative=False,
        )

    @property
    def horizon_steps(self):
        return self.terminal_step - self.source_step + 1


@dataclass(frozen=True, slots=True)
class InvestmentDecision:
    operation: str
    source_step: int
    order_index: int
    marginal_index: int
    cost: float
    available_from_step: int | None
    available_source_steps: int


@dataclass(frozen=True, slots=True)
class StepProjection:
    source_step: int
    cash_after_orders: float
    unlocked_quadrants: int
    hands: int
    existing_work: int
    land_work: int
    work_capacity: int
    completed_work: int


@dataclass(frozen=True, slots=True)
class OptimizerResult:
    success: bool
    status: int
    message: str
    mip_gap: float | None
    wall_seconds: float
    variable_count: int
    constraint_count: int
    mode: str
    investments: tuple[InvestmentDecision, ...]
    projections: tuple[StepProjection, ...]
    investment_cost: float | None
    terminal_work_value: float | None
    forecast_terminal_cash: float | None
    baseline_terminal_cash: float | None
    incremental_terminal_cash: float | None
    payback_met: bool | None
    scenario: str
    input_sha256: str


def _validate_int_vector(values, length, name, upper=None):
    if type(values) is not tuple or len(values) != length:
        raise TypeError(f"{name} must contain {length} values")
    if any(type(value) is not int for value in values):
        raise TypeError(f"{name} values must be integers")
    if any(value < 0 for value in values):
        raise ValueError(f"{name} values must be nonnegative")
    if upper is not None and any(value > upper for value in values):
        raise ValueError(f"{name} values exceed limit")


def _validate_float_vector(values, length, name, allow_negative):
    if type(values) is not tuple or len(values) != length:
        raise TypeError(f"{name} must contain {length} values")
    for value in values:
        if type(value) not in (int, float) or isinstance(value, bool):
            raise TypeError(f"{name} values must be numeric")
        if not math.isfinite(value):
            raise ValueError(f"{name} values must be finite")
        if not allow_negative and value < 0:
            raise ValueError(f"{name} values must be nonnegative")


def fibonacci(index):
    if type(index) is not int:
        raise TypeError("Fibonacci index must be an integer")
    if index < 0:
        raise ValueError("Fibonacci index must be nonnegative")
    first, second = 1, 1
    for _ in range(index):
        first, second = second, first + second
    return first


def hire_cost(hires_today, multiplier=1):
    if type(multiplier) is not int:
        raise TypeError("hire multiplier must be an integer")
    if multiplier < 0:
        raise ValueError("hire multiplier must be nonnegative")
    return multiplier * fibonacci(hires_today)


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
        for row, entries in enumerate(self.rows):
            for column, value in entries.items():
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


def _typed(value):
    if is_dataclass(value):
        return {
            field.name: _typed(getattr(value, field.name))
            for field in fields(value)
        }
    if type(value) is tuple:
        return [_typed(item) for item in value]
    return value


def input_sha256(data):
    if not isinstance(data, OptimizerInput):
        raise TypeError("data must be an OptimizerInput")
    encoded = json.dumps(
        _typed(data),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _steps(data):
    return tuple(range(data.source_step, data.terminal_step + 1))


def _day_steps(data):
    result = {}
    for step in _steps(data):
        result.setdefault(step // TURNS_PER_DAY, []).append(step)
    return {day: tuple(values) for day, values in result.items()}


def _hire_stage_specs(data):
    result = []
    current_day = data.source_step // TURNS_PER_DAY
    for day, steps in _day_steps(data).items():
        existing_hands = data.hands_today if day == current_day else 0
        starting_ordinal = data.hires_today if day == current_day else 0
        available = data.max_hands_per_day - existing_hands
        for stage in range(available):
            result.append(
                (
                    day,
                    stage,
                    starting_ordinal + stage,
                    steps,
                )
            )
    return tuple(result)


def _add_sequence_constraints(builder, stages, variables):
    for stage, step_values in stages.items():
        builder.constraint(
            {variables[stage, step]: 1 for step in step_values},
            upper=1,
        )
        if stage == min(stages):
            continue
        previous = stage - 1
        for index in range(len(step_values)):
            values = {
                variables[stage, step]: 1
                for step in step_values[: index + 1]
            }
            for step in stages[previous][: index + 1]:
                variable = variables[previous, step]
                values[variable] = values.get(variable, 0) - 1
            builder.constraint(values, upper=0)


def _build_model(data, mode):
    builder = _Builder()
    steps = _steps(data)
    land_enabled = mode in ("land-only", "combined")
    hire_enabled = mode in ("hire-only", "combined")
    remaining_land = 4 - data.unlocked_quadrants if land_enabled else 0
    land_vars = {}
    land_stages = {}
    for stage in range(remaining_land):
        land_stages[stage] = steps
        price = LAND_PRICES[data.unlocked_quadrants - 1 + stage]
        for step in steps:
            land_vars[stage, step] = builder.variable(
                ("land", stage, step),
                objective=price,
                upper=1 if land_enabled else 0,
            )
    if land_stages:
        _add_sequence_constraints(builder, land_stages, land_vars)
    hire_vars = {}
    hire_specs = _hire_stage_specs(data) if hire_enabled else ()
    by_day = {}
    for day, stage, ordinal, day_step_values in hire_specs:
        by_day.setdefault(day, {})[stage] = day_step_values
        cost = hire_cost(ordinal, data.hire_multiplier)
        for step in day_step_values:
            hire_vars[day, stage, step] = builder.variable(
                ("hire", day, stage, step),
                objective=cost,
                upper=1 if hire_enabled else 0,
            )
    for day, stages in by_day.items():
        day_vars = {
            (stage, step): hire_vars[day, stage, step]
            for stage, day_step_values in stages.items()
            for step in day_step_values
        }
        _add_sequence_constraints(builder, stages, day_vars)
    work_vars = {}
    for index, step in enumerate(steps):
        work_vars[step] = builder.variable(
            ("work", step),
            objective=-data.terminal_value_per_work[index],
            upper=data.executor_work_capacity[index],
        )
    cumulative_flow = 0.0
    for index, step in enumerate(steps):
        slot_values = {}
        for stage in range(remaining_land):
            slot_values[land_vars[stage, step]] = 1
        day = step // TURNS_PER_DAY
        for spec_day, stage, _, day_step_values in hire_specs:
            if spec_day == day and step in day_step_values:
                slot_values[hire_vars[day, stage, step]] = 1
        builder.constraint(slot_values, upper=data.market_order_slots[index])
        cumulative_flow += data.fixed_cash_flow[index]
        cash_values = {}
        for stage in range(remaining_land):
            price = LAND_PRICES[data.unlocked_quadrants - 1 + stage]
            for purchase_step in steps:
                if purchase_step <= step:
                    cash_values[land_vars[stage, purchase_step]] = price
        for spec_day, stage, ordinal, day_step_values in hire_specs:
            cost = hire_cost(ordinal, data.hire_multiplier)
            for purchase_step in day_step_values:
                if purchase_step <= step:
                    cash_values[hire_vars[spec_day, stage, purchase_step]] = cost
        builder.constraint(
            cash_values,
            upper=data.cash + cumulative_flow - data.cash_reserve,
        )
        demand_values = {work_vars[step]: 1}
        land_opportunity = data.land_work_per_quadrant[index]
        for stage in range(remaining_land):
            for purchase_step in steps:
                if purchase_step < step:
                    variable = land_vars[stage, purchase_step]
                    demand_values[variable] = (
                        demand_values.get(variable, 0) - land_opportunity
                    )
        builder.constraint(demand_values, upper=data.existing_work[index])
        capacity_values = {work_vars[step]: 1}
        for spec_day, stage, _, day_step_values in hire_specs:
            if spec_day != day:
                continue
            for purchase_step in day_step_values:
                if purchase_step < step:
                    variable = hire_vars[spec_day, stage, purchase_step]
                    capacity_values[variable] = capacity_values.get(variable, 0) - 1
        builder.constraint(capacity_values, upper=data.base_work_capacity[index])
    return builder, land_vars, hire_vars, hire_specs, work_vars


def _integer(value):
    rounded = int(round(float(value)))
    if not math.isclose(float(value), rounded, abs_tol=1e-6):
        raise ValueError("solver returned a noninteger value")
    return rounded


def _baseline(data):
    completed = tuple(
        min(existing, base, executor)
        for existing, base, executor in zip(
            data.existing_work,
            data.base_work_capacity,
            data.executor_work_capacity,
        )
    )
    work_value = sum(
        quantity * value
        for quantity, value in zip(completed, data.terminal_value_per_work)
    )
    terminal_cash = data.cash + sum(data.fixed_cash_flow) + work_value
    return completed, work_value, terminal_cash


def _available_from(data, operation, step):
    candidate = step + 1
    if candidate > data.terminal_step:
        return None
    if operation == "HIRE" and candidate // TURNS_PER_DAY != step // TURNS_PER_DAY:
        return None
    return candidate


def _available_steps(data, operation, step):
    if operation == "BUY_LAND":
        return max(0, data.terminal_step - step)
    day_end = min(data.terminal_step, (step // TURNS_PER_DAY + 1) * TURNS_PER_DAY - 1)
    return max(0, day_end - step)


def _decode_investments(data, land_vars, hire_vars, hire_specs, values):
    grouped = {}
    for (stage, step), variable in land_vars.items():
        if _integer(values[variable]):
            grouped.setdefault(step, []).append(
                (
                    0,
                    stage,
                    "BUY_LAND",
                    data.unlocked_quadrants + stage,
                    float(LAND_PRICES[data.unlocked_quadrants - 1 + stage]),
                )
            )
    ordinals = {(day, stage): ordinal for day, stage, ordinal, _ in hire_specs}
    for (day, stage, step), variable in hire_vars.items():
        if _integer(values[variable]):
            ordinal = ordinals[day, stage]
            grouped.setdefault(step, []).append(
                (
                    1,
                    stage,
                    "HIRE",
                    ordinal,
                    float(hire_cost(ordinal, data.hire_multiplier)),
                )
            )
    result = []
    for step in sorted(grouped):
        ordered = sorted(grouped[step])
        for order_index, (_, _, operation, marginal_index, cost) in enumerate(ordered):
            result.append(
                InvestmentDecision(
                    operation,
                    step,
                    order_index,
                    marginal_index,
                    cost,
                    _available_from(data, operation, step),
                    _available_steps(data, operation, step),
                )
            )
    return tuple(result)


def _active_land(data, investments, step):
    return sum(
        decision.operation == "BUY_LAND" and decision.source_step < step
        for decision in investments
    )


def _active_hires(data, investments, step):
    day = step // TURNS_PER_DAY
    return sum(
        decision.operation == "HIRE"
        and decision.source_step // TURNS_PER_DAY == day
        and decision.source_step < step
        for decision in investments
    )


def _projections(data, investments, work_vars, values):
    current_day = data.source_step // TURNS_PER_DAY
    cash = data.cash
    by_step = {}
    for decision in investments:
        by_step.setdefault(decision.source_step, []).append(decision)
    result = []
    for index, step in enumerate(_steps(data)):
        cash += data.fixed_cash_flow[index]
        for decision in sorted(by_step.get(step, ()), key=lambda value: value.order_index):
            cash -= decision.cost
        active_land = _active_land(data, investments, step)
        active_hires = _active_hires(data, investments, step)
        existing_hands = data.hands_today if step // TURNS_PER_DAY == current_day else 0
        land_work = data.land_work_per_quadrant[index] * active_land
        capacity = min(
            data.base_work_capacity[index] + active_hires,
            data.executor_work_capacity[index],
        )
        result.append(
            StepProjection(
                step,
                cash,
                data.unlocked_quadrants + active_land,
                existing_hands + active_hires,
                data.existing_work[index],
                land_work,
                capacity,
                _integer(values[work_vars[step]]),
            )
        )
    return tuple(result)


def solve_optimizer(
    data,
    mode,
    time_limit=120.0,
    mip_rel_gap=0.0,
    accept_feasible=False,
):
    if not isinstance(data, OptimizerInput):
        raise TypeError("data must be an OptimizerInput")
    if mode not in MODES:
        raise ValueError("unknown optimizer mode")
    if type(time_limit) not in (int, float) or isinstance(time_limit, bool):
        raise TypeError("time limit must be numeric")
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError("time limit must be positive")
    if type(mip_rel_gap) not in (int, float) or isinstance(mip_rel_gap, bool):
        raise TypeError("MIP gap must be numeric")
    if not math.isfinite(mip_rel_gap) or not 0 <= mip_rel_gap < 1:
        raise ValueError("MIP gap must be in 0..1")
    if type(accept_feasible) is not bool:
        raise TypeError("feasible acceptance must be a boolean")
    built = _build_model(data, mode)
    builder, land_vars, hire_vars, hire_specs, work_vars = built
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
    _, _, baseline_terminal_cash = _baseline(data)
    if not success:
        return OptimizerResult(
            False,
            int(solved.status),
            str(solved.message),
            gap,
            wall_seconds,
            len(builder.keys),
            len(builder.rows),
            mode,
            (),
            (),
            None,
            None,
            None,
            baseline_terminal_cash,
            None,
            None,
            data.scenario,
            input_sha256(data),
        )
    values = solved.x
    investments = _decode_investments(
        data,
        land_vars,
        hire_vars,
        hire_specs,
        values,
    )
    projections = _projections(data, investments, work_vars, values)
    investment_cost = sum(decision.cost for decision in investments)
    work_value = sum(
        projection.completed_work * value
        for projection, value in zip(projections, data.terminal_value_per_work)
    )
    terminal_cash = projections[-1].cash_after_orders + work_value
    incremental = terminal_cash - baseline_terminal_cash
    return OptimizerResult(
        True,
        int(solved.status),
        str(solved.message),
        gap,
        wall_seconds,
        len(builder.keys),
        len(builder.rows),
        mode,
        investments,
        projections,
        investment_cost,
        work_value,
        terminal_cash,
        baseline_terminal_cash,
        incremental,
        incremental >= -1e-7,
        data.scenario,
        input_sha256(data),
    )


def _blank_account(money, hires_today=0, unlocked_quadrants=1, hands=0):
    return PlayerAccount(
        money,
        (0,) * len(SHED_ITEMS),
        (0,) * len(CROPS),
        hires_today,
        unlocked_quadrants,
        hands,
    )


def _accepted_replay(data, result, errors):
    account = _blank_account(
        data.cash,
        data.hires_today,
        data.unlocked_quadrants,
        data.hands_today,
    )
    other = _blank_account(1_000_000)
    by_step = {}
    for decision in result.investments:
        by_step.setdefault(decision.source_step, []).append(decision)
    for index, step in enumerate(_steps(data)):
        projection = result.projections[index]
        if projection.unlocked_quadrants != account.unlocked_quadrants:
            errors.append("land availability mismatch")
        if projection.hands != account.hands:
            errors.append("hire availability mismatch")
        money = account.money + data.fixed_cash_flow[index]
        if money < 0:
            errors.append("negative cash before orders")
            return
        account = replace(account, money=money)
        decisions = sorted(
            by_step.get(step, ()),
            key=lambda value: value.order_index,
        )
        if tuple(value.order_index for value in decisions) != tuple(
            range(len(decisions))
        ):
            errors.append("investment order indexes differ")
        if len(decisions) > data.market_order_slots[index]:
            errors.append("market order capacity exceeded")
        queue = []
        replay_cash = account.money
        for decision in decisions:
            replay_cash -= decision.cost
            if replay_cash + 1e-7 < data.cash_reserve:
                errors.append("cash reserve violated")
            queue.append([decision.operation])
        state = MarketState(
            step,
            (0,) * len(PRODUCTS),
            (account, other),
            (),
            config=MarketConfig(hire_multiplier=data.hire_multiplier),
        )
        transition = apply_market_phase(state, (queue, []), trace=True)
        accepted = transition.after_town.players[0]
        events = transition.order_events
        if len(events) != len(decisions) or any(not event.accepted for event in events):
            errors.append("accepted A1a order replay differs")
        if not math.isclose(accepted.money, replay_cash, abs_tol=1e-7):
            errors.append("accepted A1a investment cost differs")
        if not math.isclose(
            projection.cash_after_orders,
            accepted.money,
            abs_tol=1e-7,
        ):
            errors.append("reported cash differs")
        account = accepted
        if step % TURNS_PER_DAY == TURNS_PER_DAY - 1:
            account = replace(account, hands=0, hires_today=0)


def verify_result(data, result):
    if not isinstance(data, OptimizerInput):
        raise TypeError("data must be an OptimizerInput")
    if not isinstance(result, OptimizerResult):
        raise TypeError("result must be an OptimizerResult")
    errors = []
    if result.input_sha256 != input_sha256(data):
        errors.append("input hash mismatch")
    if result.scenario != data.scenario:
        errors.append("scenario mismatch")
    if result.mode not in MODES:
        errors.append("unknown result mode")
    if not result.success:
        return tuple(errors)
    if len(result.projections) != data.horizon_steps:
        errors.append("projection horizon differs")
        return tuple(errors)
    if result.mode == "baseline" and result.investments:
        errors.append("baseline contains investments")
    if result.mode == "land-only" and any(
        value.operation == "HIRE" for value in result.investments
    ):
        errors.append("land-only contains hire")
    if result.mode == "hire-only" and any(
        value.operation == "BUY_LAND" for value in result.investments
    ):
        errors.append("hire-only contains land")
    land_stage = data.unlocked_quadrants
    hire_stage = {}
    seen_land_steps = []
    seen_hire_steps = {}
    for decision in result.investments:
        if decision.source_step < data.source_step or decision.source_step > data.terminal_step:
            errors.append("investment step outside horizon")
            continue
        if decision.operation == "BUY_LAND":
            if decision.marginal_index != land_stage:
                errors.append("land sequence differs")
            if land_stage > len(LAND_PRICES):
                errors.append("too many land purchases")
            else:
                expected_cost = LAND_PRICES[land_stage - 1]
                if not math.isclose(decision.cost, expected_cost, abs_tol=1e-7):
                    errors.append("land price differs")
            if seen_land_steps and decision.source_step < seen_land_steps[-1]:
                errors.append("land purchase order differs")
            seen_land_steps.append(decision.source_step)
            land_stage += 1
        elif decision.operation == "HIRE":
            day = decision.source_step // TURNS_PER_DAY
            start = data.hires_today if day == data.source_step // TURNS_PER_DAY else 0
            expected_ordinal = hire_stage.get(day, start)
            if decision.marginal_index != expected_ordinal:
                errors.append("hire sequence differs")
            expected_cost = hire_cost(expected_ordinal, data.hire_multiplier)
            if not math.isclose(decision.cost, expected_cost, abs_tol=1e-7):
                errors.append("hire price differs")
            if day in seen_hire_steps and decision.source_step < seen_hire_steps[day]:
                errors.append("hire purchase order differs")
            seen_hire_steps[day] = decision.source_step
            hire_stage[day] = expected_ordinal + 1
        else:
            errors.append("unknown investment operation")
            continue
        expected_from = _available_from(data, decision.operation, decision.source_step)
        if decision.available_from_step != expected_from:
            errors.append("investment availability differs")
        expected_steps = _available_steps(data, decision.operation, decision.source_step)
        if decision.available_source_steps != expected_steps:
            errors.append("investment capacity horizon differs")
    _accepted_replay(data, result, errors)
    total_work_value = 0.0
    for index, projection in enumerate(result.projections):
        step = data.source_step + index
        active_land = _active_land(data, result.investments, step)
        active_hires = _active_hires(data, result.investments, step)
        expected_land_work = data.land_work_per_quadrant[index] * active_land
        expected_capacity = min(
            data.base_work_capacity[index] + active_hires,
            data.executor_work_capacity[index],
        )
        if projection.source_step != step:
            errors.append("projection step differs")
        if projection.existing_work != data.existing_work[index]:
            errors.append("existing work projection differs")
        if projection.land_work != expected_land_work:
            errors.append("land work projection differs")
        if projection.work_capacity != expected_capacity:
            errors.append("work capacity projection differs")
        if projection.completed_work < 0:
            errors.append("completed work is negative")
        if projection.completed_work > projection.existing_work + projection.land_work:
            errors.append("work opportunities exceeded")
        if projection.completed_work > projection.work_capacity:
            errors.append("work capacity exceeded")
        total_work_value += (
            projection.completed_work * data.terminal_value_per_work[index]
        )
    investment_cost = sum(value.cost for value in result.investments)
    if result.investment_cost is None or not math.isclose(
        result.investment_cost,
        investment_cost,
        abs_tol=1e-7,
    ):
        errors.append("investment cost mismatch")
    if result.terminal_work_value is None or not math.isclose(
        result.terminal_work_value,
        total_work_value,
        abs_tol=1e-7,
    ):
        errors.append("terminal work value mismatch")
    _, _, baseline_terminal = _baseline(data)
    terminal_cash = (
        result.projections[-1].cash_after_orders + total_work_value
    )
    if result.baseline_terminal_cash is None or not math.isclose(
        result.baseline_terminal_cash,
        baseline_terminal,
        abs_tol=1e-7,
    ):
        errors.append("baseline terminal cash mismatch")
    if result.forecast_terminal_cash is None or not math.isclose(
        result.forecast_terminal_cash,
        terminal_cash,
        abs_tol=1e-7,
    ):
        errors.append("forecast terminal cash mismatch")
    incremental = terminal_cash - baseline_terminal
    if result.incremental_terminal_cash is None or not math.isclose(
        result.incremental_terminal_cash,
        incremental,
        abs_tol=1e-7,
    ):
        errors.append("incremental terminal cash mismatch")
    if result.payback_met != (incremental >= -1e-7):
        errors.append("terminal payback status differs")
    return tuple(errors)
