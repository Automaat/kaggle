import importlib
import math
import pathlib
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

coordinator = importlib.import_module("economics.rolling_coordinator")


def _hash(domain, value):
    return coordinator.canonical_sha256(domain, value)


def _delta(domain, target, before, after):
    return coordinator.ObservedDelta(
        domain,
        target,
        _hash(f"{domain}-state", before),
        _hash(f"{domain}-state", after),
    )


def _effect(epoch, domain="topology", target=("tile", 4, 4), before="a", after="b"):
    return coordinator.ExpectedEffectRef(
        domain,
        target,
        _hash(f"{domain}-state", before),
        _hash(f"{domain}-state", after),
        epoch,
    )


def _signal(deltas=(), completed=(), failed=False):
    return coordinator.ExecutionSignal(tuple(deltas), tuple(completed), failed)


def _observation(
    step=0,
    shops=(),
    economy="economy-0",
    topology="topology-0",
    route="route-0",
    progress="progress-0",
    signal=None,
):
    return coordinator.RollingObservation(
        step,
        tuple(shops),
        _hash("economy", economy),
        _hash("topology", topology),
        _hash("route", route),
        _hash("progress", progress),
        signal or _signal(),
    )


def _economic(epoch, suffix="whole"):
    fingerprint = _hash("economic-plan", (epoch, suffix))
    return coordinator.EconomicPlanRef(
        fingerprint,
        _hash("crop-result", (epoch, suffix)),
        _hash("animal-result", (epoch, suffix)),
        _hash("investment-result", (epoch, suffix)),
        _hash("resource-profile", (epoch, suffix)),
        (f"order-{epoch}-{suffix}",),
        (f"animal-{epoch}-{suffix}",),
    )


def _space(epoch, economy, suffix="whole"):
    return coordinator.SpacePlanRef(
        _hash("space-plan", (epoch, suffix)),
        economy.fingerprint,
        (f"space-{epoch}-{suffix}",),
        (),
    )


def _routes(epoch, economy, space, suffix="whole", effects=()):
    return coordinator.RoutePlanRef(
        _hash("route-plan", (epoch, suffix)),
        economy.fingerprint,
        space.fingerprint,
        (f"route-{epoch}-{suffix}",),
        tuple(effects),
    )


class RecordingBackend:
    def __init__(self):
        self.calls = []
        self.reset_calls = 0
        self.fail_phase = None
        self.invalid_phase = None
        self.effects_by_epoch = {}

    def reset(self):
        self.reset_calls += 1
        if self.fail_phase == "reset":
            raise RuntimeError("reset failed")

    def solve_whole_farm(self, epoch, observation, forecast, window):
        self.calls.append(("whole", epoch, observation, forecast, window))
        if self.fail_phase == "whole":
            raise RuntimeError("whole failed")
        economy = _economic(epoch)
        space = _space(epoch, economy)
        routes = _routes(
            epoch,
            economy,
            space,
            effects=self.effects_by_epoch.get(epoch, ()),
        )
        if self.invalid_phase == "whole":
            space = _space(epoch, _economic(epoch, "wrong"))
        return economy, space, routes

    def repair_space(self, epoch, observation, economy, previous_space):
        self.calls.append(("space", epoch, observation, economy, previous_space))
        if self.fail_phase == "space":
            raise RuntimeError("space failed")
        if self.invalid_phase == "space":
            return _space(epoch, _economic(epoch, "wrong"), "repair")
        return _space(epoch, economy, "repair")

    def repair_routes(self, epoch, observation, economy, space, previous_routes):
        self.calls.append(
            ("routes", epoch, observation, economy, space, previous_routes)
        )
        if self.fail_phase == "routes":
            raise RuntimeError("routes failed")
        if self.invalid_phase == "routes":
            return _routes(epoch, _economic(epoch, "wrong"), space, "repair")
        return _routes(epoch, economy, space, "repair")


def _prepared(step=0, **values):
    backend = RecordingBackend()
    rolling = coordinator.RollingCoordinator(backend)
    observation = _observation(step=step, **values)
    intent = rolling.prepare(observation)
    assert isinstance(intent, coordinator.WholeFarmIntent)
    return backend, rolling, observation, intent


