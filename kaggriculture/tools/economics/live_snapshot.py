import json
import math
from collections.abc import Mapping

from .animal_milp import ANIMALS, GOODS, AnimalOracleInput, ExistingAnimal
from .land_hire_optimizer import OptimizerInput
from .market_ledger import CROPS, PRODUCTS, SHED_ITEMS, SHOP_DEMAND
from .milp_oracle import ExistingPlant, OracleInput
from .rolling_coordinator import (
    ExecutionSignal,
    ObservedDelta,
    RollingObservation,
    canonical_sha256,
)
from .space_planner import SpaceCell
from .whole_farm_backend import SharedCapacity, WholeFarmSnapshot


TERMINAL_STEP = 718
LAST_DAY = 29
BOARD_SIZE = 10
SHED_CAPACITY = 100
MARKET_ORDER_LIMIT = 10
MAX_HANDS = 12
ROUTE_ACTION_RESERVE = 12
QUADRANTS = ("NW", "NE", "SW", "SE")


class LiveSnapshotError(RuntimeError):
    pass


def _plain_observation(value):
    if isinstance(value, Mapping):
        document = dict(value.items())
    elif hasattr(value, "data"):
        document = json.loads(value.data)
        if hasattr(value, "step") and document.get("step") != value.step:
            raise LiveSnapshotError("world step differs from observation")
        if hasattr(value, "player") and document.get("player") != value.player:
            raise LiveSnapshotError("world player differs from observation")
    else:
        raise TypeError("observation must be a mapping or World")
    document.pop("remainingOverageTime", None)
    return json.loads(json.dumps(document, allow_nan=False))


def _clock(values):
    step = values.get("step")
    day = values.get("day")
    hour = values.get("hour")
    if type(step) is not int or not 0 <= step <= TERMINAL_STEP:
        raise LiveSnapshotError("source step must be in 0..718")
    if type(day) is not int or type(hour) is not int:
        raise LiveSnapshotError("day and hour must be integers")
    if day != step // 24 or hour != step % 24:
        raise LiveSnapshotError("clock fields disagree")
    return step, day, hour


def _player_state(values):
    player = values.get("player")
    farms = values.get("farms")
    private = values.get("private")
    if type(player) is not int or player not in (0, 1):
        raise LiveSnapshotError("player must be 0 or 1")
    if type(farms) is not list or len(farms) != 2:
        raise LiveSnapshotError("farms must contain two players")
    if not isinstance(farms[player], Mapping):
        raise LiveSnapshotError("player farm must be a mapping")
    if not isinstance(private, Mapping):
        raise LiveSnapshotError("private state must be a mapping")
    return player, farms[player], private


def _nonnegative_int(value, name):
    if type(value) is not int or value < 0:
        raise LiveSnapshotError(f"{name} must be a nonnegative integer")
    return value


def _inventory(mapping, keys, name):
    if not isinstance(mapping, Mapping):
        raise LiveSnapshotError(f"{name} must be a mapping")
    unknown = set(mapping) - set(keys)
    if unknown:
        raise LiveSnapshotError(f"{name} contains unknown items")
    return tuple(_nonnegative_int(mapping.get(key, 0), name) for key in keys)


def _combined_inventory(private):
    shed = private.get("shed")
    inventories = private.get("inventories")
    if not isinstance(shed, Mapping):
        raise LiveSnapshotError("shed must be a mapping")
    if type(inventories) is not list:
        raise LiveSnapshotError("unit inventories must be a list")
    totals = {item: _nonnegative_int(shed.get(item, 0), "shed") for item in SHED_ITEMS}
    if set(shed) - set(SHED_ITEMS):
        raise LiveSnapshotError("shed contains unknown items")
    carried = 0
    for inventory in inventories:
        if not isinstance(inventory, Mapping):
            raise LiveSnapshotError("unit inventory must be a mapping")
        if set(inventory) - set(SHED_ITEMS):
            raise LiveSnapshotError("unit inventory contains unknown items")
        for item, quantity in inventory.items():
            count = _nonnegative_int(quantity, "unit inventory")
            totals[item] += count
            carried += count
    return totals, carried


