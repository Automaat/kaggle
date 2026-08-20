"""Frozen 1.4.0. A herd of seven cows and five sheep, tended by the day plan."""

import copy
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
    "GOOSE": {"cost": 300, "structure": "COOP", "product": "EGG", "first_yield_day": 4, "interval": 1, "max_held": 4, "rate": 2.0},
    "COW": {"cost": 400, "structure": "PASTURE", "product": "MILK", "first_yield_day": 8, "interval": 2, "max_held": 6, "rate": 1.5},
    "SHEEP": {"cost": 500, "structure": "PASTURE", "product": "WOOL", "first_yield_day": 6, "interval": 3, "max_held": 6, "rate": 4 / 3},
}
# Days a tile stays occupied, used to decide whether a plan item can still pay off.
LIFESPAN = {
    "WHEAT": 4, "CARROT": 3, "TOMATO": 11, "STRAWBERRY": 16, "MELON": 10,
    "GOOSE": 9, "COW": 13, "SHEEP": 11,
}
FEED_DAYS = int(os.environ.get("KAGG_FEED_DAYS", "2"))
# Units a watered, unfertilized tile returns over one full occupancy.
EXPECTED_YIELD = {"WHEAT": 4, "CARROT": 3, "TOMATO": 4, "STRAWBERRY": 4, "MELON": 6}
# Scheduled production ages for the ongoing crops: first_yield_day, then every
# `interval` days, capped at max_yield productions.
PRODUCTION_AGES = {
    "TOMATO": [8, 9, 10, 11],
    "STRAWBERRY": [10, 12, 14, 16],
}
FERTILIZE_DAYS = 3  # A FERTILIZE covers today and the next two days.
def _planner(player):
    """Per-player so a sweep can pit the dynamic planner against a fixed mix."""
    return os.environ.get(f"KAGG_PLANNER_{player}") or os.environ.get("KAGG_PLANNER") or "dynamic"
# Herd composition, as animal:count. Milk, wool and egg sit on independent price
# curves, so a mixed herd saturates three of them instead of glutting one.
HERD_SPEC = os.environ.get(
    "KAGG_HERD_EXPERIMENT", os.environ.get("KAGG_HERD_SPEC", "COW:7,SHEEP:5")
)

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