def _phases(backend):
    return [call[0] for call in backend.calls]


def test_canonical_hash_is_stable_and_domain_separated():
    left = {"b": [2, 3], "a": {"value": 1}}
    right = {"a": {"value": 1}, "b": [2, 3]}
    first = _hash("economy", left)
    assert first == _hash("economy", right)
    assert first != _hash("topology", right)
    assert len(first) == 64
    assert first == first.lower()
    int(first, 16)
    left["b"].append(4)
    assert first == _hash("economy", right)


@pytest.mark.parametrize(
    "value",
    (
        math.nan,
        math.inf,
        {"value": -math.inf},
        {"value": object()},
        {"value": {1, 2}},
    ),
)
def test_canonical_hash_rejects_unsafe_values(value):
    with pytest.raises((TypeError, ValueError)):
        _hash("invalid", value)


def test_effect_identifier_covers_epoch_and_exact_transition():
    first = _effect(0)
    assert first.identifier == _effect(0).identifier
    assert first.identifier != _effect(1).identifier
    assert first.identifier != _effect(0, after="c").identifier
    assert first.identifier == _hash(
        "expected-effect",
        (
            first.domain,
            first.target_key,
            first.pre_state_fingerprint,
            first.post_state_fingerprint,
            first.planning_epoch,
        ),
    )


@pytest.mark.parametrize(
    ("factory", "exception"),
    (
        (lambda: _delta("unknown", ("tile", 0, 0), "a", "b"), ValueError),
        (lambda: coordinator.ExecutionSignal((), ("",), False), ValueError),
        (lambda: coordinator.ExecutionSignal((), (), 1), TypeError),
        (lambda: _observation(step=-1), ValueError),
        (lambda: _observation(step=719), ValueError),
        (lambda: _observation(shops=("UNKNOWN",)), ValueError),
        (lambda: _observation(shops=("PET_CAFE",) * 9), ValueError),
    ),
)
def test_canonical_inputs_reject_invalid_values(factory, exception):
    with pytest.raises(exception):
        factory()


def test_execution_signal_rejects_duplicate_deltas_and_identifiers():
    delta = _delta("economy", "money", 3000, 2900)
    with pytest.raises(ValueError):
        _signal((delta, delta))
    with pytest.raises(ValueError):
        _signal(completed=("effect", "effect"))


def test_shop_signature_preserves_duplicates_and_ignores_order():
    first = _observation(shops=("PET_CAFE", "YARN_STORE", "PET_CAFE"))
    second = _observation(shops=("YARN_STORE", "PET_CAFE", "PET_CAFE"))
    assert first.shop_signature == (
        ("PET_CAFE", 2),
        ("YARN_STORE", 1),
    )
    assert first.shop_signature == second.shop_signature
    assert first.identity != second.identity


def test_initial_duplicate_and_progress_return_same_intent_object():
    backend, rolling, observation, intent = _prepared()
    assert intent.reasons == (coordinator.REASON_INITIAL,)
    duplicate = rolling.prepare(observation)
    progress = rolling.prepare(_observation(step=1, progress="progress-1"))
    assert duplicate is intent
    assert progress is intent
    assert _phases(backend) == ["whole"]
    assert rolling._last_observation.progress_fingerprint == _hash(
        "progress", "progress-1"
    )


def test_equal_step_change_and_step_regression_reset_epoch():
    backend, rolling, _observation_value, first = _prepared(step=3)
    changed = rolling.prepare(_observation(step=3, progress="changed"))
    assert isinstance(changed, coordinator.WholeFarmIntent)
    assert changed.epoch == first.epoch == 0
    assert changed.reasons == (coordinator.REASON_RESET,)
    assert backend.reset_calls == 1
    regressed = rolling.prepare(_observation(step=2))
    assert isinstance(regressed, coordinator.WholeFarmIntent)
    assert regressed.epoch == 0
    assert regressed.reasons == (coordinator.REASON_RESET,)
    assert backend.reset_calls == 2
    assert _phases(backend) == ["whole", "whole", "whole"]


