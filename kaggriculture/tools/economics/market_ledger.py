import math
from dataclasses import dataclass, replace


PRODUCTS = (
    "WHEAT",
    "CARROT",
    "TOMATO",
    "STRAWBERRY",
    "MELON",
    "EGG",
    "MILK",
    "WOOL",
    "FERTILIZER",
)
CROPS = ("WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON")
ANIMALS = ("GOOSE", "COW", "SHEEP")
SHED_ITEMS = PRODUCTS + ANIMALS
BUYABLE_PRODUCTS = ("WHEAT", "FERTILIZER")
SEED_COSTS = {
    "WHEAT": 10,
    "CARROT": 20,
    "TOMATO": 50,
    "STRAWBERRY": 100,
    "MELON": 80,
}
ANIMAL_COSTS = {"GOOSE": 300, "COW": 400, "SHEEP": 500}
LAND_PRICES = (1000, 2000, 4000)
SHOP_DEMAND = {
    "BAKERY": ("EGG", "WHEAT"),
    "PIZZA_SHOP": ("MILK", "TOMATO", "WHEAT"),
    "BRUNCH_SPOT": ("EGG", "WHEAT", "STRAWBERRY"),
    "YARN_STORE": ("WOOL",),
    "ICE_CREAM_SHOP": ("STRAWBERRY", "MILK", "WHEAT"),
    "PET_CAFE": ("CARROT",),
    "SMOOTHIE_SHOP": ("STRAWBERRY", "MILK"),
    "FARMERS_MARKET": ("WHEAT", "CARROT", "TOMATO", "STRAWBERRY"),
}
TOWN_CENTER_PRODUCTS = PRODUCTS[:-1]
PRICE_FLOOR = 1
MARKET_LOOP_LIMIT = 100_000


@dataclass(frozen=True, slots=True)
class MarketParam:
    base: float
    I0: float
    T: float
    below_func: str
    below_target: float
    above_func: str
    above_target: float


DEFAULT_MARKET_PARAMS = (
    MarketParam(25, 10000, 400, "sqrt", 0.80, "log", 0.20),
    MarketParam(35, 10000, 450, "hinge", 1.00, "sqrt", 0.70),
    MarketParam(60, 10000, 200, "hinge", 0.40, "sqrt", 0.60),
    MarketParam(120, 10000, 100, "sqrt", 0.70, "linear", 1.60),
    MarketParam(250, 10000, 300, "log", 0.20, "sq", 3.60),
    MarketParam(50, 10000, 332, "hinge", 0.40, "log", 0.20),
    MarketParam(160, 10000, 122, "sqrt", 0.60, "linear", 1.60),
    MarketParam(200, 10000, 105, "log", 0.20, "sq", 3.20),
    MarketParam(100, 10000, 200, "linear", 0.40, "linear", 0.40),
)


@dataclass(frozen=True, slots=True)
class MarketConfig:
    max_orders: int = 10
    shed_capacity: int = 100
    hire_multiplier: int = 1
    shop_interval: int = 4
    center_interval: int = 24

    def __post_init__(self):
        values = (
            self.max_orders,
            self.shed_capacity,
            self.hire_multiplier,
            self.shop_interval,
            self.center_interval,
        )
        if any(type(value) is not int for value in values):
            raise TypeError("market configuration values must be integers")
        if self.max_orders < 1 or self.shed_capacity < 1:
            raise ValueError("market limits must be positive")
        if self.hire_multiplier < 0:
            raise ValueError("hire multiplier must be nonnegative")
        if self.shop_interval < 1 or self.center_interval < 1:
            raise ValueError("town intervals must be positive")


@dataclass(frozen=True, slots=True)
class PlayerAccount:
    money: float
    shed: tuple[int, ...]
    seeds: tuple[int, ...]
    hires_today: int
    unlocked_quadrants: int
    hands: int

    def __post_init__(self):
        if type(self.money) not in (int, float) or isinstance(self.money, bool):
            raise TypeError("money must be numeric")
        if not math.isfinite(self.money) or self.money < 0:
            raise ValueError("money must be finite and nonnegative")
        _validate_vector(self.shed, len(SHED_ITEMS), False, "shed")
        _validate_vector(self.seeds, len(CROPS), False, "seeds")
        counters = (self.hires_today, self.unlocked_quadrants, self.hands)
        if any(type(value) is not int for value in counters):
            raise TypeError("account counters must be integers")
        if self.hires_today < 0 or self.hands < 0:
            raise ValueError("account counters must be nonnegative")
        if self.unlocked_quadrants < 1 or self.unlocked_quadrants > 4:
            raise ValueError("unlocked quadrants must be in 1..4")

    @classmethod
    def from_mappings(
        cls,
        money,
        shed,
        seeds,
        hires_today=0,
        unlocked_quadrants=1,
        hands=0,
    ):
        return cls(
            money,
            _mapping_vector(shed, SHED_ITEMS, False, "shed"),
            _mapping_vector(seeds, CROPS, False, "seeds"),
            hires_today,
            unlocked_quadrants,
            hands,
        )

    def shed_mapping(self):
        return dict(zip(SHED_ITEMS, self.shed))

    def seed_mapping(self):
        return dict(zip(CROPS, self.seeds))


