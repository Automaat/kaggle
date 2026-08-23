from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CropStrategy:
    targets: tuple[tuple[int, int, str | None], ...]


class FrozenStrategyPlanner:
    def reset(self) -> None:
        pass

    def prepare(self, world):
        return None
