import dataclasses
import pathlib
import sys

import pytest


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from economics.rolling_coordinator import canonical_sha256
from routing.execution_provider import (
    ExecutionRouteError,
    ExecutionRouteProvider,
    OfflineActionProvider,
    build_route_tasks,
    convert_execution,
    observe_execution,
    view_handoff,
)
from routing.run_execution_provider_replay import analyze_replay, build_result


REAL_REPLAY = pathlib.Path(
    "/private/tmp/kaggriculture-agent-2-stage39-16b-offline-replay/kaggriculture/"
    "replays/round39_16b/round39_16b_smoke_vs_v1_14_0_3980000_seat_0.json.gz"
)


@dataclasses.dataclass(frozen=True)
class CropTarget:
    day: int
    x: int
    y: int
    crop: str


@dataclasses.dataclass(frozen=True)
class AnimalIntent:
    identifier: str
    animal: str
    purchase_day: int
    placement_day: int


@dataclasses.dataclass(frozen=True)
class SpaceTarget:
    identifier: str
    animal: str
    x: int
    y: int
    mode: str
    placement_day: int


@dataclasses.dataclass(frozen=True)
class MarketOrder:
    identifier: str
    source_step: int
    order: tuple


@dataclasses.dataclass(frozen=True)
class Handoff:
    label: str
    epoch: int
    source_step: int
    economic_fingerprint: str
    space_fingerprint: str
    crop_targets: tuple = ()
    animal_intents: tuple = ()
    space_targets: tuple = ()
    market_orders: tuple = ()


class Source:
    def __init__(self, handoff):
        self.handoff = handoff
        self.calls = 0
        self.resets = 0

    def __call__(self, observation):
        self.calls += 1
        with pytest.raises(TypeError):
            observation["day"] = 12
        with pytest.raises(TypeError):
            observation["farms"][0]["tiles"][0][0] = "WEED"
        return self.handoff

    def reset(self):
        self.resets += 1


def _fingerprint(name):
    return canonical_sha256("test-route-execution", name)


def _handoff(
    step=0,
    epoch=0,
    crops=(),
    animals=(),
    spaces=(),
    orders=(),
):
    return Handoff(
        "strategy-2.0-execution",
        epoch,
        step,
        _fingerprint(("economic", epoch, step)),
        _fingerprint(("space", epoch, step)),
        tuple(crops),
        tuple(animals),
        tuple(spaces),
        tuple(orders),
    )


def _board():
    return [
        [None if x < 5 and y < 5 else "LOCKED" for x in range(10)]
        for y in range(10)
    ]


def _observation(
    step=0,
    tiles=None,
    farmer=(4, 4),
    hands=(),
    inventories=None,
    shed=None,
    seeds=None,
):
    board = _board() if tiles is None else tiles
    unit_inventories = (
        [{} for _unit in range(1 + len(hands))]
        if inventories is None
        else inventories
    )
    player_farm = {
        "farmer": list(farmer),
        "hands": [list(position) for position in hands],
        "tiles": board,
    }
    opponent_farm = {
        "farmer": [4, 4],
        "hands": [],
        "tiles": _board(),
    }
    return {
        "step": step,
        "day": step // 24,
        "hour": step % 24,
        "player": 0,
        "farms": [player_farm, opponent_farm],
        "private": {
            "inventories": unit_inventories,
            "shed": {} if shed is None else shed,
            "seeds": {} if seeds is None else seeds,
        },
    }


def _plant(crop, day, watered=False, yield_units=0, fertilizer=-1):
    return {
        "kind": "PLANT",
        "crop": crop,
        "planted_day": day,
        "watered_today": watered,
        "consecutive_unwatered": 0,
        "yield_units": yield_units,
        "max_lifespan_step": -1,
        "fertilized_until_day": fertilizer,
    }


