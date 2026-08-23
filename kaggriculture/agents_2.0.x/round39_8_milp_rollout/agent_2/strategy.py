import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CropStrategy:
    targets: tuple[tuple[int, int, str | None], ...]


class FrozenStrategyPlanner:
    def reset(self) -> None:
        pass

    def prepare(self, world):
        return None


MILP_DAY_ZERO_PORTFOLIO = (("CARROT", 9), ("MELON", 4))
FROZEN_HERD_TILES = 12


class MilpFirstDayStrategy:
    def __init__(self):
        self._player = None
        self._targets = None

    def reset(self) -> None:
        self._player = None
        self._targets = None

    def prepare(self, world):
        if world.step // 24 != 0:
            return None
        values = json.loads(world.data)
        tiles = values["farms"][world.player]["tiles"]
        if self._targets is None or self._player != world.player:
            self._player = world.player
            self._targets = self._build_targets(tiles)
        targets = tuple(
            (x, y, crop)
            for (x, y), crop in self._targets
            if tiles[y][x] is None
        )
        return CropStrategy(targets) if targets else None

    @staticmethod
    def _build_targets(tiles):
        size = len(tiles)
        half = size // 2
        empty = tuple(
            (x, y)
            for y, row in enumerate(tiles)
            for x, tile in enumerate(row)
            if tile is None
        )
        herd = set(
            sorted(
                (
                    position
                    for position in empty
                    if position[0] < half and position[1] < half
                ),
                key=lambda position: (
                    abs(position[0] - (half - 1))
                    + abs(position[1] - (half - 1)),
                    position,
                ),
            )[:FROZEN_HERD_TILES]
        )
        available = sorted(
            (position for position in empty if position not in herd),
            key=lambda position: (
                abs(position[0] - (half - 1)) + abs(position[1] - (half - 1)),
                position,
            ),
        )
        crops = tuple(
            crop
            for crop, quantity in MILP_DAY_ZERO_PORTFOLIO
            for _ in range(quantity)
        )
        return tuple(zip(available[: len(crops)], crops))
