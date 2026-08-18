"""Screen isolated variants: python tools/screen.py [seeds] [seed_start]."""

import os
import sys
from concurrent.futures import ProcessPoolExecutor

from bench import _one, _print, summarize


VARIANTS = (
    "KAGG_NEAR_SHED_HERD=1",
    "KAGG_PLACE_PRIORITY=1",
    "KAGG_PICKUP_BUDGET=1",
    "KAGG_CARRIED_FEED=1",
    "KAGG_COLLECT_BEFORE_HARVEST=1",
    "KAGG_CARE_BEFORE_WATER=1",
    "KAGG_BATCH_CROP_HARVEST=1",
    "KAGG_BATCH_ANIMAL_HARVEST=1",
    "KAGG_INTEGRATED_CROP_VALUE=1",
    "KAGG_SUPPLY_ACCOUNTING=1",
    "KAGG_HANDS_PER_TILE=.4;KAGG_MAX_HANDS=10",
    "KAGG_PICKUP_BUDGET=1;KAGG_FEEDER_UNITS=1",
    "KAGG_PICKUP_BUDGET=1;KAGG_FEEDER_UNITS=2",
    "KAGG_DRAIN_FACTOR=.1",
    "KAGG_DRAIN_FACTOR=.15",
    "KAGG_DRAIN_FACTOR=.2",
    "KAGG_DRAIN_FACTOR=.3",
    "KAGG_DRAIN_FACTOR=.4",
    "KAGG_DRAIN_FACTOR=.5",
    "KAGG_FUTURE_SHOP_FACTOR=.5",
    "KAGG_FUTURE_SHOP_FACTOR=.75",
    "KAGG_FUTURE_SHOP_FACTOR=1.25",
)

FINALISTS = (
    "KAGG_CARRIED_FEED=1",
    "KAGG_SUPPLY_ACCOUNTING=1",
    "KAGG_COLLECT_BEFORE_HARVEST=1",
    "KAGG_DRAIN_FACTOR=.2",
    "KAGG_FUTURE_SHOP_FACTOR=.75",
    "KAGG_CARRIED_FEED=1;KAGG_SUPPLY_ACCOUNTING=1",
    "KAGG_CARRIED_FEED=1;KAGG_SUPPLY_ACCOUNTING=1;KAGG_COLLECT_BEFORE_HARVEST=1",
    "KAGG_CARRIED_FEED=1;KAGG_SUPPLY_ACCOUNTING=1;KAGG_DRAIN_FACTOR=.2",
    "KAGG_CARRIED_FEED=1;KAGG_SUPPLY_ACCOUNTING=1;KAGG_FUTURE_SHOP_FACTOR=.75",
    "KAGG_HERD_EXPERIMENT=COW:4,SHEEP:0",
    "KAGG_HERD_EXPERIMENT=COW:4,SHEEP:1",
    "KAGG_HERD_EXPERIMENT=COW:4,SHEEP:2",
    "KAGG_HERD_EXPERIMENT=COW:5,SHEEP:0",
    "KAGG_HERD_EXPERIMENT=COW:5,SHEEP:1",
    "KAGG_MELON_TILE_CAP=14",
    "KAGG_MELON_TILE_CAP=15",
    "KAGG_MELON_TILE_CAP=16",
    "KAGG_MELON_TILE_CAP=17",
)

HERDS = (
    "KAGG_HERD_EXPERIMENT=COW:5",
    "KAGG_HERD_EXPERIMENT=COW:6",
    "KAGG_HERD_EXPERIMENT=COW:8",
    "KAGG_HERD_EXPERIMENT=COW:9",
    "KAGG_HERD_EXPERIMENT=COW:6,SHEEP:1",
    "KAGG_HERD_EXPERIMENT=COW:7,SHEEP:1",
    "KAGG_HERD_EXPERIMENT=COW:8,SHEEP:1",
    "KAGG_HERD_EXPERIMENT=COW:9,SHEEP:1",
    "KAGG_HERD_EXPERIMENT=COW:10",
    "KAGG_HERD_EXPERIMENT=COW:7,SHEEP:2",
)

HERDS2 = HERDS[-4:]

HERDS3 = (
    "KAGG_HERD_EXPERIMENT=COW:11",
    "KAGG_HERD_EXPERIMENT=COW:12",
    "KAGG_HERD_EXPERIMENT=COW:14",
    "KAGG_HERD_EXPERIMENT=COW:16",
    "KAGG_HERD_EXPERIMENT=COW:7,SHEEP:3",
    "KAGG_HERD_EXPERIMENT=COW:10,SHEEP:2",
)

POST_HERD = (
    "KAGG_HANDS_PER_TILE=.36;KAGG_MAX_HANDS=10",
    "KAGG_HANDS_PER_TILE=.4;KAGG_MAX_HANDS=10",
    "KAGG_PLACE_PRIORITY=1",
    "KAGG_COLLECT_BEFORE_HARVEST=1",
    "KAGG_PICKUP_BUDGET=1",
    "KAGG_NEAR_SHED_HERD=1",
    "KAGG_BATCH_CROP_HARVEST=1",
    "KAGG_BATCH_ANIMAL_HARVEST=1",
    "KAGG_FEED_DAYS=1",
    "KAGG_FEED_DAYS=3",
    "KAGG_MIN_CASH=200",
    "KAGG_MIN_CASH=600",
    "KAGG_MIN_CASH=800",
    "KAGG_SHED_TARGET=50",
    "KAGG_SHED_TARGET=60",
    "KAGG_SHED_TARGET=80",
    "KAGG_SHED_TARGET=90",
)

BUY_CAPS = tuple(f"KAGG_ANIMAL_BUY_CAP={cap}" for cap in (5, 7, 9, 11, 13))


def main():
    seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    start = int(sys.argv[2]) if len(sys.argv) > 2 else 200_000
    group = sys.argv[3] if len(sys.argv) > 3 else "initial"
    variants = {
        "initial": VARIANTS, "finalists": FINALISTS, "herds": HERDS, "herds2": HERDS2,
        "herds3": HERDS3,
        "post_herd": POST_HERD,
        "buy_caps": BUY_CAPS,
    }[group]
    candidates = [f"variant:{settings}" for settings in variants]
    jobs = [(candidate, "main.py", seed)
            for candidate in candidates for seed in range(start, start + seeds)]
    with ProcessPoolExecutor(max_workers=os.cpu_count()) as pool:
        raw = list(pool.map(_one, jobs, chunksize=1))
    for index, candidate in enumerate(candidates):
        result = summarize(candidate, "main.py", raw[index * seeds:(index + 1) * seeds])
        _print(result)
        print()


if __name__ == "__main__":
    main()