def _animal(animal="COW", fed=False, cared=False, yield_units=0, fertilizer=False):
    return {
        "kind": "PASTURE" if animal != "GOOSE" else "COOP",
        "animal": animal,
        "placed_day": 0,
        "fed_today": fed,
        "cared_today": cared,
        "consecutive_unfed": 0,
        "yield_units": yield_units,
        "fertilizer_available": fertilizer,
        "pending_care_bonus": 0,
    }


def _tasks(observation, handoff):
    view = observe_execution(observation)
    return build_route_tasks(view, view_handoff(handoff, view.source_step))


def _by_operation(tasks):
    return {task.action[0]: task for task in tasks}


def test_provider_is_structurally_compatible_and_resets_source():
    source = Source(_handoff())
    provider = ExecutionRouteProvider(source)
    assert isinstance(provider, OfflineActionProvider)
    assert provider.act(_observation()) == {
        "farmer": ["PASS"],
        "hands": [],
        "market": [],
    }
    provider.reset()
    assert source.calls == 1
    assert source.resets == 1
    assert provider.plans_built == 0


def test_source_reset_failure_still_clears_execution_state():
    source = Source(_handoff(crops=(CropTarget(0, 4, 4, "WHEAT"),)))
    provider = ExecutionRouteProvider(source)
    provider.act(_observation(seeds={"WHEAT": 1}))

    def fail_reset():
        raise RuntimeError("reset")

    source.reset = fail_reset
    with pytest.raises(RuntimeError, match="reset"):
        provider.reset()
    assert provider.plan is None
    assert provider.plans_built == 0


def test_crop_target_becomes_complete_plant_water_chain():
    handoff = _handoff(crops=(CropTarget(0, 4, 4, "WHEAT"),))
    tasks = _tasks(_observation(seeds={"WHEAT": 1}), handoff)
    operations = _by_operation(tasks)
    assert set(operations) == {"PLANT", "WATER"}
    assert operations["PLANT"].requires == ()
    assert operations["WATER"].dependencies == (operations["PLANT"].identifier,)


def test_crop_target_rejects_incompatible_occupied_cell():
    board = _board()
    board[4][4] = _plant("CARROT", 0)
    handoff = _handoff(crops=(CropTarget(0, 4, 4, "WHEAT"),))
    with pytest.raises(ValueError, match="incompatible tile"):
        _tasks(_observation(tiles=board), handoff)


def test_existing_crop_adds_fertilizer_water_and_harvest():
    board = _board()
    board[4][4] = _plant("STRAWBERRY", 0, yield_units=2)
    tasks = _tasks(
        _observation(step=7 * 24, tiles=board, shed={"FERTILIZER": 1}),
        _handoff(),
    )
    operations = _by_operation(tasks)
    assert set(operations) == {"FERTILIZE", "WATER"}
    assert operations["FERTILIZE"].requires == (("FERTILIZER", 1),)
    assert operations["WATER"].dependencies == (
        operations["FERTILIZE"].identifier,
    )

    board[4][4] = _plant("WHEAT", 0, yield_units=5)
    tasks = _tasks(_observation(step=4 * 24, tiles=board), _handoff())
    operations = _by_operation(tasks)
    assert set(operations) == {"WATER", "HARVEST"}
    assert operations["HARVEST"].dependencies == (
        operations["WATER"].identifier,
    )
    assert operations["HARVEST"].produces == (("WHEAT", 6),)


def test_space_target_builds_one_weed_chain_and_services_animal():
    board = _board()
    board[2][2] = {"kind": "WEED"}
    animal = AnimalIntent("cow-0", "COW", 0, 0)
    space = SpaceTarget("cow-0", "COW", 2, 2, "clear_weed", 0)
    tasks = _tasks(
        _observation(tiles=board, shed={"COW": 1, "WHEAT": 1}),
        _handoff(animals=(animal,), spaces=(space,)),
    )
    by_operation = _by_operation(tasks)
    assert [task.action[0] for task in tasks].count("DIG") == 1
    assert by_operation["BUILD_PASTURE"].dependencies == (
        by_operation["DIG"].identifier,
    )
    assert by_operation["PLACE"].dependencies == (
        by_operation["BUILD_PASTURE"].identifier,
    )
    assert by_operation["FEED"].dependencies == (
        by_operation["PLACE"].identifier,
    )
    assert by_operation["CARE"].dependencies == (
        by_operation["FEED"].identifier,
    )


