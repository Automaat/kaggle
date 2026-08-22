import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

import train_routing


def test_land_opponent_replaces_existing_land():
    opponent = "variant:KAGG_LAND=2;KAGG_MAX_HANDS=14"
    assert train_routing._land_opponent(opponent, 1) == (
        "variant:KAGG_LAND=1;KAGG_MAX_HANDS=14"
    )


def test_land_opponent_keeps_named_agent():
    assert train_routing._land_opponent("champion", 1) == "champion"
