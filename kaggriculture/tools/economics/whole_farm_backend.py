import math
from dataclasses import asdict, dataclass, replace
from typing import Callable

from .animal_milp import (
    ANIMALS,
    GOODS,
    AnimalOracleInput,
    AnimalOracleResult,
    solve_animal_oracle,
    verify_result as verify_animal_result,
)
from .animal_ledger import ANIMAL_SPECS
from .land_hire_optimizer import (
    MODES,
    OptimizerInput,
    OptimizerResult,
    solve_optimizer,
    verify_result as verify_investment_result,
)
from .market_ledger import CROPS, PRODUCTS, SHED_ITEMS
from .milp_oracle import (
    OracleInput,
    OracleResult,
    generate_crop_options,
    solve_oracle,
    verify_result as verify_crop_result,
)
from .rolling_coordinator import (
    EconomicPlanRef,
    PlanFailure,
    PlanningWindow,
    RollingCoordinator,
    RollingObservation,
    RoutePlanRef,
    SpacePlanRef,
    WholeFarmIntent,
    canonical_sha256,
)
from .shop_forecast import ShopForecastResult
from .space_planner import (
    AnimalIntent,
    SpaceCell,
    SpacePlannerInput,
    SpacePlannerResult,
    solve_space_plan,
    verify_result as verify_space_result,
)
from ..routing.offline_route_planner import (
    RouteFailure,
    RoutePlan,
    RouteProblem,
    RouteTask,
    RouteUnit,
    plan_routes,
    verify_plan as verify_route_plan,
)


REGISTERED_SEED = 3_980_000
SOURCE_COMMITS = (
    ("crop", "6c7e587"),
    ("animal", "0b1c433"),
    ("land-hire", "27225bd"),
    ("space", "c029420"),
    ("shop-coordinator", "1b0c9f5"),
    ("routes", "f43083f"),
)


@dataclass(frozen=True, slots=True)
class SharedCapacity:
    field_tiles: tuple[int, ...]
    actions: tuple[int, ...]
    storage: tuple[int, ...]
    market_orders: tuple[int, ...]
    route_action_reserve: tuple[int, ...]

    def __post_init__(self):
        values = (
            self.field_tiles,
            self.actions,
            self.storage,
            self.market_orders,
            self.route_action_reserve,
        )
        if any(type(vector) is not tuple for vector in values):
            raise TypeError("shared capacities must be tuples")
        if len({len(vector) for vector in values}) != 1:
            raise ValueError("shared capacities must have equal horizons")
        if any(
            type(value) is not int or value < 0
            for vector in values
            for value in vector
        ):
            raise ValueError("shared capacities must be nonnegative integers")
        if any(
            reserve > actions
            for reserve, actions in zip(self.route_action_reserve, self.actions)
        ):
            raise ValueError("route reserve exceeds action capacity")


@dataclass(frozen=True, slots=True)
class WholeFarmSnapshot:
    registered_seed: int
    crop: OracleInput
    animal: AnimalOracleInput
    investment: OptimizerInput
    cells: tuple[SpaceCell, ...]
    shared: SharedCapacity
    animal_portfolios: tuple[tuple[str, ...], ...] = ()
    route_units: tuple[RouteUnit, ...] = ()

    def __post_init__(self):
        if type(self.registered_seed) is not int:
            raise TypeError("registered seed must be an integer")
        if type(self.crop) is not OracleInput:
            raise TypeError("crop input has wrong type")
        if type(self.animal) is not AnimalOracleInput:
            raise TypeError("animal input has wrong type")
        if type(self.investment) is not OptimizerInput:
            raise TypeError("investment input has wrong type")
        if type(self.cells) is not tuple or any(
            type(cell) is not SpaceCell for cell in self.cells
        ):
            raise TypeError("space cells have wrong type")
        if type(self.shared) is not SharedCapacity:
            raise TypeError("shared capacity has wrong type")
        if type(self.animal_portfolios) is not tuple or any(
            type(portfolio) is not tuple for portfolio in self.animal_portfolios
        ):
            raise TypeError("animal portfolios must be tuples")
        if len(self.animal_portfolios) > 5:
            raise ValueError("animal portfolios exceed iteration limit")
        if len(set(self.animal_portfolios)) != len(self.animal_portfolios):
            raise ValueError("animal portfolios must be unique")
        if any(
            animal not in ANIMALS
            for portfolio in self.animal_portfolios
            for animal in portfolio
        ):
            raise ValueError("animal portfolio contains unknown animal")
        if any(
            len(portfolio) > self.animal.max_new_animals
            for portfolio in self.animal_portfolios
        ):
            raise ValueError("animal portfolio exceeds new animal slots")
        if self.animal_portfolios and self.animal.fixed_slot_animals:
            raise ValueError("animal portfolio enumeration conflicts with fixed slots")
        if type(self.route_units) is not tuple or any(
            type(unit) is not RouteUnit for unit in self.route_units
        ):
            raise TypeError("route units have wrong type")
        if self.crop.source_step != self.animal.source_step:
            raise ValueError("model source steps differ")
        if self.crop.terminal_step != self.animal.terminal_step:
            raise ValueError("model terminal steps differ")
        if self.investment.source_step != self.crop.source_step:
            raise ValueError("investment source step differs")
        if self.investment.terminal_step != self.crop.terminal_step:
            raise ValueError("investment terminal step differs")
        if len(self.shared.actions) != self.crop.horizon_days:
            raise ValueError("shared capacity must cover model days")
        cash_values = (self.crop.cash, self.animal.cash, self.investment.cash)
        if any(not math.isclose(value, cash_values[0]) for value in cash_values[1:]):
            raise ValueError("models must start from one cash balance")
        reserves = (
            self.crop.cash_reserve,
            self.animal.cash_reserve,
            self.investment.cash_reserve,
        )
        if any(not math.isclose(value, reserves[0]) for value in reserves[1:]):
            raise ValueError("models must use one cash reserve")
        if self.animal.goods[GOODS.index("WHEAT")] != 0:
            raise ValueError("crop model must own initial wheat")
        if self.animal.goods[GOODS.index("FERTILIZER")] != 0:
            raise ValueError("crop model must own initial fertilizer")

    @property
    def source_step(self):
        return self.crop.source_step

    @property
    def current_day(self):
        return self.crop.current_day


@dataclass(frozen=True, slots=True)
class DailyResourceLedger:
    day: int
    cash_end: float
    wheat_feed: int
    wheat_purchased: int
    wheat_end: int
    fertilizer_supply: int
    fertilizer_purchased: int
    fertilizer_used: int
    fertilizer_end: int
    field_capacity: int
    crop_field_use: int
    animal_field_use: int
    action_capacity: int
    crop_action_use: int
    animal_action_use: int
    route_action_reserve: int
    storage_capacity: int
    crop_storage_use: int
    animal_storage_use: int
    market_order_capacity: int
    crop_market_orders: int
    animal_market_orders: int


@dataclass(frozen=True, slots=True)
class SharedResourceLedger:
    source_cash: float
    investment_cost: float
    terminal_cash: float
    iterations: int
    cut_signatures: tuple[str, ...]
    days: tuple[DailyResourceLedger, ...]


@dataclass(frozen=True, slots=True)
class ModelVerification:
    investment: tuple[tuple[str, tuple[str, ...]], ...]
    animal: tuple[str, ...]
    crop: tuple[str, ...]
    space: tuple[str, ...]
    ledger: tuple[str, ...]

    @property
    def errors(self):
        investment_errors = tuple(
            f"investment:{mode}:{error}"
            for mode, errors in self.investment
            for error in errors
        )
        return (
            investment_errors
            + tuple(f"animal:{error}" for error in self.animal)
            + tuple(f"crop:{error}" for error in self.crop)
            + tuple(f"space:{error}" for error in self.space)
            + tuple(f"ledger:{error}" for error in self.ledger)
        )