def test_animal_intents_and_space_targets_must_be_a_bijection():
    animal = AnimalIntent("cow-0", "COW", 0, 0)
    with pytest.raises(ValueError, match="must match"):
        view_handoff(_handoff(animals=(animal,)), 0)

    space = SpaceTarget("cow-0", "COW", 2, 2, "use_empty", 1)
    with pytest.raises(ValueError, match="lacks matching"):
        view_handoff(_handoff(animals=(animal,), spaces=(space,)), 0)


def test_dig_crop_does_not_schedule_destroyed_crop_service():
    board = _board()
    board[2][2] = _plant("WHEAT", 0, yield_units=6)
    animal = AnimalIntent("cow-0", "COW", 0, 0)
    space = SpaceTarget("cow-0", "COW", 2, 2, "dig_crop", 0)
    tasks = _tasks(
        _observation(step=3 * 24, tiles=board, shed={"COW": 1, "WHEAT": 1}),
        _handoff(animals=(animal,), spaces=(space,)),
    )
    operations = {task.action[0] for task in tasks}
    assert "DIG" in operations
    assert "PLACE" in operations
    assert "WATER" not in operations
    assert "HARVEST" not in operations


def test_existing_animal_adds_daily_service_and_products():
    board = _board()
    board[4][4] = _animal(yield_units=3, fertilizer=True)
    tasks = _tasks(
        _observation(tiles=board, shed={"WHEAT": 1}),
        _handoff(),
    )
    operations = _by_operation(tasks)
    assert set(operations) == {"FEED", "CARE", "HARVEST", "COLLECT_FERTILIZER"}
    assert operations["FEED"].requires == (("WHEAT", 1),)
    assert operations["CARE"].dependencies == (operations["FEED"].identifier,)
    assert operations["HARVEST"].produces == (("MILK", 3),)


def test_market_orders_only_run_at_their_exact_step_and_limit():
    orders = tuple(
        MarketOrder(f"order-{index}", 4, ("BUY_SEED", "WHEAT", 1))
        for index in range(12)
    )
    handoff = _handoff(orders=orders)
    assert len(convert_execution(_observation(step=4), handoff).market_orders) == 10
    assert convert_execution(_observation(step=5), handoff).market_orders == ()


def test_current_seed_purchase_waits_one_turn_before_planting():
    handoff = _handoff(
        crops=(CropTarget(0, 4, 4, "WHEAT"),),
        orders=(MarketOrder("seed", 0, ("BUY_SEED", "WHEAT", 1)),),
    )
    provider = ExecutionRouteProvider(Source(handoff))
    assert provider.act(_observation()) == {
        "farmer": ["PASS"],
        "hands": [],
        "market": [["BUY_SEED", "WHEAT", 1]],
    }
    action = provider.act(_observation(step=1, seeds={"WHEAT": 1}))
    assert action["farmer"] == ["PLANT", "WHEAT"]
    assert provider.plans_built == 1


def test_expected_crop_progress_keeps_daily_route():
    handoff = _handoff(crops=(CropTarget(0, 4, 4, "WHEAT"),))
    provider = ExecutionRouteProvider(Source(handoff))
    first = provider.act(_observation(seeds={"WHEAT": 1}))
    fingerprint = provider.plan.fingerprint
    assert first["farmer"] == ["PLANT", "WHEAT"]

    board = _board()
    board[4][4] = _plant("WHEAT", 0)
    second = provider.act(_observation(step=1, tiles=board))
    assert second["farmer"] == ["WATER"]
    assert provider.plan.fingerprint == fingerprint
    assert provider.plans_built == 1


