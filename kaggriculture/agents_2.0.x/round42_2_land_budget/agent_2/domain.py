from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class World:
    step: int
    player: int
    identity: str
    data: str
    overage_present: bool
    overage_value: object
