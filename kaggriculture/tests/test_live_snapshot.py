import json
from types import SimpleNamespace

import pytest

from kaggriculture.tools.economics.animal_milp import ANIMALS, GOODS
from kaggriculture.tools.economics.live_snapshot import (
    LiveSnapshotAdapter,
    LiveSnapshotError,
)
from kaggriculture.tools.economics.market_ledger import CROPS, PRODUCTS, SHED_ITEMS


def _market():
    prices = {
        "WHEAT": 25,
        "CARROT": 35,
        "TOMATO": 60,
        "STRAWBERRY": 120,
        "MELON": 250,
        "EGG": 50,
        "MILK": 160,
        "WOOL": 200,
        "FERTILIZER": 100,
    }
    return {"inventory": {item: 10_000 for item in PRODUCTS}, "prices": prices}


def _board(unlocked):
    result = []
    for y in range(10):
        row = []
        for x in range(10):
            quadrant = ("SW" if y >= 5 else "NW") if x < 5 else ("SE" if y >= 5 else "NE")
            row.append(None if quadrant in unlocked else "LOCKED")
        result.append(row)
    return result


def _observation(step=0, unlocked=("NW",), hands=()):
    day = step // 24
    hour = step % 24
    board = _board(unlocked)
    farm = {
        "farmer": [4, 4],
        "hands": [list(value) for value in hands],
        "hires_today": len(hands),
        "money": 3000.0,
        "tiles": board,
        "unlocked_quadrants": list(unlocked),
    }
    other = {
        "farmer": [4, 4],
        "hands": [],
        "hires_today": 0,
        "money": 3000.0,
        "tiles": _board(("NW",)),
        "unlocked_quadrants": ["NW"],
    }
    return {
        "day": day,
        "farms": [farm, other],
        "hour": hour,
        "market": _market(),
        "player": 0,
        "private": {
            "inventories": [{} for _ in range(len(hands) + 1)],
            "seeds": {item: 0 for item in CROPS},
            "shed": {item: 0 for item in SHED_ITEMS},
        },
        "step": step,
        "town": {"unlocked_shops": []},
    }


def test_initial_observation_builds_full_horizon_snapshot():
    adapter = LiveSnapshotAdapter(3_980_000)

    rolling = adapter.observe(_observation())
    snapshot = adapter.snapshot(rolling)

    assert rolling.source_step == 0
    assert snapshot.source_step == 0
    assert snapshot.crop.horizon_days == 30
    assert snapshot.animal.horizon_days == 30
    assert snapshot.crop.tile_capacity == (25,) * 30
    assert snapshot.shared.actions == (24,) * 29 + (23,)
    assert snapshot.shared.route_action_reserve == (12,) * 30
    assert snapshot.animal_portfolios == tuple(
        ("SHEEP",) * count for count in range(4, -1, -1)
    )
    assert len(snapshot.cells) == 100
    assert sum(cell.kind == "EMPTY" and cell.unlock_day == 0 for cell in snapshot.cells) == 25


def test_live_state_maps_plants_animals_structures_and_inventory():
    values = _observation(240, ("NW", "NE"), ((4, 5), (5, 4)))
    farm = values["farms"][0]
    farm["money"] = 12_345.0
    farm["tiles"][0][0] = {
        "consecutive_unwatered": 0,
        "crop": "MELON",
        "fertilized_until_day": -1,
        "kind": "PLANT",
        "max_lifespan_step": 312,
        "planted_day": 0,
        "watered_today": False,
        "yield_units": 5,
    }
    farm["tiles"][0][1] = {
        "animal": "SHEEP",
        "cared_today": False,
        "consecutive_unfed": 1,
        "fed_today": False,
        "fertilizer_available": True,
        "kind": "PASTURE",
        "pending_care_bonus": 0,
        "placed_day": 9,
        "yield_units": 2,
    }
    farm["tiles"][0][2] = {"kind": "PASTURE"}
    farm["tiles"][0][3] = {"kind": "WEED"}
    private = values["private"]
    private["shed"]["WHEAT"] = 7
    private["shed"]["FERTILIZER"] = 3
    private["shed"]["WOOL"] = 4
    private["shed"]["SHEEP"] = 1
    private["seeds"]["STRAWBERRY"] = 6
    private["inventories"][1]["WHEAT"] = 2
    values["town"]["unlocked_shops"] = ["YARN_STORE"]
    adapter = LiveSnapshotAdapter(3_980_000)

    rolling = adapter.observe(values)
    snapshot = adapter.snapshot(rolling)

    assert rolling.open_shops == ("YARN_STORE",)
    assert snapshot.crop.cash == 12_345.0
    assert snapshot.crop.seeds[CROPS.index("STRAWBERRY")] == 6
    assert snapshot.crop.goods[CROPS.index("WHEAT")] == 9
    assert snapshot.crop.fertilizer_stock == 3
    assert snapshot.animal.goods[GOODS.index("WHEAT")] == 0
    assert snapshot.animal.goods[GOODS.index("WOOL")] == 4
    assert snapshot.animal.shed_animals[ANIMALS.index("SHEEP")] == 1
    assert snapshot.animal.fixed_shed_occupancy[0] == 12
    assert len(snapshot.crop.existing_plants) == 1
    assert snapshot.crop.existing_plants[0].position == (0, 0)
    assert len(snapshot.animal.existing_animals) == 1
    assert snapshot.animal.existing_animals[0].position == (0, 1)
    assert snapshot.animal.empty_structures == (0, 1)
    assert snapshot.shared.field_tiles == (50,) * 20
    assert len(snapshot.investment.fixed_cash_flow) == 479


