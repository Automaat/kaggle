from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from economics.animal_ledger import ANIMAL_SPECS
from economics.crop_ledger import CROP_SPECS
from economics.market_ledger import ANIMALS, CROPS, SHED_ITEMS
from economics.rolling_coordinator import canonical_sha256
from routing.offline_route_planner import (
    RouteExecutor,
    RouteFailure,
    RouteProblem,
    RouteTask,
    RouteUnit,
)


@runtime_checkable
class OfflineActionProvider(Protocol):
    def reset(self) -> None: ...

    def act(self, observation) -> dict: ...


class ExecutionHandoffSource(Protocol):
    def __call__(self, observation): ...


class ExecutionRouteError(RuntimeError):
    def __init__(self, phase, source_step, message):
        self.phase = phase
        self.source_step = source_step
        super().__init__(f"{phase} failed at step {source_step}: {message}")


@dataclass(frozen=True, slots=True)
class CropTargetView:
    day: int
    x: int
    y: int
    crop: str


@dataclass(frozen=True, slots=True)
class AnimalIntentView:
    identifier: str
    animal: str
    purchase_day: int
    placement_day: int


@dataclass(frozen=True, slots=True)
class SpaceTargetView:
    identifier: str
    animal: str
    x: int
    y: int
    mode: str
    placement_day: int


@dataclass(frozen=True, slots=True)
class MarketOrderView:
    identifier: str
    source_step: int
    order: tuple


@dataclass(frozen=True, slots=True)
class ExecutionHandoffView:
    label: str
    epoch: int
    source_step: int
    economic_fingerprint: str
    space_fingerprint: str
    crop_targets: tuple[CropTargetView, ...]
    animal_intents: tuple[AnimalIntentView, ...]
    space_targets: tuple[SpaceTargetView, ...]
    market_orders: tuple[MarketOrderView, ...]
    identity: str


@dataclass(frozen=True, slots=True)
class ObservationView:
    source_step: int
    day: int
    hour: int
    player: int
    board_size: int
    tiles: tuple[tuple[object, ...], ...]
    units: tuple[RouteUnit, ...]
    shed: tuple[tuple[str, int], ...]
    seeds: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class ConvertedExecution:
    observation: ObservationView
    handoff: ExecutionHandoffView
    tasks: tuple[RouteTask, ...]
    market_orders: tuple[tuple, ...]
    problem: RouteProblem


@dataclass(frozen=True, slots=True)
class _PendingCommand:
    unit_index: int
    identifier: str
    action: tuple
    inventory_before: tuple[tuple[str, int], ...]


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


def _validate_day(value, name):
    if type(value) is not int or not 0 <= value <= 29:
        raise ValueError(f"{name} must be in 0..29")


def _validate_position(x, y):
    if type(x) is not int or type(y) is not int:
        raise TypeError("target position must contain integers")
    if not 0 <= x < 10 or not 0 <= y < 10:
        raise ValueError("target position must be on board")


