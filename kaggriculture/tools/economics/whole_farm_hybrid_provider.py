import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

try:
    from ..artifact import load_artifact
except ImportError:
    from artifact import load_artifact

from .live_snapshot import LiveSnapshotAdapter
from .rolling_coordinator import (
    PlanFailure,
    RollingCoordinator,
    WholeFarmIntent,
)
from .whole_farm_backend import (
    PlanningHorizonConfig,
    WholeFarmPlannerBackend,
    WholeFarmSolveError,
)


ROOT = Path(__file__).resolve().parents[2]
AGENT2_TEMPLATE = ROOT / "agents_2.0.x/round39_8_milp_rollout"


@dataclass(slots=True)
class _MarketOrderProgress:
    order: tuple
    planned_quantity: int
    acknowledged_quantity: int = 0
    inflight_quantity: int = 0


def _world_identity(world):
    if hasattr(world, "identity"):
        return world.identity
    if isinstance(world, Mapping):
        values = dict(world.items())
        values.pop("remainingOverageTime", None)
        return json.dumps(
            values,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    raise TypeError("world must contain an observation")


class WholeFarmHandoffSource:
    def __init__(
        self,
        registered_seed=3_980_000,
        time_limit=30.0,
        mip_rel_gap=0.0,
        max_iterations=5,
        horizon=None,
    ):
        if horizon is None:
            horizon = PlanningHorizonConfig()
        if type(horizon) is not PlanningHorizonConfig:
            raise TypeError("horizon must be a PlanningHorizonConfig")
        self._bridge = LiveSnapshotAdapter(registered_seed)
        self._backend = WholeFarmPlannerBackend(
            self._bridge.snapshot,
            time_limit,
            mip_rel_gap,
            max_iterations,
            "frozen-1.14",
            horizon=horizon,
        )
        self._coordinator = RollingCoordinator(self._backend)
        self._traces = []
        self._last_world_identity = None
        self._last_handoff = None
        self._last_error = None

    @property
    def traces(self):
        return tuple(self._traces)

    @property
    def handoff(self):
        return self._last_handoff

    @property
    def backend(self):
        return self._backend

    @property
    def last_error(self):
        return self._last_error

    def reset(self):
        self._bridge.reset()
        failure = self._coordinator.reset()
        if failure is not None:
            raise WholeFarmSolveError(failure.exception_text)
        self._traces.clear()
        self._last_world_identity = None
        self._last_handoff = None
        self._last_error = None

    def __call__(self, world):
        world_identity = _world_identity(world)
        if world_identity == self._last_world_identity:
            if self._last_error is not None:
                raise self._last_error
            return self._last_handoff
        self._last_world_identity = world_identity
        observation = self._bridge.observe(world)
        intent = self._coordinator.prepare(observation)
        if isinstance(intent, PlanFailure):
            self._last_error = WholeFarmSolveError(intent.exception_text)
            raise self._last_error
        if type(intent) is not WholeFarmIntent:
            raise TypeError("coordinator returned wrong intent type")
        handoff = self._backend.last_handoff
        if handoff is None or handoff.epoch != intent.epoch:
            raise WholeFarmSolveError("intent lacks executable handoff")
        trace = self._backend.last_trace
        if trace is None or trace.epoch != intent.epoch:
            raise WholeFarmSolveError("intent lacks decision trace")
        if not self._traces or self._traces[-1].fingerprint != trace.fingerprint:
            self._traces.append(trace)
        if observation.source_step % 24 == 0:
            if trace.observed.source_step != observation.source_step:
                raise WholeFarmSolveError("daily observation lacks full solve")
        self._last_error = None
        self._last_handoff = handoff
        return handoff

    def verify_daily_epochs(self):
        expected = set(range(0, 697, 24))
        actual = {trace.observed.source_step for trace in self._traces}
        missing = tuple(sorted(expected - actual))
        if missing:
            raise WholeFarmSolveError(f"missing daily solves: {missing}")
        return ()


class _Agent2Seam:
    def __init__(self, source, crop_strategy):
        self._source = source
        self._crop_strategy = crop_strategy
        self._market_progress = {}
        self._inflight_seed_orders = ()
        self._last_seed_state = None
        self._last_plants = None

    def reset(self):
        self._market_progress.clear()
        self._inflight_seed_orders = ()
        self._last_seed_state = None
        self._last_plants = None

    def prepare(self, world):
        handoff = self._source(world)
        day = world.step // 24
        targets = tuple(
            (target.x, target.y, target.crop)
            for target in handoff.crop_targets
            if target.day == day
        )
        return self._crop_strategy(targets) if targets else None

    def plan(self, world, frozen_orders):
        handoff = self._source(world)
        self._acknowledge_seed_orders(world)
        candidates = []
        for intent in handoff.market_orders:
            progress = self._market_progress.get(intent.identifier)
            if progress is None:
                if intent.source_step != world.step:
                    continue
                progress = _MarketOrderProgress(
                    intent.order,
                    self._order_quantity(intent.order),
                )
                self._market_progress[intent.identifier] = progress
            else:
                self._validate_stable_order(intent.order, progress.order)
                progress.planned_quantity = min(
                    progress.planned_quantity,
                    progress.acknowledged_quantity
                    + progress.inflight_quantity
                    + self._order_quantity(intent.order),
                )
            remaining = (
                progress.planned_quantity
                - progress.acknowledged_quantity
                - progress.inflight_quantity
            )
            if remaining <= 0 or intent.source_step > world.step:
                continue
            candidates.append((intent.identifier, progress, remaining))
        selected = candidates[:10]
        inflight = []
        result = []
        for identifier, progress, remaining in selected:
            order = self._remaining_order(progress.order, remaining)
            result.append(order)
            if order[0] == "BUY_SEED":
                progress.inflight_quantity += remaining
                inflight.append(identifier)
            else:
                progress.acknowledged_quantity += remaining
        self._inflight_seed_orders = tuple(inflight)
        return tuple(result)

    @staticmethod
    def _order_quantity(order):
        if len(order) == 3:
            return order[2]
        return 1

    @staticmethod
    def _remaining_order(order, remaining):
        if len(order) == 3:
            return (*order[:2], remaining)
        return order

    @staticmethod
    def _validate_stable_order(current, registered):
        current_key = current[:2] if len(current) == 3 else current
        registered_key = registered[:2] if len(registered) == 3 else registered
        if current_key != registered_key:
            raise WholeFarmSolveError("market order identity changed")

    @staticmethod
    def _market_state(world):
        data = getattr(world, "data", None)
        if type(data) is not str:
            return None
        values = json.loads(data)
        seeds = values["private"]["seeds"]
        tiles = values["farms"][world.player]["tiles"]
        plants = frozenset(
            (y, x, tile["crop"], tile["planted_day"])
            for y, row in enumerate(tiles)
            for x, tile in enumerate(row)
            if isinstance(tile, Mapping) and tile.get("kind") == "PLANT"
        )
        return seeds, plants

    def _acknowledge_seed_orders(self, world):
        state = self._market_state(world)
        if state is None:
            return
        seeds, plants = state
        if self._last_seed_state is not None and self._last_plants is not None:
            new_plants = plants - self._last_plants
            accepted_by_crop = {
                crop: max(
                    0,
                    seeds.get(crop, 0)
                    - self._last_seed_state.get(crop, 0)
                    + sum(plant[2] == crop for plant in new_plants),
                )
                for crop in seeds
            }
            for identifier in self._inflight_seed_orders:
                progress = self._market_progress[identifier]
                crop = progress.order[1]
                accepted = min(
                    progress.inflight_quantity,
                    accepted_by_crop.get(crop, 0),
                )
                progress.acknowledged_quantity += accepted
                progress.inflight_quantity = 0
                accepted_by_crop[crop] -= accepted
        self._last_seed_state = dict(seeds)
        self._last_plants = plants
        self._inflight_seed_orders = ()


class WholeFarmControlProvider:
    def __init__(
        self,
        registered_seed=3_980_000,
        time_limit=30.0,
        mip_rel_gap=0.0,
        max_iterations=5,
        horizon=None,
    ):
        if horizon is None:
            horizon = PlanningHorizonConfig()
        if type(horizon) is not PlanningHorizonConfig:
            raise TypeError("horizon must be a PlanningHorizonConfig")
        self._settings = (
            registered_seed,
            time_limit,
            mip_rel_gap,
            max_iterations,
            horizon,
        )
        self._source = None
        self._loaded = None
        self._agent = None
        self.reset()

    @property
    def source(self):
        return self._source

    def reset(self):
        self._source = WholeFarmHandoffSource(*self._settings)
        self._loaded = load_artifact(AGENT2_TEMPLATE)
        strategy_module = next(
            module
            for module in self._loaded.package_modules
            if module.__name__ == "agent_2.strategy"
        )
        seam = _Agent2Seam(self._source, strategy_module.CropStrategy)
        self._agent = self._loaded.module._adapter.create_agent(
            economy_factory=lambda: seam,
            strategy_factory=lambda: seam,
        )

    def act(self, observation):
        action = self._agent(observation)
        step = int(observation.get("step", observation["day"] * 24 + observation["hour"]))
        if self._source.last_error is not None:
            raise self._source.last_error
        if step % 24 == 0:
            traces = self._source.traces
            if not traces or traces[-1].observed.source_step != step:
                raise WholeFarmSolveError("daily solve was not retained")
        return action