def test_world_input_and_late_partial_day_capacity():
    values = _observation(718, ("NW", "NE", "SW", "SE"))
    world = SimpleNamespace(
        data=json.dumps(values, separators=(",", ":")),
        step=718,
        player=0,
    )
    adapter = LiveSnapshotAdapter(3_980_000)

    rolling = adapter.observe(world)
    snapshot = adapter.snapshot(rolling)

    assert snapshot.crop.horizon_days == 1
    assert snapshot.shared.actions == (1,)
    assert snapshot.shared.route_action_reserve == (1,)
    assert snapshot.investment.horizon_steps == 1


def test_low_cash_keeps_only_proportional_reserve():
    values = _observation(240)
    values["farms"][0]["money"] = 55.0
    adapter = LiveSnapshotAdapter(3_980_000)

    snapshot = adapter.snapshot(adapter.observe(values))

    assert snapshot.crop.cash_reserve == 11.0
    assert snapshot.animal.cash_reserve == 11.0
    assert snapshot.investment.cash_reserve == 11.0


def test_carried_goods_extend_only_current_inventory_capacity():
    values = _observation(240, ("NW",), ((4, 5),))
    values["private"]["shed"]["WHEAT"] = 100
    values["private"]["inventories"][1]["CARROT"] = 10
    adapter = LiveSnapshotAdapter(3_980_000)

    snapshot = adapter.snapshot(adapter.observe(values))

    assert sum(snapshot.crop.goods) == 110
    assert snapshot.shared.storage[0] == 110
    assert snapshot.shared.storage[1:] == (100,) * 19


def test_fingerprints_ignore_expected_midday_progress_and_cover_events():
    adapter = LiveSnapshotAdapter(3_980_000)
    first = adapter.observe(_observation())
    moved_values = _observation(1)
    moved_values["farms"][0]["farmer"] = [3, 4]
    moved_values["farms"][0]["money"] = 2990.0

    moved = adapter.observe(moved_values)

    assert moved.economy_fingerprint == first.economy_fingerprint
    assert moved.topology_fingerprint == first.topology_fingerprint
    assert moved.route_precondition_fingerprint == first.route_precondition_fingerprint
    assert moved.progress_fingerprint != first.progress_fingerprint
    assert moved.execution_signal.observed_deltas == ()

    weed_values = _observation(2)
    weed_values["farms"][0]["tiles"][0][0] = {"kind": "WEED"}
    weed = adapter.observe(weed_values)

    assert tuple(
        delta.domain for delta in weed.execution_signal.observed_deltas
    ) == ("topology",)

    hand_values = _observation(3, ("NW",), ((4, 5),))
    hand = adapter.observe(hand_values)

    assert tuple(
        delta.domain for delta in hand.execution_signal.observed_deltas
    ) == ("topology", "route")


def test_snapshot_rejects_unmatched_rolling_observation():
    first = LiveSnapshotAdapter(1)
    second = LiveSnapshotAdapter(2)
    rolling = first.observe(_observation())
    changed = _observation()
    changed["farms"][0]["money"] = 3001.0
    second.observe(changed)

    with pytest.raises(LiveSnapshotError, match="no matching snapshot"):
        second.snapshot(rolling)


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda value: value.update(step=1), "clock fields disagree"),
        (lambda value: value.update(player=2), "player must be 0 or 1"),
        (lambda value: value["farms"][0].update(tiles=[]), "ten rows"),
        (
            lambda value: value["private"].update(inventories=[]),
            "unit inventories must match",
        ),
    ),
)
def test_invalid_observations_are_rejected(mutate, message):
    values = _observation()
    mutate(values)

    with pytest.raises(LiveSnapshotError, match=message):
        LiveSnapshotAdapter(3_980_000).observe(values)
