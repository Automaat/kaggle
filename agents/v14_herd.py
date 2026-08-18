"""Kaggriculture submission entrypoint. Must expose `agent(obs)`."""

import math
import os

CROPS = {
    "WHEAT": {"seed": 10, "first_yield_day": 2, "max_yield_day": 4, "max_yield": 6, "ongoing": False},
    "CARROT": {"seed": 20, "first_yield_day": 2, "max_yield_day": 3, "max_yield": 4, "ongoing": False},
    "TOMATO": {"seed": 50, "first_yield_day": 8, "max_yield_day": 8, "max_yield": 4, "ongoing": True},
    "STRAWBERRY": {"seed": 100, "first_yield_day": 10, "max_yield_day": 10, "max_yield": 4, "ongoing": True},
    "MELON": {"seed": 80, "first_yield_day": 10, "max_yield_day": 12, "max_yield": 6, "ongoing": False},
}
ANIMALS = {
    "GOOSE": {"cost": 300, "structure": "COOP", "product": "EGG", "first_yield_day": 4, "rate": 2.0},
    "COW": {"cost": 400, "structure": "PASTURE", "product": "MILK", "first_yield_day": 8, "rate": 1.5},
    "SHEEP": {"cost": 500, "structure": "PASTURE", "product": "WOOL", "first_yield_day": 6, "rate": 4 / 3},
}
# Days a tile stays occupied, used to decide whether a plan item can still pay off.
LIFESPAN = {
    "WHEAT": 4, "CARROT": 3, "TOMATO": 11, "STRAWBERRY": 16, "MELON": 10,
    "GOOSE": 9, "COW": 13, "SHEEP": 11,
}
FEED_DAYS = 2  # Days of wheat kept back from the market to cover the herd.
# Units a watered, unfertilized tile returns over one full occupancy.
EXPECTED_YIELD = {"WHEAT": 4, "CARROT": 3, "TOMATO": 4, "STRAWBERRY": 4, "MELON": 6}
# Scheduled production ages for the ongoing crops: first_yield_day, then every
# `interval` days, capped at max_yield productions.
PRODUCTION_AGES = {
    "TOMATO": [8, 9, 10, 11],
    "STRAWBERRY": [10, 12, 14, 16],
}
FERTILIZE_DAYS = 3  # A FERTILIZE covers today and the next two days.
PLANNER = os.environ.get("KAGG_PLANNER", "dynamic")
# Herd composition, as animal:count. Milk, wool and egg sit on independent price
# curves, so a mixed herd saturates three of them instead of glutting one.
HERD_SPEC = os.environ.get("KAGG_HERD_SPEC", "COW:4,SHEEP:3")

SHOPS = {
    "BAKERY": ["EGG", "WHEAT"],
    "PIZZA_SHOP": ["MILK", "TOMATO", "WHEAT"],
    "BRUNCH_SPOT": ["EGG", "WHEAT", "STRAWBERRY"],
    "YARN_STORE": ["WOOL"],
    "ICE_CREAM_SHOP": ["STRAWBERRY", "MILK", "WHEAT"],
    "PET_CAFE": ["CARROT"],
    "SMOOTHIE_SHOP": ["STRAWBERRY", "MILK"],
    "FARMERS_MARKET": ["WHEAT", "CARROT", "TOMATO", "STRAWBERRY"],
}
SHOP_TICKS_PER_DAY = 6  # One tick every 4 turns.
# How much of the forecast drain to believe. The opponent is also producing into
# the same market, so the raw forecast overstates how scarce a crop will be.
DRAIN_FACTOR = float(os.environ.get("KAGG_DRAIN_FACTOR", "0.25"))

