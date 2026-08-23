from dataclasses import dataclass, replace

from .market_ledger import (
    SHED_ITEMS,
    MarketState,
    MarketTransition,
    PlayerAccount,
    apply_market_phase,
)


@dataclass(frozen=True, slots=True)
class UnitInventory:
    entries: tuple[tuple[str, int], ...] = ()

    def __post_init__(self):
        if type(self.entries) is not tuple:
            raise TypeError("inventory entries must be a tuple")
        seen = set()
        for entry in self.entries:
            if type(entry) is not tuple or len(entry) != 2:
                raise TypeError("inventory entry must be a pair")
            item, quantity = entry
            if type(item) is not str or item not in SHED_ITEMS:
                raise ValueError("unknown inventory item")
            if type(quantity) is not int:
                raise TypeError("inventory quantity must be an integer")
            if quantity <= 0:
                raise ValueError("inventory quantity must be positive")
            if item in seen:
                raise ValueError("inventory items must be unique")
            seen.add(item)

    @classmethod
    def from_mapping(cls, values):
        if type(values) is not dict:
            raise TypeError("inventory must be a dictionary")
        return cls(tuple((item, quantity) for item, quantity in values.items()))

    def mapping(self):
        return dict(self.entries)

    def quantity(self, item):
        for current, quantity in self.entries:
            if current == item:
                return quantity
        return 0

    def add(self, item, quantity):
        _validate_item(item)
        _validate_positive_quantity(quantity)
        entries = list(self.entries)
        for index, (current, count) in enumerate(entries):
            if current == item:
                entries[index] = (item, count + quantity)
                return UnitInventory(tuple(entries))
        entries.append((item, quantity))
        return UnitInventory(tuple(entries))

    def take(self, item, quantity):
        _validate_item(item)
        _validate_positive_quantity(quantity)
        entries = list(self.entries)
        for index, (current, count) in enumerate(entries):
            if current != item:
                continue
            accepted = min(count, quantity)
            if accepted == count:
                del entries[index]
            else:
                entries[index] = (item, count - accepted)
            return UnitInventory(tuple(entries)), accepted
        return self, 0


@dataclass(frozen=True, slots=True)
class InventoryState:
    market: MarketState
    units: tuple[tuple[UnitInventory, ...], tuple[UnitInventory, ...]]

    def __post_init__(self):
        if not isinstance(self.market, MarketState):
            raise TypeError("market must be a MarketState")
        if type(self.units) is not tuple or len(self.units) != 2:
            raise TypeError("units must be a two-item tuple")
        for player, inventories in enumerate(self.units):
            if type(inventories) is not tuple:
                raise TypeError("player inventories must be a tuple")
            if len(inventories) != self.market.players[player].hands + 1:
                raise ValueError("inventory count must match units")
            if any(not isinstance(value, UnitInventory) for value in inventories):
                raise TypeError("invalid unit inventory")


@dataclass(frozen=True, slots=True)
class InventoryEvent:
    player: int
    unit_index: int
    operation: str
    item: str | None
    requested: int | None
    accepted: int
    discarded: int
    source: str | None
    destination: str | None
    failure_reason: str | None


@dataclass(frozen=True, slots=True)
class InventoryTransition:
    after_units: InventoryState
    market_transition: MarketTransition
    after_town: InventoryState
    events: tuple[InventoryEvent, ...]


@dataclass(frozen=True, slots=True)
class InventoryDayEnd:
    state: InventoryState
    discarded: tuple[tuple[int, ...], tuple[int, ...]]
    events: tuple[InventoryEvent, ...]


def _validate_item(item):
    if type(item) is not str or item not in SHED_ITEMS:
        raise ValueError("unknown inventory item")


def _validate_positive_quantity(quantity):
    if type(quantity) is not int:
        raise TypeError("quantity must be an integer")
    if quantity <= 0:
        raise ValueError("quantity must be positive")


def _validate_vector(values):
    if type(values) is not tuple or len(values) != len(SHED_ITEMS):
        raise TypeError("item vector must be a fixed tuple")
    if any(type(value) is not int for value in values):
        raise TypeError("item vector values must be integers")
    if any(value < 0 for value in values):
        raise ValueError("item vector values must be nonnegative")


def add(values, item, quantity):
    _validate_vector(values)
    _validate_item(item)
    _validate_positive_quantity(quantity)
    result = list(values)
    result[SHED_ITEMS.index(item)] += quantity
    return tuple(result)


def take(values, item, quantity):
    _validate_vector(values)
    _validate_item(item)
    _validate_positive_quantity(quantity)
    index = SHED_ITEMS.index(item)
    accepted = min(values[index], quantity)
    result = list(values)
    result[index] -= accepted
    return tuple(result), accepted


def remaining_capacity(values, capacity):
    _validate_vector(values)
    if type(capacity) is not int:
        raise TypeError("capacity must be an integer")
    if capacity < 1:
        raise ValueError("capacity must be positive")
    return max(0, capacity - sum(values))


