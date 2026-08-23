from dataclasses import replace
from types import SimpleNamespace

import pytest

from kaggriculture.tools.economics.animal_milp import GOODS, solve_animal_oracle
from kaggriculture.tools.economics.land_hire_optimizer import OptimizerInput
from kaggriculture.tools.economics.market_ledger import CROPS
from kaggriculture.tools.economics.milp_oracle import ExistingPlant
from kaggriculture.tools.economics.rolling_coordinator import (
    ExecutionSignal,
    PlanFailure,
    RollingCoordinator,
    RollingObservation,
    canonical_sha256,
)
from kaggriculture.tools.economics.run_animal_milp import registered_input as animal_input
from kaggriculture.tools.economics.run_milp_oracle import registered_input as crop_input
from kaggriculture.tools.economics.space_planner import SpaceCell
from kaggriculture.tools.economics.whole_farm_backend import (
    CropTargetIntent,
    ExecutionHandoff,
    Frozen114ExecutionProvider,
    MarketOrderIntent,
    PlanningHorizonConfig,
    RollingHybridExecutionProvider,
    SharedCapacity,
    SpaceTarget,
    WholeFarmPlannerBackend,
    WholeFarmSnapshot,
    WholeFarmSolveError,
    _crop_input,
    _crop_profile,
    _crop_targets,
    _build_handoff,
    _market_order_intents,
    _planning_snapshot,
    verify_shared_ledger,
)
from kaggriculture.tools.routing.offline_route_planner import (
    RouteUnit,
    verify_plan as verify_route_plan,
)


def _investment():
    steps = 23
    return OptimizerInput(
        696,
        718,
        3000.0,
        400.0,
        1,
        0,
        0,
        2,
        1,
        (0.0,) * steps,
        (2,) * steps,
        (0,) * steps,
        (0,) * steps,
        (1,) * steps,
        (2,) * steps,
        (0.0,) * steps,
        "unit-test-v1",
    )


def _snapshot():
    crop = replace(
        crop_input(),
        source_step=696,
        terminal_step=718,
        seeds=(0,) * len(CROPS),
        goods=(0,) * len(CROPS),
        tile_capacity=(1,),
        action_capacity=(8,),
        crop_storage_capacity=(10,),
        wheat_demand=(0,),
        fixed_cash_flow=(0.0,),
        fertilizer_supply=(0,),
        fertilizer_buy_price=(100.0,),
        market_order_slots=(2,),
        base_inventory=((10_000,) * len(CROPS),),
        wheat_buy_price=(25.0,),
        first_plant_day=29,
    )
    animal = replace(
        animal_input(0),
        source_step=696,
        terminal_step=718,
        goods=(0,) * len(GOODS),
        animal_tile_capacity=(1,),
        action_capacity=(8,),
        shed_capacity=(10,),
        fixed_shed_occupancy=(0,),
        market_order_slots=(2,),
        fixed_cash_flow=(0.0,),
        base_inventory=((10_000,) * len(GOODS),),
        fixed_slot_animals=(),
    )
    return WholeFarmSnapshot(
        3_980_000,
        crop,
        animal,
        _investment(),
        (SpaceCell((4, 4), 0, "EMPTY"),),
        SharedCapacity((1,), (8,), (10,), (2,), (2,)),
    )


def _observation(source_step=696):
    return RollingObservation(
        source_step,
        (),
        canonical_sha256("economy", source_step),
        canonical_sha256("topology", source_step),
        canonical_sha256("route", source_step),
        canonical_sha256("progress", source_step),
        ExecutionSignal(),
    )


