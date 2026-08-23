from types import SimpleNamespace

from kaggriculture.tools.economics.whole_farm_hybrid_provider import _Agent2Seam


class _Source:
    def __call__(self, world):
        return SimpleNamespace(crop_targets=(), market_orders=())


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
