"""Kaggriculture submission entrypoint. Must expose `agent(obs)`."""

CROPS = {
    "WHEAT": {"seed": 10, "first_yield_day": 2, "max_yield_day": 4},
    "CARROT": {"seed": 20, "first_yield_day": 2, "max_yield_day": 3},
    "MELON": {"seed": 80, "first_yield_day": 10, "max_yield_day": 12},
}
SELLABLE = ["WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON", "EGG", "MILK", "WOOL", "FERTILIZER"]

CROP = "CARROT"
HARVEST_AGE = CROPS[CROP]["max_yield_day"]
MAX_HANDS = 8
MAX_ORDERS = 10
SHED = (4, 4)


def _my_tiles(farm):
    """Coordinates of every tile this player can act on."""
    for y, row in enumerate(farm["tiles"]):
        for x, tile in enumerate(row):
            if tile != "LOCKED":
                yield x, y, tile


def _tile_task(tile, day, seeds_left):
    if tile is None:
        return "PLANT" if seeds_left > 0 else None
    kind = tile.get("kind")
    if kind == "WEED":
        return "DIG"
    if kind == "PLANT":
        if tile["crop"] not in CROPS:
            return None
        age = day - tile["planted_day"]
        if age >= CROPS[tile["crop"]]["max_yield_day"]:
            return "HARVEST"
        if not tile["watered_today"]:
            return "WATER"
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


def _act(task):
    return ["PLANT", CROP] if task == "PLANT" else [task]


def agent(obs):
    player = obs["player"]
    farm = obs["farms"][player]
    private = obs["private"]
    day, hour = obs["day"], obs["hour"]
    seeds = private.get("seeds", {}).get(CROP, 0)
    shed = private.get("shed", {})

    orders = []
    if hour == 0:
        orders += [["HIRE"]] * max(0, MAX_HANDS - farm.get("hires_today", 0))

    empty = sum(1 for _, _, t in _my_tiles(farm) if t is None)
    need_seeds = max(0, empty - seeds)
    if need_seeds and farm["money"] > need_seeds * CROPS[CROP]["seed"]:
        orders.append(["BUY_SEED", CROP, need_seeds])

    for item in SELLABLE:
        if shed.get(item, 0) > 0:
            orders.append(["SELL", item, shed[item]])

    units = [farm["farmer"]] + list(farm.get("hands", []))
    tasks = []
    budget = seeds
    for x, y, tile in _my_tiles(farm):
        task = _tile_task(tile, day, budget)
        if task == "PLANT":
            budget -= 1
        if task:
            tasks.append((_priority(task), x, y, task))
    tasks.sort()

    ops = []
    taken = set()
    for pos in units:
        chosen = None
        best = None
        for i, (prio, x, y, task) in enumerate(tasks):
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
        _, x, y, task = tasks[chosen]
        ops.append(_step_toward(pos, (x, y)) or _act(task))

    return {"farmer": ops[0], "hands": ops[1:], "market": orders[:MAX_ORDERS]}
