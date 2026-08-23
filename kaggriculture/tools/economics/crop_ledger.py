from dataclasses import dataclass, replace

from .inventory_ledger import (
    InventoryState,
    UnitInventory,
    apply_unit_transfer,
)
from .market_ledger import (
    CROPS,
    MarketState,
    MarketTransition,
    PlayerAccount,
    apply_market_phase,
)


BOARD_SIZE = 10
TURNS_PER_DAY = 24
LAND_ORDER = ("NE", "SW", "SE")


@dataclass(frozen=True, slots=True)
class CropSpec:
    seed: int
    first_yield_day: int
    max_yield_day: int
    interval: int
    max_yield: int
    ongoing: bool


CROP_SPECS = {
    "WHEAT": CropSpec(10, 2, 4, 0, 6, False),
    "CARROT": CropSpec(20, 2, 3, 0, 4, False),
    "TOMATO": CropSpec(50, 8, 8, 1, 4, True),
    "STRAWBERRY": CropSpec(100, 10, 10, 2, 4, True),
    "MELON": CropSpec(80, 10, 12, 0, 6, False),
}


@dataclass(frozen=True, slots=True)
class PlantState:
    crop: str
    planted_day: int
    watered_today: bool
    consecutive_unwatered: int
    yield_units: int
    max_lifespan_step: int
    fertilized_until_day: int

    def __post_init__(self):
        if type(self.crop) is not str or self.crop not in CROP_SPECS:
            raise ValueError("unknown crop")
        integer_values = (
            self.planted_day,
            self.consecutive_unwatered,
            self.yield_units,
            self.max_lifespan_step,
            self.fertilized_until_day,
        )
        if any(type(value) is not int for value in integer_values):
            raise TypeError("plant counters must be integers")
        if type(self.watered_today) is not bool:
            raise TypeError("watered_today must be a boolean")
        if self.planted_day < 0 or self.consecutive_unwatered < 0:
            raise ValueError("plant counters must be nonnegative")
        if self.yield_units < 0:
            raise ValueError("plant yield must be nonnegative")

    @classmethod
    def create(cls, crop, planted_day):
        _validate_crop(crop)
        _validate_day(planted_day)
        spec = CROP_SPECS[crop]
        lifespan = -1
        if not spec.ongoing:
            lifespan = (planted_day + spec.max_yield_day + 1) * TURNS_PER_DAY
        return cls(
            crop,
            planted_day,
            False,
            1,
            0 if spec.ongoing else 1,
            lifespan,
            -1,
        )


@dataclass(frozen=True, slots=True)
class CropBoard:
    cells: tuple[object, ...]

    def __post_init__(self):
        if type(self.cells) is not tuple or len(self.cells) != BOARD_SIZE**2:
            raise TypeError("crop board must contain 100 cells")
        allowed = (None, "LOCKED", "WEED", "STRUCTURE", "ANIMAL")
        for cell in self.cells:
            if cell not in allowed and not isinstance(cell, PlantState):
                raise TypeError("invalid crop board cell")

    @classmethod
    def initial(cls, unlocked_quadrants=1):
        if type(unlocked_quadrants) is not int:
            raise TypeError("unlocked quadrants must be an integer")
        if unlocked_quadrants < 1 or unlocked_quadrants > 4:
            raise ValueError("unlocked quadrants must be in 1..4")
        unlocked = {"NW", *LAND_ORDER[: unlocked_quadrants - 1]}
        return cls(
            tuple(
                None if _quadrant(x, y) in unlocked else "LOCKED"
                for y in range(BOARD_SIZE)
                for x in range(BOARD_SIZE)
            )
        )

    def get(self, position):
        x, y = _validate_position(position)
        return self.cells[y * BOARD_SIZE + x]

    def set(self, position, value):
        x, y = _validate_position(position)
        result = list(self.cells)
        result[y * BOARD_SIZE + x] = value
        return CropBoard(tuple(result))

    def unlock(self, quadrant):
        if quadrant not in LAND_ORDER:
            raise ValueError("unknown quadrant")
        result = list(self.cells)
        for y in range(BOARD_SIZE):
            for x in range(BOARD_SIZE):
                index = y * BOARD_SIZE + x
                if _quadrant(x, y) == quadrant and result[index] == "LOCKED":
                    result[index] = None
        return CropBoard(tuple(result))