def test_failed_task_effect_rebuilds_and_retries():
    handoff = _handoff(crops=(CropTarget(0, 4, 4, "WHEAT"),))
    provider = ExecutionRouteProvider(Source(handoff))
    first = provider.act(_observation(seeds={"WHEAT": 1}))
    second = provider.act(_observation(step=1, seeds={"WHEAT": 1}))
    assert first["farmer"] == ["PLANT", "WHEAT"]
    assert second["farmer"] == ["PLANT", "WHEAT"]
    assert provider.plans_built == 2


def test_failed_pickup_rebuilds_before_feed():
    board = _board()
    board[4][4] = _animal()
    provider = ExecutionRouteProvider(Source(_handoff()))
    first = provider.act(_observation(tiles=board, shed={"WHEAT": 1}))
    second = provider.act(
        _observation(step=1, tiles=board, shed={"WHEAT": 1})
    )
    assert first["farmer"] == ["PICKUP", "WHEAT", 1]
    assert second["farmer"] == ["PICKUP", "WHEAT", 1]
    assert provider.plans_built == 2


def test_failed_drop_rebuilds_and_retries():
    board = _board()
    board[4][4] = _plant("WHEAT", 0, watered=True, yield_units=6)
    provider = ExecutionRouteProvider(Source(_handoff()))
    first = provider.act(_observation(step=4 * 24, tiles=board))
    assert first["farmer"] == ["HARVEST"]

    empty = _board()
    carrying = [{"WHEAT": 6}]
    second = provider.act(
        _observation(step=4 * 24 + 1, tiles=empty, inventories=carrying)
    )
    third = provider.act(
        _observation(step=4 * 24 + 2, tiles=empty, inventories=carrying)
    )
    assert second["farmer"] == ["DROP"]
    assert third["farmer"] == ["DROP"]
    assert provider.plans_built == 2


def test_new_hand_and_handoff_epoch_each_rebuild_route():
    board = _board()
    board[0][0] = "WEED"
    source = Source(_handoff())
    provider = ExecutionRouteProvider(source)
    provider.act(_observation(tiles=board))
    source.handoff = _handoff(epoch=1)
    provider.act(_observation(step=1, tiles=board))
    provider.act(
        _observation(
            step=2,
            tiles=board,
            hands=((4, 4),),
            inventories=({}, {}),
        )
    )
    assert provider.plans_built == 3


def test_invalid_observation_and_missing_resources_fail_closed():
    with pytest.raises(TypeError, match="observation must be a mapping"):
        observe_execution([])
    invalid = _observation()
    invalid["private"]["inventories"] = []
    with pytest.raises(ValueError, match="must match current units"):
        observe_execution(invalid)

    board = _board()
    board[4][4] = _animal()
    provider = ExecutionRouteProvider(Source(_handoff()))
    with pytest.raises(ExecutionRouteError, match="missing current resources: WHEAT"):
        provider.act(_observation(tiles=board))


def test_execution_provider_has_no_control_arm_import():
    path = pathlib.Path(__file__).resolve().parent.parent / "tools/routing/execution_provider.py"
    source = path.read_text()
    assert "v1_14" not in source
    assert "agents_1.0" not in source


@pytest.mark.skipif(not REAL_REPLAY.exists(), reason="39.16B replay is external")
def test_real_replay_covers_execution_observation_shapes():
    result = analyze_replay(REAL_REPLAY)
    assert result["observations"] == 720
    assert result["actionable_observations"] == 719
    assert result["converted_observations"] == 719
    assert result["max_units"] == 11
    assert set(result["tile_states"]) == {
        "animal",
        "empty",
        "locked",
        "pasture",
        "plant",
        "weed",
    }
    assert set(result["operations"]) == {
        "CARE",
        "COLLECT_FERTILIZER",
        "FEED",
        "FERTILIZE",
        "HARVEST",
        "DIG",
        "WATER",
    }
    registered = build_result(REAL_REPLAY)
    assert registered["coverage"] == result
    assert registered["comparator"]["commit"] == "b74a3ea"
    assert registered["game_played"] is False
    assert len(registered["result_sha256"]) == 64