def test_steps_71_72_and_718_use_exact_windows_and_coalesce_shop_change():
    backend, rolling, _observation_value, first = _prepared(step=71)
    assert first.window == coordinator.PlanningWindow(71, 71, 718)
    shop_delta = _delta("economy", ("shops",), "none", "pet")
    second = rolling.prepare(
        _observation(
            step=72,
            shops=("PET_CAFE",),
            economy="economy-1",
            signal=_signal((shop_delta,)),
        )
    )
    assert isinstance(second, coordinator.WholeFarmIntent)
    assert second.window == coordinator.PlanningWindow(95, 143, 718)
    assert second.epoch == 1
    assert second.reasons == tuple(
        sorted(
            (
                coordinator.REASON_ECONOMY_DIVERGED,
                coordinator.REASON_NEW_DAY,
                coordinator.REASON_SHOP_CHANGED,
            )
        )
    )
    assert _phases(backend) == ["whole", "whole"]
    terminal_backend, _terminal_rolling, _terminal_obs, terminal = _prepared(step=718)
    assert terminal.window == coordinator.PlanningWindow(718, 718, 718)
    assert _phases(terminal_backend) == ["whole"]


def test_reordered_shop_multiset_does_not_replan():
    shops = ("PET_CAFE", "YARN_STORE", "PET_CAFE")
    backend, rolling, _observation_value, first = _prepared(shops=shops)
    second = rolling.prepare(
        _observation(
            step=1,
            shops=("YARN_STORE", "PET_CAFE", "PET_CAFE"),
        )
    )
    assert second is first
    assert _phases(backend) == ["whole"]


def test_new_day_and_unmatched_economy_delta_each_solve_whole_farm():
    backend, rolling, _observation_value, first = _prepared()
    new_day = rolling.prepare(_observation(step=24))
    assert isinstance(new_day, coordinator.WholeFarmIntent)
    assert new_day.reasons == (coordinator.REASON_NEW_DAY,)
    economy_delta = _delta("economy", ("money",), 3000, 2900)
    diverged = rolling.prepare(
        _observation(
            step=25,
            economy="economy-1",
            signal=_signal((economy_delta,)),
        )
    )
    assert isinstance(diverged, coordinator.WholeFarmIntent)
    assert diverged.reasons == (coordinator.REASON_ECONOMY_DIVERGED,)
    assert (first.epoch, new_day.epoch, diverged.epoch) == (0, 1, 2)
    assert _phases(backend) == ["whole", "whole", "whole"]


def test_unmatched_topology_delta_repairs_space_then_routes():
    backend, rolling, _observation_value, first = _prepared()
    weed = _delta("topology", ("tile", 0, 0), None, "WEED")
    repaired = rolling.prepare(
        _observation(
            step=1,
            topology="topology-1",
            signal=_signal((weed,)),
        )
    )
    assert isinstance(repaired, coordinator.WholeFarmIntent)
    assert repaired.epoch == first.epoch + 1
    assert repaired.reasons == (coordinator.REASON_TOPOLOGY_CHANGED,)
    assert _phases(backend) == ["whole", "space", "routes"]
    assert repaired.routes.space_fingerprint == repaired.space.fingerprint


@pytest.mark.parametrize("failed", (False, True))
def test_route_delta_or_explicit_failure_repairs_only_routes(failed):
    backend, rolling, _observation_value, first = _prepared()
    deltas = ()
    route = "route-0"
    if not failed:
        deltas = (_delta("route", ("target", 1), "ready", "blocked"),)
        route = "route-1"
    repaired = rolling.prepare(
        _observation(
            step=1,
            route=route,
            signal=_signal(deltas, failed=failed),
        )
    )
    assert isinstance(repaired, coordinator.WholeFarmIntent)
    assert repaired.epoch == first.epoch + 1
    expected_reason = (
        coordinator.REASON_ROUTE_PRECONDITION_FAILED
        if failed
        else coordinator.REASON_ROUTE_CHANGED
    )
    assert repaired.reasons == (expected_reason,)
    assert _phases(backend) == ["whole", "routes"]


