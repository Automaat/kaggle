import json
from types import SimpleNamespace

import pytest

from kaggriculture.tools.economics.whole_farm_backend import (
    MarketOrderIntent,
    PlanningHorizonConfig,
)
from kaggriculture.tools.economics.whole_farm_hybrid_provider import (
    WholeFarmHandoffSource,
    _Agent2Seam,
)


class _Source:
    def __call__(self, world):
        return SimpleNamespace(crop_targets=(), market_orders=())


class _SeedSource:
    def __call__(self, world):
        return SimpleNamespace(
            crop_targets=(),
            market_orders=(
                MarketOrderIntent(
                    "crop-buy:9:MELON",
                    world.step,
                    ("BUY_SEED", "MELON", 13),
                ),
            ),
        )


def _market_world(step, seeds, plants=()):
    tiles = [[None for _ in range(3)] for _ in range(3)]
    for x, crop, planted_day in plants:
        tiles[0][x] = {
            "crop": crop,
            "kind": "PLANT",
            "planted_day": planted_day,
        }
    values = {
        "farms": [{"tiles": tiles}],
        "private": {"seeds": {"MELON": seeds}},
    }
    return SimpleNamespace(step=step, player=0, data=json.dumps(values))


def test_seam_does_not_execute_unplanned_frozen_orders():
    seam = _Agent2Seam(_Source(), tuple)
    orders = seam.plan(
        SimpleNamespace(step=7),
        (("BUY_ANIMAL", "COW", 3), ("BUY_SEED", "MELON", 1)),
    )
    assert orders == ()


def test_seam_does_not_execute_unplanned_frozen_hires():
    seam = _Agent2Seam(_Source(), tuple)
    orders = seam.plan(
        SimpleNamespace(step=7),
        (("HIRE",), ("BUY_ANIMAL", "COW", 3)),
    )
    assert orders == ()


def test_seam_does_not_repeat_acknowledged_seed_order_after_repairs():
    seam = _Agent2Seam(_SeedSource(), tuple)
    assert seam.plan(_market_world(216, 1), ()) == (
        ("BUY_SEED", "MELON", 13),
    )
    assert seam.plan(_market_world(217, 14), ()) == ()
    assert seam.plan(_market_world(222, 14), ()) == ()


def test_seam_retries_only_unacknowledged_seed_quantity():
    seam = _Agent2Seam(_SeedSource(), tuple)
    assert seam.plan(_market_world(216, 1), ()) == (
        ("BUY_SEED", "MELON", 13),
    )
    assert seam.plan(
        _market_world(217, 11, ((0, "MELON", 9),)),
        (),
    ) == (("BUY_SEED", "MELON", 2),)
    assert seam.plan(
        _market_world(
            218,
            12,
            ((0, "MELON", 9), (1, "MELON", 9)),
        ),
        (),
    ) == ()


def test_handoff_source_passes_planning_horizon_to_backend():
    horizon = PlanningHorizonConfig(5, True)
    source = WholeFarmHandoffSource(horizon=horizon)
    assert source.backend.planning_horizon is horizon


def test_handoff_source_rejects_invalid_planning_horizon():
    with pytest.raises(TypeError, match="PlanningHorizonConfig"):
        WholeFarmHandoffSource(horizon=5)
