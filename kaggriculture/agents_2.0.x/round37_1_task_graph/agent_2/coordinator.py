import copy

from .baseline import BaselineDecision, BaselinePolicy
from .domain import World


MARKET_OPERATIONS = {
    "BUY_ANIMAL",
    "BUY_LAND",
    "BUY_PRODUCT",
    "BUY_SEED",
    "HIRE",
    "SELL",
}
SIMPLE_MARKET_OPERATIONS = {"BUY_LAND", "HIRE"}


class FrozenEconomyPlanner:
    def reset(self) -> None:
        pass

    def plan(self, world: World, frozen_orders: tuple[tuple, ...]):
        return frozen_orders


class Agent2Coordinator:
    def __init__(self, baseline: BaselinePolicy, economy) -> None:
        self.baseline = baseline
        self.economy = economy

    def reset(self) -> None:
        self.economy.reset()

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
