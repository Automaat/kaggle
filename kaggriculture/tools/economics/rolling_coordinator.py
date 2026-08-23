import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from .shop_forecast import (
    ShopForecastInput,
    ShopForecastResult,
    forecast_shops,
    shop_signature,
    verify_forecast,
)


DOMAINS = frozenset({"economy", "topology", "route"})
REASON_INITIAL = "initial"
REASON_RESET = "reset"
REASON_NEW_DAY = "new-day"
REASON_SHOP_CHANGED = "shop-changed"
REASON_ECONOMY_DIVERGED = "economy-diverged"
REASON_TOPOLOGY_CHANGED = "topology-changed"
REASON_ROUTE_CHANGED = "route-changed"
REASON_ROUTE_PRECONDITION_FAILED = "route-precondition-failed"
REASON_EFFECT_DIVERGED = "effect-diverged"


def _freeze_canonical(value):
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("canonical numbers must be finite")
        return value
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise TypeError("canonical mapping keys must be text")
        return (
            "mapping",
            tuple(
                (key, _freeze_canonical(value[key]))
                for key in sorted(value)
            ),
        )
    if type(value) in (list, tuple):
        return ("sequence", tuple(_freeze_canonical(item) for item in value))
    raise TypeError("value is not JSON-compatible")


