import collections
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from scaling import summarize


def test_summary_uses_paired_seat_delta():
    rows = [
        collections.Counter(score=100, opponent_score=80, unit_turns=10, movement=5),
        collections.Counter(score=90, opponent_score=100, unit_turns=10, movement=3),
    ]
    result = summarize(rows)
    assert result["mean_delta"] == 5
    assert result["movement"] == 0.4


def test_summary_reports_productive_and_neglect_rates():
    rows = [
        collections.Counter(
            score=1,
            opponent_score=1,
            owned_tile_days=10,
            productive_tile_days=9,
            plant_days=8,
            missed_water_days=1,
            animal_days=2,
            missed_feed_days=1,
            missed_care_days=2,
        ),
        collections.Counter(score=1, opponent_score=1),
    ]
    result = summarize(rows)
    assert result["occupancy"] == 0.9
    assert result["missed_water"] == 0.125
    assert result["missed_feed"] == 0.5
    assert result["missed_care"] == 1.0