# Mirrors MARKET_PARAMS in the environment: price(inv) = base +- amp * f(|inv - I0|).
MARKET_PARAMS = {
    "WHEAT": (25, 400, "sqrt", 0.80, "log", 0.20),
    "CARROT": (35, 450, "hinge", 1.00, "sqrt", 0.70),
    "TOMATO": (60, 200, "hinge", 0.40, "sqrt", 0.60),
    "STRAWBERRY": (120, 100, "sqrt", 0.70, "linear", 1.60),
    "MELON": (250, 300, "log", 0.20, "sq", 3.60),
    "EGG": (50, 332, "hinge", 0.40, "log", 0.20),
    "MILK": (160, 122, "sqrt", 0.60, "linear", 1.60),
    "WOOL": (200, 105, "log", 0.20, "sq", 3.20),
    "FERTILIZER": (100, 200, "linear", 0.40, "linear", 0.40),
}
MARKET_I0 = 10000
PRICE_FLOOR = 1
HINGE_GAIN = 8.0

# Tile mix, as weights over the tile list. Overridable for sweeps.
DEFAULT_MIX = "MELON:5,STRAWBERRY:3,CARROT:1"
LAND_PRICES = [1000, 2000, 4000]  # NE, SW, SE, in unlock order.
MAX_QUADRANTS = int(os.environ.get("KAGG_LAND", "0"))  # Extra quadrants to unlock.
SEED_RESERVE = 80  # Cash per empty tile kept back so a land buy cannot starve planting.
HANDS_PER_TILE = float(os.environ.get("KAGG_HANDS_PER_TILE", "0.34"))
MAX_HANDS = int(os.environ.get("KAGG_MAX_HANDS", "10"))
HIRES_PER_TURN = 3  # Only 10 market orders clear per turn; leave room to sell and sow.
HIRE_HOURS = 4
MAX_ORDERS = 10
SHED_CAP = 100
SHED_TARGET = int(os.environ.get("KAGG_SHED_TARGET", "70"))  # Leave room for a day of harvest before overflow is discarded.
MIN_CASH = int(os.environ.get("KAGG_MIN_CASH", "400"))
LAST_DAY = 29
CASHOUT_HOUR = int(os.environ.get("KAGG_CASHOUT_HOUR", "17"))  # Last day only: stop farming, carry everything to the shed.
SHED_TILE = (4, 4)
MIN_LAND_PAYBACK_DAYS = 8  # A quadrant unlocked later than this cannot repay itself.
DRIFT_WINDOW = int(os.environ.get("KAGG_DRIFT_WINDOW", "3"))  # Days of price history used to decide hold vs sell.

_history = {}  # (player, item) -> {day: price}


def _shape(func, x, t):
    if func == "linear":
        return x
    if func == "sq":
        return x * x
    if func == "sqrt":
        return math.sqrt(x)
    if func == "log":
        return math.log(1.0 + x)
    if func == "hinge":
        u = x / t
        return u + HINGE_GAIN * max(0.0, u - 1.0) ** 2
    return x


def market_price(item, inventory):
    """Same curve the environment quotes, so we can price a sale before making it."""
    base, t, below_f, below_target, above_f, above_target = MARKET_PARAMS[item]
    x = abs(inventory - MARKET_I0)
    if inventory < MARKET_I0:
        func, target, sign = below_f, below_target, 1.0
    else:
        func, target, sign = above_f, above_target, -1.0
    denom = _shape(func, t, t)
    amp = target * base / denom if denom else 0.0
    return max(PRICE_FLOOR, round(base + sign * amp * _shape(func, x, t)))


def _supply_forecast(farms, day):
    """Units of each product both farms will still put on the market this season.

    Every tile of both farms is public, and `CROPS`/`ANIMALS` are fixed, so this
    is a hard ceiling on remaining supply rather than an estimate. Compared
    against the town's drain it says which products will be scarce at the end of
    the season and which are already oversupplied.
    """
    days_left = max(0, LAST_DAY - day)
    supply = {}
    for farm in farms:
        for row in farm["tiles"]:
            for tile in row:
                if not isinstance(tile, dict):
                    continue
                if "animal" in tile:
                    data = ANIMALS[tile["animal"]]
                    productive = min(days_left, days_left - max(0, data["first_yield_day"] - (day - tile["placed_day"])))
                    product = data["product"]
                    supply[product] = supply.get(product, 0) + max(0, data["rate"] * productive)
                    supply["FERTILIZER"] = supply.get("FERTILIZER", 0) + days_left
                elif tile.get("kind") == "PLANT":
                    crop = tile["crop"]
                    if crop not in EXPECTED_YIELD:
                        continue
                    remaining = LIFESPAN[crop] - (day - tile["planted_day"])
                    if remaining <= days_left:
                        supply[crop] = supply.get(crop, 0) + EXPECTED_YIELD[crop]
    return supply


