import math
import random
from dataclasses import dataclass, replace

from .crop_ledger import (
    BOARD_SIZE,
    TURNS_PER_DAY,
    CropState,
    _after_market,
    _allowed_action,
    _apply_crop_action,
    _blocked_crops,
    apply_crop_decay,
    apply_crop_refresh,
    is_shed_adjacent,
)
from .inventory_ledger import (
    InventoryState,
    apply_inventory_day_end,
    apply_unit_transfer,
)
from .market_ledger import (
    SHOP_DEMAND,
    MarketTransition,
    apply_market_phase,
)


MOVES = {
    "NORTH": (0, -1),
    "SOUTH": (0, 1),
    "EAST": (1, 0),
    "WEST": (-1, 0),
}
SHED_ACCESS = ((4, 4), (5, 4), (4, 5), (5, 5))
MAX_SHOPS = 8


@dataclass(frozen=True, slots=True)
class AnimalSpec:
    cost: int
    structure: str
    first_yield_day: int
    interval: int
    max_held: int
    product: str


ANIMAL_SPECS = {
    "GOOSE": AnimalSpec(300, "COOP", 4, 1, 4, "EGG"),
    "COW": AnimalSpec(400, "PASTURE", 8, 2, 6, "MILK"),
    "SHEEP": AnimalSpec(500, "PASTURE", 6, 3, 6, "WOOL"),
}


@dataclass(frozen=True, slots=True)
class AnimalTile:
    animal: str
    placed_day: int
    yield_units: int
    consecutive_unfed: int
    fed_today: bool
    cared_today: bool
    fertilizer_available: bool
    pending_care_bonus: int

    def __post_init__(self):
        if type(self.animal) is not str or self.animal not in ANIMAL_SPECS:
            raise ValueError("unknown animal")
        counters = (
            self.placed_day,
            self.yield_units,
            self.consecutive_unfed,
            self.pending_care_bonus,
        )
        if any(type(value) is not int for value in counters):
            raise TypeError("animal counters must be integers")
        if any(value < 0 for value in counters):
            raise ValueError("animal counters must be nonnegative")
        flags = (self.fed_today, self.cared_today, self.fertilizer_available)
        if any(type(value) is not bool for value in flags):
            raise TypeError("animal flags must be booleans")

    @classmethod
    def create(cls, animal, placed_day):
        if type(animal) is not str or animal not in ANIMAL_SPECS:
            raise ValueError("unknown animal")
        _validate_day(placed_day)
        return cls(animal, placed_day, 0, 0, False, False, False, 0)


@dataclass(frozen=True, slots=True)
class AnimalBoard:
    cells: tuple[object, ...]

    def __post_init__(self):
        if type(self.cells) is not tuple or len(self.cells) != BOARD_SIZE**2:
            raise TypeError("animal board must contain 100 cells")
        for cell in self.cells:
            if cell not in (None, "COOP", "PASTURE") and not isinstance(
                cell,
                AnimalTile,
            ):
                raise TypeError("invalid animal board cell")

    @classmethod
    def empty(cls):
        return cls((None,) * (BOARD_SIZE**2))

    def get(self, position):
        x, y = _validate_position(position)
        return self.cells[y * BOARD_SIZE + x]

    def set(self, position, value):
        x, y = _validate_position(position)
        result = list(self.cells)
        result[y * BOARD_SIZE + x] = value
        return AnimalBoard(tuple(result))


@dataclass(frozen=True, slots=True)
class AnimalConfig:
    episode_seed: int = 0
    weed_chance: float = 0.005
    shop_unlock_interval: int = 3

    def __post_init__(self):
        if type(self.episode_seed) is not int:
            raise TypeError("episode seed must be an integer")
        if type(self.weed_chance) not in (int, float) or isinstance(
            self.weed_chance,
            bool,
        ):
            raise TypeError("weed chance must be numeric")
        if not math.isfinite(self.weed_chance) or not 0 <= self.weed_chance <= 1:
            raise ValueError("weed chance must be in 0..1")
        if type(self.shop_unlock_interval) is not int:
            raise TypeError("shop interval must be an integer")
        if self.shop_unlock_interval < 1:
            raise ValueError("shop interval must be positive")