def canonical_sha256(domain, value):
    if type(domain) is not str or not domain:
        raise ValueError("hash domain must be nonempty text")
    encoded = json.dumps(
        {"domain": domain, "value": _freeze_canonical(value)},
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate_fingerprint(value, name):
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be canonical SHA-256")


def _validate_text(value, name):
    if type(value) is not str or not value:
        raise ValueError(f"{name} must be nonempty text")


def _freeze_target_key(value):
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("target key numbers must be finite")
        return value
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise TypeError("target mapping keys must be text")
        return (
            "mapping",
            tuple(
                (key, _freeze_target_key(value[key]))
                for key in sorted(value)
            ),
        )
    if type(value) in (list, tuple):
        return tuple(_freeze_target_key(item) for item in value)
    raise TypeError("target key is not JSON-compatible")


def _canonical_target_key(value):
    if isinstance(value, Mapping) and not value:
        raise ValueError("target key must be canonical and nonempty")
    frozen = _freeze_target_key(value)
    if type(frozen) is str and frozen:
        return frozen
    if type(frozen) is not tuple or not frozen:
        raise ValueError("target key must be canonical and nonempty")
    return frozen


def _validate_identifiers(values, name):
    if type(values) is not tuple:
        raise TypeError(f"{name} must be a tuple")
    if any(type(value) is not str or not value for value in values):
        raise ValueError(f"{name} must contain nonempty text")
    if len(set(values)) != len(values):
        raise ValueError(f"{name} must be unique")


def _validate_epoch(value, name="epoch"):
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be nonnegative")


@dataclass(frozen=True, slots=True)
class ObservedDelta:
    domain: str
    target_key: str | tuple
    pre_state_fingerprint: str
    post_state_fingerprint: str

    def __post_init__(self):
        if self.domain not in DOMAINS:
            raise ValueError("unknown delta domain")
        object.__setattr__(
            self,
            "target_key",
            _canonical_target_key(self.target_key),
        )
        _validate_fingerprint(self.pre_state_fingerprint, "pre-state fingerprint")
        _validate_fingerprint(self.post_state_fingerprint, "post-state fingerprint")
        if self.pre_state_fingerprint == self.post_state_fingerprint:
            raise ValueError("delta states must differ")


@dataclass(frozen=True, slots=True)
class ExpectedEffectRef:
    domain: str
    target_key: str | tuple
    pre_state_fingerprint: str
    post_state_fingerprint: str
    epoch: int
    identifier: str | None = None

    def __post_init__(self):
        object.__setattr__(
            self,
            "target_key",
            _canonical_target_key(self.target_key),
        )
        ObservedDelta(
            self.domain,
            self.target_key,
            self.pre_state_fingerprint,
            self.post_state_fingerprint,
        )
        _validate_epoch(self.epoch, "planning epoch")
        expected = canonical_sha256(
            "expected-effect",
            (
                self.domain,
                self.target_key,
                self.pre_state_fingerprint,
                self.post_state_fingerprint,
                self.epoch,
            ),
        )
        if self.identifier is None:
            object.__setattr__(self, "identifier", expected)
        elif self.identifier != expected:
            raise ValueError("effect identifier mismatch")

    def observed_delta(self):
        return ObservedDelta(
            self.domain,
            self.target_key,
            self.pre_state_fingerprint,
            self.post_state_fingerprint,
        )

    @property
    def planning_epoch(self):
        return self.epoch


@dataclass(frozen=True, slots=True)
class ExecutionSignal:
    observed_deltas: tuple[ObservedDelta, ...] = ()
    completed_effect_ids: tuple[str, ...] = ()
    route_precondition_failed: bool = False

    def __post_init__(self):
        if type(self.observed_deltas) is not tuple:
            raise TypeError("observed deltas must be a tuple")
        if any(type(delta) is not ObservedDelta for delta in self.observed_deltas):
            raise TypeError("observed deltas have wrong type")
        if len(set(self.observed_deltas)) != len(self.observed_deltas):
            raise ValueError("observed deltas must be unique")
        _validate_identifiers(self.completed_effect_ids, "completed effect identifiers")
        if type(self.route_precondition_failed) is not bool:
            raise TypeError("route precondition flag must be boolean")


def _delta_value(delta):
    return (
        delta.domain,
        delta.target_key,
        delta.pre_state_fingerprint,
        delta.post_state_fingerprint,
    )


@dataclass(frozen=True, slots=True)
class RollingObservation:
    source_step: int
    open_shops: tuple[str, ...]
    economy_fingerprint: str
    topology_fingerprint: str
    route_precondition_fingerprint: str
    progress_fingerprint: str
    execution_signal: ExecutionSignal = ExecutionSignal()

    def __post_init__(self):
        if type(self.source_step) is not int:
            raise TypeError("source step must be an integer")
        if self.source_step < 0 or self.source_step > 718:
            raise ValueError("source step must be in 0..718")
        if type(self.open_shops) is not tuple:
            raise TypeError("open shops must be a tuple")
        if len(self.open_shops) > 8:
            raise ValueError("open shops exceed the shop cap")
        shop_signature(self.open_shops)
        _validate_fingerprint(self.economy_fingerprint, "economy fingerprint")
        _validate_fingerprint(self.topology_fingerprint, "topology fingerprint")
        _validate_fingerprint(
            self.route_precondition_fingerprint,
            "route-precondition fingerprint",
        )
        _validate_fingerprint(self.progress_fingerprint, "progress fingerprint")
        if type(self.execution_signal) is not ExecutionSignal:
            raise TypeError("execution signal has wrong type")

    @property
    def open_shop_signature(self):
        return shop_signature(self.open_shops)

    @property
    def shop_signature(self):
        return self.open_shop_signature

    @property
    def identity(self):
        signal = self.execution_signal
        return canonical_sha256(
            "rolling-observation",
            (
                self.source_step,
                self.open_shops,
                self.economy_fingerprint,
                self.topology_fingerprint,
                self.route_precondition_fingerprint,
                self.progress_fingerprint,
                tuple(_delta_value(delta) for delta in signal.observed_deltas),
                signal.completed_effect_ids,
                signal.route_precondition_failed,
            ),
        )


@dataclass(frozen=True, slots=True)
class EconomicPlanRef:
    fingerprint: str
    crop_result_fingerprint: str
    animal_result_fingerprint: str
    investment_result_fingerprint: str
    resource_profile_fingerprint: str
    order_intent_ids: tuple[str, ...]
    animal_purchase_intent_ids: tuple[str, ...]

    def __post_init__(self):
        _validate_fingerprint(self.fingerprint, "economic fingerprint")
        _validate_fingerprint(self.crop_result_fingerprint, "crop result fingerprint")
        _validate_fingerprint(
            self.animal_result_fingerprint,
            "animal result fingerprint",
        )
        _validate_fingerprint(
            self.investment_result_fingerprint,
            "investment result fingerprint",
        )
        _validate_fingerprint(
            self.resource_profile_fingerprint,
            "resource profile fingerprint",
        )
        _validate_identifiers(self.order_intent_ids, "order intent identifiers")
        _validate_identifiers(
            self.animal_purchase_intent_ids,
            "animal purchase intent identifiers",
        )


@dataclass(frozen=True, slots=True)
class SpacePlanRef:
    fingerprint: str
    economic_fingerprint: str
    spatial_task_ids: tuple[str, ...]
    rejected_animal_intent_ids: tuple[str, ...]

    def __post_init__(self):
        _validate_fingerprint(self.fingerprint, "space fingerprint")
        _validate_fingerprint(
            self.economic_fingerprint,
            "space economic fingerprint",
        )
        _validate_identifiers(self.spatial_task_ids, "spatial task identifiers")
        _validate_identifiers(
            self.rejected_animal_intent_ids,
            "rejected animal intent identifiers",
        )


@dataclass(frozen=True, slots=True)
class RoutePlanRef:
    fingerprint: str
    economic_fingerprint: str
    space_fingerprint: str
    route_ids: tuple[str, ...]
    pending_effects: tuple[ExpectedEffectRef, ...]

    def __post_init__(self):
        _validate_fingerprint(self.fingerprint, "route fingerprint")
        _validate_fingerprint(
            self.economic_fingerprint,
            "route economic fingerprint",
        )
        _validate_fingerprint(self.space_fingerprint, "route space fingerprint")
        _validate_identifiers(self.route_ids, "route identifiers")
        if type(self.pending_effects) is not tuple:
            raise TypeError("pending effects must be a tuple")
        if any(type(effect) is not ExpectedEffectRef for effect in self.pending_effects):
            raise TypeError("pending effects have wrong type")
        identifiers = tuple(effect.identifier for effect in self.pending_effects)
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("pending effect identifiers must be unique")


@dataclass(frozen=True, slots=True)
class PlanningWindow:
    action_end_step: int
    strategy_end_step: int
    investment_end_step: int

    def __post_init__(self):
        values = (
            self.action_end_step,
            self.strategy_end_step,
            self.investment_end_step,
        )
        if any(type(value) is not int for value in values):
            raise TypeError("planning boundaries must be integers")
        if not 0 <= self.action_end_step <= self.strategy_end_step <= 718:
            raise ValueError("planning boundaries are out of order")
        if self.investment_end_step != 718:
            raise ValueError("investment boundary must equal 718")
        if self.strategy_end_step > self.investment_end_step:
            raise ValueError("strategy boundary exceeds investment boundary")

    @classmethod
    def from_forecast(cls, forecast):
        if type(forecast) is not ShopForecastResult:
            raise TypeError("forecast has wrong type")
        return cls(
            forecast.action_end_step,
            forecast.strategy_end_step,
            forecast.investment_end_step,
        )


def _validate_bundle(epoch, economy, space, routes):
    if type(economy) is not EconomicPlanRef:
        raise TypeError("economic plan has wrong type")
    if type(space) is not SpacePlanRef:
        raise TypeError("space plan has wrong type")
    if type(routes) is not RoutePlanRef:
        raise TypeError("route plan has wrong type")
    if space.economic_fingerprint != economy.fingerprint:
        raise ValueError("space plan has wrong economic parent")
    if routes.economic_fingerprint != economy.fingerprint:
        raise ValueError("route plan has wrong economic parent")
    if routes.space_fingerprint != space.fingerprint:
        raise ValueError("route plan has wrong space parent")
    rejected = set(space.rejected_animal_intent_ids)
    if rejected.intersection(economy.animal_purchase_intent_ids):
        raise ValueError("space plan rejects a selected animal intent")
    if any(effect.epoch != epoch for effect in routes.pending_effects):
        raise ValueError("pending effect has stale epoch")


@dataclass(frozen=True, slots=True)
class WholeFarmIntent:
    epoch: int
    creation_step: int
    window: PlanningWindow
    economy: EconomicPlanRef
    space: SpacePlanRef
    routes: RoutePlanRef
    reasons: tuple[str, ...]

    def __post_init__(self):
        _validate_epoch(self.epoch)
        if type(self.creation_step) is not int:
            raise TypeError("creation step must be an integer")
        if self.creation_step < 0 or self.creation_step > 718:
            raise ValueError("creation step must be in 0..718")
        if type(self.window) is not PlanningWindow:
            raise TypeError("planning window has wrong type")
        _validate_bundle(self.epoch, self.economy, self.space, self.routes)
        _validate_identifiers(self.reasons, "planning reasons")
        if self.reasons != tuple(sorted(self.reasons)):
            raise ValueError("planning reasons must be sorted")


@dataclass(frozen=True, slots=True)
class PlanFailure:
    phase: str
    exception_text: str
    attempted_epoch: int
    last_committed_epoch: int | None

    def __post_init__(self):
        _validate_text(self.phase, "failure phase")
        _validate_text(self.exception_text, "exception text")
        _validate_epoch(self.attempted_epoch, "attempted epoch")
        if self.last_committed_epoch is not None:
            _validate_epoch(self.last_committed_epoch, "last committed epoch")
            if self.last_committed_epoch >= self.attempted_epoch:
                raise ValueError("last committed epoch must precede attempted epoch")

    @property
    def failed_phase(self):
        return "whole" if self.phase == "whole-farm" else self.phase


class PlannerBackend(Protocol):
    def reset(self) -> None: ...

    def solve_whole_farm(
        self,
        epoch: int,
        observation: RollingObservation,
        forecast: ShopForecastResult,
        window: PlanningWindow,
    ) -> tuple[EconomicPlanRef, SpacePlanRef, RoutePlanRef]: ...

    def repair_space(
        self,
        epoch: int,
        observation: RollingObservation,
        economy: EconomicPlanRef,
        previous_space: SpacePlanRef,
    ) -> SpacePlanRef: ...

    def repair_routes(
        self,
        epoch: int,
        observation: RollingObservation,
        economy: EconomicPlanRef,
        space: SpacePlanRef,
        previous_routes: RoutePlanRef,
    ) -> RoutePlanRef: ...


class RollingCoordinator:
    def __init__(self, backend: PlannerBackend):
        for method in (
            "reset",
            "solve_whole_farm",
            "repair_space",
            "repair_routes",
        ):
            if not callable(getattr(backend, method, None)):
                raise TypeError("backend does not implement PlannerBackend")
        self._backend = backend
        self._last_observation = None
        self._last_intent = None
        self._next_epoch = 0
        self._acknowledged_effect_ids = frozenset()
        self._backend_reset_required = False
        self._pending_reset_reason = False

    def _failure(self, phase, error):
        text = f"{type(error).__name__}: {error}"
        last_epoch = None if self._last_intent is None else self._last_intent.epoch
        return PlanFailure(phase, text, self._next_epoch, last_epoch)

    def _clear(self):
        self._last_observation = None
        self._last_intent = None
        self._next_epoch = 0
        self._acknowledged_effect_ids = frozenset()

    def _reset_backend(self):
        try:
            self._backend.reset()
        except Exception as error:
            self._backend_reset_required = True
            self._pending_reset_reason = True
            return self._failure("reset", error)
        self._backend_reset_required = False
        self._pending_reset_reason = True
        return None

    def reset(self):
        self._clear()
        self._backend_reset_required = True
        self._pending_reset_reason = True
        return self._reset_backend()

    def _reset_before_prepare(self):
        self._clear()
        self._backend_reset_required = True
        self._pending_reset_reason = True
        return self._reset_backend()

    @staticmethod
    def _domain_fingerprints(observation):
        return {
            "economy": observation.economy_fingerprint,
            "topology": observation.topology_fingerprint,
            "route": observation.route_precondition_fingerprint,
        }

    def _validate_delta_coverage(self, observation):
        if self._last_observation is None:
            return
        previous = self._domain_fingerprints(self._last_observation)
        current = self._domain_fingerprints(observation)
        domains = {delta.domain for delta in observation.execution_signal.observed_deltas}
        for domain in DOMAINS:
            changed = previous[domain] != current[domain]
            if changed != (domain in domains):
                raise ValueError(f"{domain} delta coverage mismatch")

    def _match_effects(self, observation):
        signal = observation.execution_signal
        deltas = signal.observed_deltas
        if self._last_intent is None:
            return frozenset(), frozenset(), bool(signal.completed_effect_ids)
        pending = {
            effect.identifier: effect for effect in self._last_intent.routes.pending_effects
        }
        matched_delta_indexes = set()
        matched_effect_ids = set()
        invalid_effect = False
        for identifier in signal.completed_effect_ids:
            effect = pending.get(identifier)
            if (
                effect is None
                or effect.epoch != self._last_intent.epoch
                or identifier in self._acknowledged_effect_ids
            ):
                invalid_effect = True
                continue
            expected_delta = effect.observed_delta()
            index = next(
                (
                    candidate
                    for candidate, delta in enumerate(deltas)
                    if candidate not in matched_delta_indexes
                    and delta == expected_delta
                ),
                None,
            )
            if index is None:
                invalid_effect = True
                continue
            matched_delta_indexes.add(index)
            matched_effect_ids.add(identifier)
        return (
            frozenset(matched_delta_indexes),
            frozenset(matched_effect_ids),
            invalid_effect,
        )

    def _dirty_state(self, observation, reset_happened):
        previous = self._last_observation
        matched_indexes, matched_effect_ids, invalid_effect = self._match_effects(
            observation
        )
        unmatched = tuple(
            delta
            for index, delta in enumerate(observation.execution_signal.observed_deltas)
            if index not in matched_indexes
        )
        reasons = set()
        economy_dirty = previous is None
        if previous is None:
            reasons.add(REASON_RESET if reset_happened else REASON_INITIAL)
        else:
            if observation.source_step // 24 != previous.source_step // 24:
                economy_dirty = True
                reasons.add(REASON_NEW_DAY)
            if observation.open_shop_signature != previous.open_shop_signature:
                economy_dirty = True
                reasons.add(REASON_SHOP_CHANGED)
        if any(delta.domain == "economy" for delta in unmatched):
            economy_dirty = True
            reasons.add(REASON_ECONOMY_DIVERGED)
        topology_dirty = any(delta.domain == "topology" for delta in unmatched)
        if topology_dirty:
            reasons.add(REASON_TOPOLOGY_CHANGED)
        route_dirty = any(delta.domain == "route" for delta in unmatched)
        if route_dirty:
            reasons.add(REASON_ROUTE_CHANGED)
        if observation.execution_signal.route_precondition_failed:
            route_dirty = True
            reasons.add(REASON_ROUTE_PRECONDITION_FAILED)
        if invalid_effect:
            route_dirty = True
            reasons.add(REASON_EFFECT_DIVERGED)
        return (
            economy_dirty,
            topology_dirty,
            route_dirty,
            tuple(sorted(reasons)),
            matched_effect_ids,
        )

    def _commit(self, observation, intent):
        self._last_observation = observation
        self._last_intent = intent
        self._next_epoch += 1
        self._acknowledged_effect_ids = frozenset()
        self._pending_reset_reason = False
        return intent

    def _prepare_whole_farm(self, observation, reasons):
        forecast_input = ShopForecastInput(
            source_step=observation.source_step,
            open_shops=observation.open_shops,
        )
        try:
            forecast = forecast_shops(forecast_input)
            errors = verify_forecast(forecast_input, forecast)
            if errors:
                raise ValueError("; ".join(errors))
            window = PlanningWindow.from_forecast(forecast)
        except Exception as error:
            return self._failure("forecast", error)
        try:
            bundle = self._backend.solve_whole_farm(
                self._next_epoch,
                observation,
                forecast,
                window,
            )
            if type(bundle) is not tuple or len(bundle) != 3:
                raise TypeError("whole-farm result must be a three-item tuple")
            economy, space, routes = bundle
            _validate_bundle(self._next_epoch, economy, space, routes)
            intent = WholeFarmIntent(
                self._next_epoch,
                observation.source_step,
                window,
                economy,
                space,
                routes,
                reasons,
            )
        except Exception as error:
            return self._failure("whole-farm", error)
        return self._commit(observation, intent)

    def _prepare_space(self, observation, reasons):
        previous = self._last_intent
        try:
            space = self._backend.repair_space(
                self._next_epoch,
                observation,
                previous.economy,
                previous.space,
            )
            if type(space) is not SpacePlanRef:
                raise TypeError("space plan has wrong type")
            if space.economic_fingerprint != previous.economy.fingerprint:
                raise ValueError("space plan has wrong economic parent")
        except Exception as error:
            return self._failure("space", error)
        try:
            routes = self._backend.repair_routes(
                self._next_epoch,
                observation,
                previous.economy,
                space,
                previous.routes,
            )
            _validate_bundle(self._next_epoch, previous.economy, space, routes)
            intent = WholeFarmIntent(
                self._next_epoch,
                observation.source_step,
                previous.window,
                previous.economy,
                space,
                routes,
                reasons,
            )
        except Exception as error:
            return self._failure("routes", error)
        return self._commit(observation, intent)

    def _prepare_routes(self, observation, reasons):
        previous = self._last_intent
        try:
            routes = self._backend.repair_routes(
                self._next_epoch,
                observation,
                previous.economy,
                previous.space,
                previous.routes,
            )
            _validate_bundle(
                self._next_epoch,
                previous.economy,
                previous.space,
                routes,
            )
            intent = WholeFarmIntent(
                self._next_epoch,
                observation.source_step,
                previous.window,
                previous.economy,
                previous.space,
                routes,
                reasons,
            )
        except Exception as error:
            return self._failure("routes", error)
        return self._commit(observation, intent)

    def prepare(self, observation):
        if type(observation) is not RollingObservation:
            raise TypeError("observation must be RollingObservation")
        reset_happened = self._pending_reset_reason
        if self._backend_reset_required:
            failure = self._reset_backend()
            if failure is not None:
                return failure
            reset_happened = True
        if (
            self._last_observation is not None
            and observation.source_step == self._last_observation.source_step
            and observation.identity == self._last_observation.identity
        ):
            return self._last_intent
        if (
            self._last_observation is not None
            and observation.source_step <= self._last_observation.source_step
        ):
            failure = self._reset_before_prepare()
            if failure is not None:
                return failure
            reset_happened = True
        try:
            self._validate_delta_coverage(observation)
            (
                economy_dirty,
                space_dirty,
                routes_dirty,
                reasons,
                matched_effect_ids,
            ) = self._dirty_state(observation, reset_happened)
        except Exception as error:
            return self._failure("observation", error)
        if economy_dirty:
            return self._prepare_whole_farm(observation, reasons)
        if space_dirty:
            return self._prepare_space(observation, reasons)
        if routes_dirty:
            return self._prepare_routes(observation, reasons)
        self._last_observation = observation
        self._acknowledged_effect_ids = (
            self._acknowledged_effect_ids | matched_effect_ids
        )
        return self._last_intent
