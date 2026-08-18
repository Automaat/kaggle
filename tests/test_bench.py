import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from bench import summarize


def test_ties_are_half_points_clustered_by_seed():
    results = [{
        "seed": 1,
        "candidate": (10, 20),
        "opponent": (10, 20),
        "statuses": ("DONE", "DONE", "DONE", "DONE"),
    }]
    summary = summarize("candidate", "opponent", results)
    assert summary["points"] == 0.5
    assert summary["win_lo"] == 0.5
    assert summary["win_hi"] == 0.5