def _tiles(farm):
    rows = farm.get("tiles")
    if type(rows) is not list or len(rows) != BOARD_SIZE:
        raise LiveSnapshotError("farm board must have ten rows")
    if any(type(row) is not list or len(row) != BOARD_SIZE for row in rows):
        raise LiveSnapshotError("farm board must be 10x10")
    return rows


def _unlocked_quadrants(farm):
    values = farm.get("unlocked_quadrants")
    if type(values) is not list or not values:
        raise LiveSnapshotError("unlocked quadrants must be a nonempty list")
    if any(value not in QUADRANTS for value in values):
        raise LiveSnapshotError("unknown unlocked quadrant")
    if len(set(values)) != len(values):
        raise LiveSnapshotError("unlocked quadrants must be unique")
    return tuple(values)


def _quadrant(x, y):
    return QUADRANTS[(2 if y >= 5 else 0) + (1 if x >= 5 else 0)]


def _tile_kind(tile):
    if tile is None:
        return "EMPTY"
    if tile == "LOCKED":
        return "LOCKED"
    if not isinstance(tile, Mapping):
        raise LiveSnapshotError("tile must be null, locked or a mapping")
    kind = tile.get("kind")
    if kind not in ("WEED", "PLANT", "COOP", "PASTURE"):
        raise LiveSnapshotError("unknown tile kind")
    return kind


def _plant(tile, position):
    crop = tile.get("crop")
    if crop not in CROPS:
        raise LiveSnapshotError("plant has unknown crop")
    return ExistingPlant(
        position,
        crop,
        _nonnegative_int(tile.get("planted_day"), "planted day"),
        _nonnegative_int(tile.get("yield_units", 0), "plant yield"),
        tile.get("watered_today"),
        _nonnegative_int(
            tile.get("consecutive_unwatered", 0),
            "unwatered counter",
        ),
        tile.get("fertilized_until_day", -1),
    )


def _animal(tile, position):
    animal = tile.get("animal")
    if animal not in ANIMALS:
        raise LiveSnapshotError("animal tile has unknown animal")
    return ExistingAnimal(
        f"existing:{position[0]}:{position[1]}:{animal}",
        position,
        animal,
        _nonnegative_int(tile.get("placed_day"), "placed day"),
        _nonnegative_int(tile.get("yield_units", 0), "animal yield"),
        _nonnegative_int(tile.get("consecutive_unfed", 0), "unfed counter"),
        tile.get("fed_today"),
        tile.get("cared_today"),
        tile.get("fertilizer_available"),
        _nonnegative_int(
            tile.get("pending_care_bonus", 0),
            "pending care bonus",
        ),
    )