@dataclass(frozen=True, slots=True)
class MarketState:
    source_step: int
    inventory: tuple[int, ...]
    players: tuple[PlayerAccount, PlayerAccount]
    shops: tuple[str, ...]
    params: tuple[MarketParam, ...] = DEFAULT_MARKET_PARAMS
    config: MarketConfig = MarketConfig()

    def __post_init__(self):
        if type(self.source_step) is not int:
            raise TypeError("source step must be an integer")
        if self.source_step < 0 or self.source_step > 718:
            raise ValueError("source step must be in 0..718")
        _validate_vector(self.inventory, len(PRODUCTS), True, "market inventory")
        if type(self.players) is not tuple or len(self.players) != 2:
            raise TypeError("players must be a two-item tuple")
        if any(not isinstance(player, PlayerAccount) for player in self.players):
            raise TypeError("players must contain PlayerAccount values")
        if type(self.shops) is not tuple:
            raise TypeError("shops must be a tuple")
        if any(type(shop) is not str or shop not in SHOP_DEMAND for shop in self.shops):
            raise ValueError("unknown shop")
        if type(self.params) is not tuple or len(self.params) != len(PRODUCTS):
            raise TypeError("market parameters must match products")
        if any(not isinstance(param, MarketParam) for param in self.params):
            raise TypeError("invalid market parameters")
        if not isinstance(self.config, MarketConfig):
            raise TypeError("invalid market configuration")

    @classmethod
    def from_mappings(
        cls,
        source_step,
        inventory,
        players,
        shops=(),
        params=DEFAULT_MARKET_PARAMS,
        config=MarketConfig(),
    ):
        return cls(
            source_step,
            _mapping_vector(inventory, PRODUCTS, True, "market inventory"),
            players,
            shops,
            params,
            config,
        )

    @property
    def prices(self):
        return tuple(
            market_price(item, inventory, self.params)
            for item, inventory in zip(PRODUCTS, self.inventory)
        )

    def inventory_mapping(self):
        return dict(zip(PRODUCTS, self.inventory))


@dataclass(frozen=True, slots=True)
class OrderEvent:
    order_index: int
    unit_index: int | None
    player: int
    operation: str
    item: str | None
    quoted_price: int | None
    item_inventory_before: int | None
    accepted: bool
    failure_reason: str | None
    cash_before: tuple[float, float]
    cash_after: tuple[float, float]
    market_before: tuple[int, ...]
    market_after: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class TownEvent:
    source: str
    source_index: int
    item: str
    quantity: int
    inventory_before: int
    inventory_after: int


@dataclass(frozen=True, slots=True)
class MarketTransition:
    after_town: MarketState
    order_events: tuple[OrderEvent, ...]
    town_events: tuple[TownEvent, ...]


def _validate_vector(values, length, allow_negative, name):
    if type(values) is not tuple or len(values) != length:
        raise TypeError(f"{name} must be a fixed tuple")
    if any(type(value) is not int for value in values):
        raise TypeError(f"{name} values must be integers")
    if not allow_negative and any(value < 0 for value in values):
        raise ValueError(f"{name} values must be nonnegative")


def _mapping_vector(values, names, allow_negative, name):
    if type(values) is not dict:
        raise TypeError(f"{name} must be a dictionary")
    if set(values) != set(names):
        raise ValueError(f"{name} keys differ")
    vector = tuple(values[item] for item in names)
    _validate_vector(vector, len(names), allow_negative, name)
    return vector


def _shape(function, value, scale=None):
    value = max(0.0, value)
    if function == "linear":
        return value
    if function == "sq":
        return value * value
    if function == "sqrt":
        return math.sqrt(value)
    if function == "log":
        return math.log(1.0 + value)
    if function == "log10":
        return math.log10(1.0 + value)
    if function == "hinge":
        if not scale or scale <= 0:
            return value
        ratio = value / scale
        return ratio + 8.0 * max(0.0, ratio - 1.0) ** 2
    return value


def resolve_market_params(overrides=None):
    params = list(DEFAULT_MARKET_PARAMS)
    if not overrides:
        return tuple(params)
    for item, patch in overrides.items():
        if item not in PRODUCTS or not isinstance(patch, dict):
            continue
        index = PRODUCTS.index(item)
        current = params[index]
        fields = {
            key: value
            for key, value in patch.items()
            if key in MarketParam.__dataclass_fields__
        }
        params[index] = replace(current, **fields)
    return tuple(params)