def _enabled(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() not in ("", "0", "false", "no", "off")


# Replicated winners default on; unresolved or losing experiments stay opt-in.
EFFECTIVE_PROJECTION = _enabled("KAGG_EFFECTIVE_PROJECTION")
FUTURE_SHOPS = _enabled("KAGG_FUTURE_SHOPS", True)
FUTURE_SHOP_FACTOR = float(os.environ.get("KAGG_FUTURE_SHOP_FACTOR", "1"))
PARTIAL_SCARCITY = _enabled("KAGG_PARTIAL_SCARCITY")
EXACT_SELL_ORDER = _enabled("KAGG_EXACT_SELL_ORDER")
SELL_LOT = int(os.environ.get("KAGG_SELL_LOT", "0"))
MELON_RACE = _enabled("KAGG_MELON_RACE")
MELON_RACE_THRESHOLD = int(os.environ.get("KAGG_MELON_RACE_THRESHOLD", "4"))
MELON_FERTILIZER_CAP = int(os.environ.get("KAGG_MELON_FERTILIZER_CAP", "13"))
SEASONAL_PLANNER = _enabled("KAGG_SEASONAL_PLANNER")
MELON_QUOTA = int(os.environ.get("KAGG_MELON_QUOTA", "13"))
STRAWBERRY_QUOTA = int(os.environ.get("KAGG_STRAWBERRY_QUOTA", "5"))
HERD_START_DAY = int(os.environ.get("KAGG_HERD_START_DAY", "0"))
HERD_BUY_PER_DAY = int(os.environ.get("KAGG_HERD_BUY_PER_DAY", "99"))
OPPONENT_STOCK = _enabled("KAGG_OPPONENT_STOCK", True)
OPPONENT_DUMP_THRESHOLD = int(os.environ.get("KAGG_OPPONENT_DUMP_THRESHOLD", "12"))
DAIRY_LAND_COWS = int(os.environ.get("KAGG_DAIRY_LAND_COWS", "0"))
DAIRY_LAND_START_DAY = int(os.environ.get("KAGG_DAIRY_LAND_START_DAY", "10"))
NEAR_SHED_HERD = _enabled("KAGG_NEAR_SHED_HERD")
FEEDER_UNITS = int(os.environ.get("KAGG_FEEDER_UNITS", "0"))
CARE_BEFORE_WATER = _enabled("KAGG_CARE_BEFORE_WATER")
COLLECT_BEFORE_HARVEST = _enabled("KAGG_COLLECT_BEFORE_HARVEST")
SUPPLY_ACCOUNTING = _enabled("KAGG_SUPPLY_ACCOUNTING", True)
PLACE_PRIORITY = _enabled("KAGG_PLACE_PRIORITY", True)
PICKUP_BUDGET = _enabled("KAGG_PICKUP_BUDGET")
CARRIED_FEED = _enabled("KAGG_CARRIED_FEED", True)
BATCH_CROP_HARVEST = _enabled("KAGG_BATCH_CROP_HARVEST")
BATCH_ANIMAL_HARVEST = _enabled("KAGG_BATCH_ANIMAL_HARVEST")
INTEGRATED_CROP_VALUE = _enabled("KAGG_INTEGRATED_CROP_VALUE")
MELON_TILE_CAP = int(os.environ.get("KAGG_MELON_TILE_CAP", "0"))
ALWAYS_SELL = _enabled("KAGG_ALWAYS_SELL")
ALWAYS_HOLD = _enabled("KAGG_ALWAYS_HOLD")
ANIMAL_BUY_CAP = int(os.environ.get("KAGG_ANIMAL_BUY_CAP", "0"))
TRIP_RADIUS = int(os.environ.get("KAGG_TRIP_RADIUS", "2"))
ROUTE_STICKY = _enabled("KAGG_ROUTE_STICKY")
ROUTE_CLUSTERS = _enabled("KAGG_ROUTE_CLUSTERS", True)
ZONE_PENALTY = int(os.environ.get("KAGG_ZONE_PENALTY", "1"))

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
MAX_QUADRANTS = int(os.environ.get("KAGG_LAND", "1"))  # Extra quadrants to unlock.
SEED_RESERVE = 80  # Cash per empty tile kept back so a land buy cannot starve planting.
HANDS_PER_TILE = float(os.environ.get("KAGG_HANDS_PER_TILE", "0.2"))
MAX_HANDS = int(os.environ.get("KAGG_MAX_HANDS", "12"))
HIRES_PER_TURN = 3  # Only 10 market orders clear per turn; leave room to sell and sow.
HIRE_HOURS = 4
MAX_ORDERS = 10
SHED_TARGET = int(os.environ.get("KAGG_SHED_TARGET", "70"))  # Leave room for a day of harvest before overflow is discarded.
MIN_CASH = int(os.environ.get("KAGG_MIN_CASH", "400"))
LAST_DAY = 29
LIQUIDATION_DAYS = int(os.environ.get("KAGG_LIQ_DAYS", "6"))  # Days before the end to start unwinding held stock.
LAST_HOUR = 22  # Step 718 is the last turn the market clears; 718 % 24 == 22.

MIN_LAND_PAYBACK_DAYS = 8  # A quadrant unlocked later than this cannot repay itself.

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


_market_params = None  # Episode overrides, if the configuration supplies any.
_race_active = False
_opponent_state = {}
_routes = {}
_day_plans = {}


def _town_consumption(step, shops):
    consumed = {p: 0 for p in MARKET_PARAMS}
    if step % 4 == 0:
        for shop in shops:
            products = SHOPS.get(shop, [])
            multiplier = 2 if len(products) == 1 else 1
            for product in products:
                consumed[product] += multiplier
    if step % 24 == 0:
        for product in consumed:
            if product != "FERTILIZER":
                consumed[product] += 1
    return consumed


def _public_harvest(previous, current):
    harvested = {p: 0 for p in MARKET_PARAMS}
    for y, row in enumerate(previous):
        for x, old in enumerate(row):
            if not isinstance(old, dict) or old.get("yield_units", 0) <= 0:
                continue
            new = current[y][x]
            if old.get("kind") == "PLANT" and not CROPS.get(old.get("crop"), {}).get("ongoing"):
                same = isinstance(new, dict) and new.get("kind") == "PLANT" and new.get("crop") == old.get("crop")
                if not same:
                    harvested[old["crop"]] += old["yield_units"]
            elif isinstance(new, dict):
                if "animal" in old and new.get("animal") == old.get("animal"):
                    product = ANIMALS[old["animal"]]["product"]
                elif old.get("kind") == "PLANT" and new.get("crop") == old.get("crop"):
                    product = old["crop"]
                else:
                    continue
                harvested[product] += max(0, old["yield_units"] - new.get("yield_units", 0))
    return harvested


def _update_opponent_stock(obs, player):
    """Lower-bound hidden stock from public harvests and market deltas."""
    if not OPPONENT_STOCK:
        return {}
    step = obs.get("step", obs["day"] * 24 + obs["hour"])
    opponent_tiles = obs["farms"][1 - player]["tiles"]
    state = _opponent_state.get(player)
    if state is None or step <= state["step"]:
        state = {
            "step": step,
            "tiles": copy.deepcopy(opponent_tiles),
            "inventory": dict(obs["market"]["inventory"]),
            "shops": list(obs["town"]["unlocked_shops"]),
            "orders": [],
            "stock": {p: 0 for p in MARKET_PARAMS},
        }
        _opponent_state[player] = state
        return state["stock"]

    harvested = _public_harvest(state["tiles"], opponent_tiles)
    consumed = _town_consumption(state["step"], state["shops"])
    own_sells = {p: 0 for p in MARKET_PARAMS}
    own_buys = {p: 0 for p in MARKET_PARAMS}
    for order in state["orders"]:
        if len(order) < 3 or order[1] not in MARKET_PARAMS:
            continue
        if order[0] == "SELL" and market_price(order[1], state["inventory"][order[1]]) > PRICE_FLOOR:
            own_sells[order[1]] += order[2]
        elif order[0] == "BUY_PRODUCT":
            own_buys[order[1]] += order[2]
    for product in MARKET_PARAMS:
        delta = obs["market"]["inventory"][product] - state["inventory"][product]
        rival_sales = max(0, delta + consumed[product] - own_sells[product] + own_buys[product])
        state["stock"][product] = max(0, state["stock"][product] + harvested[product] - rival_sales)
    state.update({
        "step": step,
        "tiles": copy.deepcopy(opponent_tiles),
        "inventory": dict(obs["market"]["inventory"]),
        "shops": list(obs["town"]["unlocked_shops"]),
        "orders": [],
    })
    return dict(state["stock"])


def _remember_market_orders(player, orders):
    if OPPONENT_STOCK and player in _opponent_state:
        _opponent_state[player]["orders"] = [list(order) for order in orders]


def market_price(item, inventory, params=None):
    """Same curve the environment quotes, so we can price a sale before making it."""
    params = params if params is not None else _market_params
    if params and item in params:
        row = params[item]
        base, t = row["base"], row["T"]
        below_f, below_target = row["below_func"], row["below_target"]
        above_f, above_target = row["above_func"], row["above_target"]
    else:
        base, t, below_f, below_target, above_f, above_target = MARKET_PARAMS[item]
    x = abs(inventory - MARKET_I0)
    if inventory < MARKET_I0:
        func, target, sign = below_f, below_target, 1.0
    else:
        func, target, sign = above_f, above_target, -1.0
    denom = _shape(func, t, t)
    amp = target * base / denom if denom else 0.0
    return max(PRICE_FLOOR, round(base + sign * amp * _shape(func, x, t)))


def _farm_supply(farm, farm_index, day, player=None):
    """Units of each product one farm will still put on the market this season.

    Every tile of both farms is public, and `CROPS`/`ANIMALS` are fixed, so this
    is a hard ceiling on remaining supply rather than an estimate. Compared
    against the town's drain it says which products will be scarce at the end of
    the season and which are already oversupplied.
    """
    days_left = max(0, LAST_DAY - day)
    supply = {}
    for row in farm["tiles"]:
        for tile in row:
            if not isinstance(tile, dict):
                continue
            if "animal" in tile:
                data = ANIMALS[tile["animal"]]
                if SUPPLY_ACCOUNTING:
                    supply[data["product"]] = supply.get(data["product"], 0) + tile.get("yield_units", 0)
                productive = min(days_left, days_left - max(0, data["first_yield_day"] - (day - tile["placed_day"])))
                product = data["product"]
                supply[product] = supply.get(product, 0) + max(0, data["rate"] * productive)
                supply["FERTILIZER"] = supply.get("FERTILIZER", 0) + days_left
            elif tile.get("kind") == "PLANT":
                crop = tile["crop"]
                if crop not in EXPECTED_YIELD:
                    continue
                age = day - tile["planted_day"]
                remaining = LIFESPAN[crop] - age
                if (SUPPLY_ACCOUNTING and crop in PRODUCTION_AGES
                        and tile.get("yield_units", 0) > 0):
                    supply[crop] = supply.get(crop, 0) + tile["yield_units"]
                if remaining <= days_left or (SUPPLY_ACCOUNTING and crop in PRODUCTION_AGES):
                    # A fertilized ongoing crop yields 2 per production, not
                    # 1, and the flag is public on every opponent tile.
                    fertilized = (tile.get("fertilized_until_day", -1) >= day
                                  or (SUPPLY_ACCOUNTING and farm_index == player
                                      and crop in PRODUCTION_AGES))
                    if crop in PRODUCTION_AGES:
                        # A tile that has already fired most of its
                        # productions has little supply left to contribute.
                        left = sum(
                            1 for p in PRODUCTION_AGES[crop]
                            if p > age and (not SUPPLY_ACCOUNTING or p - age <= days_left)
                        )
                    else:
                        left = EXPECTED_YIELD[crop]
                    supply[crop] = supply.get(crop, 0) + left * (2 if fertilized else 1)
    return supply


def _supply_forecast(farms, day, player=None):
    """Both farms' remaining supply, summed the way the shared curve sees it."""
    total = {}
    for farm_index, farm in enumerate(farms):
        for product, units in _farm_supply(farm, farm_index, day, player).items():
            total[product] = total.get(product, 0) + units
    return total


def _expected_shop_daily_drain():
    """Expected daily demand contributed by one future random shop."""
    expected = {p: 0.0 for p in MARKET_PARAMS}
    for products in SHOPS.values():
        multiplier = 2 if len(products) == 1 else 1
        for product in products:
            expected[product] += SHOP_TICKS_PER_DAY * multiplier / len(SHOPS)
    return expected


def _demand_until(shops, day, days):
    """Town demand over a horizon, including expected future shop unlocks."""
    current = _daily_drain(shops)
    total = {p: current.get(p, 0) * days for p in MARKET_PARAMS}
    if not FUTURE_SHOPS or days <= 0:
        return total
    expected = _expected_shop_daily_drain()
    room = max(0, 8 - len(shops))
    for offset in range(days):
        future_day = day + offset
        unlocks = min(room, max(0, future_day // 3 - day // 3))
        for product in total:
            total[product] += unlocks * expected.get(product, 0) * FUTURE_SHOP_FACTOR
    return total


def _scarcity(farms, shops, day, player=None):
    """Per product, town drain minus remaining supply from both farms.

    Positive means the price still has room to climb before the last turn, so
    the stock is worth holding. Negative means every further unit sold walks
    down the glut curve, so the best sale is the one made now.
    """
    days_left = max(0, LAST_DAY - day)
    demand = _demand_until(shops, day, days_left)
    supply = _supply_forecast(farms, day, player)
    return {p: demand.get(p, 0) - supply.get(p, 0) for p in MARKET_PARAMS}


def _hold_quota(kept_total, money, cheapest_price, cash_needed):
    """How much of the still-rising stock we are forced to sell anyway."""
    quota = max(0, kept_total - SHED_TARGET)
    if money < cash_needed and cheapest_price > 0:
        quota = max(quota, math.ceil((cash_needed - money) / cheapest_price))
    return min(quota, kept_total)


def _advance_inventory(item, inventory, count):
    for _ in range(max(0, count)):
        if market_price(item, inventory) > PRICE_FLOOR:
            inventory += 1
    return inventory


def _sale_revenue(item, inventory, count):
    revenue = 0
    for _ in range(max(0, count)):
        price = market_price(item, inventory)
        revenue += price
        if price > PRICE_FLOOR:
            inventory += 1
    return revenue


def _sell_orders(shed, inventory, money, day, player, cash_needed, feed_reserve, scarcity,
                 fertilizer_reserve, opponent_stock=None):
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
        rival_holds = (opponent_stock or {}).get(item, 0)
        forced_race = OPPONENT_STOCK and rival_holds >= OPPONENT_DUMP_THRESHOLD
        should_dump = ALWAYS_SELL or forced_race or scarcity.get(item, 0) <= count
        if dumping or (should_dump and not ALWAYS_HOLD):
            # Falling price: the best sale of this product is the one made now.
            sell_count = count
            if PARTIAL_SCARCITY and not dumping and not forced_race:
                sell_count = max(0, count - max(0, math.ceil(scarcity.get(item, 0))))
            if SELL_LOT > 0:
                sell_count = min(sell_count, SELL_LOT)
            if sell_count <= 0:
                keepers.append((price, item, count))
                continue
            inv = inventory.get(item, MARKET_I0)
            if EXACT_SELL_ORDER:
                delayed = _advance_inventory(item, inv, rival_holds or sell_count)
                loss = _sale_revenue(item, inv, sell_count) - _sale_revenue(item, delayed, sell_count)
                raised += _sale_revenue(item, inv, sell_count)
            else:
                after = market_price(item, inv + sell_count)
                loss = (price - after) * sell_count
                raised += price * sell_count
            dumps.append((loss, item, sell_count))
            if sell_count < count:
                keepers.append((price, item, count - sell_count))
        else:
            keepers.append((price, item, count))

    # Steepest curve first. Two sells of one item at the same order index split
    # the curve evenly, but an order at index 0 clears the whole top of the
    # curve before the opponent's index-3 order starts. The item that loses the
    # most to being second in line goes first.
    dumps.sort(reverse=True)
    orders = [["SELL", item, count] for _loss, item, count in dumps]

    kept_total = sum(c for _, _, c in keepers)
    quota = _hold_quota(kept_total, money + raised, keepers[0][0] if keepers else 0, cash_needed)
    # Both farms dumping into the same 24 turns floors strawberry after 62 units
    # and milk after 76. Start unwinding a few days out so the curve recovers
    # between sales.
    days_left = LAST_DAY - day + 1
    if 0 < days_left <= LIQUIDATION_DAYS:
        quota = max(quota, math.ceil(kept_total / days_left))
    quota = min(quota, kept_total)
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
            try:
                pattern += [item] * max(1, int(weight or 1))
            except ValueError:
                pattern.append(item)
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
            try:
                herd += [animal] * max(0, int(count or 1))
            except ValueError:
                herd.append(animal)
    return herd


def _desired_herd():
    return _parse_herd() + ["COW"] * DAIRY_LAND_COWS


def _crop_value(crop, projected_inv, day):
    """Profit per tile-day, priced at the market we will actually sell into.

    Quoting at post-harvest inventory rather than today's price is what stops
    the farm piling into one crop: each tile allocated pushes the next tile's
    quote for that crop down its glut curve.
    """
    units = EXPECTED_YIELD[crop] * (2 if crop in PRODUCTION_AGES else 1)
    inventory = projected_inv.get(crop, MARKET_I0)
    if INTEGRATED_CROP_VALUE:
        revenue = _sale_revenue(crop, inventory, units)
        return (revenue - CROPS[crop]["seed"]) / LIFESPAN[crop]
    price = market_price(crop, inventory + units)
    return (units * price - CROPS[crop]["seed"]) / LIFESPAN[crop]


def _harvest_inventory(inventory, drain, days):
    """Market inventory as it will stand when this planting is ready to sell."""
    return {p: n - drain.get(p, 0) * days * DRAIN_FACTOR for p, n in inventory.items()}


def _effective_yield(crop):
    return EXPECTED_YIELD[crop] * (2 if crop in PRODUCTION_AGES else 1)


def _projected_inventory(inventory, shops, day, days):
    if FUTURE_SHOPS:
        demand = _demand_until(shops, day, days)
        return {p: n - demand.get(p, 0) * DRAIN_FACTOR for p, n in inventory.items()}
    return _harvest_inventory(inventory, _daily_drain(shops), days)


def _seasonal_crop(ready, standing, allocated, projected, day):
    """Deadline-aware seasonal quotas before falling back to marginal value."""
    if "MELON" in ready and day <= 1 and standing.get("MELON", 0) + allocated.get("MELON", 0) < MELON_QUOTA:
        return "MELON"
    if ("STRAWBERRY" in ready
            and standing.get("STRAWBERRY", 0) + allocated.get("STRAWBERRY", 0) < STRAWBERRY_QUOTA):
        return "STRAWBERRY"
    candidates = [crop for crop in ready if crop != "MELON" or day <= 1]
    return max(candidates or ready, key=lambda crop: _crop_value(crop, projected, day))


def _dairy_positions(tiles, board_size):
    half = board_size // 2
    positions = sorted(
        ((x, y) for x, y, _tile in tiles if x >= half and y < half),
        key=lambda pos: abs(pos[0] - (half - 1)) + abs(pos[1] - (half - 1)),
    )
    return set(positions[:DAIRY_LAND_COWS])


def _dynamic_plan(tiles, day, inventory, shops, board_size=10):
    """Assign each empty tile the crop with the best marginal return at harvest.

    The first `HERD` tiles are reserved for livestock. Letting the value model
    bid for every tile floods the farm with animals it cannot pay for, and a
    tile reserved for an unaffordable animal simply sits empty.
    """
    banned = set(os.environ.get("KAGG_BAN", "").upper().split(",")) - {""}
    ready = [c for c in CROPS if day + LIFESPAN[c] <= LAST_DAY and c not in banned]
    projected = {c: _projected_inventory(inventory, shops, day, LIFESPAN[c]).get(c, MARKET_I0) for c in ready}
    herd = _parse_herd() if day >= HERD_START_DAY else []
    plan, reserved = {}, 0
    standing = {crop: 0 for crop in CROPS}
    for x, y, tile in tiles:
        if isinstance(tile, dict) and ("animal" in tile or tile.get("kind") in ("COOP", "PASTURE")):
            reserved += 1
        elif isinstance(tile, dict) and tile.get("kind") == "PLANT" and tile.get("crop") in standing:
            standing[tile["crop"]] += 1
    allocated = {crop: 0 for crop in CROPS}
    half = int(board_size / 2)
    herd_positions = {}
    if NEAR_SHED_HERD:
        candidates = sorted(
            ((x, y) for x, y, _tile in tiles if x < half and y < half),
            key=lambda pos: (abs(pos[0] - (half - 1)) + abs(pos[1] - (half - 1)), pos),
        )
        herd_positions = dict(zip(candidates, herd))
    dairy_positions = _dairy_positions(tiles, board_size) if DAIRY_LAND_COWS > 0 else set()
    for x, y, tile in tiles:
        if tile is not None:
            continue
        if DAIRY_LAND_COWS > 0 and x >= half and y < half:
            plan[(x, y)] = "COW" if (x, y) in dairy_positions else None
            continue
        if NEAR_SHED_HERD and (x, y) in herd_positions:
            animal = herd_positions[(x, y)]
            plan[(x, y)] = animal if day <= LAST_DAY - LIFESPAN[animal] else None
            continue
        if not NEAR_SHED_HERD and reserved < len(herd) and day <= LAST_DAY - LIFESPAN[herd[reserved]]:
            plan[(x, y)] = herd[reserved]
            reserved += 1
            continue
        if not ready:
            plan[(x, y)] = None
            continue
        eligible = ready
        if MELON_TILE_CAP > 0 and standing.get("MELON", 0) + allocated.get("MELON", 0) >= MELON_TILE_CAP:
            eligible = [crop for crop in ready if crop != "MELON"] or ready
        if SEASONAL_PLANNER:
            best = _seasonal_crop(eligible, standing, allocated, projected, day)
        else:
            best = max(eligible, key=lambda c: _crop_value(c, projected, day))
        plan[(x, y)] = best
        allocated[best] += 1
        projected[best] += _effective_yield(best) if EFFECTIVE_PROJECTION else EXPECTED_YIELD[best]
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
    wanted_quadrants = max(MAX_QUADRANTS, 1 if DAIRY_LAND_COWS > 0 else 0)
    payback_days = LIFESPAN["COW"] if DAIRY_LAND_COWS > 0 else MIN_LAND_PAYBACK_DAYS
    if DAIRY_LAND_COWS > 0 and day < DAIRY_LAND_START_DAY:
        return []
    if bought >= min(wanted_quadrants, len(LAND_PRICES)) or day > LAST_DAY - payback_days:
        return []
    if DAIRY_LAND_COWS > 0 and bought == 0:
        needed = LAND_PRICES[0] + DAIRY_LAND_COWS * ANIMALS["COW"]["cost"] + MIN_CASH
        return [["BUY_LAND"]] if farm["money"] >= needed else []
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


def _herd_deficit(tiles):
    """Animals the planned herd still wants, by kind."""
    want = {}
    for animal in _desired_herd():
        want[animal] = want.get(animal, 0) + 1
    for _, _, tile in tiles:
        if isinstance(tile, dict) and "animal" in tile:
            want[tile["animal"]] = want.get(tile["animal"], 0) - 1
    return {animal: n for animal, n in want.items() if n > 0}


def _needed_animal(kind, deficit):
    """Which animal an empty structure of this kind should receive.

    Picking the first entry of `ANIMALS` that matches the structure always
    returned COW for a pasture, so sheep were bought, never carried and never
    placed — the mixed herd was inert and the money dead.
    """
    return next((a for a in deficit if ANIMALS[a]["structure"] == kind and deficit[a] > 0), None)


def _empty_structures(tiles):
    """Built but unoccupied coops and pastures, by the animal the herd still wants."""
    deficit = _herd_deficit(tiles)
    want = {}
    for _, _, tile in tiles:
        if isinstance(tile, dict) and tile.get("kind") in ("COOP", "PASTURE") and "animal" not in tile:
            animal = _needed_animal(tile["kind"], deficit)
            if animal is None:
                continue
            want[animal] = want.get(animal, 0) + 1
            deficit[animal] -= 1
    return want


def _animal_orders(wanted, shed, money, buy_allowance=None):
    """Buy livestock for the structures that are standing empty."""
    orders = []
    if sum(shed.values()) >= SHED_TARGET:
        return orders
    budget = money
    remaining_today = HERD_BUY_PER_DAY
    if buy_allowance is not None:
        remaining_today = min(remaining_today, max(0, buy_allowance))
    for animal, need in sorted(wanted.items(), key=lambda kv: ANIMALS[kv[0]]["cost"]):
        take = min(
            need - shed.get(animal, 0), int(budget // ANIMALS[animal]["cost"]), remaining_today
        )
        if take > 0:
            orders.append(["BUY_ANIMAL", animal, take])
            budget -= take * ANIMALS[animal]["cost"]
            remaining_today -= take
    return orders


def _feed_orders(herd, shed, prices, money, carried_wheat=0):
    """Top up the wheat store when the herd would otherwise go hungry."""
    if not herd or sum(shed.values()) >= SHED_TARGET:
        # Bought goods land in the shed and are refused once it is full.
        return []
    stock = shed.get("WHEAT", 0) + (carried_wheat if CARRIED_FEED else 0)
    short = herd * FEED_DAYS - stock
    price = max(1, prices.get("WHEAT", 25))
    take = min(short, int(money // price))
    return [["BUY_PRODUCT", "WHEAT", take]] if take > 0 else []


def _fertilizer_orders(need, stock, shed, prices, money):
    if not MELON_RACE or need <= stock or sum(shed.values()) >= SHED_TARGET:
        return []
    price = max(1, prices.get("FERTILIZER", 100))
    take = min(need - stock, MELON_FERTILIZER_CAP, int(money // price))
    return [["BUY_PRODUCT", "FERTILIZER", take]] if take > 0 else []


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


def _tile_task(tile, day, want, seeds, stock, wanted_animals):
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
            animal = next((a for a in wanted_animals
                           if ANIMALS[a]["structure"] == kind and stock.get(a, 0) > 0), None)
            return [("PLACE", animal)] if animal else []
        return _animal_tasks(tile, day)
    if kind == "PLANT":
        crop = tile["crop"]
        if crop not in CROPS:
            return []
        data = CROPS[crop]
        age = day - tile["planted_day"]
        ripe = tile.get("yield_units", 0) > 0 and age >= data["first_yield_day"]
        if data["ongoing"]:
            jobs = []
            if not tile["watered_today"]:
                jobs.append(("WATER!" if tile.get("consecutive_unwatered", 0) >= 1 else "WATER", None))
            if _fertilize_pays(tile, crop, age, day):
                jobs.append(("FERTILIZE", None))
            future_productions = [p for p in PRODUCTION_AGES[crop] if p > age]
            batch_ready = (not future_productions
                           or tile.get("yield_units", 0) + 2 > data["max_yield"])
            if ripe and (not BATCH_CROP_HARVEST or day >= LAST_DAY or batch_ready):
                jobs.append(("HARVEST", None))
            return jobs
        jobs = []
        window_start = (data["max_yield_day"] + 1) // 2
        bonus_left = (window_start <= age <= data["max_yield_day"]
                      and tile["yield_units"] < data["max_yield"])
        if not tile["watered_today"]:
            # A plant already on one dry day turns to weed tonight.
            jobs.append(("WATER!" if tile.get("consecutive_unwatered", 0) >= 1 else "WATER", None))
        if _fertilize_pays(tile, crop, age, day):
            jobs.append(("FERTILIZE", None))
        done = tile["yield_units"] >= data["max_yield"] or age >= data["max_yield_day"]
        # Harvesting before the day's watering throws away the last bonus unit —
        # a quarter of every carrot tile and every wheat tile.
        if ripe and (day >= LAST_DAY or (done and (tile["watered_today"] or not bonus_left))):
            jobs.append(("HARVEST", None))
        return jobs
    return []


def _fertilize_pays(tile, crop, age, day):
    """Whether fertilizing this ongoing crop now doubles a production it reaches.

    An ongoing crop yields 1 per scheduled production, or 2 if it was fertilized
    AND watered that day. The effect lasts three days, so two applications cover
    all four of a strawberry's productions.
    """
    if (MELON_RACE and crop == "MELON" and _race_active
            and tile.get("planted_day", LAST_DAY) <= 1 and age == 5
            and tile.get("yield_units", 0) < CROPS["MELON"]["max_yield"]):
        return tile.get("fertilized_until_day", -1) < day
    if crop not in PRODUCTION_AGES:
        return False
    if tile.get("fertilized_until_day", -1) >= day:
        return False
    # The production for age p is computed during the refresh of day p-1, and a
    # FERTILIZE on age a sets the flag through a+2 — so it doubles productions
    # at ages a+1 through a+3, not a through a+2.
    return any(age < p <= age + FERTILIZE_DAYS for p in PRODUCTION_AGES[crop])


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
    harvest = tile.get("yield_units", 0) > 0
    if harvest and BATCH_ANIMAL_HARVEST and day < LAST_DAY:
        data = ANIMALS[tile["animal"]]
        age = day - tile["placed_day"]
        first = data["first_yield_day"]
        if age < first:
            next_age = first
        else:
            next_age = first + ((age - first) // data["interval"] + 1) * data["interval"]
        if next_age <= LAST_DAY - tile["placed_day"]:
            days_until = next_age - age
            next_yield = 1 + tile.get("pending_care_bonus", 0) + max(0, days_until - 1)
            harvest = tile["yield_units"] + next_yield > data["max_held"]
    if harvest:
        jobs.append(("HARVEST", None))
    if tile.get("fertilizer_available"):
        jobs.append(("COLLECT_FERTILIZER", None))
    return jobs


def _priority(task):
    # Water first: an unwatered plant dies. Harvest before planting.
    # CARE banks one extra unit per fed day, paid out on the next production, so
    # on a cow it is worth a whole extra milk — far more than sowing another
    # tile. Fertilizer is 1/animal/day that nothing else in the game drains.
    priorities = {
        "WATER!": 0, "FEED!": 0, "FEED": 1, "WATER": 2, "CARE": 3,
        "FERTILIZE": 4, "HARVEST": 5, "COLLECT_FERTILIZER": 6, "PLACE": 7,
        "PLANT": 8, "BUILD": 8, "DIG": 9,
    }
    if PLACE_PRIORITY:
        priorities["PLACE"] = 1
    if CARE_BEFORE_WATER:
        priorities["CARE"], priorities["WATER"] = 2, 3
    if COLLECT_BEFORE_HARVEST:
        priorities["COLLECT_FERTILIZER"], priorities["HARVEST"] = 4, 6
    return priorities.get(task, 9)


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


def _route_targets(player, step):
    """Per unit, the tile it was walking to last turn, cleared between episodes."""
    state = _routes.get(player)
    if state is None or step <= state["step"]:
        state = {"step": step, "targets": {}}
        _routes[player] = state
    state["step"] = step
    return state["targets"]


def _snake_order(tiles, board_size):
    """Tiles in a boustrophedon sweep, so neighbours stay neighbours in the list."""
    return sorted(tiles, key=lambda pos: (pos[1], pos[0] if pos[1] % 2 == 0 else board_size - pos[0]))


def _cluster_plan(player, day, step, tiles, units, board_size):
    """Split the board into one contiguous strip per unit, once a day.

    Deciding this per turn is what made the quadrant experiment thrash: the
    assignment moved under the units while they walked. A strip that holds for
    the whole day gives a unit a neighbourhood to work, and the walk out is paid
    once rather than every time the task list reshuffles.
    """
    state = _day_plans.get(player)
    if (state is not None and step >= state["step"]
            and state["day"] == day and state["units"] == units):
        state["step"] = step
        return state["zones"]
    # Split the tiles that carry work, not the acreage: a strip of bare ground
    # leaves its unit idle while the next strip drowns.
    working = [(x, y) for x, y, tile in tiles
               if isinstance(tile, dict) and (tile.get("kind") == "PLANT" or "animal" in tile)]
    positions = _snake_order(working, board_size)
    zones = {}
    if positions and units > 0:
        size = max(1, -(-len(positions) // units))
        for index, pos in enumerate(positions):
            zones[pos] = min(units - 1, index // size)
    _day_plans[player] = {"day": day, "units": units, "zones": zones, "step": step}
    return zones


def _task_key(prio, dist):
    """Nearby work before distant work, but never before a dying plant.

    Movement is most of every unit-turn, so a unit that walks past a tile it
    could have watered pays the walk twice. Priority still wins inside a trip,
    and an emergency still outranks the trip itself.
    """
    if prio == 0:
        return (0, prio, dist)
    return (1 if TRIP_RADIUS > 0 and dist <= TRIP_RADIUS else 2, prio, dist)


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
        carriers = FEEDER_UNITS if FEEDER_UNITS > 0 else n_units
        share = max(1, -(-hungry // max(1, carriers)))
        return ["PICKUP", "WHEAT", min(share, shed["WHEAT"])]
    if needs_fertilizer > 0 and not carried.get("FERTILIZER") and shed.get("FERTILIZER", 0) > 0:
        share = max(1, -(-needs_fertilizer // max(1, n_units)))
        return ["PICKUP", "FERTILIZER", min(share, shed["FERTILIZER"])]
    return None


def _shed_tile(board_size):
    """The one shed-access tile that starts unlocked, in the NW quadrant."""
    half = board_size // 2
    return (half - 1, half - 1)


def _is_shed_adjacent(pos, board_size):
    half = board_size // 2
    return tuple(pos) in {(half - 1, half - 1), (half, half - 1), (half - 1, half), (half, half)}


def _cashout_op(pos, carried, shed_tile):
    """Final-day run to the shed: produce still in hand when the season ends is lost."""
    if not carried:
        return ["PASS"]
    return _step_toward(pos, shed_tile) or ["DROP"]


def agent(obs):
    player = obs["player"]
    farm = obs["farms"][player]
    private = obs["private"]
    day, hour = obs["day"], obs["hour"]
    seeds = dict(private.get("seeds", {}))
    shed = private.get("shed", {})
    board_size = len(farm["tiles"])
    inventories = private.get("inventories", [{}])
    carried_wheat = sum(inv.get("WHEAT", 0) for inv in inventories)

    global _market_params, _race_active
    _market_params = obs["market"].get("params")
    opponent = obs["farms"][1 - player]
    _race_active = MELON_RACE and sum(
        1 for row in opponent["tiles"] for tile in row
        if isinstance(tile, dict) and tile.get("kind") == "PLANT" and tile.get("crop") == "MELON"
    ) >= MELON_RACE_THRESHOLD
    opponent_stock = _update_opponent_stock(obs, player)

    tiles = [t for t in _my_tiles(farm)]
    hires = []
    if hour < HIRE_HOURS and day < LAST_DAY:
        want_hands = min(MAX_HANDS, max(4, round(len(tiles) * HANDS_PER_TILE)))
        missing = max(0, want_hands - farm.get("hires_today", 0))
        hires = [["HIRE"]] * min(missing, HIRES_PER_TURN)
    orders = []

    if _planner(player) == "dynamic":
        plan = _dynamic_plan(
            tiles, day, obs["market"]["inventory"], obs["town"]["unlocked_shops"], board_size
        )
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

    land_orders = _land_orders(farm, day, sum(1 for _, _, t in tiles if t is None))
    orders += _sell_orders(
        shed, obs["market"]["inventory"], farm["money"], day, player,
        seed_bill + MIN_CASH,
        0 if day >= LAST_DAY else max(0, herd * FEED_DAYS - (carried_wheat if CARRIED_FEED else 0)),
        _scarcity(obs["farms"], obs["town"]["unlocked_shops"], day, player),
        0 if day >= LAST_DAY else fertilize_jobs, opponent_stock,
    )
    land_reserve = LAND_PRICES[0] if DAIRY_LAND_COWS > 0 and land_orders else 0
    if DAIRY_LAND_COWS > 0:
        orders += land_orders
    orders += _seed_orders(wanted_seeds, farm["money"] - land_reserve)
    if day < LAST_DAY:
        orders += _feed_orders(
            herd, shed, obs["market"]["prices"], farm["money"] - seed_bill, carried_wheat
        )
        fertilizer_stock = shed.get("FERTILIZER", 0) + sum(
            inv.get("FERTILIZER", 0) for inv in inventories
        )
        orders += _fertilizer_orders(
            fertilize_jobs, fertilizer_stock, shed, obs["market"]["prices"],
            farm["money"] - seed_bill - land_reserve,
        )
    if day <= LAST_DAY - LIFESPAN["GOOSE"]:
        # Count animals already in a unit's hands: a pasture stays "empty" until
        # the animal is placed, and PLACE only puts down one per turn.
        in_hand = {}
        for inv in inventories:
            for item, n in inv.items():
                if item in ANIMALS:
                    in_hand[item] = in_hand.get(item, 0) + n
        held = {a: shed.get(a, 0) + in_hand.get(a, 0) for a in ANIMALS}
        buy_allowance = None
        if ANIMAL_BUY_CAP > 0:
            buy_allowance = ANIMAL_BUY_CAP - herd - sum(held.values())
        orders += _animal_orders(
            structures, held, farm["money"] - seed_bill - MIN_CASH, buy_allowance
        )
    # Hires last: both players share one descending price curve per order index,
    # so a sell at index 0 clears above a sell the opponent placed at index 3.
    # Truncation also drops the tail, and losing a hire beats losing a sale.
    if DAIRY_LAND_COWS <= 0:
        orders += land_orders
    orders += hires

    units = [farm["farmer"]] + list(farm.get("hands", []))
    def _must_leave(pos):
        """Per unit, not a global hour.

        A unit eight steps out that leaves when a shed-adjacent one does never
        arrives, and its whole load is lost; a shed-adjacent unit that leaves
        early idles for hours on the highest-price day of the season.
        """
        shed_tile = _shed_tile(board_size)
        walk = abs(pos[0] - shed_tile[0]) + abs(pos[1] - shed_tile[1])
        return day >= LAST_DAY and hour >= LAST_HOUR - walk - 1

    wanted_animals = _herd_deficit(tiles)
    stock = dict(shed)
    for inv in inventories:
        for item, n in inv.items():
            stock[item] = stock.get(item, 0) + n

    tasks = []
    if True:
        budget = dict(seeds)
        dairy_positions = _dairy_positions(tiles, board_size)
        half = board_size // 2
        for x, y, tile in tiles:
            if DAIRY_LAND_COWS > 0 and x >= half and y < half and (x, y) not in dairy_positions:
                continue
            want = plan.get((x, y))
            for task in _tile_task(tile, day, want, budget, stock, wanted_animals):
                if task[0] == "PLANT":
                    budget[task[1]] -= 1
                tasks.append((_priority(task[0]), x, y, task))
        tasks.sort(key=lambda t: t[:3])

    hungry = sum(1 for _, _, t in tiles if isinstance(t, dict) and "animal" in t and not t["fed_today"])

    unplaced = _empty_structures(tiles)
    pickup_hungry = hungry
    pickup_fertilizer = fertilize_jobs
    pickup_shed = dict(shed)

    ops = []
    taken = set()
    targets = _route_targets(player, obs.get("step", day * 24 + hour))
    zones = (_cluster_plan(player, day, obs.get("step", day * 24 + hour), tiles, len(units), board_size)
             if ROUTE_CLUSTERS else {})
    for idx, pos in enumerate(units):
        carried = dict(inventories[idx]) if idx < len(inventories) else {}
        if _must_leave(pos):
            ops.append(_cashout_op(pos, sum(carried.values()), _shed_tile(board_size)))
            continue
        available_shed = pickup_shed if PICKUP_BUDGET else shed
        load = _pickup_op(
            pos, carried, available_shed,
            pickup_hungry if PICKUP_BUDGET else hungry,
            unplaced, len(units), board_size,
            pickup_fertilizer if PICKUP_BUDGET else fertilize_jobs,
        )
        if load is not None:
            if load[0] == "PICKUP" and load[1] in ANIMALS:
                unplaced[load[1]] = unplaced.get(load[1], 0) - load[2]
            if PICKUP_BUDGET and load[0] == "PICKUP":
                pickup_shed[load[1]] = max(0, pickup_shed.get(load[1], 0) - load[2])
                if load[1] == "WHEAT":
                    pickup_hungry = max(0, pickup_hungry - load[2])
                elif load[1] == "FERTILIZER":
                    pickup_fertilizer = max(0, pickup_fertilizer - load[2])
            ops.append(load)
            continue
        chosen = None
        best = None
        for i, (prio, x, y, (task, item)) in enumerate(tasks):
            if i in taken or not _can_do(task, item, carried):
                continue
            dist = abs(pos[0] - x) + abs(pos[1] - y)
            zone = zones.get((x, y)) if zones else None
            # A strip is a preference, not a fence: work still goes to whoever
            # is nearest, but a unit pays a few tiles of penalty for leaving the
            # neighbourhood it was given for the day.
            if zone is not None and zone != idx and prio > 0:
                dist += ZONE_PENALTY
            key = _task_key(prio, dist)
            if ROUTE_STICKY and targets.get(idx) == (x, y) and prio > 0:
                key = (key[0], -1, dist)
            if best is None or key < best:
                best, chosen = key, i
        if chosen is None:
            targets.pop(idx, None)
            ops.append(["PASS"])
            continue
        taken.add(chosen)
        _, x, y, (task, crop) = tasks[chosen]
        targets[idx] = (x, y)
        ops.append(_step_toward(pos, (x, y)) or _act(task, crop))

    market_orders = orders[:MAX_ORDERS]
    _remember_market_orders(player, market_orders)
    return {"farmer": ops[0], "hands": ops[1:], "market": market_orders}