@dataclass(frozen=True, slots=True)
class AnimalState:
    crop: CropState
    positions: tuple[tuple[tuple[int, int], ...], tuple[tuple[int, int], ...]]
    animal_boards: tuple[AnimalBoard, AnimalBoard]

    def __post_init__(self):
        if not isinstance(self.crop, CropState):
            raise TypeError("crop state must be a CropState")
        if type(self.positions) is not tuple or len(self.positions) != 2:
            raise TypeError("positions must contain two players")
        if type(self.animal_boards) is not tuple or len(self.animal_boards) != 2:
            raise TypeError("animal boards must contain two players")
        for player in range(2):
            positions = self.positions[player]
            if type(positions) is not tuple:
                raise TypeError("player positions must be a tuple")
            if len(positions) != len(self.crop.inventory.units[player]):
                raise ValueError("position count must match units")
            for position in positions:
                _validate_position(position)
            board = self.animal_boards[player]
            if not isinstance(board, AnimalBoard):
                raise TypeError("invalid animal board")
            for crop_cell, animal_cell in zip(
                self.crop.boards[player].cells,
                board.cells,
            ):
                if animal_cell in ("COOP", "PASTURE"):
                    if crop_cell != "STRUCTURE":
                        raise ValueError("structure overlay differs from crop board")
                elif isinstance(animal_cell, AnimalTile):
                    if crop_cell != "ANIMAL":
                        raise ValueError("animal overlay differs from crop board")
                elif crop_cell in ("STRUCTURE", "ANIMAL"):
                    raise ValueError("crop marker lacks animal overlay")


@dataclass(frozen=True, slots=True)
class AnimalEvent:
    player: int
    unit_index: int | None
    position: tuple[int, int] | None
    operation: str
    animal: str | None
    accepted: bool
    failure_reason: str | None


@dataclass(frozen=True, slots=True)
class AnimalTransition:
    after_units: AnimalState
    market_transition: MarketTransition
    after_town: AnimalState
    after_decay: AnimalState
    after_crop_refresh: AnimalState | None
    after_animal_refresh: AnimalState | None
    after_weeds: AnimalState | None
    after_end: AnimalState | None
    events: tuple[object, ...]


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


def _event(player, unit_index, position, operation, animal, accepted, reason):
    return AnimalEvent(
        player,
        unit_index,
        position,
        operation,
        animal,
        accepted,
        reason,
    )


def _replace_state(
    state,
    player,
    account,
    shed,
    seeds,
    units,
    crop_board,
    animal_board,
    positions,
):
    accounts = list(state.crop.inventory.market.players)
    accounts[player] = replace(account, shed=shed, seeds=seeds)
    market = replace(state.crop.inventory.market, players=tuple(accounts))
    all_units = list(state.crop.inventory.units)
    all_units[player] = tuple(units)
    inventory = InventoryState(market, tuple(all_units))
    crop_boards = list(state.crop.boards)
    crop_boards[player] = crop_board
    crop = CropState(inventory, tuple(crop_boards))
    animal_boards = list(state.animal_boards)
    animal_boards[player] = animal_board
    all_positions = list(state.positions)
    all_positions[player] = tuple(positions)
    return AnimalState(crop, tuple(all_positions), tuple(animal_boards))


def _move(position, operation):
    if operation not in MOVES:
        return None
    x, y = position
    dx, dy = MOVES[operation]
    target = (x + dx, y + dy)
    if 0 <= target[0] < BOARD_SIZE and 0 <= target[1] < BOARD_SIZE:
        return target
    return position


def _apply_animal_place(
    crop_board,
    animal_board,
    inventory,
    action,
    position,
    day,
):
    if not isinstance(action, list) or not action or action[0] != "PLACE":
        return False, crop_board, animal_board, inventory
    if len(action) < 2:
        return False, crop_board, animal_board, inventory
    item = action[1]
    if item not in ANIMAL_SPECS:
        return False, crop_board, animal_board, inventory
    current = animal_board.get(position)
    if current != ANIMAL_SPECS[item].structure:
        return False, crop_board, animal_board, inventory
    inventory_after, accepted = inventory.take(item, 1)
    if accepted:
        crop_board = crop_board.set(position, "ANIMAL")
        animal_board = animal_board.set(position, AnimalTile.create(item, day))
    return True, crop_board, animal_board, inventory_after