def _scarcity(farms, shops, day):
    """Per product, town drain minus remaining supply from both farms.

    Positive means the price still has room to climb before the last turn, so
    the stock is worth holding. Negative means every further unit sold walks
    down the glut curve, so the best sale is the one made now.
    """
    days_left = max(0, LAST_DAY - day)
    drain = _daily_drain(shops)
    supply = _supply_forecast(farms, day)
    return {p: drain.get(p, 0) * days_left - supply.get(p, 0) for p in MARKET_PARAMS}


def _record_prices(player, prices, day):
    """Sample one price per item per day so we can measure the trend."""
    for item, price in prices.items():
        _history.setdefault((player, item), {})[day] = price


def _drift(player, item, day):
    """Price change per day over the recent window; negative means the market is glutting."""
    seen = _history.get((player, item))
    if not seen:
        return 0.0
    past_day = max((d for d in seen if d <= day - DRIFT_WINDOW), default=None)
    if past_day is None:
        return 0.0
    span = day - past_day
    return (seen[day] - seen[past_day]) / span if span else 0.0


def _hold_quota(kept_total, money, cheapest_price, cash_needed):
    """How much of the still-rising stock we are forced to sell anyway."""
    quota = max(0, kept_total - SHED_TARGET)
    if money < cash_needed and cheapest_price > 0:
        quota = max(quota, math.ceil((cash_needed - money) / cheapest_price))
    return min(quota, kept_total)


def _sell_orders(shed, inventory, money, day, player, cash_needed, feed_reserve, scarcity, fertilizer_reserve):
    """Decide hold vs sell per product, never as one global flag.

    A single flag meant melon — which starts falling the moment either farm
    sells one, since no shop demands it — dragged strawberry and milk out of
    the shed with it, and those two climb all season.
    """
    held = []
    for item, count in shed.items():
        if item == "WHEAT":
            count -= feed_reserve
        if item == "FERTILIZER":
            count -= fertilizer_reserve
        if count > 0 and item in MARKET_PARAMS:
            held.append((market_price(item, inventory.get(item, MARKET_I0)), item, count))
    if not held:
        return []
    held.sort()

    dumping = day >= LAST_DAY
    dumps, keepers, raised = [], [], 0
    for price, item, count in held:
        if dumping or scarcity.get(item, 0) <= count:
            # Falling price: the best sale of this product is the one made now.
            after = market_price(item, inventory.get(item, MARKET_I0) + count)
            dumps.append(((price - after) * count, item, count))
            raised += price * count
        else:
            keepers.append((price, item, count))

    # Steepest curve first. Two sells of one item at the same order index split
    # the curve evenly, but an order at index 0 clears the whole top of the
    # curve before the opponent's index-3 order starts. The item that loses the
    # most to being second in line goes first.
    dumps.sort(reverse=True)
    orders = [["SELL", item, count] for _loss, item, count in dumps]

    quota = _hold_quota(
        sum(c for _, _, c in keepers), money + raised,
        keepers[0][0] if keepers else 0, cash_needed,
    )
    for price, item, count in keepers:
        if quota <= 0:
            break
        take = min(count, quota)
        orders.append(["SELL", item, take])
        quota -= take
    return orders


def _parse_mix(spec):
    pattern = []
    for part in spec.split(","):
        item, _, weight = part.partition(":")
        item = item.strip().upper()
        if item in CROPS or item in ANIMALS:
            pattern += [item] * max(1, int(weight or 1))
    return pattern or ["CARROT"]