def _read_attribute(value, name):
    if not hasattr(value, name):
        raise TypeError(f"handoff lacks {name}")
    return getattr(value, name)


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze(item) for key, item in value.items()}
        )
    if type(value) in (list, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _plain_tile(tile):
    if isinstance(tile, Mapping):
        return {key: _plain_tile(value) for key, value in tile.items()}
    if type(tile) in (list, tuple):
        return tuple(_plain_tile(value) for value in tile)
    return tile


def _is_weed(tile):
    return tile == "WEED" or (
        isinstance(tile, Mapping) and tile.get("kind") == "WEED"
    )


def _positive_inventory(values, name):
    if not isinstance(values, Mapping):
        raise TypeError(f"{name} must be a mapping")
    entries = []
    for item, quantity in values.items():
        if item not in SHED_ITEMS and name != "seeds":
            raise ValueError(f"{name} contains unknown item")
        if name == "seeds" and item not in CROPS:
            raise ValueError("seeds contain unknown crop")
        if type(quantity) is not int or quantity < 0:
            raise ValueError(f"{name} quantities must be nonnegative integers")
        if quantity:
            entries.append((item, quantity))
    order = CROPS if name == "seeds" else SHED_ITEMS
    return tuple(sorted(entries, key=lambda entry: order.index(entry[0])))


def _source_step(observation):
    if "step" in observation:
        step = observation["step"]
    else:
        day = observation.get("day")
        hour = observation.get("hour")
        if type(day) is not int or type(hour) is not int:
            raise TypeError("observation day and hour must be integers")
        step = day * 24 + hour
    if type(step) is not int or not 0 <= step <= 718:
        raise ValueError("observation source step must be in 0..718")
    return step


def observe_execution(observation):
    if not isinstance(observation, Mapping):
        raise TypeError("observation must be a mapping")
    step = _source_step(observation)
    day = observation.get("day", step // 24)
    hour = observation.get("hour", step % 24)
    if type(day) is not int or type(hour) is not int:
        raise TypeError("observation day and hour must be integers")
    if day != step // 24 or hour != step % 24:
        raise ValueError("observation clock disagrees with source step")
    player = observation.get("player")
    farms = observation.get("farms")
    private = observation.get("private")
    if type(player) is not int or player not in (0, 1):
        raise ValueError("observation player must be 0 or 1")
    if not isinstance(farms, (list, tuple)) or len(farms) != 2:
        raise TypeError("observation farms must contain two players")
    if not isinstance(private, Mapping):
        raise TypeError("observation private state must be a mapping")
    farm = farms[player]
    if not isinstance(farm, Mapping):
        raise TypeError("player farm must be a mapping")
    raw_tiles = farm.get("tiles")
    if not isinstance(raw_tiles, (list, tuple)) or not raw_tiles:
        raise TypeError("farm tiles must be a nonempty square")
    board_size = len(raw_tiles)
    if any(not isinstance(row, (list, tuple)) or len(row) != board_size for row in raw_tiles):
        raise TypeError("farm tiles must be a square")
    if board_size != 10:
        raise ValueError("route execution requires a 10x10 board")
    tiles = tuple(
        tuple(_plain_tile(tile) for tile in row)
        for row in raw_tiles
    )
    farmer = farm.get("farmer")
    hands = farm.get("hands", [])
    if not isinstance(farmer, (list, tuple)) or len(farmer) != 2:
        raise TypeError("farmer position must be a pair")
    if not isinstance(hands, (list, tuple)):
        raise TypeError("hands must be a sequence")
    positions = (farmer, *hands)
    inventories = private.get("inventories", [])
    if not isinstance(inventories, (list, tuple)):
        raise TypeError("unit inventories must be a sequence")
    if len(inventories) != len(positions):
        raise ValueError("unit inventories must match current units")
    units = []
    for index, (position, inventory) in enumerate(
        zip(positions, inventories, strict=True)
    ):
        if not isinstance(position, (list, tuple)) or len(position) != 2:
            raise TypeError("unit position must be a pair")
        identifier = "farmer" if index == 0 else f"hand-{index - 1}"
        units.append(
            RouteUnit(
                identifier,
                (position[0], position[1]),
                _positive_inventory(inventory, "unit inventory"),
            )
        )
    return ObservationView(
        step,
        day,
        hour,
        player,
        board_size,
        tiles,
        tuple(units),
        _positive_inventory(private.get("shed", {}), "shed"),
        _positive_inventory(private.get("seeds", {}), "seeds"),
    )


def _target_tuple(raw, expected_type, convert):
    if type(raw) is not tuple:
        raise TypeError(f"{expected_type} must be a tuple")
    return tuple(convert(value) for value in raw)


def _crop_target(value):
    day = _read_attribute(value, "day")
    x = _read_attribute(value, "x")
    y = _read_attribute(value, "y")
    crop = _read_attribute(value, "crop")
    _validate_day(day, "crop target day")
    _validate_position(x, y)
    if crop not in CROPS:
        raise ValueError("crop target has unknown crop")
    return CropTargetView(day, x, y, crop)


def _animal_intent(value):
    identifier = _read_attribute(value, "identifier")
    animal = _read_attribute(value, "animal")
    purchase_day = _read_attribute(value, "purchase_day")
    placement_day = _read_attribute(value, "placement_day")
    _validate_text(identifier, "animal intent identifier")
    if animal not in ANIMALS:
        raise ValueError("animal intent has unknown animal")
    _validate_day(purchase_day, "animal purchase day")
    _validate_day(placement_day, "animal placement day")
    if placement_day < purchase_day:
        raise ValueError("animal placement precedes purchase")
    return AnimalIntentView(identifier, animal, purchase_day, placement_day)


def _space_target(value):
    identifier = _read_attribute(value, "identifier")
    animal = _read_attribute(value, "animal")
    x = _read_attribute(value, "x")
    y = _read_attribute(value, "y")
    mode = _read_attribute(value, "mode")
    placement_day = _read_attribute(value, "placement_day")
    _validate_text(identifier, "space target identifier")
    if animal not in ANIMALS:
        raise ValueError("space target has unknown animal")
    _validate_position(x, y)
    _validate_text(mode, "space target mode")
    if mode not in {
        "clear_weed",
        "dig_crop",
        "future_land",
        "use_empty",
        "use_structure",
        "wait_crop",
    }:
        raise ValueError("space target has unknown mode")
    _validate_day(placement_day, "space placement day")
    return SpaceTargetView(identifier, animal, x, y, mode, placement_day)


def _market_order(value):
    identifier = _read_attribute(value, "identifier")
    source_step = _read_attribute(value, "source_step")
    order = _read_attribute(value, "order")
    _validate_text(identifier, "market order identifier")
    if type(source_step) is not int or not 0 <= source_step <= 718:
        raise ValueError("market order step must be in 0..718")
    if type(order) is not tuple or not order or type(order[0]) is not str:
        raise TypeError("market order must be a nonempty tuple")
    return MarketOrderView(identifier, source_step, tuple(order))


def view_handoff(handoff, current_step):
    label = _read_attribute(handoff, "label")
    epoch = _read_attribute(handoff, "epoch")
    source_step = _read_attribute(handoff, "source_step")
    economic = _read_attribute(handoff, "economic_fingerprint")
    space = _read_attribute(handoff, "space_fingerprint")
    _validate_text(label, "handoff label")
    if type(epoch) is not int or epoch < 0:
        raise ValueError("handoff epoch must be nonnegative")
    if type(source_step) is not int or not 0 <= source_step <= current_step:
        raise ValueError("handoff source step must not be in the future")
    _validate_fingerprint(economic, "economic fingerprint")
    _validate_fingerprint(space, "space fingerprint")
    crop_targets = _target_tuple(
        _read_attribute(handoff, "crop_targets"),
        "crop targets",
        _crop_target,
    )
    animal_intents = _target_tuple(
        _read_attribute(handoff, "animal_intents"),
        "animal intents",
        _animal_intent,
    )
    space_targets = _target_tuple(
        _read_attribute(handoff, "space_targets"),
        "space targets",
        _space_target,
    )
    market_orders = _target_tuple(
        _read_attribute(handoff, "market_orders"),
        "market orders",
        _market_order,
    )
    animal_by_id = {intent.identifier: intent for intent in animal_intents}
    if len(animal_by_id) != len(animal_intents):
        raise ValueError("animal intent identifiers must be unique")
    if len({target.identifier for target in space_targets}) != len(space_targets):
        raise ValueError("space target identifiers must be unique")
    if len({(target.x, target.y) for target in space_targets}) != len(space_targets):
        raise ValueError("space target cells must be unique")
    for target in space_targets:
        intent = animal_by_id.get(target.identifier)
        if (
            intent is None
            or intent.animal != target.animal
            or intent.placement_day != target.placement_day
        ):
            raise ValueError("space target lacks matching animal intent")
    if {target.identifier for target in space_targets} != set(animal_by_id):
        raise ValueError("animal intents and space targets must match")
    if len({order.identifier for order in market_orders}) != len(market_orders):
        raise ValueError("market order identifiers must be unique")
    if len({(target.day, target.x, target.y) for target in crop_targets}) != len(
        crop_targets
    ):
        raise ValueError("crop target cells must be unique per day")
    data = (
        label,
        epoch,
        source_step,
        economic,
        space,
        tuple((value.day, value.x, value.y, value.crop) for value in crop_targets),
        tuple(
            (
                value.identifier,
                value.animal,
                value.purchase_day,
                value.placement_day,
            )
            for value in animal_intents
        ),
        tuple(
            (
                value.identifier,
                value.animal,
                value.x,
                value.y,
                value.mode,
                value.placement_day,
            )
            for value in space_targets
        ),
        tuple(
            (value.identifier, value.source_step, value.order)
            for value in market_orders
        ),
    )
    return ExecutionHandoffView(
        label,
        epoch,
        source_step,
        economic,
        space,
        crop_targets,
        animal_intents,
        space_targets,
        market_orders,
        canonical_sha256("execution-handoff-view", data),
    )


def _task_identifier(day, x, y, operation, subject):
    return f"day-{day}:{x}:{y}:{operation}:{subject}"


def _make_task(
    observation,
    x,
    y,
    operation,
    subject,
    priority,
    dependencies=(),
    requires=(),
    produces=(),
):
    identifier = _task_identifier(
        observation.day,
        x,
        y,
        operation,
        subject,
    )
    action = (operation, subject) if operation in ("PLANT", "PLACE") else (operation,)
    tile = observation.tiles[y][x]
    precondition = canonical_sha256(
        "execution-route-task-precondition",
        (observation.day, x, y, operation, subject, tile),
    )
    effect = canonical_sha256(
        "execution-route-task-effect",
        (identifier, action, dependencies, requires, produces),
    )
    remaining = 24 - observation.hour
    if observation.day == 29:
        remaining = 719 - observation.source_step
    return RouteTask(
        identifier,
        (x, y),
        action,
        priority,
        remaining,
        tuple(dependencies),
        tuple(requires),
        tuple(produces),
        precondition,
        effect,
    )


def _add_task(tasks, task, source_step):
    previous = tasks.get(task.identifier)
    if previous is not None and previous != task:
        raise ExecutionRouteError(
            "convert",
            source_step,
            f"conflicting task {task.identifier}",
        )
    tasks[task.identifier] = task
    return task.identifier


def _plant_tasks(observation, tasks, x, y, tile):
    crop = tile.get("crop")
    if crop not in CROP_SPECS:
        raise ValueError("plant tile has unknown crop")
    fertilizer = None
    age = observation.day - tile.get("planted_day", observation.day)
    spec = CROP_SPECS[crop]
    production_ages = tuple(
        spec.first_yield_day + index * spec.interval
        for index in range(spec.max_yield)
    ) if spec.ongoing else ()
    fertilizer_active = tile.get("fertilized_until_day", -1)
    if type(fertilizer_active) is not int:
        raise TypeError("plant fertilizer day must be an integer")
    fertilize = (
        observation.day < 29
        and fertilizer_active < observation.day
        and any(
            age < production_age <= age + 3
            and tile.get("planted_day", observation.day) + production_age <= 29
            for production_age in production_ages
        )
    )
    if fertilize:
        fertilizer = _add_task(
            tasks,
            _make_task(
                observation,
                x,
                y,
                "FERTILIZE",
                crop,
                2,
                requires=(("FERTILIZER", 1),),
            ),
            observation.source_step,
        )
    water = None
    if not tile.get("watered_today", False):
        water = _add_task(
            tasks,
            _make_task(
                observation,
                x,
                y,
                "WATER",
                crop,
                0 if tile.get("consecutive_unwatered", 0) else 2,
                (fertilizer,) if fertilizer is not None else (),
            ),
            observation.source_step,
        )
    yield_units = tile.get("yield_units", 0)
    if type(yield_units) is not int or yield_units < 0:
        raise ValueError("plant yield must be nonnegative integer")
    predicted = yield_units
    if water is not None and not spec.ongoing:
        window_start = (spec.max_yield_day + 1) // 2
        if window_start <= age <= spec.max_yield_day:
            bonus = 2 if fertilizer_active >= observation.day else 1
            predicted = min(spec.max_yield, predicted + bonus)
    if predicted > 0 and age >= spec.first_yield_day:
        harvest = _make_task(
            observation,
            x,
            y,
            "HARVEST",
            crop,
            5,
            tuple(
                dependency
                for dependency in (fertilizer, water)
                if dependency is not None
            ),
            produces=((crop, predicted),),
        )
        return _add_task(tasks, harvest, observation.source_step)
    return None


def _animal_tasks(observation, tasks, x, y, tile, dependency=None):
    animal = tile.get("animal")
    if animal not in ANIMAL_SPECS:
        raise ValueError("animal tile has unknown animal")
    feed = None
    if observation.day < 29 and not tile.get("fed_today", False):
        feed = _add_task(
            tasks,
            _make_task(
                observation,
                x,
                y,
                "FEED",
                animal,
                0 if tile.get("consecutive_unfed", 0) else 1,
                (dependency,) if dependency is not None else (),
                requires=(("WHEAT", 1),),
            ),
            observation.source_step,
        )
    if observation.day < 29 and not tile.get("cared_today", False):
        care_dependencies = (feed,) if feed is not None else (
            (dependency,) if dependency is not None else ()
        )
        _add_task(
            tasks,
            _make_task(
                observation,
                x,
                y,
                "CARE",
                animal,
                3,
                care_dependencies,
            ),
            observation.source_step,
        )
    yield_units = tile.get("yield_units", 0)
    if type(yield_units) is not int or yield_units < 0:
        raise ValueError("animal yield must be nonnegative integer")
    if yield_units:
        product = ANIMAL_SPECS[animal].product
        _add_task(
            tasks,
            _make_task(
                observation,
                x,
                y,
                "HARVEST",
                animal,
                5,
                produces=((product, yield_units),),
            ),
            observation.source_step,
        )
    if tile.get("fertilizer_available", False):
        _add_task(
            tasks,
            _make_task(
                observation,
                x,
                y,
                "COLLECT_FERTILIZER",
                animal,
                6,
                produces=(("FERTILIZER", 1),),
            ),
            observation.source_step,
        )


def _empty_animal_tasks(observation, tasks, target, tile, harvest_dependency):
    if tile == "LOCKED":
        return
    dependency = None
    if _is_weed(tile) or (
        isinstance(tile, Mapping) and tile.get("kind") == "PLANT" and target.mode == "dig_crop"
    ):
        subject = "WEED" if _is_weed(tile) else target.animal
        dependency = _add_task(
            tasks,
            _make_task(observation, target.x, target.y, "DIG", subject, 0),
            observation.source_step,
        )
        tile = None
    elif isinstance(tile, Mapping) and tile.get("kind") == "PLANT":
        crop = tile.get("crop")
        if (
            target.mode == "wait_crop"
            and crop in CROP_SPECS
            and not CROP_SPECS[crop].ongoing
            and harvest_dependency is not None
        ):
            dependency = harvest_dependency
            tile = None
        else:
            return
    structure = ANIMAL_SPECS[target.animal].structure
    matching = (
        isinstance(tile, Mapping)
        and tile.get("kind") == structure
        and "animal" not in tile
    )
    if not matching:
        if tile is not None:
            return
        build = "BUILD_COOP" if structure == "COOP" else "BUILD_PASTURE"
        dependency = _add_task(
            tasks,
            _make_task(
                observation,
                target.x,
                target.y,
                build,
                target.animal,
                7,
                (dependency,) if dependency is not None else (),
            ),
            observation.source_step,
        )
    place = _add_task(
        tasks,
        _make_task(
            observation,
            target.x,
            target.y,
            "PLACE",
            target.animal,
            1,
            (dependency,) if dependency is not None else (),
            requires=((target.animal, 1),),
        ),
        observation.source_step,
    )
    synthetic = {
        "animal": target.animal,
        "fed_today": False,
        "cared_today": False,
        "yield_units": 0,
        "fertilizer_available": False,
        "consecutive_unfed": 0,
    }
    _animal_tasks(
        observation,
        tasks,
        target.x,
        target.y,
        synthetic,
        place,
    )


def build_route_tasks(observation, handoff):
    if type(observation) is not ObservationView:
        raise TypeError("observation view has wrong type")
    if type(handoff) is not ExecutionHandoffView:
        raise TypeError("handoff view has wrong type")
    tasks = {}
    harvest_by_position = {}
    destroy_positions = {
        (target.x, target.y)
        for target in handoff.space_targets
        if target.placement_day <= observation.day and target.mode == "dig_crop"
    }
    crop_positions = {
        (target.x, target.y)
        for target in handoff.crop_targets
        if target.day == observation.day
    }
    if crop_positions & {
        (target.x, target.y)
        for target in handoff.space_targets
        if target.placement_day <= observation.day
    }:
        raise ValueError("crop and animal targets overlap")
    for y, row in enumerate(observation.tiles):
        for x, tile in enumerate(row):
            if _is_weed(tile):
                _add_task(
                    tasks,
                    _make_task(observation, x, y, "DIG", "WEED", 0),
                    observation.source_step,
                )
            elif isinstance(tile, Mapping) and tile.get("kind") == "PLANT":
                if (x, y) in destroy_positions:
                    continue
                harvest = _plant_tasks(observation, tasks, x, y, tile)
                if harvest is not None:
                    harvest_by_position[(x, y)] = harvest
            elif isinstance(tile, Mapping) and "animal" in tile:
                _animal_tasks(observation, tasks, x, y, tile)
    for target in handoff.crop_targets:
        if target.day != observation.day:
            continue
        tile = observation.tiles[target.y][target.x]
        if tile is not None and not _is_weed(tile):
            if (
                isinstance(tile, Mapping)
                and tile.get("kind") == "PLANT"
                and tile.get("crop") == target.crop
            ):
                continue
            raise ValueError("crop target contains incompatible tile")
        dependency = None
        if _is_weed(tile):
            dependency = _task_identifier(
                observation.day,
                target.x,
                target.y,
                "DIG",
                "WEED",
            )
        plant = _add_task(
            tasks,
            _make_task(
                observation,
                target.x,
                target.y,
                "PLANT",
                target.crop,
                8,
                (dependency,) if dependency is not None else (),
            ),
            observation.source_step,
        )
        _add_task(
            tasks,
            _make_task(
                observation,
                target.x,
                target.y,
                "WATER",
                target.crop,
                2,
                (plant,),
            ),
            observation.source_step,
        )
    for target in handoff.space_targets:
        if target.placement_day > observation.day:
            continue
        tile = observation.tiles[target.y][target.x]
        if isinstance(tile, Mapping) and "animal" in tile:
            if tile.get("animal") != target.animal:
                raise ValueError("space target contains a different animal")
            continue
        _empty_animal_tasks(
            observation,
            tasks,
            target,
            tile,
            harvest_by_position.get((target.x, target.y)),
        )
    return tuple(
        sorted(
            tasks.values(),
            key=lambda task: (
                task.priority,
                task.position[1],
                task.position[0],
                task.identifier,
            ),
        )
    )


def _market_orders(handoff, source_step, limit):
    result = tuple(
        intent.order
        for intent in handoff.market_orders
        if intent.source_step == source_step
    )
    return result[:limit]


def _route_precondition(observation, handoff, tasks):
    return canonical_sha256(
        "execution-route-precondition",
        (
            observation.day,
            handoff.identity,
            tuple(unit.identifier for unit in observation.units),
            tuple(
                (
                    task.identifier,
                    task.precondition_fingerprint,
                    task.effect_fingerprint,
                )
                for task in tasks
            ),
        ),
    )


def convert_execution(observation, handoff, shed_capacity=100, market_limit=10):
    view = observe_execution(observation)
    handoff_view = view_handoff(handoff, view.source_step)
    if type(shed_capacity) is not int or shed_capacity < 1:
        raise ValueError("shed capacity must be positive")
    if type(market_limit) is not int or market_limit < 1:
        raise ValueError("market limit must be positive")
    tasks = build_route_tasks(view, handoff_view)
    remaining = 24 - view.hour
    if view.day == 29:
        remaining = 719 - view.source_step
    problem = RouteProblem(
        view.source_step,
        view.board_size,
        view.units,
        view.shed,
        shed_capacity,
        tasks,
        remaining,
        _route_precondition(view, handoff_view, tasks),
    )
    return ConvertedExecution(
        view,
        handoff_view,
        tasks,
        _market_orders(handoff_view, view.source_step, market_limit),
        problem,
    )


def _counts(entries):
    return dict(entries)


def _purchase_quantities(orders):
    result = {}
    for order in orders:
        if len(order) < 3 or type(order[2]) is not int or order[2] <= 0:
            continue
        if order[0] == "BUY_PRODUCT" and order[1] in ("WHEAT", "FERTILIZER"):
            result[order[1]] = result.get(order[1], 0) + order[2]
        elif order[0] == "BUY_ANIMAL" and order[1] in ANIMALS:
            result[order[1]] = result.get(order[1], 0) + order[2]
        elif order[0] == "BUY_SEED" and order[1] in CROPS:
            key = f"seed:{order[1]}"
            result[key] = result.get(key, 0) + order[2]
    return result


def _requires_current_purchase(converted):
    available = _counts(converted.observation.shed)
    for unit in converted.observation.units:
        for item, quantity in unit.inventory:
            available[item] = available.get(item, 0) + quantity
    required = {}
    seeds = _counts(converted.observation.seeds)
    for task in converted.tasks:
        for item, quantity in task.requires:
            required[item] = required.get(item, 0) + quantity
        if task.action[0] == "PLANT":
            key = f"seed:{task.action[1]}"
            required[key] = required.get(key, 0) + 1
            available[key] = seeds.get(task.action[1], 0)
    purchases = _purchase_quantities(converted.market_orders)
    missing = {
        item: quantity - available.get(item, 0)
        for item, quantity in required.items()
        if quantity > available.get(item, 0)
    }
    if not missing:
        return False
    if all(purchases.get(item, 0) >= quantity for item, quantity in missing.items()):
        return True
    names = ", ".join(sorted(missing))
    raise ExecutionRouteError(
        "resources",
        converted.observation.source_step,
        f"missing current resources: {names}",
    )


def _pass_action(unit_count, market_orders):
    return {
        "farmer": ["PASS"],
        "hands": [["PASS"] for _index in range(max(0, unit_count - 1))],
        "market": [list(order) for order in market_orders],
    }


class ExecutionRouteProvider:
    def __init__(self, handoff_source, shed_capacity=100, market_limit=10):
        if not callable(handoff_source):
            raise TypeError("handoff source must be callable")
        if type(shed_capacity) is not int or shed_capacity < 1:
            raise ValueError("shed capacity must be positive")
        if type(market_limit) is not int or market_limit < 1:
            raise ValueError("market limit must be positive")
        self._source = handoff_source
        self._shed_capacity = shed_capacity
        self._market_limit = market_limit
        self._executor = RouteExecutor()
        self._pending = None
        self._planned_task_ids = frozenset()
        self._handoff_identity = None
        self._planned_day = None
        self._planned_unit_ids = ()
        self._plans_built = 0

    @property
    def plans_built(self):
        return self._plans_built

    @property
    def plan(self):
        return self._executor.plan

    @property
    def handoff_identity(self):
        return self._handoff_identity

    def reset(self):
        self._executor.reset()
        self._pending = None
        self._planned_task_ids = frozenset()
        self._handoff_identity = None
        self._planned_day = None
        self._planned_unit_ids = ()
        self._plans_built = 0
        reset = getattr(self._source, "reset", None)
        if reset is not None:
            if not callable(reset):
                raise TypeError("handoff source reset must be callable")
            reset()

    def _current_handoff(self, observation, step):
        try:
            handoff = self._source(_freeze(observation))
            return view_handoff(handoff, step)
        except Exception as error:
            if isinstance(error, ExecutionRouteError):
                raise
            raise ExecutionRouteError("handoff", step, str(error)) from error

    def _acknowledge(self, observation):
        if self._pending is None:
            return
        current = observe_execution(observation)
        if self._planned_day != current.day:
            self._executor.reset()
            self._pending = None
            return
        route_count = len(self._executor.plan.routes) if self._executor.plan is not None else 0
        if len(current.units) < route_count:
            self._executor.reset()
            self._pending = None
            return
        valid = True
        for pending in self._pending:
            inventory = _counts(current.units[pending.unit_index].inventory)
            before = _counts(pending.inventory_before)
            if pending.action[0] == "PICKUP":
                item, quantity = pending.action[1:]
                valid = valid and inventory.get(item, 0) >= before.get(item, 0) + quantity
            elif pending.action[0] == "DROP":
                valid = valid and not inventory
        positions = tuple(unit.position for unit in current.units[:route_count])
        identifiers = tuple(pending.identifier for pending in self._pending)
        precondition = (
            self._executor.plan.route_precondition_fingerprint
            if self._executor.plan is not None
            else ""
        )
        result = self._executor.acknowledge(
            identifiers,
            positions,
            precondition,
        )
        if isinstance(result, RouteFailure) or not valid:
            self._executor.reset()
        self._pending = None

    def _remaining_task_ids(self):
        plan = self._executor.plan
        if plan is None:
            return frozenset()
        result = set()
        for route, cursor in zip(plan.routes, self._executor.cursors, strict=True):
            for command in route.commands[cursor:]:
                if command.task_identifier in self._planned_task_ids:
                    result.add(command.task_identifier)
        return frozenset(result)

    def _needs_plan(self, converted):
        if self._executor.plan is None:
            return True
        if converted.observation.day != self._planned_day:
            return True
        if converted.handoff.identity != self._handoff_identity:
            return True
        unit_ids = tuple(unit.identifier for unit in converted.observation.units)
        if unit_ids != self._planned_unit_ids:
            return True
        current = frozenset(task.identifier for task in converted.tasks)
        return current != self._remaining_task_ids()

    def _prepare(self, converted):
        self._executor.reset()
        result = self._executor.prepare(converted.problem)
        if isinstance(result, RouteFailure):
            if any(order and order[0] == "HIRE" for order in converted.market_orders):
                return False
            raise ExecutionRouteError(
                result.phase,
                converted.observation.source_step,
                result.message,
            )
        self._planned_task_ids = frozenset(
            task.identifier for task in converted.tasks
        )
        self._handoff_identity = converted.handoff.identity
        self._planned_day = converted.observation.day
        self._planned_unit_ids = tuple(
            unit.identifier for unit in converted.observation.units
        )
        self._plans_built += 1
        return True

    def _dispatch(self, converted):
        positions = tuple(unit.position for unit in converted.observation.units)
        cursors = self._executor.cursors
        plan = self._executor.plan
        actions = self._executor.next_actions(positions)
        if isinstance(actions, RouteFailure):
            raise ExecutionRouteError(
                actions.phase,
                converted.observation.source_step,
                actions.message,
            )
        pending = []
        for unit_index, (route, cursor) in enumerate(
            zip(plan.routes, cursors, strict=True)
        ):
            if cursor >= len(route.commands):
                continue
            command = route.commands[cursor]
            pending.append(
                _PendingCommand(
                    unit_index,
                    command.identifier,
                    command.action,
                    converted.observation.units[unit_index].inventory,
                )
            )
        self._pending = tuple(pending)
        ordered = dict(actions)
        unit_ids = tuple(unit.identifier for unit in converted.observation.units)
        return {
            "farmer": list(ordered[unit_ids[0]]),
            "hands": [list(ordered[identifier]) for identifier in unit_ids[1:]],
            "market": [list(order) for order in converted.market_orders],
        }

    def act(self, observation):
        if not isinstance(observation, Mapping):
            raise TypeError("observation must be a mapping")
        step = _source_step(observation)
        try:
            self._acknowledge(observation)
            handoff = self._current_handoff(observation, step)
            converted = convert_execution(
                observation,
                handoff,
                self._shed_capacity,
                self._market_limit,
            )
            if _requires_current_purchase(converted):
                self._executor.reset()
                self._pending = None
                return _pass_action(
                    len(converted.observation.units),
                    converted.market_orders,
                )
            if self._needs_plan(converted) and not self._prepare(converted):
                return _pass_action(
                    len(converted.observation.units),
                    converted.market_orders,
                )
            return self._dispatch(converted)
        except ExecutionRouteError:
            raise
        except Exception as error:
            raise ExecutionRouteError("act", step, str(error)) from error