def market_price(item, inventory, params=DEFAULT_MARKET_PARAMS):
    index = PRODUCTS.index(item)
    param = params[index]
    if inventory < param.I0:
        function = param.below_func
        amplitude = (
            param.below_target
            * param.base
            / _shape(function, param.T, param.T)
        )
        price = param.base + amplitude * _shape(
            function,
            param.I0 - inventory,
            param.T,
        )
    else:
        function = param.above_func
        amplitude = (
            param.above_target
            * param.base
            / _shape(function, param.T, param.T)
        )
        price = param.base - amplitude * _shape(
            function,
            inventory - param.I0,
            param.T,
        )
    return max(PRICE_FLOOR, int(round(price)))


def sell_quote(item, inventory, params=DEFAULT_MARKET_PARAMS):
    return market_price(item, inventory, params)


def buy_quote(item, inventory, params=DEFAULT_MARKET_PARAMS):
    if item not in BUYABLE_PRODUCTS:
        raise ValueError("product cannot be bought")
    return market_price(item, inventory - 1, params)


def _fib(index):
    first, second = 1, 1
    for _ in range(index):
        first, second = second, first + second
    return first


def _parse_order(order):
    if not isinstance(order, list) or not order:
        return None
    operation = order[0]
    if operation in ("HIRE", "BUY_LAND"):
        return {"type": operation}
    if operation in ("BUY_SEED", "BUY_PRODUCT", "BUY_ANIMAL", "SELL"):
        if len(order) < 3:
            return None
        try:
            quantity = int(order[2])
        except (TypeError, ValueError):
            return None
        if quantity <= 0:
            return None
        return {"type": operation, "item": order[1], "remaining": quantity}
    return None


def _mutable_account(account):
    return {
        "money": account.money,
        "shed": list(account.shed),
        "seeds": list(account.seeds),
        "hires_today": account.hires_today,
        "unlocked_quadrants": account.unlocked_quadrants,
        "hands": account.hands,
    }


def _freeze_account(account):
    return PlayerAccount(
        account["money"],
        tuple(account["shed"]),
        tuple(account["seeds"]),
        account["hires_today"],
        account["unlocked_quadrants"],
        account["hands"],
    )


def _cash(accounts):
    return accounts[0]["money"], accounts[1]["money"]


def _event(
    events,
    enabled,
    order_index,
    unit_index,
    player,
    operation,
    item,
    price,
    item_inventory_before,
    accepted,
    failure_reason,
    cash_before,
    cash_after,
    market_before,
    market_after,
):
    if enabled:
        events.append(
            OrderEvent(
                order_index,
                unit_index,
                player,
                operation,
                item,
                price,
                item_inventory_before,
                accepted,
                failure_reason,
                cash_before,
                cash_after,
                market_before,
                market_after,
            )
        )


def _apply_atomic(operation, account, config):
    if operation == "HIRE":
        cost = config.hire_multiplier * _fib(account["hires_today"])
        if account["money"] < cost:
            return False, "insufficient_cash"
        account["money"] -= cost
        account["hires_today"] += 1
        account["hands"] += 1
        return True, None
    if account["unlocked_quadrants"] >= 4:
        return False, "all_land_unlocked"
    cost = LAND_PRICES[account["unlocked_quadrants"] - 1]
    if account["money"] < cost:
        return False, "insufficient_cash"
    account["money"] -= cost
    account["unlocked_quadrants"] += 1
    return True, None


def _quote(order_state, inventory, params):
    operation = order_state["type"]
    item = order_state["item"]
    if operation == "SELL" and item in PRODUCTS:
        index = PRODUCTS.index(item)
        return operation, item, sell_quote(item, inventory[index], params), inventory[index]
    if operation == "BUY_PRODUCT" and item in BUYABLE_PRODUCTS:
        index = PRODUCTS.index(item)
        return operation, item, buy_quote(item, inventory[index], params), inventory[index]
    if operation == "BUY_SEED" and item in SEED_COSTS:
        return operation, item, SEED_COSTS[item], None
    if operation == "BUY_ANIMAL" and item in ANIMAL_COSTS:
        return operation, item, ANIMAL_COSTS[item], None
    return None


