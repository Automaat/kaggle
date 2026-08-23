import collections
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from scaling import summarize


def test_summary_uses_paired_seat_delta():
    rows = [
        collections.Counter(
            score=100, opponent_score=80, calls=2, unit_turns=10, movement=5, hire_orders=1,
        ),
        collections.Counter(
            score=90, opponent_score=100, calls=2, unit_turns=10, movement=3, hire_orders=1,
        ),
    ]
    result = summarize(rows)
    assert result["mean_delta"] == 5
    assert result["movement"] == 0.4
    assert result["daily_unit_turns"] == 120
    assert result["daily_hires"] == 12
    assert result["same_tile_work"] == 0
    assert result["work_gap_distance"] == 0


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
            calls=2,
            carrier_calls=1,
            multi_carrier_calls=1,
            carried_wheat_units=6,
            carried_wheat_turns=2,
            eod_calls=1,
            eod_carriers=2,
            eod_wheat=6,
        ),
        collections.Counter(score=1, opponent_score=1),
    ]
    result = summarize(rows)
    assert result["occupancy"] == 0.9
    assert result["missed_water"] == 0.125
    assert result["missed_feed"] == 0.5
    assert result["missed_care"] == 1.0
    assert result["carrier_calls"] == 0.5
    assert result["multi_carrier_calls"] == 0.5
    assert result["wheat_per_carrier"] == 3
    assert result["eod_carriers"] == 2
    assert result["eod_wheat"] == 6