def test_simultaneous_daily_shop_economy_topology_and_route_changes_solve_once():
    backend, rolling, _observation_value, first = _prepared(step=71)
    deltas = (
        _delta("economy", ("shops",), "none", "pet"),
        _delta("topology", ("tile", 0, 0), None, "WEED"),
        _delta("route", ("target", 1), "ready", "blocked"),
    )
    combined = rolling.prepare(
        _observation(
            step=72,
            shops=("PET_CAFE",),
            economy="economy-1",
            topology="topology-1",
            route="route-1",
            signal=_signal(deltas, failed=True),
        )
    )
    assert isinstance(combined, coordinator.WholeFarmIntent)
    assert combined.epoch == first.epoch + 1
    assert _phases(backend) == ["whole", "whole"]
    assert combined.reasons == tuple(
        sorted(
            (
                coordinator.REASON_ECONOMY_DIVERGED,
                coordinator.REASON_NEW_DAY,
                coordinator.REASON_ROUTE_CHANGED,
                coordinator.REASON_ROUTE_PRECONDITION_FAILED,
                coordinator.REASON_SHOP_CHANGED,
                coordinator.REASON_TOPOLOGY_CHANGED,
            )
        )
    )


def test_exact_expected_effect_updates_state_without_backend_call():
    effect = _effect(0)
    backend = RecordingBackend()
    backend.effects_by_epoch[0] = (effect,)
    rolling = coordinator.RollingCoordinator(backend)
    first = rolling.prepare(_observation())
    observed = coordinator.ObservedDelta(
        effect.domain,
        effect.target_key,
        effect.pre_state_fingerprint,
        effect.post_state_fingerprint,
    )
    completed = rolling.prepare(
        _observation(
            step=1,
            topology="topology-1",
            signal=_signal((observed,), (effect.identifier,)),
        )
    )
    assert completed is first
    assert _phases(backend) == ["whole"]
    assert rolling._last_observation.source_step == 1


def test_expected_effect_does_not_hide_simultaneous_weed():
    effect = _effect(0)
    backend = RecordingBackend()
    backend.effects_by_epoch[0] = (effect,)
    rolling = coordinator.RollingCoordinator(backend)
    first = rolling.prepare(_observation())
    expected = coordinator.ObservedDelta(
        effect.domain,
        effect.target_key,
        effect.pre_state_fingerprint,
        effect.post_state_fingerprint,
    )
    weed = _delta("topology", ("tile", 0, 0), None, "WEED")
    repaired = rolling.prepare(
        _observation(
            step=1,
            topology="topology-mixed",
            signal=_signal((expected, weed), (effect.identifier,)),
        )
    )
    assert isinstance(repaired, coordinator.WholeFarmIntent)
    assert repaired.epoch == first.epoch + 1
    assert _phases(backend) == ["whole", "space", "routes"]


@pytest.mark.parametrize("case", ("stale", "mismatch", "replay"))
def test_stale_mismatched_and_replayed_effects_are_unexplained(case):
    effect = _effect(0)
    backend = RecordingBackend()
    backend.effects_by_epoch[0] = (effect,)
    rolling = coordinator.RollingCoordinator(backend)
    rolling.prepare(_observation())
    exact = coordinator.ObservedDelta(
        effect.domain,
        effect.target_key,
        effect.pre_state_fingerprint,
        effect.post_state_fingerprint,
    )
    if case == "replay":
        rolling.prepare(
            _observation(
                step=1,
                topology="topology-1",
                signal=_signal((exact,), (effect.identifier,)),
            )
        )
        delta = _delta("topology", effect.target_key, "b", "c")
        step = 2
        topology = "topology-2"
    elif case == "mismatch":
        delta = _delta("topology", effect.target_key, "a", "c")
        step = 1
        topology = "topology-1"
    else:
        stale = _effect(1)
        delta = coordinator.ObservedDelta(
            stale.domain,
            stale.target_key,
            stale.pre_state_fingerprint,
            stale.post_state_fingerprint,
        )
        effect = stale
        step = 1
        topology = "topology-1"
    result = rolling.prepare(
        _observation(
            step=step,
            topology=topology,
            signal=_signal((delta,), (effect.identifier,)),
        )
    )
    assert isinstance(result, coordinator.WholeFarmIntent)
    assert _phases(backend)[-2:] == ["space", "routes"]


