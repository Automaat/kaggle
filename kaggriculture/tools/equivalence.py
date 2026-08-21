import argparse
import copy
import importlib.metadata
import json
import math
import os
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from runner import load_agent, run_match


ROOT = Path(__file__).resolve().parent.parent
BASELINE = "agents_1.0.x/v1_14_0_central_herd.py"
OPPONENT = "agents_1.0.x/v1_13_0_rl_routing.py"


def _plain(value):
    return json.loads(json.dumps(value))


def _one(job):
    candidate_path, baseline_path, opponent_path, seed, seat, mode = job
    start = time.perf_counter_ns()
    candidate = load_agent(candidate_path)
    candidate_import_ns = time.perf_counter_ns() - start
    start = time.perf_counter_ns()
    baseline = load_agent(baseline_path)
    baseline_import_ns = time.perf_counter_ns() - start
    actions_compared = mode == "exact"
    mismatches = [] if actions_compared else None
    candidate_times = []
    baseline_times = []
    steps = []

    def shadow(obs):
        step = int(obs.get("step", obs["day"] * 24 + obs["hour"]))
        candidate_obs = copy.deepcopy(obs)
        baseline_obs = copy.deepcopy(obs)
        start = time.perf_counter_ns()
        candidate_action = candidate(candidate_obs)
        candidate_times.append(time.perf_counter_ns() - start)
        start = time.perf_counter_ns()
        baseline_action = baseline(baseline_obs)
        baseline_times.append(time.perf_counter_ns() - start)
        steps.append(step)
        if actions_compared and candidate_action != baseline_action and not mismatches:
            mismatches.append({
                "seed": seed,
                "seat": seat,
                "step": step,
                "observation": _plain(obs),
                "candidate_action": candidate_action,
                "baseline_action": baseline_action,
                "candidate_modules": [
                    id(module) for module in getattr(candidate, "package_modules", ())
                ],
                "baseline_module": id(getattr(baseline, "module", baseline)),
            })
        return candidate_action

    agents = (shadow, opponent_path) if seat == 0 else (opponent_path, shadow)
    start = time.perf_counter_ns()
    environment, rewards, statuses = run_match(*agents, seed=seed)
    elapsed = time.perf_counter_ns() - start
    return {
        "seed": seed,
        "seat": seat,
        "mode": mode,
        "actions_compared": actions_compared,
        "mismatches": mismatches,
        "candidate_times_ns": candidate_times,
        "baseline_times_ns": baseline_times,
        "steps": steps,
        "candidate_import_ns": candidate_import_ns,
        "baseline_import_ns": baseline_import_ns,
        "elapsed_ns": elapsed,
        "rewards": rewards,
        "statuses": statuses,
        "configuration": _plain(environment.configuration),
    }


