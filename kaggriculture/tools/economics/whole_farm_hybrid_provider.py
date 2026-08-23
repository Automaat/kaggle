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
from .whole_farm_backend import WholeFarmPlannerBackend, WholeFarmSolveError


ROOT = Path(__file__).resolve().parents[2]
AGENT2_TEMPLATE = ROOT / "agents_2.0.x/round39_8_milp_rollout"


class WholeFarmHandoffSource:
    def __init__(
        self,
        registered_seed=3_980_000,
        time_limit=30.0,
        mip_rel_gap=0.0,
        max_iterations=5,
    ):
        self._bridge = LiveSnapshotAdapter(registered_seed)
        self._backend = WholeFarmPlannerBackend(
            self._bridge.snapshot,
            time_limit,
            mip_rel_gap,
            max_iterations,
            "frozen-1.14",
        )
        self._coordinator = RollingCoordinator(self._backend)
        self._traces = []
        self._last_world_identity = None
        self._last_handoff = None

    @property
    def traces(self):
        return tuple(self._traces)

    @property
    def handoff(self):
        return self._last_handoff

    @property
    def backend(self):
        return self._backend

    def reset(self):
        self._bridge.reset()
        failure = self._coordinator.reset()
        if failure is not None:
            raise WholeFarmSolveError(failure.exception_text)
        self._traces.clear()
        self._last_world_identity = None
        self._last_handoff = None

    def __call__(self, world):
        observation = self._bridge.observe(world)
        if observation.identity == self._last_world_identity:
            return self._last_handoff
        intent = self._coordinator.prepare(observation)
        if isinstance(intent, PlanFailure):
            raise WholeFarmSolveError(intent.exception_text)
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
        self._last_world_identity = observation.identity
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

    def reset(self):
        pass

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
        planned = tuple(
            intent.order
            for intent in handoff.market_orders
            if intent.source_step == world.step
        )
        frozen_hires = tuple(
            order for order in frozen_orders if order and order[0] == "HIRE"
        )
        combined = planned + tuple(
            order for order in frozen_hires if order not in planned
        )
        return combined[:10] or frozen_orders


class WholeFarmControlProvider:
    def __init__(
        self,
        registered_seed=3_980_000,
        time_limit=30.0,
        mip_rel_gap=0.0,
        max_iterations=5,
    ):
        self._settings = (
            registered_seed,
            time_limit,
            mip_rel_gap,
            max_iterations,
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
        if step % 24 == 0:
            traces = self._source.traces
            if not traces or traces[-1].observed.source_step != step:
                raise WholeFarmSolveError("daily solve was not retained")
        return action