def _apply_animal_action(
    crop_board,
    animal_board,
    inventory,
    action,
    position,
    player,
    unit_index,
    trace,
):
    if not isinstance(action, list) or not action:
        return False, crop_board, animal_board, inventory, None
    operation = action[0]
    if operation not in (
        "HARVEST",
        "DIG",
        "BUILD_COOP",
        "BUILD_PASTURE",
        "FEED",
        "COLLECT_FERTILIZER",
        "CARE",
    ):
        return False, crop_board, animal_board, inventory, None
    current = animal_board.get(position)
    accepted = False
    reason = None
    animal = current.animal if isinstance(current, AnimalTile) else None
    if operation == "HARVEST":
        if not isinstance(current, AnimalTile):
            reason = "not_animal"
        elif current.yield_units <= 0:
            reason = "no_yield"
        else:
            inventory = inventory.add(
                ANIMAL_SPECS[current.animal].product,
                current.yield_units,
            )
            animal_board = animal_board.set(
                position,
                replace(current, yield_units=0),
            )
            accepted = True
    elif operation == "DIG":
        if current in ("COOP", "PASTURE"):
            crop_board = crop_board.set(position, None)
            animal_board = animal_board.set(position, None)
            accepted = True
        elif isinstance(current, AnimalTile):
            reason = "occupied_animal"
        else:
            reason = "not_structure"
    elif operation in ("BUILD_COOP", "BUILD_PASTURE"):
        if crop_board.get(position) is not None:
            reason = "occupied"
        else:
            structure = "COOP" if operation == "BUILD_COOP" else "PASTURE"
            crop_board = crop_board.set(position, "STRUCTURE")
            animal_board = animal_board.set(position, structure)
            accepted = True
    elif operation == "FEED":
        if not isinstance(current, AnimalTile):
            reason = "not_animal"
        elif current.fed_today:
            reason = "already_fed"
        else:
            inventory_after, quantity = inventory.take("WHEAT", 1)
            if quantity == 0:
                reason = "unavailable_wheat"
            else:
                inventory = inventory_after
                animal_board = animal_board.set(
                    position,
                    replace(current, fed_today=True),
                )
                accepted = True
    elif operation == "COLLECT_FERTILIZER":
        if not isinstance(current, AnimalTile):
            reason = "not_animal"
        elif not current.fertilizer_available:
            reason = "unavailable_fertilizer"
        else:
            inventory = inventory.add("FERTILIZER", 1)
            animal_board = animal_board.set(
                position,
                replace(current, fertilizer_available=False),
            )
            accepted = True
    elif not isinstance(current, AnimalTile):
        reason = "not_animal"
    elif current.cared_today:
        reason = "already_cared"
    else:
        animal_board = animal_board.set(
            position,
            replace(current, cared_today=True),
        )
        accepted = True
    event = None
    if trace:
        event = _event(
            player,
            unit_index,
            position,
            operation,
            animal,
            accepted,
            reason,
        )
    return True, crop_board, animal_board, inventory, event