_mix_cache = {}


def _mix(player):
    """Per-player mix so a sweep can pit two mixes against each other."""
    if player not in _mix_cache:
        spec = os.environ.get(f"KAGG_MIX_{player}") or os.environ.get("KAGG_MIX") or DEFAULT_MIX
        _mix_cache[player] = _parse_mix(spec)
    return _mix_cache[player]


def _daily_drain(shops):
    """Units the town removes from the market per day, per product.

    Deterministic and fully visible, so a crop whose price is being drained can
    be planted before the scarcity shows up in the quoted price.
    """
    drain = {p: 1 for p in MARKET_PARAMS if p != "FERTILIZER"}  # Town centre, once a day.
    for shop in shops:
        products = SHOPS.get(shop, [])
        multiplier = 2 if len(products) == 1 else 1
        for product in products:
            drain[product] = drain.get(product, 0) + SHOP_TICKS_PER_DAY * multiplier
    return drain


def _parse_herd():
    """Expand "COW:4,SHEEP:3" into the list of animals to reserve tiles for."""
    herd = []
    for part in HERD_SPEC.split(","):
        animal, _, count = part.partition(":")
        animal = animal.strip().upper()
        if animal in ANIMALS:
            herd += [animal] * max(0, int(count or 1))
    return herd


def _crop_value(crop, projected_inv, day):
    """Profit per tile-day, priced at the market we will actually sell into.

    Quoting at post-harvest inventory rather than today's price is what stops
    the farm piling into one crop: each tile allocated pushes the next tile's
    quote for that crop down its glut curve.
    """
    units = EXPECTED_YIELD[crop]
    price = market_price(crop, projected_inv.get(crop, MARKET_I0) + units)
    return (units * price - CROPS[crop]["seed"]) / LIFESPAN[crop]


def _harvest_inventory(inventory, drain, days):
    """Market inventory as it will stand when this planting is ready to sell."""
    return {p: n - drain.get(p, 0) * days * DRAIN_FACTOR for p, n in inventory.items()}


def _animal_value(animal, projected, day, wheat_price):
    """Profit per tile-day for livestock, cared for daily.

    CARE banks one unit per fed day and pays it out on the next production, so
    the rate is 1 + interval per production — 3x on a cow. Every surviving
    animal also yields one fertilizer a day, and nothing in the game drains
    fertilizer, so that curve stays untouched all season.
    """
    data = ANIMALS[animal]
    occupancy = LAST_DAY - day
    productive = occupancy - data["first_yield_day"]
    if productive <= 0:
        return float("-inf")
    units = data["rate"] * productive
    product = data["product"]
    revenue = units * market_price(product, projected.get(product, MARKET_I0) + units)
    fertilizer = occupancy * market_price("FERTILIZER", projected.get("FERTILIZER", MARKET_I0) + occupancy)
    return (revenue + fertilizer - data["cost"] - occupancy * wheat_price) / occupancy


def _dynamic_plan(tiles, day, inventory, shops):
    """Assign each empty tile the crop with the best marginal return at harvest.

    The first `HERD` tiles are reserved for livestock. Letting the value model
    bid for every tile floods the farm with animals it cannot pay for, and a
    tile reserved for an unaffordable animal simply sits empty.
    """
    drain = _daily_drain(shops)
    ready = [c for c in CROPS if day + LIFESPAN[c] <= LAST_DAY]
    projected = {c: _harvest_inventory(inventory, drain, LIFESPAN[c]).get(c, MARKET_I0) for c in ready}
    herd = _parse_herd()
    plan, reserved = {}, 0
    for x, y, tile in tiles:
        if isinstance(tile, dict) and ("animal" in tile or tile.get("kind") in ("COOP", "PASTURE")):
            reserved += 1
    for x, y, tile in tiles:
        if tile is not None:
            continue
        if reserved < len(herd) and day <= LAST_DAY - LIFESPAN[herd[reserved]]:
            plan[(x, y)] = herd[reserved]
            reserved += 1
            continue
        if not ready:
            plan[(x, y)] = None
            continue
        best = max(ready, key=lambda c: _crop_value(c, projected, day))
        plan[(x, y)] = best
        projected[best] += EXPECTED_YIELD[best]
    return plan