def _rolling_snapshot(day):
    source_step = day * 24
    days = 30 - day
    steps = 719 - source_step
    crop = replace(
        crop_input(),
        source_step=source_step,
        tile_capacity=(1,) * days,
        action_capacity=(20,) * days,
        crop_storage_capacity=(100,) * days,
        wheat_demand=(0,) * days,
        fixed_cash_flow=(0.0,) * days,
        fertilizer_supply=(0,) * days,
        fertilizer_buy_price=(100.0,) * days,
        market_order_slots=(10,) * days,
        base_inventory=((10_000,) * len(CROPS),) * days,
        wheat_buy_price=(25.0,) * days,
        first_plant_day=day,
    )
    animal = replace(
        animal_input(0),
        source_step=source_step,
        animal_tile_capacity=(1,) * days,
        action_capacity=(20,) * days,
        shed_capacity=(100,) * days,
        fixed_shed_occupancy=(0,) * days,
        market_order_slots=(10,) * days,
        fixed_cash_flow=(0.0,) * days,
        base_inventory=((10_000,) * len(GOODS),) * days,
        fixed_slot_animals=(),
        max_new_animals=1,
    )
    investment = OptimizerInput(
        source_step,
        718,
        3000.0,
        400.0,
        1,
        0,
        0,
        2,
        1,
        (0.0,) * steps,
        (10,) * steps,
        (0,) * steps,
        (25,) * steps,
        (1,) * steps,
        (2,) * steps,
        (0.0,) * steps,
        "unit-test-v1",
    )
    return WholeFarmSnapshot(
        3_980_000,
        crop,
        animal,
        investment,
        (SpaceCell((4, 4), 0, "EMPTY"),),
        SharedCapacity(
            (1,) * days,
            (20,) * days,
            (100,) * days,
            (10,) * days,
            (2,) * days,
        ),
    )


def _tail_forecast(snapshot):
    return SimpleNamespace(
        expected_drain_by_day=((0.0,) * 9,) * snapshot.crop.horizon_days,
        expected_total_drain=(0.0,) * 9,
        open_shop_signature=(),
    )


@pytest.fixture(scope="module")
def solved_backend():
    snapshot = _snapshot()
    backend = WholeFarmPlannerBackend(lambda observation: snapshot, 5, 0, 5)
    coordinator = RollingCoordinator(backend)
    intent = coordinator.prepare(_observation())
    return backend, coordinator, intent


def test_real_models_build_verified_shared_plan(solved_backend):
    backend, _, intent = solved_backend
    solved = backend.last_solve
    assert solved is not None
    assert solved.verification.errors == ()
    assert verify_shared_ledger(solved.ledger) == ()
    assert solved.ledger.terminal_cash == solved.crop_result.terminal_cash
    assert intent.economy.resource_profile_fingerprint == backend.last_trace.fingerprints[4][1]


def test_handoff_targets_frozen_execution_seams(solved_backend):
    backend, _, _ = solved_backend
    handoff = backend.last_handoff
    executor = Frozen114ExecutionProvider(handoff, lambda targets: targets)
    world = SimpleNamespace(step=696)
    planned = executor.plan(world, (("HIRE",),))
    assert planned == (("HIRE",),)
    assert executor.prepare(world) is None
    assert handoff.label == "strategy-2.0-execution-1.14"


def test_frozen_execution_provider_exports_crop_and_market_intents():
    handoff = ExecutionHandoff(
        "strategy-2.0-execution-1.14",
        0,
        0,
        "a" * 64,
        "b" * 64,
        (CropTargetIntent(0, 1, 2, "WHEAT"),),
        (),
        (),
        (MarketOrderIntent("seed", 0, ("BUY_SEED", "WHEAT", 1)),),
    )
    executor = Frozen114ExecutionProvider(handoff, lambda targets: targets)
    world = SimpleNamespace(step=0)
    assert executor.prepare(world) == ((1, 2, "WHEAT"),)
    assert executor.plan(world, (("HIRE",),)) == (("BUY_SEED", "WHEAT", 1),)


def test_decision_trace_is_compact_and_complete(solved_backend):
    backend, _, _ = solved_backend
    trace = backend.last_trace
    assert trace.epoch == 0
    assert trace.day == 29
    assert trace.observed.source_step == 696
    assert len(trace.candidates) == 8
    assert len(trace.fingerprint) == 64
    assert trace.planning_horizon == PlanningHorizonConfig()
    assert trace.strategic_tail is None
    assert trace.runtime_seconds >= 0