@dataclass(frozen=True, slots=True)
class WholeFarmSolve:
    snapshot_seed: int
    investment_results: tuple[OptimizerResult, ...]
    selected_investment: OptimizerResult
    animal_input: AnimalOracleInput
    animal_result: AnimalOracleResult
    crop_input: OracleInput
    crop_result: OracleResult
    space_input: SpacePlannerInput
    space_result: SpacePlannerResult
    ledger: SharedResourceLedger
    verification: ModelVerification
    animal_candidate_summary: tuple[tuple, ...]


@dataclass(frozen=True, slots=True)
class CropTargetIntent:
    day: int
    x: int
    y: int
    crop: str


@dataclass(frozen=True, slots=True)
class AnimalExecutionIntent:
    identifier: str
    animal: str
    purchase_day: int
    placement_day: int


@dataclass(frozen=True, slots=True)
class SpaceTarget:
    identifier: str
    animal: str
    x: int
    y: int
    mode: str
    placement_day: int


@dataclass(frozen=True, slots=True)
class MarketOrderIntent:
    identifier: str
    source_step: int
    order: tuple


@dataclass(frozen=True, slots=True)
class ExecutionHandoff:
    label: str
    epoch: int
    source_step: int
    economic_fingerprint: str
    space_fingerprint: str
    crop_targets: tuple[CropTargetIntent, ...]
    animal_intents: tuple[AnimalExecutionIntent, ...]
    space_targets: tuple[SpaceTarget, ...]
    market_orders: tuple[MarketOrderIntent, ...]
    route_arm: str = "frozen-1.14"
    route_plan_fingerprint: str | None = None
    route_commands: tuple[tuple, ...] = ()


@dataclass(frozen=True, slots=True)
class CandidateTrace:
    domain: str
    identifier: str
    accepted: bool
    objective: float | None
    rejection_reason: str | None


@dataclass(frozen=True, slots=True)
class ObservedResourceState:
    source_step: int
    cash: float
    cash_reserve: float
    crop_goods: tuple[int, ...]
    animal_goods: tuple[int, ...]
    fertilizer: int
    field_tiles: tuple[int, ...]
    actions: tuple[int, ...]
    storage: tuple[int, ...]
    market_orders: tuple[int, ...]
    route_action_reserve: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class DecisionTrace:
    epoch: int
    day: int
    reasons: tuple[str, ...]
    observed: ObservedResourceState
    resource_ledger: SharedResourceLedger
    candidates: tuple[CandidateTrace, ...]
    selected_crop_plan: tuple[tuple, ...]
    selected_animal_plan: tuple[tuple, ...]
    selected_investment_plan: tuple[tuple, ...]
    space_targets: tuple[SpaceTarget, ...]
    market_orders: tuple[MarketOrderIntent, ...]
    constraints: tuple[str, ...]
    cuts: tuple[str, ...]
    fingerprints: tuple[tuple[str, str], ...]
    fingerprint: str


class Frozen114ExecutionProvider:
    def __init__(self, handoff, crop_strategy_factory=None):
        if type(handoff) is not ExecutionHandoff:
            raise TypeError("handoff has wrong type")
        if crop_strategy_factory is not None and not callable(crop_strategy_factory):
            raise TypeError("crop strategy factory must be callable")
        self._handoff = handoff
        self._crop_strategy_factory = crop_strategy_factory

    @property
    def handoff(self):
        return self._handoff

    def reset(self):
        pass

    def plan(self, world, frozen_orders):
        planned = tuple(
            intent.order
            for intent in self._handoff.market_orders
            if intent.source_step == world.step
        )
        return planned or frozen_orders

    def prepare(self, world):
        if self._crop_strategy_factory is None:
            return None
        day = world.step // 24
        targets = tuple(
            (intent.x, intent.y, intent.crop)
            for intent in self._handoff.crop_targets
            if intent.day == day
        )
        return self._crop_strategy_factory(targets) if targets else None


class RollingHybridExecutionProvider:
    def __init__(
        self,
        coordinator,
        backend,
        observation_factory,
        crop_strategy_factory=None,
    ):
        if type(coordinator) is not RollingCoordinator:
            raise TypeError("coordinator has wrong type")
        if type(backend) is not WholeFarmPlannerBackend:
            raise TypeError("backend has wrong type")
        if not callable(observation_factory):
            raise TypeError("observation factory must be callable")
        if crop_strategy_factory is not None and not callable(crop_strategy_factory):
            raise TypeError("crop strategy factory must be callable")
        self._coordinator = coordinator
        self._backend = backend
        self._observation_factory = observation_factory
        self._crop_strategy_factory = crop_strategy_factory
        self._executor = None
        self._last_observation_identity = None
        self._traces = []

    @property
    def traces(self):
        return tuple(self._traces)

    @property
    def handoff(self):
        return None if self._executor is None else self._executor.handoff

    def reset(self):
        self._coordinator.reset()
        self._executor = None
        self._last_observation_identity = None
        self._traces.clear()

    def _refresh(self, world):
        observation = self._observation_factory(world)
        if type(observation) is not RollingObservation:
            raise TypeError("observation factory returned wrong type")
        if observation.identity == self._last_observation_identity:
            return
        intent = self._coordinator.prepare(observation)
        if isinstance(intent, PlanFailure):
            raise WholeFarmSolveError(intent.exception_text)
        if type(intent) is not WholeFarmIntent:
            raise TypeError("coordinator returned wrong intent type")
        handoff = self._backend.last_handoff
        trace = self._backend.last_trace
        if handoff is not None and handoff.epoch == intent.epoch:
            self._executor = Frozen114ExecutionProvider(
                handoff,
                self._crop_strategy_factory,
            )
            if trace is None or trace.epoch != intent.epoch:
                raise WholeFarmSolveError("new handoff lacks decision trace")
            if not self._traces or self._traces[-1].fingerprint != trace.fingerprint:
                self._traces.append(trace)
        if self._executor is None:
            raise WholeFarmSolveError("coordinator has no executable handoff")
        if observation.source_step % 24 == 0:
            if trace is None or trace.observed.source_step != observation.source_step:
                raise WholeFarmSolveError("daily observation lacks full solve")
        self._last_observation_identity = observation.identity

    def prepare(self, world):
        self._refresh(world)
        return self._executor.prepare(world)

    def plan(self, world, frozen_orders):
        self._refresh(world)
        return self._executor.plan(world, frozen_orders)

    def verify_daily_epochs(self):
        expected = set(range(0, 697, 24))
        actual = {trace.observed.source_step for trace in self._traces}
        missing = tuple(sorted(expected - actual))
        if missing:
            raise WholeFarmSolveError(f"missing daily solves: {missing}")
        return ()


class WholeFarmSolveError(RuntimeError):
    pass


def _forecast_inventory(base_rows, items, forecast):
    product_indices = tuple(PRODUCTS.index(item) for item in items)
    cumulative = [0.0] * len(PRODUCTS)
    rows = []
    for base, drain in zip(base_rows, forecast.expected_drain_by_day):
        rows.append(
            tuple(
                max(0, int(math.floor(value - cumulative[product_index])))
                for value, product_index in zip(base, product_indices)
            )
        )
        cumulative = [value + delta for value, delta in zip(cumulative, drain)]
    return tuple(rows)