def test_changed_domain_without_delta_is_invalid_and_does_not_advance_state():
    backend, rolling, first_observation, first = _prepared()
    result = rolling.prepare(_observation(step=1, topology="topology-1"))
    assert isinstance(result, coordinator.PlanFailure)
    assert result.last_committed_epoch == first.epoch
    assert rolling._last_observation is first_observation
    assert _phases(backend) == ["whole"]


@pytest.mark.parametrize("phase", ("whole", "space", "routes"))
def test_backend_failures_are_atomic_and_same_observation_retries(phase):
    backend, rolling, first_observation, first = _prepared()
    if phase == "whole":
        observation = _observation(step=24)
    elif phase == "space":
        delta = _delta("topology", ("tile", 0, 0), None, "WEED")
        observation = _observation(
            step=1,
            topology="topology-1",
            signal=_signal((delta,)),
        )
    else:
        delta = _delta("route", ("target", 1), "ready", "blocked")
        observation = _observation(
            step=1,
            route="route-1",
            signal=_signal((delta,)),
        )
    backend.fail_phase = phase
    failed = rolling.prepare(observation)
    assert isinstance(failed, coordinator.PlanFailure)
    assert failed.failed_phase == phase
    assert failed.attempted_epoch == first.epoch + 1
    assert failed.last_committed_epoch == first.epoch
    assert rolling._last_observation is first_observation
    backend.fail_phase = None
    retried = rolling.prepare(observation)
    assert isinstance(retried, coordinator.WholeFarmIntent)
    assert retried.epoch == first.epoch + 1
    assert rolling._last_observation is observation


@pytest.mark.parametrize("phase", ("whole", "space", "routes"))
def test_parent_mismatch_is_atomic(phase):
    backend, rolling, first_observation, first = _prepared()
    backend.invalid_phase = phase
    if phase == "whole":
        observation = _observation(step=24)
    elif phase == "space":
        delta = _delta("topology", ("tile", 0, 0), None, "WEED")
        observation = _observation(
            step=1,
            topology="topology-1",
            signal=_signal((delta,)),
        )
    else:
        delta = _delta("route", ("target", 1), "ready", "blocked")
        observation = _observation(
            step=1,
            route="route-1",
            signal=_signal((delta,)),
        )
    failed = rolling.prepare(observation)
    assert isinstance(failed, coordinator.PlanFailure)
    assert failed.last_committed_epoch == first.epoch
    assert rolling._last_observation is first_observation


def test_unsafe_backend_output_is_rejected():
    class UnsafeBackend(RecordingBackend):
        def solve_whole_farm(self, epoch, observation, forecast, window):
            self.calls.append(("whole", epoch, observation, forecast, window))
            return [], [], []

    backend = UnsafeBackend()
    rolling = coordinator.RollingCoordinator(backend)
    result = rolling.prepare(_observation())
    assert isinstance(result, coordinator.PlanFailure)
    assert result.last_committed_epoch is None
    assert rolling._last_observation is None


def test_reset_failure_leaves_coordinator_empty():
    backend, rolling, _observation_value, _intent = _prepared()
    backend.fail_phase = "reset"
    result = rolling.reset()
    assert isinstance(result, coordinator.PlanFailure)
    assert result.failed_phase == "reset"
    assert rolling._last_observation is None
    assert rolling._last_intent is None


def test_registered_runner_is_deterministic_and_atomic():
    runner = importlib.import_module("economics.run_rolling_coordinator")
    first = runner.run()
    second = runner.run()
    assert first == second
    assert first["status"] == "accepted-contract-only"
    assert first["sequence"]["failure_atomic"] is True
    assert first["sequence"]["parent_validation_errors"] == 0
    assert tuple(
        tuple(call) for call in first["sequence"]["expected_backend_calls"]
    ) == (
        ("whole", 0, 0),
        ("whole", 1, 24),
        ("whole", 2, 48),
        ("whole", 2, 48),
        ("whole", 3, 72),
        ("space", 4, 73),
        ("routes", 4, 73),
        ("whole", 5, 96),
        ("routes", 6, 97),
    )


def test_live_agent_does_not_import_standalone_coordinator():
    live = ROOT / "agents_2.0.x" / "round39_8_milp_rollout" / "agent_2"
    sources = tuple(live.rglob("*.py"))
    assert sources
    assert all("rolling_coordinator" not in path.read_text() for path in sources)