@pytest.mark.parametrize(
    ("days", "expected_day", "expected_step"),
    ((3, 2, 71), (5, 4, 119)),
)
def test_exact_horizon_truncates_crop_and_animal_only(
    days,
    expected_day,
    expected_step,
):
    snapshot = _rolling_snapshot(0)
    config = PlanningHorizonConfig(days, True)
    planned, tail = _planning_snapshot(snapshot, _tail_forecast(snapshot), config)
    assert planned.crop.last_day == expected_day
    assert planned.crop.terminal_step == expected_step
    assert planned.animal.last_day == expected_day
    assert planned.crop.horizon_days == days
    assert planned.investment is snapshot.investment
    assert planned.investment.terminal_step == 718
    assert tail.cutoff_day == expected_day
    assert tail.terminal_step == expected_step
    assert tail.crop_active[CROPS.index("STRAWBERRY")] > 100
    assert planned.crop.terminal_values.goods[CROPS.index("WHEAT")] == tail.wheat
    assert planned.animal.terminal_values.goods[GOODS.index("WHEAT")] == 0
    assert planned.animal.terminal_values.goods[GOODS.index("FERTILIZER")] == 0


@pytest.mark.parametrize("day", (27, 28, 29))
def test_exact_horizon_caps_at_game_end_without_fictional_animal_salvage(day):
    snapshot = _rolling_snapshot(day)
    config = PlanningHorizonConfig(5, True)
    planned, tail = _planning_snapshot(snapshot, _tail_forecast(snapshot), config)
    assert planned.crop.last_day == 29
    assert planned.crop.terminal_step == 718
    assert planned.animal.terminal_values is None
    assert tail.animal_active == (0.0,) * 3
    result = solve_animal_oracle(planned.animal, 5, 0)
    assert result.success
    assert not any(purchase.item in ("GOOSE", "COW", "SHEEP") for purchase in result.purchases)
    assert result.structures == ()


def test_default_horizon_preserves_snapshot_identity():
    snapshot = _rolling_snapshot(0)
    planned, tail = _planning_snapshot(
        snapshot,
        _tail_forecast(snapshot),
        PlanningHorizonConfig(),
    )
    assert planned is snapshot
    assert tail is None


def test_exact_horizon_uses_end_of_cutoff_day_from_midday_step():
    snapshot = _rolling_snapshot(0)
    source_step = 10
    investment = replace(
        snapshot.investment,
        source_step=source_step,
        fixed_cash_flow=snapshot.investment.fixed_cash_flow[source_step:],
        market_order_slots=snapshot.investment.market_order_slots[source_step:],
        existing_work=snapshot.investment.existing_work[source_step:],
        land_work_per_quadrant=snapshot.investment.land_work_per_quadrant[
            source_step:
        ],
        base_work_capacity=snapshot.investment.base_work_capacity[source_step:],
        executor_work_capacity=snapshot.investment.executor_work_capacity[
            source_step:
        ],
        terminal_value_per_work=snapshot.investment.terminal_value_per_work[
            source_step:
        ],
    )
    snapshot = replace(
        snapshot,
        crop=replace(snapshot.crop, source_step=source_step),
        animal=replace(snapshot.animal, source_step=source_step),
        investment=investment,
    )
    planned, tail = _planning_snapshot(
        snapshot,
        _tail_forecast(snapshot),
        PlanningHorizonConfig(5, True),
    )
    assert planned.source_step == source_step
    assert planned.crop.terminal_step == 119
    assert planned.investment.terminal_step == 718
    assert tail.cutoff_day == 4


@pytest.mark.parametrize("days", (0, 31))
def test_exact_horizon_rejects_invalid_days(days):
    with pytest.raises(ValueError, match="1..30"):
        PlanningHorizonConfig(days, False)


def test_strategic_tail_rejects_full_horizon():
    with pytest.raises(ValueError, match="shorter horizon"):
        PlanningHorizonConfig(30, True)


def test_market_handoff_commits_only_current_day_orders():
    snapshot = _rolling_snapshot(0)
    crop_result = SimpleNamespace(
        purchases=(
            SimpleNamespace(item="STRAWBERRY_SEED", day=0, quantity=1),
            SimpleNamespace(item="WHEAT_SEED", day=1, quantity=1),
        ),
        sales=(),
    )
    animal_result = SimpleNamespace(purchases=(), sales=())
    selected_investment = SimpleNamespace(
        investments=(
            SimpleNamespace(operation="BUY_LAND", source_step=0, order_index=0),
            SimpleNamespace(operation="HIRE", source_step=24, order_index=0),
        )
    )
    solved = SimpleNamespace(
        crop_result=crop_result,
        animal_result=animal_result,
        selected_investment=selected_investment,
    )
    intents = _market_order_intents(snapshot, solved)
    assert tuple(intent.order for intent in intents) == (
        ("BUY_SEED", "STRAWBERRY", 1),
        ("BUY_LAND",),
    )