def _select_investment(snapshot, time_limit, mip_rel_gap):
    results = tuple(
        solve_optimizer(
            snapshot.investment,
            mode,
            time_limit,
            mip_rel_gap,
            accept_feasible=True,
        )
        for mode in sorted(MODES)
    )
    verification = tuple(
        (result.mode, verify_investment_result(snapshot.investment, result))
        for result in results
    )
    valid = tuple(
        result
        for result, (_, errors) in zip(results, verification)
        if result.success and not errors and result.forecast_terminal_cash is not None
    )
    if not valid:
        raise WholeFarmSolveError("no verified investment result")
    selected = max(valid, key=lambda result: result.forecast_terminal_cash)
    return results, selected, verification


def _daily_investment_capacity(snapshot, selected):
    projections_by_day = {}
    for projection in selected.projections:
        projections_by_day.setdefault(projection.source_step // 24, []).append(
            projection
        )
    fields = []
    actions = []
    for index, day in enumerate(
        range(snapshot.current_day, snapshot.current_day + len(snapshot.shared.actions))
    ):
        projections = projections_by_day.get(day, ())
        quadrants = snapshot.investment.unlocked_quadrants
        if projections:
            quadrants = max(value.unlocked_quadrants for value in projections)
        fields.append(
            snapshot.shared.field_tiles[index]
            + 25 * (quadrants - snapshot.investment.unlocked_quadrants)
        )
        baseline_hands = (
            snapshot.investment.hands_today
            if day == snapshot.current_day
            else 0
        )
        hired_actions = sum(
            max(0, value.hands - baseline_hands) for value in projections
        )
        actions.append(
            snapshot.shared.actions[index]
            + hired_actions
        )
    return tuple(fields), tuple(actions)


def _animal_input(
    snapshot,
    selected_investment,
    field_capacity,
    action_capacity,
    forecast,
    max_new_animals,
    animal_storage_capacity,
):
    investment_cost = selected_investment.investment_cost or 0.0
    return replace(
        snapshot.animal,
        cash=snapshot.animal.cash - investment_cost,
        max_new_animals=max_new_animals,
        animal_tile_capacity=field_capacity,
        action_capacity=tuple(
            actions - reserve
            for actions, reserve in zip(
                action_capacity,
                snapshot.shared.route_action_reserve,
            )
        ),
        shed_capacity=animal_storage_capacity,
        market_order_slots=snapshot.shared.market_orders,
        base_inventory=_forecast_inventory(
            snapshot.animal.base_inventory,
            GOODS,
            forecast,
        ),
    )


def _animal_profile(data, result):
    days = tuple(range(data.current_day, data.last_day + 1))
    day_index = {day: index for index, day in enumerate(days)}
    actions = [0] * len(days)
    fields = [len(data.existing_animals) + sum(data.empty_structures)] * len(days)
    storage = [0] * len(days)
    orders = [0] * len(days)
    cash_flow = [0.0] * len(days)
    wheat_feed = [0] * len(days)
    fertilizer_supply = [0] * len(days)
    for decision in result.animals:
        if not decision.existing:
            actions[day_index[decision.placement_day]] += 2 + data.placement_travel_actions
    for structure in result.structures:
        index = day_index[structure.day]
        actions[index] += structure.quantity
        for active_index in range(index, len(days)):
            fields[active_index] += structure.quantity
    for service in result.services:
        index = day_index[service.day]
        if service.harvest_action:
            actions[index] += 1
            if not service.harvest_deferred:
                actions[index] += data.return_actions
        if service.fertilizer_collected:
            fertilizer_supply[index] += service.fertilizer_collected
            actions[index] += service.fertilizer_collected
            if not service.fertilizer_deferred:
                actions[index] += data.return_actions * service.fertilizer_collected
        if service.feed_action:
            wheat_feed[index] += 1
            actions[index] += data.feed_actions_per_unit
        if service.care_action:
            actions[index] += 1
    integrated_purchase_keys = set()
    for purchase in result.purchases:
        if purchase.item == "WHEAT":
            continue
        index = day_index[purchase.day]
        cash_flow[index] -= purchase.cost
        integrated_purchase_keys.add((purchase.day, purchase.item))
    integrated_sale_keys = set()
    for sale in result.sales:
        if sale.item in ("WHEAT", "FERTILIZER"):
            continue
        index = day_index[sale.day]
        cash_flow[index] += sale.revenue
        integrated_sale_keys.add((sale.day, sale.item))
    for day, _ in integrated_purchase_keys | integrated_sale_keys:
        orders[day_index[day]] += 1
    for index, balance in enumerate(result.balances):
        nonowned_goods = sum(
            quantity
            for item, quantity in zip(GOODS, balance.goods)
            if item not in ("WHEAT", "FERTILIZER")
        )
        storage[index] = (
            nonowned_goods
            + sum(balance.shed_animals)
            + sum(balance.empty_structures)
        )
    return {
        "actions": tuple(actions),
        "fields": tuple(fields),
        "storage": tuple(storage),
        "orders": tuple(orders),
        "cash_flow": tuple(cash_flow),
        "wheat_feed": tuple(wheat_feed),
        "fertilizer_supply": tuple(fertilizer_supply),
    }


def _crop_input(
    snapshot,
    selected_investment,
    animal_profile,
    field_capacity,
    action_capacity,
    forecast,
):
    investment_cost = selected_investment.investment_cost or 0.0
    return replace(
        snapshot.crop,
        cash=snapshot.crop.cash - investment_cost,
        tile_capacity=tuple(
            min(original, capacity - use)
            for original, capacity, use in zip(
                snapshot.crop.tile_capacity,
                field_capacity,
                animal_profile["fields"],
            )
        ),
        action_capacity=tuple(
            capacity - animal - route
            for capacity, animal, route in zip(
                action_capacity,
                animal_profile["actions"],
                snapshot.shared.route_action_reserve,
            )
        ),
        crop_storage_capacity=tuple(
            capacity - animal
            for capacity, animal in zip(
                snapshot.shared.storage,
                animal_profile["storage"],
            )
        ),
        wheat_demand=tuple(
            existing + animal
            for existing, animal in zip(
                snapshot.crop.wheat_demand,
                animal_profile["wheat_feed"],
            )
        ),
        fixed_cash_flow=tuple(
            existing + animal
            for existing, animal in zip(
                snapshot.crop.fixed_cash_flow,
                animal_profile["cash_flow"],
            )
        ),
        fertilizer_supply=tuple(
            existing + animal
            for existing, animal in zip(
                snapshot.crop.fertilizer_supply,
                animal_profile["fertilizer_supply"],
            )
        ),
        market_order_slots=tuple(
            capacity - animal
            for capacity, animal in zip(
                snapshot.shared.market_orders,
                animal_profile["orders"],
            )
        ),
        base_inventory=_forecast_inventory(
            snapshot.crop.base_inventory,
            CROPS,
            forecast,
        ),
    )