def _tile_plan(player, tiles, day):
    """Map every workable tile to the item it should hold.

    Indexed by position in the tile list, not by `y * board_size + x`: with a
    10-wide board the latter aliases, so any pattern whose length divides 10
    collapses into columns and most of the mix is never planted.
    """
    mix = _mix(player)
    plan = {}
    for i, (x, y, _tile) in enumerate(tiles):
        wanted = mix[i % len(mix)]
        order = [wanted] + sorted(CROPS, key=lambda c: LIFESPAN[c])
        plan[(x, y)] = next((item for item in order if day + LIFESPAN[item] <= LAST_DAY), None)
    return plan


def _land_orders(farm, day, empty_tiles):
    """More tiles beat idle cash, so unlock quadrants as soon as we can afford one.

    A quadrant bought too late cannot pay for itself, so stop once the slowest
    crop in the mix can no longer finish.
    """
    bought = len(farm.get("unlocked_quadrants", ["NW"])) - 1
    if bought >= min(MAX_QUADRANTS, len(LAND_PRICES)) or day > LAST_DAY - MIN_LAND_PAYBACK_DAYS:
        return []
    # Price in the tiles we are about to unlock, not just the ones we hold: a
    # quadrant that leaves us unable to sow it is worse than no quadrant.
    quadrant_tiles = (len(farm["tiles"]) // 2) ** 2
    if farm["money"] < LAND_PRICES[bought] + (empty_tiles + quadrant_tiles) * SEED_RESERVE:
        return []
    return [["BUY_LAND"]]


def _plan_counts(plan, tiles):
    """What the empty tiles are planned to hold, split into crops and animals."""
    crops, animals = {}, {}
    for x, y, tile in tiles:
        if tile is not None:
            continue
        item = plan.get((x, y))
        if item in ANIMALS:
            animals[item] = animals.get(item, 0) + 1
        elif item:
            crops[item] = crops.get(item, 0) + 1
    return crops, animals


def _wanted_seeds(crops, seeds):
    return {c: n - seeds.get(c, 0) for c, n in crops.items() if n > seeds.get(c, 0)}


def _empty_structures(tiles):
    """Built but unoccupied coops and pastures, by the animal that fits them."""
    want = {}
    for _, _, tile in tiles:
        if isinstance(tile, dict) and tile.get("kind") in ("COOP", "PASTURE") and "animal" not in tile:
            for animal, data in ANIMALS.items():
                if data["structure"] == tile["kind"]:
                    want[animal] = want.get(animal, 0) + 1
                    break
    return want


def _animal_orders(wanted, shed, money):
    """Buy livestock for the structures that are standing empty."""
    orders = []
    budget = money
    for animal, need in sorted(wanted.items(), key=lambda kv: ANIMALS[kv[0]]["cost"]):
        take = min(need - shed.get(animal, 0), int(budget // ANIMALS[animal]["cost"]))
        if take > 0:
            orders.append(["BUY_ANIMAL", animal, take])
            budget -= take * ANIMALS[animal]["cost"]
    return orders


def _feed_orders(herd, shed, prices, money):
    """Top up the wheat store when the herd would otherwise go hungry."""
    if not herd:
        return []
    short = herd * FEED_DAYS - shed.get("WHEAT", 0)
    price = max(1, prices.get("WHEAT", 25))
    take = min(short, int(money // price))
    return [["BUY_PRODUCT", "WHEAT", take]] if take > 0 else []


def _seed_orders(wanted, money):
    """Buy exactly the seeds the empty tiles are planned to hold, dearest first."""
    orders = []
    budget = money
    for crop, need in sorted(wanted.items(), key=lambda kv: -CROPS[kv[0]]["seed"]):
        take = min(need, int(budget // CROPS[crop]["seed"]))
        if take > 0:
            orders.append(["BUY_SEED", crop, take])
            budget -= take * CROPS[crop]["seed"]
    return orders


def _my_tiles(farm):
    """Coordinates of every tile this player can act on."""
    for y, row in enumerate(farm["tiles"]):
        for x, tile in enumerate(row):
            if tile != "LOCKED":
                yield x, y, tile


def _tile_task(tile, day, want, seeds, stock):
    if tile is None:
        if want in ANIMALS:
            # Build only once the animal is bought: an empty coop is a dead tile.
            return [("BUILD", want)] if stock.get(want, 0) > 0 else []
        return [("PLANT", want)] if want and seeds.get(want, 0) > 0 else []
    kind = tile.get("kind")
    if kind == "WEED":
        return [("DIG", None)]
    if kind in ("COOP", "PASTURE"):
        if "animal" not in tile:
            # `stock` counts the shed plus every unit's hands: picking an animal
            # up empties the shed slot, and gating on the shed alone deadlocked
            # the placement of the animal already being carried to the tile.
            animal = next((a for a in ANIMALS if ANIMALS[a]["structure"] == kind and stock.get(a, 0) > 0), None)
            return [("PLACE", animal)] if animal else []
        return _animal_tasks(tile, day)
    if kind == "PLANT":
        crop = tile["crop"]
        if crop not in CROPS:
            return None
        data = CROPS[crop]
        age = day - tile["planted_day"]
        ripe = tile.get("yield_units", 0) > 0 and age >= data["first_yield_day"]
        if data["ongoing"]:
            jobs = []
            if not tile["watered_today"]:
                jobs.append(("WATER!" if tile.get("consecutive_unwatered", 0) >= 1 else "WATER", None))
            if _fertilize_pays(tile, crop, age, day):
                jobs.append(("FERTILIZE", None))
            if ripe:
                jobs.append(("HARVEST", None))
            return jobs
        elif ripe and (
            tile["yield_units"] >= data["max_yield"]
            or age >= data["max_yield_day"]
            or day >= LAST_DAY
        ):
            # Watering past the cap adds nothing, so the tile should turn over.
            # Melon hits its cap of 6 at age 10 but max_yield_day is 12.
            return [("HARVEST", None)]
        if not tile["watered_today"]:
            # A plant already on one dry day turns to weed tonight.
            return [("WATER!" if tile.get("consecutive_unwatered", 0) >= 1 else "WATER", None)]
    return []


def _fertilize_pays(tile, crop, age, day):
    """Whether fertilizing this ongoing crop now doubles a production it reaches.

    An ongoing crop yields 1 per scheduled production, or 2 if it was fertilized
    AND watered that day. The effect lasts three days, so two applications cover
    all four of a strawberry's productions.
    """
    if crop not in PRODUCTION_AGES:
        return False
    if tile.get("fertilized_until_day", -1) >= day:
        return False
    return any(age <= p < age + FERTILIZE_DAYS for p in PRODUCTION_AGES[crop])


def _animal_tasks(tile, day):
    """Every job this animal still wants today, not just the most urgent one.

    An animal tile wants up to four actions a day. Emitting one at a time let
    lower-priority crop work pull the unit off the tile between actions, so
    CARE and COLLECT_FERTILIZER — the two that pay best — were rarely reached.
    """
    jobs = []
    if not tile["fed_today"]:
        jobs.append(("FEED!" if tile.get("consecutive_unfed", 0) >= 1 else "FEED", None))
    else:
        # Care only banks on a day the animal was actually fed.
        if not tile["cared_today"] and day < LAST_DAY:
            jobs.append(("CARE", None))
    if tile.get("yield_units", 0) > 0:
        jobs.append(("HARVEST", None))
    if tile.get("fertilizer_available"):
        jobs.append(("COLLECT_FERTILIZER", None))
    return jobs


def _priority(task):
    # Water first: an unwatered plant dies. Harvest before planting.
    # CARE banks one extra unit per fed day, paid out on the next production, so
    # on a cow it is worth a whole extra milk — far more than sowing another
    # tile. Fertilizer is 1/animal/day that nothing else in the game drains.
    return {
        "WATER!": 0, "FEED!": 0, "FEED": 1, "WATER": 2, "CARE": 3,
        "FERTILIZE": 4, "HARVEST": 5, "COLLECT_FERTILIZER": 6, "PLACE": 7,
        "PLANT": 8, "BUILD": 8, "DIG": 9,
    }.get(task, 9)


def _step_toward(src, dst):
    sx, sy = src
    dx, dy = dst
    if sx < dx:
        return ["EAST"]
    if sx > dx:
        return ["WEST"]
    if sy < dy:
        return ["SOUTH"]
    if sy > dy:
        return ["NORTH"]
    return None


def _act(task, item):
    if task == "PLANT":
        return ["PLANT", item]
    if task == "PLACE":
        return ["PLACE", item]
    if task == "BUILD":
        return ["BUILD_COOP"] if ANIMALS[item]["structure"] == "COOP" else ["BUILD_PASTURE"]
    if task == "WATER!":
        return ["WATER"]
    if task == "FEED!":
        return ["FEED"]
    return [task]


def _can_do(task, item, carried):
    """Feeding needs wheat in hand and placing needs the animal in hand."""
    if task in ("FEED", "FEED!"):
        return carried.get("WHEAT", 0) > 0
    if task == "FERTILIZE":
        return carried.get("FERTILIZER", 0) > 0
    if task == "PLACE":
        return carried.get(item, 0) > 0
    return True


def _pickup_op(pos, carried, shed, hungry, unplaced, n_units, board_size, needs_fertilizer):
    """Load up before leaving the shed: feed and livestock only move in hand."""
    if not _is_shed_adjacent(pos, board_size):
        return None
    for animal, n in unplaced.items():
        if n > 0 and shed.get(animal, 0) > 0 and not carried.get(animal):
            return ["PICKUP", animal, min(n, shed[animal])]
    if hungry > 0 and not carried.get("WHEAT") and shed.get("WHEAT", 0) > 0:
        share = max(1, -(-hungry // max(1, n_units)))
        return ["PICKUP", "WHEAT", min(share, shed["WHEAT"])]
    if needs_fertilizer > 0 and not carried.get("FERTILIZER") and shed.get("FERTILIZER", 0) > 0:
        share = max(1, -(-needs_fertilizer // max(1, n_units)))
        return ["PICKUP", "FERTILIZER", min(share, shed["FERTILIZER"])]
    return None


def _is_shed_adjacent(pos, board_size):
    half = board_size // 2
    return tuple(pos) in {(half - 1, half - 1), (half, half - 1), (half - 1, half), (half, half)}


def _cashout_op(pos, carried):
    """Final-day run to the shed: produce still in hand when the season ends is lost."""
    if not carried:
        return ["PASS"]
    return _step_toward(pos, SHED_TILE) or ["DROP"]


def agent(obs):
    player = obs["player"]
    farm = obs["farms"][player]
    private = obs["private"]
    day, hour = obs["day"], obs["hour"]
    seeds = dict(private.get("seeds", {}))
    shed = private.get("shed", {})
    board_size = len(farm["tiles"])
    inventories = private.get("inventories", [{}])

    tiles = [t for t in _my_tiles(farm)]
    hires = []
    if hour < HIRE_HOURS and day < LAST_DAY:
        want_hands = min(MAX_HANDS, max(4, round(len(tiles) * HANDS_PER_TILE)))
        missing = max(0, want_hands - farm.get("hires_today", 0))
        hires = [["HIRE"]] * min(missing, HIRES_PER_TURN)
    orders = []

    if PLANNER == "dynamic":
        plan = _dynamic_plan(tiles, day, obs["market"]["inventory"], obs["town"]["unlocked_shops"])
    else:
        plan = _tile_plan(player, tiles, day)
    plan_crops, plan_animals = _plan_counts(plan, tiles)
    wanted_seeds = _wanted_seeds(plan_crops, seeds)
    seed_bill = sum(n * CROPS[c]["seed"] for c, n in wanted_seeds.items())

    herd = sum(1 for _, _, t in tiles if isinstance(t, dict) and "animal" in t)
    fertilize_jobs = sum(
        1 for _x, _y, t in tiles
        if isinstance(t, dict) and t.get("kind") == "PLANT"
        and _fertilize_pays(t, t["crop"], day - t["planted_day"], day)
    )
    structures = _empty_structures(tiles)
    for animal, n in plan_animals.items():
        structures[animal] = structures.get(animal, 0) + n

    if hour == 0:
        if day == 0:
            _history.clear()
        _record_prices(player, obs["market"]["prices"], day)
    orders += _sell_orders(
        shed, obs["market"]["inventory"], farm["money"], day, player,
        seed_bill + MIN_CASH, herd * FEED_DAYS,
        _scarcity(obs["farms"], obs["town"]["unlocked_shops"], day), fertilize_jobs,
    )
    orders += _seed_orders(wanted_seeds, farm["money"])
    orders += _feed_orders(herd, shed, obs["market"]["prices"], farm["money"] - seed_bill)
    if day <= LAST_DAY - LIFESPAN["GOOSE"]:
        orders += _animal_orders(structures, shed, farm["money"] - seed_bill - MIN_CASH)
    # Hires last: both players share one descending price curve per order index,
    # so a sell at index 0 clears above a sell the opponent placed at index 3.
    # Truncation also drops the tail, and losing a hire beats losing a sale.
    orders += _land_orders(farm, day, sum(1 for _, _, t in tiles if t is None))
    orders += hires

    units = [farm["farmer"]] + list(farm.get("hands", []))
    cashing_out = day >= LAST_DAY and hour >= CASHOUT_HOUR

    stock = dict(shed)
    for inv in inventories:
        for item, n in inv.items():
            stock[item] = stock.get(item, 0) + n

    tasks = []
    if not cashing_out:
        budget = dict(seeds)
        for x, y, tile in tiles:
            want = plan.get((x, y))
            for task in _tile_task(tile, day, want, budget, stock):
                if task[0] == "PLANT":
                    budget[task[1]] -= 1
                tasks.append((_priority(task[0]), x, y, task))
        tasks.sort(key=lambda t: t[:3])

    hungry = sum(1 for _, _, t in tiles if isinstance(t, dict) and "animal" in t and not t["fed_today"])

    unplaced = _empty_structures(tiles)

    ops = []
    taken = set()
    for idx, pos in enumerate(units):
        carried = dict(inventories[idx]) if idx < len(inventories) else {}
        if cashing_out:
            ops.append(_cashout_op(pos, sum(carried.values())))
            continue
        load = _pickup_op(pos, carried, shed, hungry, unplaced, len(units), board_size, fertilize_jobs)
        if load is not None:
            if load[0] == "PICKUP" and load[1] in ANIMALS:
                unplaced[load[1]] = unplaced.get(load[1], 0) - load[2]
            ops.append(load)
            continue
        chosen = None
        best = None
        for i, (prio, x, y, (task, item)) in enumerate(tasks):
            if i in taken or not _can_do(task, item, carried):
                continue
            dist = abs(pos[0] - x) + abs(pos[1] - y)
            key = (prio, dist)
            if best is None or key < best:
                best, chosen = key, i
        if chosen is None:
            ops.append(["PASS"])
            continue
        taken.add(chosen)
        _, x, y, (task, crop) = tasks[chosen]
        ops.append(_step_toward(pos, (x, y)) or _act(task, crop))

    return {"farmer": ops[0], "hands": ops[1:], "market": orders[:MAX_ORDERS]}