def apply_animal_player(state, player, player_actions, trace=False):
    if not isinstance(state, AnimalState):
        raise TypeError("state must be an AnimalState")
    if type(player) is not int or player not in (0, 1):
        raise ValueError("player must be 0 or 1")
    if type(player_actions) is not tuple or len(player_actions) != 2:
        raise TypeError("player actions must contain farmer and hands")
    if type(trace) is not bool:
        raise TypeError("trace must be a boolean")
    units = list(state.crop.inventory.units[player])
    positions = list(state.positions[player])
    account = state.crop.inventory.market.players[player]
    shed = account.shed
    seeds = account.seeds
    crop_board = state.crop.boards[player]
    animal_board = state.animal_boards[player]
    farmer_action, raw_hands = player_actions
    events = []
    day = state.crop.inventory.market.source_step // TURNS_PER_DAY
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
            if not isinstance(action, list) or not action:
                continue
            operation = action[0]
            hash(operation)
            moved = _move(positions[unit_index], operation)
            if moved is not None:
                positions[unit_index] = moved
                continue
            handled, crop_board, animal_board, inventory = _apply_animal_place(
                crop_board,
                animal_board,
                units[unit_index],
                action,
                positions[unit_index],
                day,
            )
            if handled:
                units[unit_index] = inventory
                continue
            handled, shed, inventory, inventory_event = apply_unit_transfer(
                shed,
                units[unit_index],
                action,
                is_shed_adjacent(positions[unit_index]),
                state.crop.inventory.market.config.shed_capacity,
                player,
                unit_index,
                trace,
            )
            if handled:
                units[unit_index] = inventory
                if inventory_event is not None:
                    events.append(inventory_event)
                continue
            handled, crop_board, inventory, seeds, crop_event = _apply_crop_action(
                crop_board,
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
                if crop_event is not None:
                    events.append(crop_event)
                continue
            handled, crop_board, animal_board, inventory, animal_event = (
                _apply_animal_action(
                    crop_board,
                    animal_board,
                    units[unit_index],
                    action,
                    positions[unit_index],
                    player,
                    unit_index,
                    trace,
                )
            )
            if handled:
                units[unit_index] = inventory
            if animal_event is not None:
                events.append(animal_event)
    except Exception as error:
        error.partial_state = _replace_state(
            state,
            player,
            account,
            shed,
            seeds,
            units,
            crop_board,
            animal_board,
            positions,
        )
        raise
    return (
        _replace_state(
            state,
            player,
            account,
            shed,
            seeds,
            units,
            crop_board,
            animal_board,
            positions,
        ),
        tuple(events),
    )


def _spawn_position(positions):
    occupancy = {position: 0 for position in SHED_ACCESS}
    for position in positions:
        if position in occupancy:
            occupancy[position] += 1
    return min(SHED_ACCESS, key=lambda position: (occupancy[position], SHED_ACCESS.index(position)))


def _after_market_state(state, market_transition):
    crop = _after_market(state.crop, market_transition)
    positions = []
    for player in range(2):
        player_positions = list(state.positions[player])
        added = len(crop.inventory.units[player]) - len(player_positions)
        for _ in range(added):
            player_positions.append(_spawn_position(player_positions))
        positions.append(tuple(player_positions))
    return AnimalState(crop, tuple(positions), state.animal_boards)


def apply_animal_refresh(state, trace=False):
    if not isinstance(state, AnimalState):
        raise TypeError("state must be an AnimalState")
    if type(trace) is not bool:
        raise TypeError("trace must be a boolean")
    current_day = state.crop.inventory.market.source_step // TURNS_PER_DAY
    next_day = current_day + 1
    crop_boards = list(state.crop.boards)
    animal_boards = []
    events = []
    for player, board in enumerate(state.animal_boards):
        updated = board
        crop_board = crop_boards[player]
        for index, current in enumerate(board.cells):
            if not isinstance(current, AnimalTile):
                continue
            position = (index % BOARD_SIZE, index // BOARD_SIZE)
            consecutive_unfed = 0 if current.fed_today else current.consecutive_unfed + 1
            if consecutive_unfed >= 2:
                structure = ANIMAL_SPECS[current.animal].structure
                updated = updated.set(position, structure)
                crop_board = crop_board.set(position, "STRUCTURE")
                if trace:
                    events.append(
                        _event(
                            player,
                            None,
                            position,
                            "ESCAPE",
                            current.animal,
                            True,
                            None,
                        )
                    )
                continue
            spec = ANIMAL_SPECS[current.animal]
            yield_units = current.yield_units
            pending = current.pending_care_bonus
            age = next_day - current.placed_day - spec.first_yield_day
            if age >= 0 and age % spec.interval == 0:
                bonus = pending if current.fed_today else 0
                yield_units = min(spec.max_held, yield_units + 1 + bonus)
                pending = 0
            if current.cared_today and current.fed_today:
                pending += 1
            replacement = replace(
                current,
                yield_units=yield_units,
                consecutive_unfed=consecutive_unfed,
                fed_today=False,
                cared_today=False,
                fertilizer_available=True,
                pending_care_bonus=pending,
            )
            updated = updated.set(position, replacement)
            if trace:
                events.append(
                    _event(
                        player,
                        None,
                        position,
                        "REFRESH_ANIMAL",
                        current.animal,
                        True,
                        None,
                    )
                )
        crop_boards[player] = crop_board
        animal_boards.append(updated)
    crop = CropState(
        state.crop.inventory,
        (crop_boards[0], crop_boards[1]),
    )
    return (
        AnimalState(crop, state.positions, tuple(animal_boards)),
        tuple(events),
    )


def apply_weeds(state, config):
    if not isinstance(state, AnimalState):
        raise TypeError("state must be an AnimalState")
    if not isinstance(config, AnimalConfig):
        raise TypeError("config must be an AnimalConfig")
    day = state.crop.inventory.market.source_step // TURNS_PER_DAY
    rng = random.Random((config.episode_seed * 1_000_003) ^ day)
    crop_boards = []
    for board in state.crop.boards:
        updated = board
        for index, cell in enumerate(board.cells):
            if cell is None and rng.random() < config.weed_chance:
                updated = updated.set(
                    (index % BOARD_SIZE, index // BOARD_SIZE),
                    "WEED",
                )
        crop_boards.append(updated)
    crop = CropState(
        state.crop.inventory,
        (crop_boards[0], crop_boards[1]),
    )
    return AnimalState(crop, state.positions, state.animal_boards), rng


def _finish_day(state, config, rng):
    inventory_end = apply_inventory_day_end(state.crop.inventory).state
    next_day = state.crop.inventory.market.source_step // TURNS_PER_DAY + 1
    market = inventory_end.market
    if (
        next_day > 0
        and next_day % config.shop_unlock_interval == 0
        and len(market.shops) < MAX_SHOPS
    ):
        market = replace(
            market,
            shops=market.shops + (rng.choice(sorted(SHOP_DEMAND)),),
        )
        inventory_end = InventoryState(market, inventory_end.units)
    crop = CropState(inventory_end, state.crop.boards)
    positions = (((4, 4),), ((4, 4),))
    return AnimalState(crop, positions, state.animal_boards)


def apply_animal_phase(
    state,
    unit_actions,
    market_queues,
    config=AnimalConfig(),
    trace=False,
):
    if not isinstance(state, AnimalState):
        raise TypeError("state must be an AnimalState")
    if type(unit_actions) is not tuple or len(unit_actions) != 2:
        raise TypeError("unit actions must contain two players")
    if not isinstance(config, AnimalConfig):
        raise TypeError("config must be an AnimalConfig")
    if type(trace) is not bool:
        raise TypeError("trace must be a boolean")
    current = state
    events = []
    for player in range(2):
        current, player_events = apply_animal_player(
            current,
            player,
            unit_actions[player],
            trace,
        )
        events.extend(player_events)
    after_units = current
    market_transition = apply_market_phase(
        after_units.crop.inventory.market,
        market_queues,
        trace,
    )
    after_town = _after_market_state(after_units, market_transition)
    decay_crop, decay_events = apply_crop_decay(after_town.crop, trace)
    after_decay = AnimalState(
        decay_crop,
        after_town.positions,
        after_town.animal_boards,
    )
    events.extend(decay_events)
    after_crop_refresh = None
    after_animal_refresh = None
    after_weeds = None
    after_end = None
    if (state.crop.inventory.market.source_step + 1) % TURNS_PER_DAY == 0:
        refresh_crop, crop_events = apply_crop_refresh(after_decay.crop, trace)
        after_crop_refresh = AnimalState(
            refresh_crop,
            after_decay.positions,
            after_decay.animal_boards,
        )
        events.extend(crop_events)
        after_animal_refresh, animal_events = apply_animal_refresh(
            after_crop_refresh,
            trace,
        )
        events.extend(animal_events)
        after_weeds, rng = apply_weeds(after_animal_refresh, config)
        after_end = _finish_day(after_weeds, config, rng)
    return AnimalTransition(
        after_units,
        market_transition,
        after_town,
        after_decay,
        after_crop_refresh,
        after_animal_refresh,
        after_weeds,
        after_end,
        tuple(events),
    )


def advance_animal_state(state):
    if not isinstance(state, AnimalState):
        raise TypeError("state must be an AnimalState")
    step = state.crop.inventory.market.source_step
    if step >= 718:
        raise ValueError("terminal state cannot advance")
    market = replace(state.crop.inventory.market, source_step=step + 1)
    inventory = InventoryState(market, state.crop.inventory.units)
    crop = CropState(inventory, state.crop.boards)
    return AnimalState(crop, state.positions, state.animal_boards)
