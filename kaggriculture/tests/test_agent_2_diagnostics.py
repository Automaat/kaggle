import importlib.util
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))


def _equivalence():
    specification = importlib.util.spec_from_file_location(
        "agent_2_equivalence_test",
        TOOLS / "equivalence.py",
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _result(actions_compared, mismatches, statuses=("DONE", "DONE")):
    return {
        "seed": 7,
        "seat": 0,
        "mode": "exact" if actions_compared else "timing-only",
        "actions_compared": actions_compared,
        "mismatches": mismatches,
        "candidate_times_ns": [10, 20],
        "baseline_times_ns": [5, 10],
        "steps": [0, 1],
        "candidate_import_ns": 20,
        "baseline_import_ns": 10,
        "elapsed_ns": 100,
        "rewards": [100, 90],
        "statuses": statuses,
        "configuration": {"episodeSteps": 720},
    }


def test_exact_summary_keeps_mismatch_failure_contract():
    module = _equivalence()
    mismatch = {"step": 1}
    summary = module.summarize(
        [_result(True, [mismatch])],
        "candidate",
        "baseline",
        "opponent",
    )
    assert summary["mode"] == "exact"
    assert summary["actions_compared"] is True
    assert summary["mismatches"] == 1
    assert summary["first_mismatch"] == mismatch
    assert module._should_fail(summary) is True


def test_timing_summary_does_not_claim_action_comparison():
    module = _equivalence()
    summary = module.summarize(
        [_result(False, None)],
        "candidate",
        "baseline",
        "opponent",
    )
    assert summary["mode"] == "timing-only"
    assert summary["actions_compared"] is False
    assert summary["mismatches"] is None
    assert summary["first_mismatch"] is None
    assert summary["agent_cpu_ratio"] == 2.0
    assert module._should_fail(summary) is False


def test_timing_summary_still_fails_on_episode_failure():
    module = _equivalence()
    summary = module.summarize(
        [_result(False, None, ("ERROR", "DONE"))],
        "candidate",
        "baseline",
        "opponent",
    )
    assert summary["failures"] == 1
    assert module._should_fail(summary) is True


def test_real_timing_episode_calls_both_policies_without_comparison():
    module = _equivalence()
    result = module._one(
        (
            "agents_1.0.x/v1_13_0_rl_routing.py",
            "agents_1.0.x/v1_14_0_central_herd.py",
            "pass",
            3731900,
            0,
            "timing-only",
        )
    )
    assert result["statuses"] == ["DONE", "DONE"]
    assert result["mismatches"] is None
    assert result["actions_compared"] is False
    assert len(result["candidate_times_ns"]) == len(result["baseline_times_ns"])
    assert len(result["candidate_times_ns"]) == len(result["steps"])
