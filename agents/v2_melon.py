"""Kaggriculture submission entrypoint. Must expose `agent(obs)`."""

import math
import os

CROPS = {
    "WHEAT": {"seed": 10, "first_yield_day": 2, "max_yield_day": 4, "ongoing": False},
    "CARROT": {"seed": 20, "first_yield_day": 2, "max_yield_day": 3, "ongoing": False},
    "TOMATO": {"seed": 50, "first_yield_day": 8, "max_yield_day": 8, "ongoing": True},
    "STRAWBERRY": {"seed": 100, "first_yield_day": 10, "max_yield_day": 10, "ongoing": True},
    "MELON": {"seed": 80, "first_yield_day": 10, "max_yield_day": 12, "ongoing": False},
}
# Days a tile stays occupied, used to decide whether a crop can still finish.
LIFESPAN = {"WHEAT": 4, "CARROT": 3, "TOMATO": 11, "STRAWBERRY": 16, "MELON": 12}

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
DEFAULT_MIX = "MELON:4,STRAWBERRY:1,CARROT:1"
MAX_HANDS = 8
MAX_ORDERS = 10
SHED_CAP = 100
SHED_TARGET = 70  # Leave room for a day of harvest before overflow is discarded.
MIN_CASH = 400
LAST_DAY = 29
CASHOUT_HOUR = 17  # Last day only: stop farming, carry everything to the shed.
SHED_TILE = (4, 4)
DRIFT_WINDOW = 3  # Days of price history used to decide hold vs sell.

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


def _sell_quota(shed_total, money, cheapest_price, day, rising):
    """How many units we must convert to cash this turn."""
    if day >= LAST_DAY or not rising:
        return shed_total
    quota = max(0, shed_total - SHED_TARGET)
    if money < MIN_CASH and cheapest_price > 0:
        quota = max(quota, math.ceil((MIN_CASH - money) / cheapest_price))
    return min(quota, shed_total)


def _sell_orders(shed, inventory, money, day, player):
    """Hold produce only while its price is climbing; sell the cheapest units first.

    Town demand can outpace supply and lift prices all season, but two farms
    dumping the same crop glut it instead. So the hold is conditional on a
    positive price trend. The shed holds 100 units, so we sell the overflow and
    keep the units worth the most per shed slot.
    """
    held = [(market_price(i, inventory.get(i, MARKET_I0)), i, n) for i, n in shed.items() if n > 0 and i in MARKET_PARAMS]
    if not held:
        return []
    held.sort()
    rising = all(_drift(player, item, day) > 0 for _, item, _ in held)
    quota = _sell_quota(sum(n for _, _, n in held), money, held[0][0], day, rising)
    orders = []
    for _, item, n in held:
        if quota <= 0:
            break
        take = min(n, quota)
        orders.append(["SELL", item, take])
        quota -= take
    return orders


def _parse_mix(spec):
    pattern = []
    for part in spec.split(","):
        crop, _, weight = part.partition(":")
        crop = crop.strip().upper()
        if crop in CROPS:
            pattern += [crop] * max(1, int(weight or 1))
    return pattern or ["CARROT"]


_mix_cache = {}


def _mix(player):
    """Per-player mix so a sweep can pit two mixes against each other."""
    if player not in _mix_cache:
        spec = os.environ.get(f"KAGG_MIX_{player}") or os.environ.get("KAGG_MIX") or DEFAULT_MIX
        _mix_cache[player] = _parse_mix(spec)
    return _mix_cache[player]


def _planned_crop(player, x, y, board_size, day):
    """Crop this tile should hold. Falls back to a faster crop late in the season."""
    mix = _mix(player)
    wanted = mix[(y * board_size + x) % len(mix)]
    order = [wanted] + sorted(CROPS, key=lambda c: LIFESPAN[c])
    for crop in order:
        if day + LIFESPAN[crop] <= LAST_DAY:
            return crop
    return None


def _seed_orders(player, farm, seeds, day, board_size):
    """Buy exactly the seeds the empty tiles are planned to hold."""
    wanted = {}
    for x, y, tile in _my_tiles(farm):
        if tile is not None:
            continue
        crop = _planned_crop(player, x, y, board_size, day)
        if crop:
            wanted[crop] = wanted.get(crop, 0) + 1
    orders = []
    budget = farm["money"] - MIN_CASH
    for crop, n in sorted(wanted.items(), key=lambda kv: -CROPS[kv[0]]["seed"]):
        need = max(0, n - seeds.get(crop, 0))
        afford = int(budget // CROPS[crop]["seed"])
        take = min(need, afford)
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


def _tile_task(tile, day, want, seeds):
    if tile is None:
        return ("PLANT", want) if want and seeds.get(want, 0) > 0 else None
    kind = tile.get("kind")
    if kind == "WEED":
        return ("DIG", None)
    if kind == "PLANT":
        crop = tile["crop"]
        if crop not in CROPS:
            return None
        data = CROPS[crop]
        age = day - tile["planted_day"]
        ripe = tile.get("yield_units", 0) > 0 and age >= data["first_yield_day"]
        if data["ongoing"]:
            if ripe:
                return ("HARVEST", None)
        elif ripe and (age >= data["max_yield_day"] or day >= LAST_DAY):
            return ("HARVEST", None)
        if not tile["watered_today"]:
            return ("WATER", None)
    return None


def _priority(task):
    # Water first: an unwatered plant dies. Harvest before planting.
    return {"WATER": 0, "HARVEST": 1, "DIG": 2, "PLANT": 3}.get(task, 9)


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


def _act(task, crop):
    return ["PLANT", crop] if task == "PLANT" else [task]


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

    orders = []
    if hour == 0 and day < LAST_DAY:
        orders += [["HIRE"]] * max(0, MAX_HANDS - farm.get("hires_today", 0))

    orders += _seed_orders(player, farm, seeds, day, board_size)

    if hour == 0:
        if day == 0:
            _history.clear()
        _record_prices(player, obs["market"]["prices"], day)
    orders += _sell_orders(shed, obs["market"]["inventory"], farm["money"], day, player)

    units = [farm["farmer"]] + list(farm.get("hands", []))
    cashing_out = day >= LAST_DAY and hour >= CASHOUT_HOUR

    tasks = []
    if not cashing_out:
        budget = dict(seeds)
        for x, y, tile in _my_tiles(farm):
            want = _planned_crop(player, x, y, board_size, day)
            task = _tile_task(tile, day, want, budget)
            if task is None:
                continue
            if task[0] == "PLANT":
                budget[task[1]] -= 1
            tasks.append((_priority(task[0]), x, y, task))
        tasks.sort(key=lambda t: t[:3])

    ops = []
    taken = set()
    for idx, pos in enumerate(units):
        if cashing_out:
            carried = sum((inventories[idx] if idx < len(inventories) else {}).values())
            ops.append(_cashout_op(pos, carried))
            continue
        chosen = None
        best = None
        for i, (prio, x, y, _task) in enumerate(tasks):
            if i in taken:
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