def test_hybrid_provider_requires_every_daily_epoch(solved_backend):
    backend, coordinator, _ = solved_backend
    provider = RollingHybridExecutionProvider(
        coordinator,
        backend,
        lambda world: _observation(),
    )
    provider.prepare(SimpleNamespace(step=696))
    with pytest.raises(WholeFarmSolveError, match="missing daily solves"):
        provider.verify_daily_epochs()


def test_macro_portfolio_enumeration_records_joint_objective():
    snapshot = replace(_snapshot(), animal_portfolios=((),))
    backend = WholeFarmPlannerBackend(lambda observation: snapshot, 5, 0, 5)
    intent = RollingCoordinator(backend).prepare(_observation())
    assert intent.epoch == 0
    assert backend.last_solve.animal_candidate_summary == (
        ("none", True, backend.last_solve.ledger.terminal_cash, None),
    )


def test_storage_cut_preserves_observed_animal_goods():
    snapshot = _snapshot()
    crop_goods = list(snapshot.crop.goods)
    crop_goods[CROPS.index("WHEAT")] = 1
    animal_goods = list(snapshot.animal.goods)
    animal_goods[GOODS.index("EGG")] = 1
    snapshot = replace(
        snapshot,
        crop=replace(
            snapshot.crop,
            goods=tuple(crop_goods),
            tile_capacity=(3,),
        ),
        animal=replace(
            snapshot.animal,
            goods=tuple(animal_goods),
            animal_tile_capacity=(3,),
            fixed_shed_occupancy=(1,),
        ),
        shared=SharedCapacity((3,), (8,), (10,), (2,), (2,)),
        animal_portfolios=((),),
    )
    backend = WholeFarmPlannerBackend(lambda observation: snapshot, 5, 0, 5)
    intent = RollingCoordinator(backend).prepare(_observation())
    assert intent.epoch == 0
    assert backend.last_solve.verification.errors == ()


def test_crop_capacity_excludes_existing_plants():
    snapshot = _snapshot()
    plant = ExistingPlant((4, 4), "CARROT", 27, 1, False, 0, -1)
    snapshot = replace(
        snapshot,
        crop=replace(snapshot.crop, existing_plants=(plant,)),
        cells=(SpaceCell((4, 4), 0, "PLANT", "CARROT", 35, 29),),
    )
    empty = (0,)
    animal_profile = {
        "actions": empty,
        "fields": empty,
        "storage": empty,
        "orders": empty,
        "cash_flow": (0.0,),
        "wheat_feed": empty,
        "fertilizer_supply": empty,
    }
    forecast = SimpleNamespace(
        expected_drain_by_day=((0.0,) * 9,),
    )
    crop = _crop_input(
        snapshot,
        SimpleNamespace(investment_cost=0.0),
        animal_profile,
        (1,),
        (8,),
        forecast,
    )
    assert crop.tile_capacity == (0,)


def test_crop_profile_counts_unreleased_existing_plants():
    plant = ExistingPlant((4, 4), "CARROT", 0, 1, False, 0, -1)
    data = replace(crop_input(), existing_plants=(plant,))
    balances = tuple(
        SimpleNamespace(goods=(0,) * len(CROPS), fertilizer=0)
        for _ in range(data.horizon_days)
    )
    result = SimpleNamespace(
        decisions=(),
        purchases=(),
        sales=(),
        balances=balances,
    )
    profile = _crop_profile(data, result)
    assert profile["fields"] == (1,) * data.horizon_days