@dataclass(frozen=True, slots=True)
class CropState:
    inventory: InventoryState
    boards: tuple[CropBoard, CropBoard]

    def __post_init__(self):
        if not isinstance(self.inventory, InventoryState):
            raise TypeError("inventory must be an InventoryState")
        if type(self.boards) is not tuple or len(self.boards) != 2:
            raise TypeError("boards must be a two-item tuple")
        if any(not isinstance(board, CropBoard) for board in self.boards):
            raise TypeError("invalid crop board")


@dataclass(frozen=True, slots=True)
class CropEvent:
    player: int
    unit_index: int | None
    position: tuple[int, int] | None
    operation: str
    crop: str | None
    quantity_before: int | None
    quantity_after: int | None
    accepted: bool
    failure_reason: str | None


@dataclass(frozen=True, slots=True)
class CropTransition:
    after_units: CropState
    market_transition: MarketTransition
    after_town: CropState
    after_decay: CropState
    after_refresh: CropState | None
    events: tuple[CropEvent, ...]


def _validate_crop(crop):
    if type(crop) is not str or crop not in CROP_SPECS:
        raise ValueError("unknown crop")


def _validate_day(day):
    if type(day) is not int:
        raise TypeError("day must be an integer")
    if day < 0:
        raise ValueError("day must be nonnegative")


def _validate_position(position):
    if type(position) is not tuple or len(position) != 2:
        raise TypeError("position must be a pair")
    x, y = position
    if type(x) is not int or type(y) is not int:
        raise TypeError("position values must be integers")
    if x < 0 or y < 0 or x >= BOARD_SIZE or y >= BOARD_SIZE:
        raise ValueError("position must be on board")
    return x, y


def _quadrant(x, y):
    half = BOARD_SIZE // 2
    return ("N" if y < half else "S") + ("W" if x < half else "E")


def is_shed_adjacent(position):
    x, y = _validate_position(position)
    return (x, y) in ((4, 4), (5, 4), (4, 5), (5, 5))


def first_harvest_day(crop, planted_day):
    _validate_crop(crop)
    _validate_day(planted_day)
    return planted_day + CROP_SPECS[crop].first_yield_day


def scheduled_production_days(crop, planted_day, terminal_day=29):
    _validate_crop(crop)
    _validate_day(planted_day)
    _validate_day(terminal_day)
    spec = CROP_SPECS[crop]
    if not spec.ongoing:
        return ()
    first = first_harvest_day(crop, planted_day)
    return tuple(
        first + index * spec.interval
        for index in range(spec.max_yield)
        if first + index * spec.interval <= terminal_day
    )


def latest_maturing_plant_day(crop, terminal_day=29):
    _validate_crop(crop)
    _validate_day(terminal_day)
    result = terminal_day - CROP_SPECS[crop].first_yield_day
    return None if result < 0 else result


def _replace_market_players(state, players):
    return MarketState(
        state.source_step,
        state.inventory,
        players,
        state.shops,
        state.params,
        state.config,
    )


def _replace_account(account, shed=None, seeds=None):
    return PlayerAccount(
        account.money,
        account.shed if shed is None else shed,
        account.seeds if seeds is None else seeds,
        account.hires_today,
        account.unlocked_quadrants,
        account.hands,
    )


def _replace_crop_player(state, player, account, shed, seeds, units, board):
    accounts = list(state.inventory.market.players)
    accounts[player] = _replace_account(account, shed, seeds)
    market = _replace_market_players(state.inventory.market, tuple(accounts))
    all_units = list(state.inventory.units)
    all_units[player] = tuple(units)
    inventory = InventoryState(market, tuple(all_units))
    boards = list(state.boards)
    boards[player] = board
    return CropState(inventory, tuple(boards))


def _crop_event(
    player,
    unit_index,
    position,
    operation,
    crop,
    before,
    after,
    accepted,
    reason,
):
    return CropEvent(
        player,
        unit_index,
        position,
        operation,
        crop,
        before,
        after,
        accepted,
        reason,
    )