def _release_day(tile):
    value = tile.get("max_lifespan_step", -1)
    if type(value) is not int:
        raise LiveSnapshotError("plant lifespan must be an integer")
    if value < 0:
        return None
    return min(LAST_DAY, max(0, value // 24))


def _board_state(rows, unlocked, prices):
    plants = []
    animals = []
    structures = {"COOP": 0, "PASTURE": 0}
    cells = []
    usable = 0
    for y, row in enumerate(rows):
        for x, tile in enumerate(row):
            position = (y, x)
            kind = _tile_kind(tile)
            quadrant = _quadrant(x, y)
            unlock_day = 0 if quadrant in unlocked else LAST_DAY
            if kind != "LOCKED":
                usable += 1
            if kind == "PLANT":
                plant = _plant(tile, position)
                plants.append(plant)
                value = plant.yield_units * prices[plant.crop]
                cells.append(
                    SpaceCell(
                        position,
                        unlock_day,
                        "PLANT",
                        plant.crop,
                        float(value),
                        _release_day(tile),
                    )
                )
            elif kind in ("COOP", "PASTURE") and tile.get("animal") is not None:
                animals.append(_animal(tile, position))
                cells.append(SpaceCell(position, unlock_day, "ANIMAL"))
            elif kind in ("COOP", "PASTURE"):
                structures[kind] += 1
                cells.append(SpaceCell(position, unlock_day, kind))
            elif kind == "LOCKED":
                cells.append(SpaceCell(position, unlock_day, "EMPTY"))
            else:
                cells.append(SpaceCell(position, unlock_day, kind))
    return tuple(plants), tuple(animals), structures, tuple(cells), usable


def _market(values):
    market = values.get("market")
    if not isinstance(market, Mapping):
        raise LiveSnapshotError("market must be a mapping")
    prices = market.get("prices")
    inventory = market.get("inventory")
    if not isinstance(prices, Mapping) or not isinstance(inventory, Mapping):
        raise LiveSnapshotError("market prices and inventory must be mappings")
    if set(prices) != set(PRODUCTS) or set(inventory) != set(PRODUCTS):
        raise LiveSnapshotError("market must cover all products")
    price_values = {}
    inventory_values = {}
    for product in PRODUCTS:
        price = prices[product]
        if type(price) not in (int, float) or isinstance(price, bool):
            raise LiveSnapshotError("market prices must be numeric")
        if not math.isfinite(price) or price <= 0:
            raise LiveSnapshotError("market prices must be finite and positive")
        price_values[product] = float(price)
        inventory_values[product] = _nonnegative_int(
            inventory[product],
            "market inventory",
        )
    return price_values, inventory_values


def _shops(values):
    town = values.get("town")
    if not isinstance(town, Mapping):
        raise LiveSnapshotError("town must be a mapping")
    shops = town.get("unlocked_shops")
    if type(shops) is not list:
        raise LiveSnapshotError("unlocked shops must be a list")
    if any(shop not in SHOP_DEMAND for shop in shops):
        raise LiveSnapshotError("town contains an unknown shop")
    if len(shops) > 8:
        raise LiveSnapshotError("town exceeds the shop limit")
    return tuple(shops)


def _daily_capacity(day, hour, unit_count):
    horizon = LAST_DAY - day + 1
    remaining = 24 - hour
    if day == LAST_DAY:
        remaining = TERMINAL_STEP - (day * 24 + hour) + 1
    actions = (remaining * unit_count,) + tuple(
        23 if future_day == LAST_DAY else 24
        for future_day in range(day + 1, LAST_DAY + 1)
    )
    if len(actions) != horizon:
        raise LiveSnapshotError("daily action horizon is inconsistent")
    route = tuple(min(ROUTE_ACTION_RESERVE, value) for value in actions)
    return actions, route


def _optimizer(
    step,
    money,
    reserve,
    unlocked_count,
    hand_count,
    hires_today,
    tile_work,
):
    steps = tuple(range(step, TERMINAL_STEP + 1))
    existing = tuple(tile_work if value % 24 < 12 else 0 for value in steps)
    return OptimizerInput(
        step,
        TERMINAL_STEP,
        money,
        reserve,
        unlocked_count,
        hand_count,
        hires_today,
        MAX_HANDS,
        1,
        (0.0,) * len(steps),
        (MARKET_ORDER_LIMIT,) * len(steps),
        existing,
        tuple(4 if value % 24 < 12 else 0 for value in steps),
        tuple(1 + hand_count for _ in steps),
        (MAX_HANDS + 1,) * len(steps),
        tuple(1.0 if value < TERMINAL_STEP else 0.0 for value in steps),
        "registered-executor-capacity-v1",
    )


def _build_snapshot(values, registered_seed):
    step, day, hour = _clock(values)
    _player, farm, private = _player_state(values)
    money = farm.get("money")
    if type(money) not in (int, float) or isinstance(money, bool):
        raise LiveSnapshotError("farm money must be numeric")
    money = float(money)
    if not math.isfinite(money) or money < 0:
        raise LiveSnapshotError("farm money must be finite and nonnegative")
    reserve = min(400.0, money * 0.2)
    unlocked = _unlocked_quadrants(farm)
    rows = _tiles(farm)
    prices, market_inventory = _market(values)
    plants, animals, structures, cells, usable = _board_state(
        rows,
        unlocked,
        prices,
    )
    totals, carried = _combined_inventory(private)
    seeds = _inventory(private.get("seeds"), CROPS, "seeds")
    hands = farm.get("hands")
    if type(hands) is not list:
        raise LiveSnapshotError("farm hands must be a list")
    farmer = farm.get("farmer")
    if type(farmer) is not list or len(farmer) != 2:
        raise LiveSnapshotError("farmer position must be a pair")
    if any(type(value) is not int or not 0 <= value < BOARD_SIZE for value in farmer):
        raise LiveSnapshotError("farmer position must be on board")
    for hand in hands:
        if type(hand) is not list or len(hand) != 2:
            raise LiveSnapshotError("hand position must be a pair")
        if any(type(value) is not int or not 0 <= value < BOARD_SIZE for value in hand):
            raise LiveSnapshotError("hand position must be on board")
    inventories = private.get("inventories")
    if len(inventories) != len(hands) + 1:
        raise LiveSnapshotError("unit inventories must match farm units")
    hires_today = _nonnegative_int(farm.get("hires_today", 0), "hires today")
    if hires_today != len(hands):
        raise LiveSnapshotError("hires today must match current hands")
    horizon = LAST_DAY - day + 1
    actions, route = _daily_capacity(day, hour, len(hands) + 1)
    field_capacity = (usable,) * horizon
    storage = (SHED_CAPACITY + carried,) + (SHED_CAPACITY,) * (horizon - 1)
    orders = (MARKET_ORDER_LIMIT,) * horizon
    market_rows = tuple(
        tuple(market_inventory[item] for item in PRODUCTS)
        for _ in range(horizon)
    )
    crop_rows = tuple(
        tuple(market_inventory[item] for item in CROPS)
        for _ in range(horizon)
    )
    animal_rows = tuple(
        tuple(market_inventory[item] for item in GOODS)
        for _ in range(horizon)
    )
    crop_goods = tuple(totals[item] for item in CROPS)
    animal_goods = tuple(0 if item in ("WHEAT", "FERTILIZER") else totals[item] for item in GOODS)
    crop_occupancy = sum(crop_goods) + totals["FERTILIZER"]
    crop = OracleInput(
        step,
        TERMINAL_STEP,
        money,
        reserve,
        seeds,
        crop_goods,
        plants,
        field_capacity,
        actions,
        storage,
        (0,) * horizon,
        (0.0,) * horizon,
        totals["FERTILIZER"],
        (0,) * horizon,
        (prices["FERTILIZER"],) * horizon,
        orders,
        crop_rows,
        (prices["WHEAT"],) * horizon,
        day,
        4,
        SHED_CAPACITY,
        "no-future-opponent-orders-v1",
    )
    animal = AnimalOracleInput(
        step,
        TERMINAL_STEP,
        money,
        reserve,
        animal_goods,
        tuple(totals[item] for item in ANIMALS),
        animals,
        tuple(structures[item] for item in ("COOP", "PASTURE")),
        field_capacity,
        actions,
        storage,
        (crop_occupancy,) * horizon,
        orders,
        (0.0,) * horizon,
        animal_rows,
        120,
        4,
        2,
        4,
        40,
        4,
        ANIMALS,
        (),
        "no-future-opponent-orders-v1",
    )
    shared = SharedCapacity(field_capacity, actions, storage, orders, route)
    tile_work = len(plants) + len(animals) + sum(structures.values())
    investment = _optimizer(
        step,
        money,
        reserve,
        len(unlocked),
        len(hands),
        hires_today,
        tile_work,
    )
    portfolios = tuple(("SHEEP",) * count for count in range(4, -1, -1))
    return WholeFarmSnapshot(
        registered_seed,
        crop,
        animal,
        investment,
        cells,
        shared,
        portfolios,
    ), _shops(values), market_rows


class LiveSnapshotAdapter:
    def __init__(self, registered_seed):
        if type(registered_seed) is not int:
            raise TypeError("registered seed must be an integer")
        self._registered_seed = registered_seed
        self._last_identity = None
        self._last_snapshot = None
        self._last_day = None
        self._fingerprints = None
        self._weed_positions = None
        self._unit_count = None

    @property
    def last_snapshot(self):
        return self._last_snapshot

    def reset(self):
        self._last_identity = None
        self._last_snapshot = None
        self._last_day = None
        self._fingerprints = None
        self._weed_positions = None
        self._unit_count = None

    def observe(self, value):
        values = _plain_observation(value)
        snapshot, shops, market_rows = _build_snapshot(
            values,
            self._registered_seed,
        )
        step = snapshot.source_step
        raw_economy = canonical_sha256(
            "live-economy",
            (
                snapshot.crop.cash,
                snapshot.crop.seeds,
                snapshot.crop.goods,
                snapshot.animal.goods,
                snapshot.animal.shed_animals,
                snapshot.crop.fertilizer_stock,
                market_rows[0],
                shops,
            ),
        )
        raw_topology = canonical_sha256(
            "live-topology",
            tuple(
                (
                    cell.position,
                    cell.unlock_day,
                    cell.kind,
                    cell.crop,
                    cell.release_day,
                )
                for cell in snapshot.cells
            ),
        )
        raw_route = canonical_sha256(
            "live-route",
            (
                values["farms"][values["player"]]["farmer"],
                values["farms"][values["player"]]["hands"],
                values["private"]["inventories"],
            ),
        )
        weeds = frozenset(
            cell.position for cell in snapshot.cells if cell.kind == "WEED"
        )
        unit_count = len(values["farms"][values["player"]]["hands"]) + 1
        raw = {
            "economy": raw_economy,
            "topology": raw_topology,
            "route": raw_route,
        }
        previous = self._fingerprints
        if previous is None or self._last_day != snapshot.current_day:
            fingerprints = raw
        else:
            fingerprints = dict(previous)
            if weeds != self._weed_positions:
                fingerprints["topology"] = raw_topology
            if unit_count != self._unit_count:
                fingerprints["route"] = raw_route
        deltas = ()
        if previous is not None:
            deltas = tuple(
                ObservedDelta(
                    domain,
                    ("live", domain),
                    previous[domain],
                    fingerprints[domain],
                )
                for domain in ("economy", "topology", "route")
                if previous[domain] != fingerprints[domain]
            )
        progress = canonical_sha256(
            "live-progress",
            (
                step,
                tuple(
                    (
                        plant.position,
                        plant.yield_units,
                        plant.watered_today,
                        plant.consecutive_unwatered,
                    )
                    for plant in snapshot.crop.existing_plants
                ),
                tuple(
                    (
                        animal.position,
                        animal.yield_units,
                        animal.fed_today,
                        animal.cared_today,
                    )
                    for animal in snapshot.animal.existing_animals
                ),
            ),
        )
        observation = RollingObservation(
            step,
            shops,
            fingerprints["economy"],
            fingerprints["topology"],
            fingerprints["route"],
            progress,
            ExecutionSignal(deltas),
        )
        self._last_identity = observation.identity
        self._last_snapshot = snapshot
        self._last_day = snapshot.current_day
        self._fingerprints = fingerprints
        self._weed_positions = weeds
        self._unit_count = unit_count
        return observation

    def snapshot(self, observation):
        if type(observation) is not RollingObservation:
            raise TypeError("observation has wrong type")
        if observation.identity != self._last_identity or self._last_snapshot is None:
            raise LiveSnapshotError("rolling observation has no matching snapshot")
        if observation.source_step != self._last_snapshot.source_step:
            raise LiveSnapshotError("rolling observation step differs from snapshot")
        return self._last_snapshot