def transfer(source, destination, item, quantity, capacity):
    _validate_vector(source)
    room = remaining_capacity(destination, capacity)
    source_after, available = take(source, item, quantity)
    accepted = min(available, room)
    if available > accepted:
        source_after = add(source_after, item, available - accepted)
    destination_after = destination
    if accepted:
        destination_after = add(destination, item, accepted)
    return source_after, destination_after, accepted


def _inventory_event(
    player,
    unit_index,
    operation,
    item,
    requested,
    accepted,
    discarded,
    source,
    destination,
    failure_reason,
):
    return InventoryEvent(
        player,
        unit_index,
        operation,
        item,
        requested,
        accepted,
        discarded,
        source,
        destination,
        failure_reason,
    )


def _raw_item_known(item):
    hash(item)
    return item in SHED_ITEMS


def apply_unit_transfer(
    shed,
    inventory,
    action,
    adjacent,
    capacity,
    player=0,
    unit_index=0,
    trace=False,
):
    _validate_vector(shed)
    if not isinstance(inventory, UnitInventory):
        raise TypeError("inventory must be a UnitInventory")
    if type(adjacent) is not bool:
        raise TypeError("adjacent must be a boolean")
    if type(trace) is not bool:
        raise TypeError("trace must be a boolean")
    if not isinstance(action, list) or not action:
        return False, shed, inventory, None
    operation = action[0]
    hash(operation)
    if operation == "PASS":
        event = None
        if trace:
            event = _inventory_event(
                player,
                unit_index,
                operation,
                None,
                None,
                0,
                0,
                None,
                None,
                None,
            )
        return True, shed, inventory, event
    if operation == "DROP":
        if not adjacent:
            reason = "not_adjacent"
            event = None
            if trace:
                event = _inventory_event(
                    player,
                    unit_index,
                    operation,
                    None,
                    None,
                    0,
                    0,
                    "unit",
                    "shed",
                    reason,
                )
            return True, shed, inventory, event
        updated_shed = shed
        accepted = 0
        discarded = 0
        for item, quantity in inventory.entries:
            deposited = min(quantity, remaining_capacity(updated_shed, capacity))
            if deposited:
                updated_shed = add(updated_shed, item, deposited)
            accepted += deposited
            discarded += quantity - deposited
        event = None
        if trace:
            event = _inventory_event(
                player,
                unit_index,
                operation,
                None,
                sum(quantity for _, quantity in inventory.entries),
                accepted,
                discarded,
                "unit",
                "shed",
                None,
            )
        return True, updated_shed, UnitInventory(), event
    if operation not in ("PICKUP", "PLACE"):
        return False, shed, inventory, None
    if not adjacent and operation == "PICKUP":
        event = None
        if trace:
            event = _inventory_event(
                player,
                unit_index,
                operation,
                None,
                None,
                0,
                0,
                "shed",
                "unit",
                "not_adjacent",
            )
        return True, shed, inventory, event
    if len(action) < 2:
        event = None
        if trace:
            event = _inventory_event(
                player,
                unit_index,
                operation,
                None,
                None,
                0,
                0,
                "shed" if operation == "PICKUP" else "unit",
                "unit" if operation == "PICKUP" else "shed",
                "missing_item",
            )
        return True, shed, inventory, event
    item = action[1]
    if operation == "PLACE":
        hash(item)
    if operation == "PLACE" and not adjacent:
        event = None
        if trace:
            event = _inventory_event(
                player,
                unit_index,
                operation,
                item if type(item) is str else None,
                None,
                0,
                0,
                "unit",
                "shed",
                "not_adjacent",
            )
        return True, shed, inventory, event
    requested = int(action[2]) if len(action) >= 3 else 1
    if requested <= 0:
        event = None
        if trace:
            event = _inventory_event(
                player,
                unit_index,
                operation,
                item if type(item) is str else None,
                requested,
                0,
                0,
                "shed" if operation == "PICKUP" else "unit",
                "unit" if operation == "PICKUP" else "shed",
                "nonpositive_quantity",
            )
        return True, shed, inventory, event
    known = _raw_item_known(item)
    if operation == "PICKUP":
        accepted = min(requested, shed[SHED_ITEMS.index(item)]) if known else 0
        updated_shed = shed
        updated_inventory = inventory
        if accepted:
            updated_shed, accepted = take(shed, item, requested)
            updated_inventory = inventory.add(item, accepted)
        reason = None if accepted else "unavailable"
        source = "shed"
        destination = "unit"
    else:
        available = inventory.quantity(item) if known else 0
        accepted = min(requested, available, remaining_capacity(shed, capacity))
        updated_shed = shed
        updated_inventory = inventory
        if accepted:
            updated_inventory, accepted = inventory.take(item, accepted)
            updated_shed = add(shed, item, accepted)
        if accepted:
            reason = None
        elif not available:
            reason = "unavailable"
        else:
            reason = "shed_full"
        source = "unit"
        destination = "shed"
    event = None
    if trace:
        event = _inventory_event(
            player,
            unit_index,
            operation,
            item if type(item) is str else None,
            requested,
            accepted,
            0,
            source,
            destination,
            reason,
        )
    return True, updated_shed, updated_inventory, event