def _blocked_crops(farmer_action, raw_hands, seeds):
    hand_actions = raw_hands if isinstance(raw_hands, list) else []
    demand = {}
    for action in [farmer_action, *hand_actions]:
        if isinstance(action, list) and len(action) >= 2 and action[0] == "PLANT":
            crop = action[1]
            demand[crop] = demand.get(crop, 0) + 1
    seed_mapping = dict(zip(CROPS, seeds))
    return {crop for crop, quantity in demand.items() if quantity > seed_mapping.get(crop, 0)}


def _allowed_action(action, blocked):
    if (
        isinstance(action, list)
        and len(action) >= 2
        and action[0] == "PLANT"
        and action[1] in blocked
    ):
        return ["PASS"]
    return action


def _apply_crop_action(
    board,
    inventory,
    seeds,
    action,
    position,
    day,
    player,
    unit_index,
    trace,
):
    if not isinstance(action, list) or not action:
        return False, board, inventory, seeds, None
    operation = action[0]
    hash(operation)
    if operation not in ("PLANT", "WATER", "HARVEST", "FERTILIZE", "DIG"):
        return False, board, inventory, seeds, None
    tile = board.get(position)
    if tile == "LOCKED":
        event = None
        if trace:
            event = _crop_event(
                player,
                unit_index,
                position,
                operation,
                None,
                None,
                None,
                False,
                "locked",
            )
        return True, board, inventory, seeds, event
    crop = tile.crop if isinstance(tile, PlantState) else None
    before = tile.yield_units if isinstance(tile, PlantState) else None
    accepted = False
    reason = None
    if operation == "PLANT":
        if len(action) < 2:
            reason = "missing_crop"
        else:
            requested_crop = action[1]
            if requested_crop not in CROP_SPECS:
                reason = "unknown_crop"
            elif tile is not None:
                reason = "occupied"
            else:
                seed_index = CROPS.index(requested_crop)
                if seeds[seed_index] <= 0:
                    reason = "unavailable_seed"
                else:
                    updated = list(seeds)
                    updated[seed_index] -= 1
                    seeds = tuple(updated)
                    board = board.set(position, PlantState.create(requested_crop, day))
                    crop = requested_crop
                    before = 0
                    accepted = True
    elif operation == "WATER":
        if not isinstance(tile, PlantState):
            reason = "not_plant"
        elif tile.watered_today:
            reason = "already_watered"
        else:
            spec = CROP_SPECS[tile.crop]
            updated_yield = tile.yield_units
            if not spec.ongoing:
                age = day - tile.planted_day
                window_start = (spec.max_yield_day + 1) // 2
                if window_start <= age <= spec.max_yield_day:
                    bonus = 2 if tile.fertilized_until_day >= day else 1
                    updated_yield = min(spec.max_yield, updated_yield + bonus)
            board = board.set(
                position,
                replace(tile, watered_today=True, yield_units=updated_yield),
            )
            accepted = True
    elif operation == "HARVEST":
        if tile == "ANIMAL":
            return False, board, inventory, seeds, None
        if not isinstance(tile, PlantState):
            reason = "not_plant"
        elif tile.yield_units <= 0:
            reason = "no_yield"
        elif day - tile.planted_day < CROP_SPECS[tile.crop].first_yield_day:
            reason = "immature"
        else:
            inventory = inventory.add(tile.crop, tile.yield_units)
            if CROP_SPECS[tile.crop].ongoing:
                board = board.set(position, replace(tile, yield_units=0))
            else:
                board = board.set(position, None)
            accepted = True
    elif operation == "FERTILIZE":
        if not isinstance(tile, PlantState):
            reason = "not_plant"
        else:
            inventory_after, quantity = inventory.take("FERTILIZER", 1)
            if quantity == 0:
                reason = "unavailable_fertilizer"
            else:
                inventory = inventory_after
                board = board.set(
                    position,
                    replace(
                        tile,
                        fertilized_until_day=max(tile.fertilized_until_day, day + 2),
                    ),
                )
                accepted = True
    elif isinstance(tile, PlantState) or tile == "WEED":
        board = board.set(position, None)
        accepted = True
    elif tile in ("STRUCTURE", "ANIMAL"):
        return False, board, inventory, seeds, None
    else:
        reason = "empty"
    current = board.get(position)
    after = current.yield_units if isinstance(current, PlantState) else None
    event = None
    if trace:
        event = _crop_event(
            player,
            unit_index,
            position,
            operation,
            crop,
            before,
            after,
            accepted,
            reason,
        )
    return True, board, inventory, seeds, event


