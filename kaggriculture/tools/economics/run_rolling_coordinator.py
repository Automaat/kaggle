import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

try:
    from .rolling_coordinator import (
        EconomicPlanRef,
        ExecutionSignal,
        ExpectedEffectRef,
        ObservedDelta,
        PlanFailure,
        RollingCoordinator,
        RollingObservation,
        RoutePlanRef,
        SpacePlanRef,
        WholeFarmIntent,
        canonical_sha256,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.economics.rolling_coordinator import (
        EconomicPlanRef,
        ExecutionSignal,
        ExpectedEffectRef,
        ObservedDelta,
        PlanFailure,
        RollingCoordinator,
        RollingObservation,
        RoutePlanRef,
        SpacePlanRef,
        WholeFarmIntent,
        canonical_sha256,
    )


COMPARATOR_COMMIT = "b74a3ea"
COMPARATOR_SHA256 = "86951703eac27253938500eac664650c1e927d1b86b26ed84be008f24739d699"


def _hash(domain, value):
    return canonical_sha256(domain, value)


def _delta(domain, target, before, after):
    return ObservedDelta(
        domain,
        target,
        _hash(f"{domain}-state", before),
        _hash(f"{domain}-state", after),
    )


def _observation(
    step,
    shops,
    economy,
    topology,
    route,
    progress,
    deltas=(),
    completed=(),
    failed=False,
):
    return RollingObservation(
        step,
        shops,
        _hash("economy", economy),
        _hash("topology", topology),
        _hash("route", route),
        _hash("progress", progress),
        ExecutionSignal(deltas, completed, failed),
    )


def _economic(epoch):
    return EconomicPlanRef(
        _hash("economic-plan", epoch),
        _hash("crop-result", epoch),
        _hash("animal-result", epoch),
        _hash("investment-result", epoch),
        _hash("resource-profile", epoch),
        (f"order-{epoch}",),
        (f"animal-{epoch}",),
    )


def _space(epoch, economy, phase):
    return SpacePlanRef(
        _hash("space-plan", (epoch, phase)),
        economy.fingerprint,
        (f"space-{epoch}-{phase}",),
        (),
    )


def _routes(epoch, economy, space, phase, effects=()):
    return RoutePlanRef(
        _hash("route-plan", (epoch, phase)),
        economy.fingerprint,
        space.fingerprint,
        (f"route-{epoch}-{phase}",),
        effects,
    )


class RecordingBackend:
    def __init__(self):
        self.calls = []
        self.failed_epochs = set()

    def reset(self):
        self.calls.append({"phase": "reset"})

    def solve_whole_farm(self, epoch, observation, forecast, window):
        self.calls.append(
            {
                "phase": "whole",
                "epoch": epoch,
                "step": observation.source_step,
                "window": asdict(window),
                "shop_signature": forecast.open_shop_signature,
            }
        )
        if epoch == 2 and epoch not in self.failed_epochs:
            self.failed_epochs.add(epoch)
            raise RuntimeError("registered whole-farm failure")
        economy = _economic(epoch)
        space = _space(epoch, economy, "whole")
        effects = ()
        if epoch == 0:
            effects = (
                ExpectedEffectRef(
                    "topology",
                    ("tile", 4, 4),
                    _hash("topology-state", "empty"),
                    _hash("topology-state", "planted"),
                    epoch,
                ),
            )
        routes = _routes(epoch, economy, space, "whole", effects)
        return economy, space, routes

    def repair_space(self, epoch, observation, economy, previous_space):
        self.calls.append(
            {
                "phase": "space",
                "epoch": epoch,
                "step": observation.source_step,
                "previous": previous_space.fingerprint,
            }
        )
        return _space(epoch, economy, "repair")

    def repair_routes(self, epoch, observation, economy, space, previous_routes):
        self.calls.append(
            {
                "phase": "routes",
                "epoch": epoch,
                "step": observation.source_step,
                "previous": previous_routes.fingerprint,
            }
        )
        return _routes(epoch, economy, space, "repair")


def _event(label, result):
    if isinstance(result, PlanFailure):
        return {
            "label": label,
            "status": "failure",
            "phase": result.failed_phase,
            "attempted_epoch": result.attempted_epoch,
            "last_committed_epoch": result.last_committed_epoch,
        }
    if not isinstance(result, WholeFarmIntent):
        raise TypeError("coordinator returned an unsupported result")
    parents_valid = (
        result.space.economic_fingerprint == result.economy.fingerprint
        and result.routes.economic_fingerprint == result.economy.fingerprint
        and result.routes.space_fingerprint == result.space.fingerprint
    )
    if not parents_valid:
        raise ValueError("registered intent has invalid parents")
    return {
        "label": label,
        "status": "intent",
        "epoch": result.epoch,
        "creation_step": result.creation_step,
        "window": asdict(result.window),
        "reasons": result.reasons,
        "parents_valid": parents_valid,
    }


def _run_sequence():
    backend = RecordingBackend()
    rolling = RollingCoordinator(backend)
    events = []
    first = _observation(0, (), "e0", "t0", "r0", "p0")
    initial = rolling.prepare(first)
    events.append(_event("day-0-initial", initial))
    effect = initial.routes.pending_effects[0]
    planned = _observation(
        1,
        (),
        "e0",
        "t1",
        "r0",
        "p1",
        (effect.observed_delta(),),
        (effect.identifier,),
    )
    matched = rolling.prepare(planned)
    events.append(_event("day-0-expected-effect", matched))
    day_one = _observation(24, (), "e0", "t1", "r0", "p24")
    events.append(_event("day-1-replan", rolling.prepare(day_one)))
    day_two = _observation(48, (), "e0", "t1", "r0", "p48")
    failed = rolling.prepare(day_two)
    events.append(_event("day-2-injected-failure", failed))
    if rolling._last_observation is not day_one or rolling._next_epoch != 2:
        raise ValueError("failure changed committed coordinator state")
    events.append(_event("day-2-retry", rolling.prepare(day_two)))
    shop_delta = _delta("economy", ("shops",), (), ("PET_CAFE",))
    day_three = _observation(
        72,
        ("PET_CAFE",),
        "e3",
        "t1",
        "r0",
        "p72",
        (shop_delta,),
    )
    events.append(_event("day-3-shop-open", rolling.prepare(day_three)))
    weed_delta = _delta("topology", ("tile", 0, 0), None, "WEED")
    weed = _observation(
        73,
        ("PET_CAFE",),
        "e3",
        "t2",
        "r0",
        "p73",
        (weed_delta,),
    )
    events.append(_event("day-3-weed", rolling.prepare(weed)))
    day_four = _observation(
        96,
        ("PET_CAFE",),
        "e3",
        "t2",
        "r0",
        "p96",
    )
    events.append(_event("day-4-replan", rolling.prepare(day_four)))
    blocked = _observation(
        97,
        ("PET_CAFE",),
        "e3",
        "t2",
        "r0",
        "p97",
        failed=True,
    )
    events.append(_event("day-4-route-failure", rolling.prepare(blocked)))
    expected_calls = (
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
    actual_calls = tuple(
        (call["phase"], call["epoch"], call["step"])
        for call in backend.calls
    )
    if actual_calls != expected_calls:
        raise ValueError("registered backend call order changed")
    return {
        "events": events,
        "backend_calls": backend.calls,
        "expected_backend_calls": expected_calls,
        "failure_atomic": True,
        "parent_validation_errors": 0,
    }


def _source_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run():
    first = _run_sequence()
    second = _run_sequence()
    if first != second:
        raise ValueError("registered sequence is not deterministic")
    module = Path(__file__).with_name("rolling_coordinator.py")
    runner = Path(__file__)
    payload = {
        "experiment": "round39_15_rolling_coordinator",
        "status": "accepted-contract-only",
        "comparator": {
            "version": "1.14.0",
            "commit": COMPARATOR_COMMIT,
            "sha256": COMPARATOR_SHA256,
        },
        "scope": {
            "live_integration": False,
            "economic_ranking_claim": False,
            "simulator_score_claim": False,
            "runtime_claim": False,
        },
        "sequence": first,
        "model_sha256": _source_hash(module),
        "runner_sha256": _source_hash(runner),
    }
    encoded = json.dumps(payload, allow_nan=False, sort_keys=True)
    payload["deterministic_sha256"] = hashlib.sha256(encoded.encode()).hexdigest()
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    arguments = parser.parse_args()
    payload = run()
    encoded = json.dumps(payload, allow_nan=False, indent=2, sort_keys=True) + "\n"
    if arguments.output:
        Path(arguments.output).write_text(encoded)
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
