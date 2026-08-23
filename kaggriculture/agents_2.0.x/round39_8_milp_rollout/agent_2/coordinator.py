import copy

from .baseline import BaselineDecision, BaselinePolicy
from .domain import World
from .model import thaw
from .strategy import CropStrategy


MARKET_OPERATIONS = {
    "BUY_ANIMAL",
    "BUY_LAND",
    "BUY_PRODUCT",
    "BUY_SEED",
    "HIRE",
    "SELL",
}
SIMPLE_MARKET_OPERATIONS = {"BUY_LAND", "HIRE"}
CROPS = frozenset({"WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON"})


class FrozenEconomyPlanner:
    def reset(self) -> None:
        pass

    def plan(self, world: World, frozen_orders: tuple[tuple, ...]):
        return frozen_orders


class Agent2Coordinator:
    def __init__(self, baseline: BaselinePolicy, economy, strategy=None) -> None:
        self.baseline = baseline
        self.economy = economy
        if strategy is not None:
            self.strategy = strategy
            self.decide = self._decide_with_strategy

    def reset(self) -> None:
        self.economy.reset()
        if hasattr(self, "strategy"):
            self.strategy.reset()

    def decide(self, obs, world: World) -> BaselineDecision:
        decision = self.baseline.decide(obs)
        frozen_orders = self._freeze_orders(decision.action.get("market", ()))
        final_orders = self._choose_orders(world, frozen_orders)
        action = copy.deepcopy(decision.action)
        action["market"] = [list(order) for order in final_orders]
        try:
            self.baseline.remember_market(world.player, action["market"])
        except Exception:
            self._reset_economy()
            action = copy.deepcopy(decision.action)
            self._restore_frozen_market(world.player, frozen_orders)
        return BaselineDecision(action, decision.task_graph)

    def _decide_with_strategy(self, obs, world: World) -> BaselineDecision:
        strategy = self._prepare_strategy(world)
        decision = self.baseline.decide(obs, strategy)
        frozen_orders = self._freeze_orders(decision.action.get("market", ()))
        final_orders = self._choose_orders(world, frozen_orders)
        action = copy.deepcopy(decision.action)
        action["market"] = [list(order) for order in final_orders]
        try:
            self.baseline.remember_market(world.player, action["market"])
        except Exception:
            self._reset_economy()
            action = copy.deepcopy(decision.action)
            self._restore_frozen_market(world.player, frozen_orders)
        return BaselineDecision(action, decision.task_graph)

    def _prepare_strategy(self, world: World) -> CropStrategy | None:
        try:
            strategy = self.strategy.prepare(world)
            if strategy is None:
                return None
            self._validate_strategy(world, strategy)
            return strategy
        except Exception:
            self._reset_strategy()
            return None

    @staticmethod
    def _validate_strategy(world: World, strategy) -> None:
        if not isinstance(strategy, CropStrategy):
            raise TypeError("strategy must be a CropStrategy")
        if type(strategy.targets) is not tuple:
            raise TypeError("crop targets must be a tuple")
        values = thaw(world)
        tiles = values["farms"][world.player]["tiles"]
        if len(strategy.targets) > sum(len(row) for row in tiles):
            raise ValueError("too many crop targets")
        seen = set()
        for target in strategy.targets:
            if type(target) is not tuple or len(target) != 3:
                raise TypeError("crop target must be a three-item tuple")
            x, y, crop = target
            if type(x) is not int or type(y) is not int:
                raise TypeError("crop coordinates must be integers")
            if y < 0 or y >= len(tiles) or x < 0 or x >= len(tiles[y]):
                raise ValueError("crop coordinates are outside the board")
            if (x, y) in seen:
                raise ValueError("duplicate crop coordinates")
            if crop is not None and (type(crop) is not str or crop not in CROPS):
                raise ValueError("unknown crop")
            if tiles[y][x] is not None:
                raise ValueError("crop target tile is unavailable")
            seen.add((x, y))

    def _choose_orders(
        self,
        world: World,
        frozen_orders: tuple[tuple, ...],
    ) -> tuple[tuple, ...]:
        try:
            planned = self.economy.plan(world, frozen_orders)
            orders = self._freeze_orders(planned)
            return orders[: self.baseline.market_order_limit()]
        except Exception:
            self._reset_economy()
            return frozen_orders

    def _restore_frozen_market(
        self,
        player: int,
        frozen_orders: tuple[tuple, ...],
    ) -> None:
        try:
            self.baseline.remember_market(
                player,
                [list(order) for order in frozen_orders],
            )
        except Exception:
            pass

    def _reset_economy(self) -> None:
        try:
            self.economy.reset()
        except Exception:
            pass

    def _reset_strategy(self) -> None:
        try:
            self.strategy.reset()
        except Exception:
            pass

    @staticmethod
    def _freeze_orders(orders) -> tuple[tuple, ...]:
        if isinstance(orders, (str, bytes)):
            raise TypeError("market orders must be a sequence")
        frozen = []
        for order in orders:
            if not isinstance(order, (list, tuple)) or not order:
                raise TypeError("market order must be a non-empty sequence")
            operation = order[0]
            if operation not in MARKET_OPERATIONS:
                raise ValueError("unknown market operation")
            expected_length = 1 if operation in SIMPLE_MARKET_OPERATIONS else 3
            if len(order) != expected_length:
                raise ValueError("invalid market order length")
            if expected_length == 3:
                item, quantity = order[1], order[2]
                if not isinstance(item, str):
                    raise TypeError("market item must be text")
                if isinstance(quantity, bool) or not isinstance(quantity, int):
                    raise TypeError("market quantity must be an integer")
                if quantity <= 0:
                    raise ValueError("market quantity must be positive")
            frozen.append(tuple(copy.deepcopy(order)))
        return tuple(frozen)