def _percentile(values, percentile):
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def summarize(results, candidate, baseline, opponent):
    candidate_times = [value for result in results for value in result["candidate_times_ns"]]
    baseline_times = [value for result in results for value in result["baseline_times_ns"]]
    actions_compared = all(result["actions_compared"] for result in results)
    mismatches = (
        [item for result in results for item in result["mismatches"]]
        if actions_compared else None
    )
    failures = sum(status != "DONE" for result in results for status in result["statuses"])
    first_candidate = [result["candidate_times_ns"][0] for result in results]
    first_baseline = [result["baseline_times_ns"][0] for result in results]
    warm_candidate = [
        value for result in results for value in result["candidate_times_ns"][1:]
    ]
    warm_baseline = [
        value for result in results for value in result["baseline_times_ns"][1:]
    ]
    day_candidate = [
        value for result in results
        for step, value in zip(result["steps"], result["candidate_times_ns"])
        if step % 24 == 0
    ]
    day_baseline = [
        value for result in results
        for step, value in zip(result["steps"], result["baseline_times_ns"])
        if step % 24 == 0
    ]
    candidate_total = sum(candidate_times)
    baseline_total = sum(baseline_times)
    candidate_rewards = [result["rewards"][result["seat"]] for result in results]
    opponent_rewards = [result["rewards"][1 - result["seat"]] for result in results]
    return {
        "candidate": candidate,
        "baseline": baseline,
        "opponent": opponent,
        "mode": "exact" if actions_compared else "timing-only",
        "actions_compared": actions_compared,
        "seed_start": min(result["seed"] for result in results),
        "seed_end": max(result["seed"] for result in results),
        "seeds": len({result["seed"] for result in results}),
        "episodes": len(results),
        "calls": len(candidate_times),
        "mismatches": len(mismatches) if mismatches is not None else None,
        "first_mismatch": mismatches[0] if mismatches else None,
        "failures": failures,
        "candidate_reward_mean": statistics.mean(candidate_rewards),
        "opponent_reward_mean": statistics.mean(opponent_rewards),
        "candidate_mean_ms": statistics.mean(candidate_times) / 1_000_000,
        "baseline_mean_ms": statistics.mean(baseline_times) / 1_000_000,
        "candidate_p99_ms": _percentile(candidate_times, 0.99) / 1_000_000,
        "baseline_p99_ms": _percentile(baseline_times, 0.99) / 1_000_000,
        "p99_overhead_ms": (
            _percentile(candidate_times, 0.99) - _percentile(baseline_times, 0.99)
        ) / 1_000_000,
        "candidate_warm_p99_ms": _percentile(warm_candidate, 0.99) / 1_000_000,
        "baseline_warm_p99_ms": _percentile(warm_baseline, 0.99) / 1_000_000,
        "candidate_first_p99_ms": _percentile(first_candidate, 0.99) / 1_000_000,
        "baseline_first_p99_ms": _percentile(first_baseline, 0.99) / 1_000_000,
        "candidate_day_boundary_p99_ms": _percentile(day_candidate, 0.99) / 1_000_000,
        "baseline_day_boundary_p99_ms": _percentile(day_baseline, 0.99) / 1_000_000,
        "candidate_worst_ms": max(candidate_times) / 1_000_000,
        "baseline_worst_ms": max(baseline_times) / 1_000_000,
        "candidate_total_s": candidate_total / 1_000_000_000,
        "baseline_total_s": baseline_total / 1_000_000_000,
        "agent_cpu_ratio": candidate_total / baseline_total,
        "candidate_cold_import_mean_ms": statistics.mean(
            result["candidate_import_ns"] for result in results
        ) / 1_000_000,
        "baseline_cold_import_mean_ms": statistics.mean(
            result["baseline_import_ns"] for result in results
        ) / 1_000_000,
        "environment_version": importlib.metadata.version("kaggle-environments"),
        "configuration": results[0]["configuration"],
    }


def _should_fail(summary):
    return bool(
        summary["failures"]
        or (summary["actions_compared"] and summary["mismatches"])
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate")
    parser.add_argument("--baseline", default=BASELINE)
    parser.add_argument("--opponent", default=OPPONENT)
    parser.add_argument("--seed-start", type=int, default=3_700_000)
    parser.add_argument("--seeds", type=int, default=1)
    parser.add_argument("--workers", type=int, default=os.cpu_count())
    parser.add_argument("--output")
    parser.add_argument("--timing-only", action="store_true")
    args = parser.parse_args()
    mode = "timing-only" if args.timing_only else "exact"
    jobs = [
        (args.candidate, args.baseline, args.opponent, seed, seat, mode)
        for seed in range(args.seed_start, args.seed_start + args.seeds)
        for seat in (0, 1)
    ]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(_one, jobs, chunksize=1))
    summary = summarize(results, args.candidate, args.baseline, args.opponent)
    encoded = json.dumps(summary, indent=2, sort_keys=True)
    print(encoded)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded + "\n")
    if _should_fail(summary):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