def test_rolling_ledger_reuses_released_existing_crop_tile():
    source_step = 576
    days = 6
    steps = 143
    plant = ExistingPlant((4, 4), "CARROT", 20, 1, False, 0, -1)
    seeds = [0] * len(CROPS)
    seeds[CROPS.index("CARROT")] = 2
    crop = replace(
        crop_input(),
        source_step=source_step,
        seeds=tuple(seeds),
        existing_plants=(plant,),
        tile_capacity=(1,) * days,
        action_capacity=(20,) * days,
        crop_storage_capacity=(100,) * days,
        wheat_demand=(0,) * days,
        fixed_cash_flow=(0.0,) * days,
        fertilizer_supply=(0,) * days,
        fertilizer_buy_price=(100.0,) * days,
        market_order_slots=(10,) * days,
        base_inventory=((10_000,) * len(CROPS),) * days,
        wheat_buy_price=(25.0,) * days,
        first_plant_day=24,
    )
    animal = replace(
        animal_input(0),
        source_step=source_step,
        animal_tile_capacity=(1,) * days,
        action_capacity=(20,) * days,
        shed_capacity=(100,) * days,
        fixed_shed_occupancy=(0,) * days,
        market_order_slots=(10,) * days,
        fixed_cash_flow=(0.0,) * days,
        base_inventory=((10_000,) * len(GOODS),) * days,
    )
    investment = OptimizerInput(
        source_step,
        718,
        3000.0,
        400.0,
        4,
        0,
        0,
        0,
        1,
        (0.0,) * steps,
        (10,) * steps,
        (0,) * steps,
        (0,) * steps,
        (1,) * steps,
        (1,) * steps,
        (0.0,) * steps,
        "registered-executor-capacity-v1",
    )
    snapshot = WholeFarmSnapshot(
        3_980_000,
        crop,
        animal,
        investment,
        (SpaceCell((4, 4), 0, "PLANT", "CARROT", 35, 24),),
        SharedCapacity(
            (1,) * days,
            (20,) * days,
            (100,) * days,
            (10,) * days,
            (2,) * days,
        ),
        ((),),
    )
    backend = WholeFarmPlannerBackend(lambda observation: snapshot, 5, 0.05, 5)
    intent = RollingCoordinator(backend).prepare(_observation(source_step))
    assert not isinstance(intent, PlanFailure), intent.exception_text
    assert intent.epoch == 0
    assert verify_shared_ledger(backend.last_solve.ledger) == ()
    assert all(
        day.crop_field_use + day.animal_field_use <= day.field_capacity
        for day in backend.last_solve.ledger.days
    )
    assert backend.last_handoff.crop_targets
    assert {
        (target.y, target.x) for target in backend.last_handoff.crop_targets
    } == {(4, 4)}


def test_crop_target_uses_projected_land_from_unlock_day():
    snapshot = replace(
        _snapshot(),
        cells=(
            SpaceCell((0, 5), 29, "EMPTY"),
            SpaceCell((5, 0), 29, "EMPTY"),
        ),
    )
    investment = SimpleNamespace(
        projections=(SimpleNamespace(source_step=120, unlocked_quadrants=2),),
    )
    before = SimpleNamespace(
        crop="CARROT",
        count=1,
        existing_position=None,
        plant_day=4,
        release_day=None,
    )
    solved = SimpleNamespace(
        crop_result=SimpleNamespace(decisions=(before,)),
        selected_investment=investment,
    )
    with pytest.raises(WholeFarmSolveError, match="lacks crop target"):
        _crop_targets(snapshot, solved, ())

    at_unlock = SimpleNamespace(
        crop="CARROT",
        count=1,
        existing_position=None,
        plant_day=5,
        release_day=None,
    )
    solved = SimpleNamespace(
        crop_result=SimpleNamespace(decisions=(at_unlock,)),
        selected_investment=investment,
    )
    targets = _crop_targets(snapshot, solved, ())
    assert tuple((target.y, target.x) for target in targets) == ((0, 5),)


def test_crop_target_can_precede_future_animal_placement():
    snapshot = replace(
        _snapshot(),
        cells=(SpaceCell((4, 4), 0, "EMPTY"),),
    )
    decision = SimpleNamespace(
        crop="CARROT",
        count=1,
        existing_position=None,
        plant_day=1,
        release_day=4,
    )
    solved = SimpleNamespace(
        crop_result=SimpleNamespace(decisions=(decision,)),
        selected_investment=SimpleNamespace(projections=()),
    )
    animal_target = SpaceTarget("animal-0", "SHEEP", 4, 4, "BUILD", 5)
    targets = _crop_targets(snapshot, solved, (animal_target,))
    assert tuple((target.y, target.x) for target in targets) == ((4, 4),)

    decision = SimpleNamespace(
        crop="STRAWBERRY",
        count=1,
        existing_position=None,
        plant_day=1,
        release_day=None,
    )
    solved = SimpleNamespace(
        crop_result=SimpleNamespace(decisions=(decision,)),
        selected_investment=SimpleNamespace(projections=()),
    )
    with pytest.raises(WholeFarmSolveError, match="lacks crop target"):
        _crop_targets(snapshot, solved, (animal_target,))