def apply_crop_player(
    state,
    player,
    player_actions,
    unit_positions,
    animal_place_priority,
    trace=False,
):
    if not isinstance(state, CropState):
        raise TypeError("state must be a CropState")
    if type(player) is not int or player not in (0, 1):
        raise ValueError("player must be 0 or 1")
    if type(player_actions) is not tuple or len(player_actions) != 2:
        raise TypeError("player actions must contain farmer and hands")
    units = list(state.inventory.units[player])
    if type(unit_positions) is not tuple or len(unit_positions) != len(units):
        raise TypeError("unit positions must match units")
    positions = tuple(_validate_position(position) for position in unit_positions)
    if (
        type(animal_place_priority) is not tuple
        or len(animal_place_priority) != len(units)
        or any(type(value) is not bool for value in animal_place_priority)
    ):
        raise TypeError("animal placement facts must match units")
    farmer_action, raw_hands = player_actions
    account = state.inventory.market.players[player]
    shed = account.shed
    seeds = account.seeds
    board = state.boards[player]
    events = []
    day = state.inventory.market.source_step // TURNS_PER_DAY
    try:
        blocked = _blocked_crops(farmer_action, raw_hands, seeds)
        hand_actions = raw_hands if isinstance(raw_hands, list) else []
        actions = [(0, farmer_action)]
        actions.extend(
            (index + 1, action)
            for index, action in enumerate(hand_actions[: len(units) - 1])
        )
        for unit_index, raw_action in actions:
            action = _allowed_action(raw_action, blocked)
            if animal_place_priority[unit_index]:
                continue
            handled, shed, inventory, _ = apply_unit_transfer(
                shed,
                units[unit_index],
                action,
                is_shed_adjacent(positions[unit_index]),
                state.inventory.market.config.shed_capacity,
                player,
                unit_index,
                False,
            )
            if handled:
                units[unit_index] = inventory
                continue
            handled, board, inventory, seeds, event = _apply_crop_action(
                board,
                units[unit_index],
                seeds,
                action,
                positions[unit_index],
                day,
                player,
                unit_index,
                trace,
            )
            if handled:
                units[unit_index] = inventory
            if event is not None:
                events.append(event)
    except Exception as error:
        error.partial_state = _replace_crop_player(
            state,
            player,
            account,
            shed,
            seeds,
            units,
            board,
        )
        raise
    return (
        _replace_crop_player(state, player, account, shed, seeds, units, board),
        tuple(events),
    )


def _after_market(state, market_transition):
    units = [list(player_units) for player_units in state.inventory.units]
    boards = list(state.boards)
    for player in range(2):
        before = state.inventory.market.players[player]
        after = market_transition.after_town.players[player]
        units[player].extend(UnitInventory() for _ in range(after.hands - before.hands))
        for count in range(before.unlocked_quadrants, after.unlocked_quadrants):
            boards[player] = boards[player].unlock(LAND_ORDER[count - 1])
    inventory = InventoryState(
        market_transition.after_town,
        (tuple(units[0]), tuple(units[1])),
    )
    return CropState(inventory, (boards[0], boards[1]))