def _commit(operation, item, price, account, inventory, config):
    if operation == "SELL":
        shed_index = SHED_ITEMS.index(item)
        if account["shed"][shed_index] <= 0:
            return False, "unavailable"
        account["shed"][shed_index] -= 1
        account["money"] += price
        if price > PRICE_FLOOR:
            inventory[PRODUCTS.index(item)] += 1
        return True, None
    if operation == "BUY_PRODUCT":
        if account["money"] < price:
            return False, "insufficient_cash"
        if sum(account["shed"]) >= config.shed_capacity:
            return False, "shed_full"
        account["money"] -= price
        account["shed"][SHED_ITEMS.index(item)] += 1
        inventory[PRODUCTS.index(item)] -= 1
        return True, None
    if operation == "BUY_SEED":
        if account["money"] < price:
            return False, "insufficient_cash"
        account["money"] -= price
        account["seeds"][CROPS.index(item)] += 1
        return True, None
    if account["money"] < price:
        return False, "insufficient_cash"
    if sum(account["shed"]) >= config.shed_capacity:
        return False, "shed_full"
    account["money"] -= price
    account["shed"][SHED_ITEMS.index(item)] += 1
    return True, None


def apply_market_phase(state, queues, trace=False):
    if type(queues) is not tuple or len(queues) != 2:
        raise TypeError("queues must be a two-item tuple")
    if type(trace) is not bool:
        raise TypeError("trace must be a boolean")
    inventory = list(state.inventory)
    accounts = [_mutable_account(account) for account in state.players]
    parsed_queues = []
    for queue in queues:
        orders = list(queue) if isinstance(queue, list) else []
        parsed_queues.append(orders[: state.config.max_orders])
    order_events = []
    max_length = max((len(queue) for queue in parsed_queues), default=0)
    for order_index in range(max_length):
        order_states = [
            _parse_order(queue[order_index]) if order_index < len(queue) else None
            for queue in parsed_queues
        ]
        for player, order_state in enumerate(order_states):
            if order_state is None or order_state["type"] not in ("HIRE", "BUY_LAND"):
                continue
            cash_before = _cash(accounts)
            market_before = tuple(inventory)
            accepted, reason = _apply_atomic(
                order_state["type"],
                accounts[player],
                state.config,
            )
            _event(
                order_events,
                trace,
                order_index,
                None,
                player,
                order_state["type"],
                None,
                None,
                None,
                accepted,
                reason,
                cash_before,
                _cash(accounts),
                market_before,
                tuple(inventory),
            )
            order_states[player] = None
        iteration = 0
        while True:
            iteration += 1
            if iteration >= MARKET_LOOP_LIMIT:
                break
            quotes = [None, None]
            for player, order_state in enumerate(order_states):
                if order_state is None or order_state["remaining"] <= 0:
                    continue
                quote = _quote(order_state, inventory, state.params)
                if quote is None:
                    _event(
                        order_events,
                        trace,
                        order_index,
                        iteration - 1,
                        player,
                        order_state["type"],
                        order_state["item"],
                        None,
                        None,
                        False,
                        "unsupported_item",
                        _cash(accounts),
                        _cash(accounts),
                        tuple(inventory),
                        tuple(inventory),
                    )
                    order_states[player] = None
                else:
                    quotes[player] = quote
            if all(quote is None for quote in quotes):
                break
            committed_any = False
            for player, quote in enumerate(quotes):
                if quote is None:
                    continue
                operation, item, price, item_inventory_before = quote
                cash_before = _cash(accounts)
                market_before = tuple(inventory)
                accepted, reason = _commit(
                    operation,
                    item,
                    price,
                    accounts[player],
                    inventory,
                    state.config,
                )
                _event(
                    order_events,
                    trace,
                    order_index,
                    iteration - 1,
                    player,
                    operation,
                    item,
                    price,
                    item_inventory_before,
                    accepted,
                    reason,
                    cash_before,
                    _cash(accounts),
                    market_before,
                    tuple(inventory),
                )
                if accepted:
                    order_states[player]["remaining"] -= 1
                    committed_any = True
                else:
                    order_states[player] = None
            if not committed_any:
                break
    town_events = []
    if state.source_step % state.config.shop_interval == 0:
        for source_index, shop in enumerate(state.shops):
            demand = SHOP_DEMAND[shop]
            quantity = 2 if len(demand) == 1 else 1
            for item in demand:
                item_index = PRODUCTS.index(item)
                before = inventory[item_index]
                inventory[item_index] -= quantity
                if trace:
                    town_events.append(
                        TownEvent(
                            shop,
                            source_index,
                            item,
                            quantity,
                            before,
                            inventory[item_index],
                        )
                    )
    if state.source_step % state.config.center_interval == 0:
        for source_index, item in enumerate(TOWN_CENTER_PRODUCTS):
            item_index = PRODUCTS.index(item)
            before = inventory[item_index]
            inventory[item_index] -= 1
            if trace:
                town_events.append(
                    TownEvent(
                        "TOWN_CENTER",
                        source_index,
                        item,
                        1,
                        before,
                        inventory[item_index],
                    )
                )
    after_town = MarketState(
        state.source_step,
        tuple(inventory),
        tuple(_freeze_account(account) for account in accounts),
        state.shops,
        state.params,
        state.config,
    )
    return MarketTransition(after_town, tuple(order_events), tuple(town_events))