def test_current_crop_target_respects_uncommitted_future_animal_slot():
    snapshot = replace(
        _rolling_snapshot(0),
        cells=(
            SpaceCell((4, 4), 0, "EMPTY"),
            SpaceCell((0, 0), 0, "EMPTY"),
        ),
    )
    crop_decision = SimpleNamespace(
        crop="STRAWBERRY",
        count=1,
        existing_position=None,
        plant_day=0,
        release_day=None,
    )
    assignment = SimpleNamespace(
        intent="animal-0",
        animal="SHEEP",
        position=(4, 4),
        mode="BUILD",
        placement_day=2,
    )
    solved = SimpleNamespace(
        crop_result=SimpleNamespace(
            decisions=(crop_decision,),
            purchases=(),
            sales=(),
        ),
        animal_result=SimpleNamespace(animals=(), purchases=(), sales=()),
        space_result=SimpleNamespace(assignments=(assignment,)),
        selected_investment=SimpleNamespace(investments=(), projections=()),
    )
    economy = SimpleNamespace(fingerprint="a" * 64)
    space = SimpleNamespace(fingerprint="b" * 64)
    handoff = _build_handoff(0, snapshot, solved, economy, space)
    assert handoff.space_targets == ()
    assert tuple((target.y, target.x) for target in handoff.crop_targets) == ((0, 0),)


def test_second_arm_plans_daily_crop_service_route():
    snapshot = _snapshot()
    plant = ExistingPlant((4, 4), "CARROT", 27, 3, False, 0, -1)
    crop = replace(
        snapshot.crop,
        existing_plants=(plant,),
        tile_capacity=(0,),
    )
    snapshot = replace(
        snapshot,
        crop=crop,
        cells=(SpaceCell((4, 4), 0, "PLANT", "CARROT", 100, 29),),
        route_units=(RouteUnit("unit-0", (4, 4)),),
    )
    backend = WholeFarmPlannerBackend(
        lambda observation: snapshot,
        5,
        0,
        5,
        "planner-2.0",
    )
    intent = RollingCoordinator(backend).prepare(_observation())
    assert intent.epoch == 0
    assert backend.last_handoff.label == "strategy-2.0-execution-route-2.0"
    assert backend.last_handoff.route_commands
    assert verify_route_plan(
        backend.last_route_plan_problem,
        backend.last_route_plan,
    ) == ()


def test_second_arm_replans_route_and_exports_new_epoch():
    snapshot = replace(
        _snapshot(),
        route_units=(RouteUnit("unit-0", (4, 4)),),
    )
    backend = WholeFarmPlannerBackend(
        lambda observation: snapshot,
        5,
        0,
        5,
        "planner-2.0",
    )
    intent = RollingCoordinator(backend).prepare(_observation())
    routes = backend.repair_routes(
        1,
        _observation(),
        intent.economy,
        intent.space,
        intent.routes,
    )
    assert routes.route_ids == ("route-2.0:unit-0",)
    assert backend.last_handoff.epoch == 1
    assert backend.last_trace.epoch == 1
    assert backend.last_trace.reasons == ("route-repair",)
    assert verify_route_plan(
        backend.last_route_plan_problem,
        backend.last_route_plan,
    ) == ()


def test_shared_capacity_rejects_route_overcommit():
    with pytest.raises(ValueError, match="route reserve"):
        SharedCapacity((1,), (1,), (1,), (1,), (2,))


def test_snapshot_enforces_crop_wheat_ownership():
    snapshot = _snapshot()
    goods = list(snapshot.animal.goods)
    goods[GOODS.index("WHEAT")] = 1
    with pytest.raises(ValueError, match="initial wheat"):
        replace(snapshot, animal=replace(snapshot.animal, goods=tuple(goods)))