def _replace_shed(account, shed):
    return PlayerAccount(
        account.money,
        shed,
        account.seeds,
        account.hires_today,
        account.unlocked_quadrants,
        account.hands,
    )


def _replace_market_players(state, players):
    return MarketState(
        state.source_step,
        state.inventory,
        players,
        state.shops,
        state.params,
        state.config,
    )


def apply_inventory_phase(
    state,
    unit_actions,
    shed_adjacency,
    market_queues,
    trace=False,
):
    if not isinstance(state, InventoryState):
        raise TypeError("state must be an InventoryState")
    if type(unit_actions) is not tuple or len(unit_actions) != 2:
        raise TypeError("unit actions must be a two-item tuple")
    if type(shed_adjacency) is not tuple or len(shed_adjacency) != 2:
        raise TypeError("shed adjacency must be a two-item tuple")
    if type(trace) is not bool:
        raise TypeError("trace must be a boolean")
    units = [list(player_units) for player_units in state.units]
    accounts = list(state.market.players)
    events = []
    for player in range(2):
        actions = unit_actions[player]
        if type(actions) is not tuple or len(actions) != 2:
            raise TypeError("player actions must contain farmer and hands")
        adjacency = shed_adjacency[player]
        if type(adjacency) is not tuple or len(adjacency) != len(units[player]):
            raise TypeError("shed adjacency must match units")
        if any(type(value) is not bool for value in adjacency):
            raise TypeError("shed adjacency values must be booleans")
        farmer_action, raw_hands = actions
        hand_actions = raw_hands if isinstance(raw_hands, list) else []
        resolved_actions = [(0, farmer_action)]
        resolved_actions.extend(
            (index + 1, action)
            for index, action in enumerate(hand_actions[: len(units[player]) - 1])
        )
        shed = accounts[player].shed
        for unit_index, action in resolved_actions:
            handled, shed, inventory, event = apply_unit_transfer(
                shed,
                units[player][unit_index],
                action,
                adjacency[unit_index],
                state.market.config.shed_capacity,
                player,
                unit_index,
                trace,
            )
            if handled:
                units[player][unit_index] = inventory
            if event is not None:
                events.append(event)
        accounts[player] = _replace_shed(accounts[player], shed)
    after_units_market = _replace_market_players(state.market, tuple(accounts))
    after_units = InventoryState(
        after_units_market,
        (tuple(units[0]), tuple(units[1])),
    )
    market_transition = apply_market_phase(after_units.market, market_queues, trace)
    after_town_units = [list(player_units) for player_units in after_units.units]
    for player in range(2):
        old_hands = after_units.market.players[player].hands
        new_hands = market_transition.after_town.players[player].hands
        after_town_units[player].extend(
            UnitInventory() for _ in range(new_hands - old_hands)
        )
    after_town = InventoryState(
        market_transition.after_town,
        (tuple(after_town_units[0]), tuple(after_town_units[1])),
    )
    return InventoryTransition(
        after_units,
        market_transition,
        after_town,
        tuple(events),
    )


def apply_inventory_day_end(state, trace=False):
    if not isinstance(state, InventoryState):
        raise TypeError("state must be an InventoryState")
    if type(trace) is not bool:
        raise TypeError("trace must be a boolean")
    accounts = []
    discarded_by_player = []
    events = []
    for player, player_units in enumerate(state.units):
        account = state.market.players[player]
        shed = account.shed
        discarded = (0,) * len(SHED_ITEMS)
        for unit_index, inventory in enumerate(player_units):
            for item, quantity in inventory.entries:
                accepted = min(
                    quantity,
                    remaining_capacity(shed, state.market.config.shed_capacity),
                )
                lost = quantity - accepted
                if accepted:
                    shed = add(shed, item, accepted)
                if lost:
                    discarded = add(discarded, item, lost)
                if trace:
                    events.append(
                        _inventory_event(
                            player,
                            unit_index,
                            "DAY_END_DROP",
                            item,
                            quantity,
                            accepted,
                            lost,
                            "unit",
                            "shed",
                            None if accepted else "shed_full",
                        )
                    )
        accounts.append(
            replace(
                account,
                shed=shed,
                hires_today=0,
                hands=0,
            )
        )
        discarded_by_player.append(discarded)
    market = _replace_market_players(state.market, tuple(accounts))
    result = InventoryState(
        market,
        ((UnitInventory(),), (UnitInventory(),)),
    )
    return InventoryDayEnd(
        result,
        (discarded_by_player[0], discarded_by_player[1]),
        tuple(events),
    )
