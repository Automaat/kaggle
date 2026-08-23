from dataclasses import replace
from types import SimpleNamespace

import pytest

from kaggriculture.tools.economics.animal_milp import GOODS
from kaggriculture.tools.economics.land_hire_optimizer import OptimizerInput
from kaggriculture.tools.economics.market_ledger import CROPS
from kaggriculture.tools.economics.milp_oracle import ExistingPlant
from kaggriculture.tools.economics.rolling_coordinator import (
    ExecutionSignal,
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
    RollingHybridExecutionProvider,
    SharedCapacity,
    WholeFarmPlannerBackend,
    WholeFarmSnapshot,
    WholeFarmSolveError,
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


def _observation():
    return RollingObservation(
        696,
        (),
        canonical_sha256("economy", 696),
        canonical_sha256("topology", 696),
        canonical_sha256("route", 696),
        canonical_sha256("progress", 696),
        ExecutionSignal(),
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