def apply_crop_decay(state, trace=False):
    if not isinstance(state, CropState):
        raise TypeError("state must be a CropState")
    if type(trace) is not bool:
        raise TypeError("trace must be a boolean")
    step = state.inventory.market.source_step
    boards = []
    events = []
    for player, board in enumerate(state.boards):
        updated = board
        for index, cell in enumerate(board.cells):
            if not isinstance(cell, PlantState):
                continue
            if cell.max_lifespan_step < 0 or step < cell.max_lifespan_step:
                continue
            if (step - cell.max_lifespan_step) % 2 != 0:
                continue
            position = (index % BOARD_SIZE, index // BOARD_SIZE)
            next_yield = cell.yield_units - 1
            replacement = "WEED" if next_yield <= 0 else replace(cell, yield_units=next_yield)
            updated = updated.set(position, replacement)
            if trace:
                events.append(
                    _crop_event(
                        player,
                        None,
                        position,
                        "DECAY",
                        cell.crop,
                        cell.yield_units,
                        max(0, next_yield),
                        True,
                        None,
                    )
                )
        boards.append(updated)
    return CropState(state.inventory, (boards[0], boards[1])), tuple(events)


def apply_crop_refresh(state, trace=False):
    if not isinstance(state, CropState):
        raise TypeError("state must be a CropState")
    if type(trace) is not bool:
        raise TypeError("trace must be a boolean")
    current_day = state.inventory.market.source_step // TURNS_PER_DAY
    next_day = current_day + 1
    boards = []
    events = []
    for player, board in enumerate(state.boards):
        updated = board
        for index, cell in enumerate(board.cells):
            if not isinstance(cell, PlantState):
                continue
            position = (index % BOARD_SIZE, index // BOARD_SIZE)
            unwatered = 0 if cell.watered_today else cell.consecutive_unwatered + 1
            replacement = replace(
                cell,
                watered_today=False,
                consecutive_unwatered=unwatered,
            )
            if unwatered >= 2:
                replacement = "WEED"
            else:
                spec = CROP_SPECS[cell.crop]
                if spec.ongoing:
                    age = next_day - cell.planted_day - spec.first_yield_day
                    if age >= 0 and age % spec.interval == 0:
                        production_count = age // spec.interval + 1
                        if production_count <= spec.max_yield:
                            fertilized = (
                                cell.watered_today
                                and cell.fertilized_until_day >= current_day
                            )
                            produced = 2 if fertilized else 1
                            replacement = replace(
                                replacement,
                                yield_units=min(
                                    spec.max_yield,
                                    replacement.yield_units + produced,
                                ),
                            )
                            if production_count == spec.max_yield:
                                replacement = replace(
                                    replacement,
                                    max_lifespan_step=(next_day + 1) * TURNS_PER_DAY,
                                )
            updated = updated.set(position, replacement)
            if trace:
                after = replacement.yield_units if isinstance(replacement, PlantState) else 0
                events.append(
                    _crop_event(
                        player,
                        None,
                        position,
                        "REFRESH",
                        cell.crop,
                        cell.yield_units,
                        after,
                        True,
                        None,
                    )
                )
        boards.append(updated)
    return CropState(state.inventory, (boards[0], boards[1])), tuple(events)


def apply_crop_phase(
    state,
    unit_actions,
    unit_positions,
    animal_place_priority,
    market_queues,
    trace=False,
):
    if not isinstance(state, CropState):
        raise TypeError("state must be a CropState")
    inputs = (unit_actions, unit_positions, animal_place_priority)
    if any(type(value) is not tuple or len(value) != 2 for value in inputs):
        raise TypeError("phase inputs must contain two players")
    if type(trace) is not bool:
        raise TypeError("trace must be a boolean")
    current = state
    events = []
    for player in range(2):
        current, player_events = apply_crop_player(
            current,
            player,
            unit_actions[player],
            unit_positions[player],
            animal_place_priority[player],
            trace,
        )
        events.extend(player_events)
    after_units = current
    market_transition = apply_market_phase(
        after_units.inventory.market,
        market_queues,
        trace,
    )
    after_town = _after_market(after_units, market_transition)
    after_decay, decay_events = apply_crop_decay(after_town, trace)
    events.extend(decay_events)
    after_refresh = None
    if (state.inventory.market.source_step + 1) % TURNS_PER_DAY == 0:
        after_refresh, refresh_events = apply_crop_refresh(after_decay, trace)
        events.extend(refresh_events)
    return CropTransition(
        after_units,
        market_transition,
        after_town,
        after_decay,
        after_refresh,
        tuple(events),
    )