def _crop_option_key(data, option):
    position = None
    if option.existing_index is not None:
        position = data.existing_plants[option.existing_index].position
    return (
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


def _crop_decision_key(decision):
    return (
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


def _crop_profile(data, result):
    options = {
        _crop_option_key(data, option): option for option in generate_crop_options(data)
    }
    actions = [0] * data.horizon_days
    fields = [0] * data.horizon_days
    for decision in result.decisions:
        option = options[_crop_decision_key(decision)]
        for index, action_count in enumerate(option.actions):
            actions[index] += action_count * decision.count
        for day in option.active_days:
            fields[day - data.current_day] += decision.count
    orders = [0] * data.horizon_days
    for day, _ in {
        (purchase.day, purchase.item) for purchase in result.purchases
    } | {(sale.day, sale.crop) for sale in result.sales}:
        orders[day - data.current_day] += 1
    wheat_purchased = [0] * data.horizon_days
    fertilizer_purchased = [0] * data.horizon_days
    for purchase in result.purchases:
        index = purchase.day - data.current_day
        if purchase.item == "WHEAT":
            wheat_purchased[index] += purchase.quantity
        if purchase.item == "FERTILIZER":
            fertilizer_purchased[index] += purchase.quantity
    storage = tuple(sum(balance.goods) + balance.fertilizer for balance in result.balances)
    wheat_end = tuple(balance.goods[CROPS.index("WHEAT")] for balance in result.balances)
    fertilizer_end = tuple(balance.fertilizer for balance in result.balances)
    fertilizer_used = []
    previous = data.fertilizer_stock
    for index, end in enumerate(fertilizer_end):
        available = previous + data.fertilizer_supply[index] + fertilizer_purchased[index]
        fertilizer_used.append(available - end)
        previous = end
    return {
        "actions": tuple(actions),
        "fields": tuple(fields),
        "orders": tuple(orders),
        "storage": storage,
        "wheat_purchased": tuple(wheat_purchased),
        "wheat_end": wheat_end,
        "fertilizer_purchased": tuple(fertilizer_purchased),
        "fertilizer_used": tuple(fertilizer_used),
        "fertilizer_end": fertilizer_end,
    }


def _animal_intents(data, result):
    selected = tuple(animal for animal in result.animals if not animal.existing)
    remaining_days = max(1, data.last_day - data.current_day + 1)
    profit = max(0.0, result.incremental_animal_profit or 0.0)
    daily_value = profit / max(1, len(selected)) / remaining_days
    return tuple(
        AnimalIntent(
            animal.identifier,
            animal.animal,
            animal.placement_day,
            daily_value,
        )
        for animal in selected
    )


def _space_input(snapshot, animal_data, animal_result, crop_data, crop_result, actions):
    crop_profile = _crop_profile(crop_data, crop_result)
    space_actions = tuple(
        max(0, capacity - crop - route)
        for capacity, crop, route in zip(
            actions,
            crop_profile["actions"],
            snapshot.shared.route_action_reserve,
        )
    )
    return SpacePlannerInput(
        snapshot.current_day,
        snapshot.crop.terminal_step // 24,
        snapshot.cells,
        _animal_intents(animal_data, animal_result),
        space_actions,
        1.0,
        1,
        1,
        2,
    )


def build_shared_ledger(
    snapshot,
    selected_investment,
    animal_data,
    animal_result,
    crop_data,
    crop_result,
    field_capacity,
    action_capacity,
    iterations,
    cut_signatures,
):
    animal = _animal_profile(animal_data, animal_result)
    crop = _crop_profile(crop_data, crop_result)
    days = []
    for index, balance in enumerate(crop_result.balances):
        days.append(
            DailyResourceLedger(
                balance.day,
                balance.cash,
                animal["wheat_feed"][index],
                crop["wheat_purchased"][index],
                crop["wheat_end"][index],
                crop_data.fertilizer_supply[index],
                crop["fertilizer_purchased"][index],
                crop["fertilizer_used"][index],
                crop["fertilizer_end"][index],
                field_capacity[index],
                crop["fields"][index],
                animal["fields"][index],
                action_capacity[index],
                crop["actions"][index],
                animal["actions"][index],
                snapshot.shared.route_action_reserve[index],
                snapshot.shared.storage[index],
                crop["storage"][index],
                animal["storage"][index],
                snapshot.shared.market_orders[index],
                crop["orders"][index],
                animal["orders"][index],
            )
        )
    return SharedResourceLedger(
        snapshot.crop.cash,
        selected_investment.investment_cost or 0.0,
        crop_result.terminal_cash,
        iterations,
        tuple(cut_signatures),
        tuple(days),
    )


def verify_shared_ledger(ledger):
    if type(ledger) is not SharedResourceLedger:
        return ("ledger has wrong type",)
    errors = []
    expected_initial = ledger.source_cash - ledger.investment_cost
    if ledger.days and ledger.days[0].cash_end < 0:
        errors.append("negative cash")
    if ledger.terminal_cash != ledger.days[-1].cash_end:
        errors.append("terminal cash mismatch")
    if expected_initial < 0:
        errors.append("investment exceeds source cash")
    for day in ledger.days:
        if day.wheat_feed < 0 or day.wheat_purchased < 0 or day.wheat_end < 0:
            errors.append(f"day {day.day} has invalid wheat balance")
        if min(
            day.fertilizer_supply,
            day.fertilizer_purchased,
            day.fertilizer_used,
            day.fertilizer_end,
        ) < 0:
            errors.append(f"day {day.day} has invalid fertilizer balance")
        if day.crop_field_use + day.animal_field_use > day.field_capacity:
            errors.append(f"day {day.day} exceeds field capacity")
        if (
            day.crop_action_use
            + day.animal_action_use
            + day.route_action_reserve
            > day.action_capacity
        ):
            errors.append(f"day {day.day} exceeds action capacity")
        if day.crop_storage_use + day.animal_storage_use > day.storage_capacity:
            errors.append(f"day {day.day} exceeds storage capacity")
        if day.crop_market_orders + day.animal_market_orders > day.market_order_capacity:
            errors.append(f"day {day.day} exceeds market order capacity")
    return tuple(errors)


def _result_fingerprint(domain, result, fields):
    values = asdict(result)
    return canonical_sha256(
        domain,
        tuple((name, values[name]) for name in fields),
    )


def _market_order_intents(snapshot, solved):
    intents = []
    for purchase in solved.crop_result.purchases:
        if purchase.item.endswith("_SEED"):
            order = ("BUY_SEED", purchase.item.removesuffix("_SEED"), purchase.quantity)
        else:
            order = ("BUY_PRODUCT", purchase.item, purchase.quantity)
        step = max(snapshot.source_step, purchase.day * 24)
        intents.append(
            MarketOrderIntent(
                f"crop-buy:{purchase.day}:{purchase.item}",
                step,
                order,
            )
        )
    for sale in solved.crop_result.sales:
        step = max(snapshot.source_step, sale.day * 24)
        intents.append(
            MarketOrderIntent(
                f"crop-sell:{sale.day}:{sale.crop}",
                step,
                ("SELL", sale.crop, sale.quantity),
            )
        )
    for purchase in solved.animal_result.purchases:
        if purchase.item == "WHEAT":
            continue
        operation = "BUY_ANIMAL" if purchase.item in ANIMALS else "BUY_PRODUCT"
        step = max(snapshot.source_step, purchase.day * 24)
        intents.append(
            MarketOrderIntent(
                f"animal-buy:{purchase.day}:{purchase.item}",
                step,
                (operation, purchase.item, purchase.quantity),
            )
        )
    for sale in solved.animal_result.sales:
        if sale.item in ("WHEAT", "FERTILIZER"):
            continue
        step = max(snapshot.source_step, sale.day * 24)
        intents.append(
            MarketOrderIntent(
                f"animal-sell:{sale.day}:{sale.item}",
                step,
                ("SELL", sale.item, sale.quantity),
            )
        )
    for investment in solved.selected_investment.investments:
        intents.append(
            MarketOrderIntent(
                f"investment:{investment.source_step}:{investment.operation}:{investment.order_index}",
                investment.source_step,
                (investment.operation,),
            )
        )
    return tuple(
        sorted(
            intents,
            key=lambda value: (value.source_step, value.identifier, value.order),
        )
    )


def _space_targets(solved):
    return tuple(
        SpaceTarget(
            assignment.intent,
            assignment.animal,
            assignment.position[1],
            assignment.position[0],
            assignment.mode,
            assignment.placement_day,
        )
        for assignment in solved.space_result.assignments
    )


def _animal_execution_intents(solved):
    purchases = {}
    for purchase in solved.animal_result.purchases:
        if purchase.item in ANIMALS:
            purchases.setdefault(purchase.item, []).extend(
                [purchase.day] * purchase.quantity
            )
    result = []
    for decision in solved.animal_result.animals:
        if decision.existing:
            continue
        days = purchases.get(decision.animal, [])
        eligible = [day for day in days if day <= decision.placement_day]
        purchase_day = min(eligible) if eligible else decision.placement_day
        if eligible:
            days.remove(purchase_day)
        result.append(
            AnimalExecutionIntent(
                decision.identifier,
                decision.animal,
                purchase_day,
                decision.placement_day,
            )
        )
    return tuple(result)


def _crop_targets(snapshot, solved, space_targets):
    blocked = {(target.y, target.x) for target in space_targets}
    by_day = {}
    for decision in solved.crop_result.decisions:
        if decision.plant_day is None:
            continue
        by_day.setdefault(decision.plant_day, []).extend(
            [decision.crop] * decision.count
        )
    targets = []
    for day, crops in sorted(by_day.items()):
        available = sorted(
            (
                cell.position
                for cell in snapshot.cells
                if cell.unlock_day <= day
                and cell.kind == "EMPTY"
                and cell.position not in blocked
            ),
            key=lambda position: (
                abs(position[0] - 4) + abs(position[1] - 4),
                position,
            ),
        )
        if len(available) < len(crops):
            raise WholeFarmSolveError("execution handoff lacks crop target cells")
        targets.extend(
            CropTargetIntent(day, position[1], position[0], crop)
            for position, crop in zip(available, crops)
        )
    return tuple(targets)


def _route_commands(route_plan):
    if route_plan is None:
        return ()
    return tuple(
        (
            route.unit_identifier,
            tuple(
                (
                    command.identifier,
                    command.expected_pre_position,
                    command.action,
                    command.expected_post_position,
                    command.task_identifier,
                    command.effect_fingerprint,
                )
                for command in route.commands
            ),
        )
        for route in route_plan.routes
    )


def _build_handoff(
    epoch,
    snapshot,
    solved,
    economy,
    space,
    label="strategy-2.0-execution-1.14",
    route_arm="frozen-1.14",
    route_plan=None,
):
    space_targets = _space_targets(solved)
    return ExecutionHandoff(
        label,
        epoch,
        snapshot.source_step,
        economy.fingerprint,
        space.fingerprint,
        _crop_targets(snapshot, solved, space_targets),
        _animal_execution_intents(solved),
        space_targets,
        _market_order_intents(snapshot, solved),
        route_arm,
        None if route_plan is None else route_plan.fingerprint,
        _route_commands(route_plan),
    )


def _route_inventory(snapshot, handoff):
    counts = {item: 0 for item in SHED_ITEMS}
    for item, quantity in zip(CROPS, snapshot.crop.goods):
        counts[item] += quantity
    for item, quantity in zip(GOODS, snapshot.animal.goods):
        counts[item] += quantity
    counts["FERTILIZER"] += snapshot.crop.fertilizer_stock
    for animal, quantity in zip(ANIMALS, snapshot.animal.shed_animals):
        counts[animal] += quantity
    day_end = (snapshot.current_day + 1) * 24 - 1
    for intent in handoff.market_orders:
        if not snapshot.source_step <= intent.source_step <= day_end:
            continue
        operation = intent.order[0]
        if operation in ("BUY_ANIMAL", "BUY_PRODUCT"):
            counts[intent.order[1]] += intent.order[2]
    return tuple((item, counts[item]) for item in SHED_ITEMS if counts[item] > 0)


def _task(
    identifier,
    position,
    action,
    deadline,
    dependencies=(),
    requires=(),
    produces=(),
):
    return RouteTask(
        identifier,
        position,
        action,
        2,
        deadline,
        dependencies,
        requires,
        produces,
        canonical_sha256("whole-farm-route-pre", identifier),
        canonical_sha256("whole-farm-route-effect", identifier),
    )


def _crop_route_tasks(snapshot, solved, handoff, deadline):
    options = {
        _crop_option_key(solved.crop_input, option): option
        for option in generate_crop_options(solved.crop_input)
    }
    available_targets = {}
    for target in handoff.crop_targets:
        available_targets.setdefault((target.day, target.crop), []).append(target)
    tasks = []
    for decision_index, decision in enumerate(solved.crop_result.decisions):
        option = options[_crop_decision_key(decision)]
        positions = []
        if decision.existing_position is not None:
            positions = [
                (decision.existing_position[1], decision.existing_position[0])
            ]
        elif decision.plant_day == snapshot.current_day:
            targets = available_targets.get((decision.plant_day, decision.crop), [])
            positions = [(target.x, target.y) for target in targets[: decision.count]]
            del targets[: decision.count]
        if not positions:
            continue
        day_index = snapshot.current_day - solved.crop_input.current_day
        action_count = option.actions[day_index]
        fixed_actions = 0
        if decision.plant_day == snapshot.current_day:
            fixed_actions += 1
        if snapshot.current_day in decision.fertilizer_days:
            fixed_actions += 1
        harvest_quantity = dict(decision.harvests).get(snapshot.current_day, 0)
        if harvest_quantity:
            fixed_actions += 1 + solved.crop_input.terminal_return_actions
        if decision.release_day == snapshot.current_day:
            fixed_actions += 1
        water = action_count > fixed_actions
        for position_index, position in enumerate(positions):
            previous = ()
            operations = []
            if decision.plant_day == snapshot.current_day:
                operations.append(("PLANT", ("PLANT", decision.crop), (), ()))
            if water:
                operations.append(("WATER", ("WATER",), (), ()))
            if snapshot.current_day in decision.fertilizer_days:
                operations.append(
                    (
                        "FERTILIZE",
                        ("FERTILIZE",),
                        (("FERTILIZER", 1),),
                        (),
                    )
                )
            if harvest_quantity:
                operations.append(
                    (
                        "HARVEST",
                        ("HARVEST",),
                        (),
                        ((decision.crop, harvest_quantity),),
                    )
                )
            if decision.release_day == snapshot.current_day:
                operations.append(("DIG", ("DIG",), (), ()))
            for operation_index, (name, action, requires, produces) in enumerate(
                operations
            ):
                identifier = (
                    f"crop:{decision_index}:{position_index}:"
                    f"{snapshot.current_day}:{operation_index}:{name}"
                )
                tasks.append(
                    _task(
                        identifier,
                        position,
                        action,
                        deadline,
                        previous,
                        requires,
                        produces,
                    )
                )
                previous = (identifier,)
    return tuple(tasks)


def _route_tasks(snapshot, solved, handoff, deadline):
    tasks = []
    dependency_by_intent = {}
    for assignment in solved.space_result.assignments:
        previous = ()
        for index, task in enumerate(assignment.tasks):
            if task.day != snapshot.current_day:
                continue
            identifier = f"space:{assignment.intent}:{index}:{task.operation}"
            requires = ()
            if task.operation == "PLACE":
                requires = ((assignment.animal, 1),)
            tasks.append(
                _task(
                    identifier,
                    (task.position[1], task.position[0]),
                    (task.operation, assignment.animal)
                    if task.operation == "PLACE"
                    else (task.operation,),
                    deadline,
                    previous,
                    requires,
                )
            )
            previous = (identifier,)
        if previous:
            dependency_by_intent[assignment.intent] = previous
    tasks.extend(_crop_route_tasks(snapshot, solved, handoff, deadline))
    targets = {target.identifier: target for target in handoff.space_targets}
    existing = {
        animal.identifier: animal.position for animal in snapshot.animal.existing_animals
    }
    for service in solved.animal_result.services:
        if service.day != snapshot.current_day or not service.active:
            continue
        target = targets.get(service.identifier)
        if target is None:
            position = existing.get(service.identifier)
            if position is None:
                continue
            position = (position[1], position[0])
        else:
            position = (target.x, target.y)
        previous = dependency_by_intent.get(service.identifier, ())
        actions = []
        if service.feed_action:
            actions.append(("FEED", (), (("WHEAT", 1),), ()))
        if service.care_action:
            actions.append(("CARE", (), (), ()))
        if service.harvest_action:
            product = ANIMAL_SPECS[service.animal].product
            actions.append(("HARVEST", (), (), ((product, service.harvested),)))
        for _ in range(service.fertilizer_collected):
            actions.append(
                ("COLLECT_FERTILIZER", (), (), (("FERTILIZER", 1),))
            )
        for index, (operation, extra_dependencies, requires, produces) in enumerate(
            actions
        ):
            identifier = f"animal:{service.identifier}:{service.day}:{index}:{operation}"
            dependencies = previous + extra_dependencies
            tasks.append(
                _task(
                    identifier,
                    position,
                    (operation,),
                    deadline,
                    dependencies,
                    requires,
                    produces,
                )
            )
            previous = (identifier,)
    return tuple(tasks)


def _build_route_problem(snapshot, solved, handoff, observation):
    if not snapshot.route_units:
        raise WholeFarmSolveError("route arm requires observed units")
    remaining = 24 - snapshot.source_step % 24
    if snapshot.current_day == 29:
        remaining = 719 - snapshot.source_step
    deadline = max(1, remaining)
    return RouteProblem(
        snapshot.source_step,
        10,
        snapshot.route_units,
        _route_inventory(snapshot, handoff),
        snapshot.shared.storage[0],
        _route_tasks(snapshot, solved, handoff, deadline),
        deadline,
        observation.route_precondition_fingerprint,
    )


def _build_decision_trace(
    epoch,
    reasons,
    snapshot,
    solved,
    handoff,
    economy,
    space,
    routes,
    route_arm,
    route_plan,
):
    investment_errors = dict(solved.verification.investment)
    selected_animals = tuple(
        value.animal for value in solved.animal_result.animals if not value.existing
    )
    selected_animal_identifier = "+".join(selected_animals) or "none"
    animal_candidates = tuple(
        CandidateTrace(
            "animal",
            identifier,
            feasible and identifier == selected_animal_identifier,
            objective,
            rejection
            if not feasible
            else None
            if identifier == selected_animal_identifier
            else "lower joint objective",
        )
        for identifier, feasible, objective, rejection in solved.animal_candidate_summary
    )
    candidates = tuple(
        CandidateTrace(
            "investment",
            result.mode,
            result is solved.selected_investment,
            result.forecast_terminal_cash,
            "; ".join(investment_errors[result.mode]) or result.message
            if not result.success or investment_errors[result.mode]
            else None
            if result is solved.selected_investment
            else "lower objective",
        )
        for result in sorted(solved.investment_results, key=lambda value: value.mode)
    ) + animal_candidates + (
        CandidateTrace(
            "crop",
            "selected-portfolio",
            not solved.verification.crop,
            solved.crop_result.terminal_cash,
            "; ".join(solved.verification.crop) or None,
        ),
        CandidateTrace(
            "space",
            "selected-layout",
            not solved.verification.space,
            solved.space_result.objective_value,
            "; ".join(solved.verification.space) or None,
        ),
        CandidateTrace(
            "route",
            route_arm,
            True,
            None if route_plan is None else -float(route_plan.total_cost),
            None,
        ),
    )
    observed = ObservedResourceState(
        snapshot.source_step,
        snapshot.crop.cash,
        snapshot.crop.cash_reserve,
        snapshot.crop.goods,
        snapshot.animal.goods,
        snapshot.crop.fertilizer_stock,
        snapshot.shared.field_tiles,
        snapshot.shared.actions,
        snapshot.shared.storage,
        snapshot.shared.market_orders,
        snapshot.shared.route_action_reserve,
    )
    crop_plan = tuple(
        (
            decision.crop,
            decision.plant_day,
            decision.harvest_day,
            decision.sale_day,
            decision.count,
        )
        for decision in solved.crop_result.decisions
    )
    animal_plan = tuple(
        (
            decision.identifier,
            decision.animal,
            decision.existing,
            decision.placement_day,
        )
        for decision in solved.animal_result.animals
    )
    investment_plan = tuple(
        (
            decision.operation,
            decision.source_step,
            decision.cost,
            decision.available_from_step,
        )
        for decision in solved.selected_investment.investments
    )
    route_constraint = (
        "routes:conservative-action-reserve"
        if route_plan is None
        else "routes:planner-2.0-complete-day"
    )
    constraints = (
        "cash-owner:crop-ledger",
        "fertilizer-owner:crop-model",
        "iterations:max-5-cycle-detect",
        "resources:shared-fields-actions-storage-orders",
        route_constraint,
        "wheat-owner:crop-model",
    )
    fingerprints = (
        ("animal", economy.animal_result_fingerprint),
        ("crop", economy.crop_result_fingerprint),
        ("economy", economy.fingerprint),
        ("investment", economy.investment_result_fingerprint),
        ("resources", economy.resource_profile_fingerprint),
        ("routes", routes.fingerprint),
        ("space", space.fingerprint),
    )
    payload = {
        "epoch": epoch,
        "day": snapshot.current_day,
        "reasons": reasons,
        "observed": asdict(observed),
        "resource_ledger": asdict(solved.ledger),
        "candidates": tuple(asdict(value) for value in candidates),
        "crop_plan": crop_plan,
        "animal_plan": animal_plan,
        "investment_plan": investment_plan,
        "space_targets": tuple(asdict(value) for value in handoff.space_targets),
        "market_orders": tuple(asdict(value) for value in handoff.market_orders),
        "constraints": constraints,
        "cuts": solved.ledger.cut_signatures,
        "fingerprints": fingerprints,
    }
    return DecisionTrace(
        epoch,
        snapshot.current_day,
        reasons,
        observed,
        solved.ledger,
        candidates,
        crop_plan,
        animal_plan,
        investment_plan,
        handoff.space_targets,
        handoff.market_orders,
        constraints,
        solved.ledger.cut_signatures,
        fingerprints,
        canonical_sha256("whole-farm-decision-trace", payload),
    )


class WholeFarmPlannerBackend:
    def __init__(
        self,
        snapshot_provider: Callable[[RollingObservation], WholeFarmSnapshot],
        time_limit=30.0,
        mip_rel_gap=0.0,
        max_iterations=5,
        route_arm="frozen-1.14",
    ):
        if not callable(snapshot_provider):
            raise TypeError("snapshot provider must be callable")
        if type(max_iterations) is not int or not 1 <= max_iterations <= 5:
            raise ValueError("iteration limit must be in 1..5")
        if type(time_limit) not in (int, float) or isinstance(time_limit, bool):
            raise TypeError("time limit must be numeric")
        if not math.isfinite(time_limit) or time_limit <= 0:
            raise ValueError("time limit must be positive")
        if type(mip_rel_gap) not in (int, float) or isinstance(mip_rel_gap, bool):
            raise TypeError("MIP gap must be numeric")
        if not math.isfinite(mip_rel_gap) or not 0 <= mip_rel_gap < 1:
            raise ValueError("MIP gap must be in 0..1")
        if route_arm not in ("frozen-1.14", "planner-2.0"):
            raise ValueError("unknown route arm")
        self._snapshot_provider = snapshot_provider
        self._time_limit = float(time_limit)
        self._mip_rel_gap = float(mip_rel_gap)
        self._max_iterations = max_iterations
        self._route_arm = route_arm
        self._last_solve = None
        self._last_handoff = None
        self._last_trace = None
        self._last_shop_signature = None
        self._last_route_plan = None
        self._last_route_plan_problem = None

    @property
    def last_solve(self):
        return self._last_solve

    @property
    def last_handoff(self):
        return self._last_handoff

    @property
    def last_trace(self):
        return self._last_trace

    @property
    def last_route_plan(self):
        return self._last_route_plan

    @property
    def last_route_plan_problem(self):
        return self._last_route_plan_problem

    def reset(self):
        self._last_solve = None
        self._last_handoff = None
        self._last_trace = None
        self._last_shop_signature = None
        self._last_route_plan = None
        self._last_route_plan_problem = None

    def _solve(self, snapshot, forecast):
        investment_results, selected, investment_verification = _select_investment(
            snapshot,
            self._time_limit,
            self._mip_rel_gap,
        )
        field_capacity, action_capacity = _daily_investment_capacity(snapshot, selected)
        max_animals = snapshot.animal.max_new_animals
        storage_cut_step = max(1, max(snapshot.crop.tile_capacity))
        animal_storage_capacity = tuple(
            max(0, capacity - min(capacity, reserve * 4))
            for capacity, reserve in zip(
                snapshot.shared.storage,
                snapshot.crop.tile_capacity,
            )
        )
        portfolios = snapshot.animal_portfolios[: self._max_iterations]
        portfolio_mode = bool(portfolios)
        feasible_solutions = []
        animal_candidate_summary = []
        cut_signatures = []
        seen = set()
        last_errors = ()
        for iteration in range(1, self._max_iterations + 1):
            if portfolio_mode and iteration > len(portfolios):
                break
            portfolio = portfolios[iteration - 1] if portfolio_mode else None
            if portfolio is not None:
                max_animals = len(portfolio)
            animal_data = _animal_input(
                snapshot,
                selected,
                field_capacity,
                action_capacity,
                forecast,
                max_animals,
                animal_storage_capacity,
            )
            if portfolio is not None:
                animal_data = replace(
                    animal_data,
                    max_new_animals=len(portfolio),
                    fixed_slot_animals=portfolio,
                    min_new_animals=len(portfolio),
                )
            animal_result = solve_animal_oracle(
                animal_data,
                self._time_limit,
                self._mip_rel_gap,
                accept_feasible=True,
            )
            animal_errors = verify_animal_result(animal_data, animal_result)
            if not animal_result.success or animal_errors:
                failure_domain = "animal"
                last_errors = tuple(animal_errors) or ("animal solver failed",)
                selected_animals = ()
                rejected = ()
            else:
                animal_profile = _animal_profile(animal_data, animal_result)
                crop_data = _crop_input(
                    snapshot,
                    selected,
                    animal_profile,
                    field_capacity,
                    action_capacity,
                    forecast,
                )
                if any(
                    value < 0
                    for vector in (
                        crop_data.tile_capacity,
                        crop_data.action_capacity,
                        crop_data.crop_storage_capacity,
                        crop_data.market_order_slots,
                    )
                    for value in vector
                ):
                    failure_domain = "capacity"
                    last_errors = ("animal plan leaves negative crop capacity",)
                    selected_animals = tuple(
                        animal.animal
                        for animal in animal_result.animals
                        if not animal.existing
                    )
                    rejected = ()
                else:
                    crop_result = solve_oracle(
                        crop_data,
                        self._time_limit,
                        self._mip_rel_gap,
                        accept_feasible=True,
                    )
                    crop_errors = verify_crop_result(crop_data, crop_result)
                    if not crop_result.success or crop_errors:
                        failure_domain = "crop"
                        last_errors = tuple(crop_errors) or ("crop solver failed",)
                        selected_animals = tuple(
                            animal.animal
                            for animal in animal_result.animals
                            if not animal.existing
                        )
                        rejected = ()
                    else:
                        space_data = _space_input(
                            snapshot,
                            animal_data,
                            animal_result,
                            crop_data,
                            crop_result,
                            action_capacity,
                        )
                        space_result = solve_space_plan(
                            space_data,
                            self._time_limit,
                            self._mip_rel_gap,
                        )
                        space_errors = verify_space_result(space_data, space_result)
                        ledger = build_shared_ledger(
                            snapshot,
                            selected,
                            animal_data,
                            animal_result,
                            crop_data,
                            crop_result,
                            field_capacity,
                            action_capacity,
                            iteration,
                            cut_signatures,
                        )
                        ledger_errors = verify_shared_ledger(ledger)
                        selected_animals = tuple(
                            animal.animal
                            for animal in animal_result.animals
                            if not animal.existing
                        )
                        rejected = space_result.rejected_intents
                        verification = ModelVerification(
                            investment_verification,
                            animal_errors,
                            crop_errors,
                            space_errors,
                            ledger_errors,
                        )
                        last_errors = verification.errors + tuple(
                            f"space:rejected:{identifier}" for identifier in rejected
                        )
                        failure_domain = "space-ledger"
                        if not last_errors:
                            solution = WholeFarmSolve(
                                snapshot.registered_seed,
                                investment_results,
                                selected,
                                animal_data,
                                animal_result,
                                crop_data,
                                crop_result,
                                space_data,
                                space_result,
                                ledger,
                                verification,
                                (),
                            )
                            if not portfolio_mode:
                                identifier = "+".join(selected_animals) or "none"
                                return replace(
                                    solution,
                                    animal_candidate_summary=(
                                        (
                                            identifier,
                                            True,
                                            ledger.terminal_cash,
                                            None,
                                        ),
                                    ),
                                )
                            identifier = "+".join(portfolio) or "none"
                            animal_candidate_summary.append(
                                (identifier, True, ledger.terminal_cash, None)
                            )
                            feasible_solutions.append(solution)
                            continue
            signature = canonical_sha256(
                "whole-farm-cut",
                (
                    portfolio,
                    max_animals,
                    animal_storage_capacity,
                    selected_animals,
                    rejected,
                    last_errors,
                ),
            )
            if signature in seen:
                raise WholeFarmSolveError(f"cut cycle detected: {signature}")
            seen.add(signature)
            cut_signatures.append(signature)
            if portfolio_mode:
                identifier = "+".join(portfolio) or "none"
                animal_candidate_summary.append(
                    (identifier, False, None, "; ".join(last_errors))
                )
                continue
            if failure_domain == "crop" and any(animal_storage_capacity):
                reduced = tuple(
                    max(0, capacity - storage_cut_step)
                    for capacity in animal_storage_capacity
                )
                if reduced != animal_storage_capacity:
                    animal_storage_capacity = reduced
                    continue
            if max_animals == 0 or snapshot.animal.fixed_slot_animals:
                break
            max_animals = max(0, max_animals - max(1, len(rejected)))
        if feasible_solutions:
            best = max(
                feasible_solutions,
                key=lambda value: value.ledger.terminal_cash,
            )
            ledger = replace(
                best.ledger,
                iterations=len(portfolios),
                cut_signatures=tuple(cut_signatures),
            )
            return replace(
                best,
                ledger=ledger,
                animal_candidate_summary=tuple(animal_candidate_summary),
            )
        text = "; ".join(last_errors) if last_errors else "no feasible coupled plan"
        raise WholeFarmSolveError(
            f"coupled solve failed after {len(cut_signatures)} cuts: {text}"
        )

    def solve_whole_farm(self, epoch, observation, forecast, window):
        if type(observation) is not RollingObservation:
            raise TypeError("observation has wrong type")
        if type(forecast) is not ShopForecastResult:
            raise TypeError("forecast has wrong type")
        if type(window) is not PlanningWindow:
            raise TypeError("planning window has wrong type")
        snapshot = self._snapshot_provider(observation)
        if type(snapshot) is not WholeFarmSnapshot:
            raise TypeError("snapshot provider returned wrong type")
        if snapshot.source_step != observation.source_step:
            raise ValueError("snapshot and observation source steps differ")
        reasons = []
        if self._last_solve is None:
            reasons.append("initial-plan")
        elif observation.source_step % 24 == 0:
            reasons.append("daily-replan")
        else:
            reasons.append("economic-replan")
        if (
            self._last_shop_signature is not None
            and self._last_shop_signature != forecast.open_shop_signature
        ):
            reasons.append("shop-signature-change")
        reasons = tuple(sorted(reasons))
        solved = self._solve(snapshot, forecast)
        crop_fingerprint = _result_fingerprint(
            "whole-farm-crop",
            solved.crop_result,
            ("input_sha256", "decisions", "purchases", "sales", "terminal_cash"),
        )
        animal_fingerprint = _result_fingerprint(
            "whole-farm-animal",
            solved.animal_result,
            ("input_sha256", "animals", "structures", "purchases", "sales"),
        )
        investment_fingerprint = _result_fingerprint(
            "whole-farm-investment",
            solved.selected_investment,
            ("input_sha256", "mode", "investments", "investment_cost"),
        )
        resource_fingerprint = canonical_sha256(
            "whole-farm-resource-ledger",
            asdict(solved.ledger),
        )
        order_ids = tuple(
            sorted(
                {
                    f"crop-buy:{value.day}:{value.item}"
                    for value in solved.crop_result.purchases
                }
                | {
                    f"crop-sell:{value.day}:{value.crop}"
                    for value in solved.crop_result.sales
                }
                | {
                    f"animal-buy:{value.day}:{value.item}"
                    for value in solved.animal_result.purchases
                    if value.item != "WHEAT"
                }
                | {
                    f"animal-sell:{value.day}:{value.item}"
                    for value in solved.animal_result.sales
                    if value.item not in ("WHEAT", "FERTILIZER")
                }
                | {
                    f"investment:{value.source_step}:{value.operation}:{value.order_index}"
                    for value in solved.selected_investment.investments
                }
            )
        )
        animal_ids = tuple(
            sorted(
                value.identifier
                for value in solved.animal_result.animals
                if not value.existing
            )
        )
        economy = EconomicPlanRef(
            canonical_sha256(
                "whole-farm-economy",
                (
                    epoch,
                    forecast.input_hash,
                    crop_fingerprint,
                    animal_fingerprint,
                    investment_fingerprint,
                    resource_fingerprint,
                ),
            ),
            crop_fingerprint,
            animal_fingerprint,
            investment_fingerprint,
            resource_fingerprint,
            order_ids,
            animal_ids,
        )
        task_ids = tuple(
            sorted(
                f"{task.day}:{task.operation}:{task.position[0]}:{task.position[1]}:{task.subject}"
                for assignment in solved.space_result.assignments
                for task in assignment.tasks
            )
        )
        space = SpacePlanRef(
            canonical_sha256(
                "whole-farm-space",
                (epoch, economy.fingerprint, task_ids),
            ),
            economy.fingerprint,
            task_ids,
            (),
        )
        route_plan = None
        route_problem = None
        handoff = _build_handoff(epoch, snapshot, solved, economy, space)
        if self._route_arm == "planner-2.0":
            route_problem = _build_route_problem(
                snapshot,
                solved,
                handoff,
                observation,
            )
            route_result = plan_routes(route_problem)
            if type(route_result) is RouteFailure:
                raise WholeFarmSolveError(
                    f"route {route_result.phase} failed: {route_result.message}"
                )
            if type(route_result) is not RoutePlan:
                raise WholeFarmSolveError("route planner returned wrong type")
            route_errors = verify_route_plan(route_problem, route_result)
            if route_errors:
                raise WholeFarmSolveError(
                    f"route verification failed: {'; '.join(route_errors)}"
                )
            route_plan = route_result
            route_ids = tuple(
                f"route-2.0:{route.unit_identifier}"
                for route in route_plan.routes
            )
        else:
            route_ids = tuple(
                f"shadow-route:{identifier}" for identifier in task_ids
            ) or (f"shadow-route-reserve:{epoch}",)
        routes = RoutePlanRef(
            canonical_sha256(
                "whole-farm-routes",
                (
                    epoch,
                    economy.fingerprint,
                    space.fingerprint,
                    route_ids,
                    None if route_plan is None else route_plan.fingerprint,
                ),
            ),
            economy.fingerprint,
            space.fingerprint,
            route_ids,
            (),
        )
        if route_plan is not None:
            handoff = _build_handoff(
                epoch,
                snapshot,
                solved,
                economy,
                space,
                "strategy-2.0-execution-route-2.0",
                "planner-2.0",
                route_plan,
            )
        trace = _build_decision_trace(
            epoch,
            reasons,
            snapshot,
            solved,
            handoff,
            economy,
            space,
            routes,
            self._route_arm,
            route_plan,
        )
        self._last_solve = solved
        self._last_handoff = handoff
        self._last_trace = trace
        self._last_shop_signature = forecast.open_shop_signature
        self._last_route_plan = route_plan
        self._last_route_plan_problem = route_problem
        return economy, space, routes

    def repair_space(self, epoch, observation, economy, previous_space):
        if self._last_solve is None:
            raise WholeFarmSolveError("no whole-farm plan to repair")
        fingerprint = canonical_sha256(
            "whole-farm-space-repair",
            (epoch, observation.identity, economy.fingerprint, previous_space.fingerprint),
        )
        return SpacePlanRef(
            fingerprint,
            economy.fingerprint,
            previous_space.spatial_task_ids,
            (),
        )

    def repair_routes(self, epoch, observation, economy, space, previous_routes):
        if self._last_solve is None:
            raise WholeFarmSolveError("no whole-farm plan to repair")
        snapshot = self._snapshot_provider(observation)
        if type(snapshot) is not WholeFarmSnapshot:
            raise TypeError("snapshot provider returned wrong type")
        if snapshot.source_step != observation.source_step:
            raise ValueError("snapshot and observation source steps differ")
        handoff = _build_handoff(
            epoch,
            snapshot,
            self._last_solve,
            economy,
            space,
        )
        route_plan = None
        route_problem = None
        if self._route_arm == "planner-2.0":
            route_problem = _build_route_problem(
                snapshot,
                self._last_solve,
                handoff,
                observation,
            )
            result = plan_routes(route_problem)
            if type(result) is RouteFailure:
                raise WholeFarmSolveError(
                    f"route {result.phase} failed: {result.message}"
                )
            errors = verify_route_plan(route_problem, result)
            if errors:
                raise WholeFarmSolveError(
                    f"route verification failed: {'; '.join(errors)}"
                )
            route_plan = result
            route_ids = tuple(
                f"route-2.0:{route.unit_identifier}"
                for route in route_plan.routes
            )
            handoff = _build_handoff(
                epoch,
                snapshot,
                self._last_solve,
                economy,
                space,
                "strategy-2.0-execution-route-2.0",
                "planner-2.0",
                route_plan,
            )
        else:
            route_ids = previous_routes.route_ids or (
                f"shadow-route-reserve:{epoch}",
            )
        routes = RoutePlanRef(
            canonical_sha256(
                "whole-farm-route-repair",
                (
                    epoch,
                    observation.identity,
                    economy.fingerprint,
                    space.fingerprint,
                    route_ids,
                ),
            ),
            economy.fingerprint,
            space.fingerprint,
            route_ids,
            (),
        )
        trace = _build_decision_trace(
            epoch,
            ("route-repair",),
            snapshot,
            self._last_solve,
            handoff,
            economy,
            space,
            routes,
            self._route_arm,
            route_plan,
        )
        self._last_handoff = handoff
        self._last_trace = trace
        self._last_route_plan = route_plan
        self._last_route_plan_problem = route_problem
        return routes
