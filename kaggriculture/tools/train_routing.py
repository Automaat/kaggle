"""Train the masked routing policy with terminal self-play rewards."""

import argparse
import importlib.util
import json
import math
import os
import pathlib
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from kaggle_environments import make

from runner import load_agent
from variants import _environment

ROOT = pathlib.Path(__file__).resolve().parent.parent
INITIAL_WEIGHTS = [2.0, 0.0, 0.5, 0.0, 0.0, 0.0, 0.0] + [0.0] * 12


def _load_policy(settings, identity, candidate):
    name = f"routing_policy_{os.getpid()}_{identity}"
    with _environment(settings):
        if candidate != "main.py":
            loaded = load_agent(candidate)
            policy = loaded.module._policy_agent.policy
            return loaded, policy.baseline.module
        specification = importlib.util.spec_from_file_location(name, ROOT / "main.py")
        module = importlib.util.module_from_spec(specification)
        sys.modules[name] = module
        specification.loader.exec_module(module)
    return module.agent, module


def _land_opponent(opponent, land):
    if not opponent.startswith("variant:"):
        return opponent
    settings = opponent.removeprefix("variant:").split(";")
    settings = [setting for setting in settings if not setting.startswith("KAGG_LAND=")]
    return "variant:" + ";".join([f"KAGG_LAND={land}", *settings])


def _episode(job):
    weights, mode, temperature, opponent, candidate, land, seed, seat, policy_seed = job
    settings = {
        "KAGG_ROUTE_RL": "1",
        "KAGG_ROUTE_RL_TRAIN": "1",
        "KAGG_ROUTE_RL_MODE": mode,
        "KAGG_ROUTE_RL_TEMPERATURE": str(temperature),
        "KAGG_ROUTE_RL_SEED": str(policy_seed),
        "KAGG_ROUTE_RL_WEIGHTS": ",".join(str(value) for value in weights),
        "AGENT2_LAND": str(land),
    }
    policy, stats_module = _load_policy(settings, f"{seed}_{seat}_{policy_seed}", candidate)
    rival = load_agent(_land_opponent(opponent, land))
    agents = [policy, rival] if seat == 0 else [rival, policy]
    environment = make("kaggriculture", configuration={"episodeSteps": 720, "seed": seed})
    environment.run(agents)
    rewards = [state.reward for state in environment.steps[-1]]
    stats = stats_module._route_rl_stats.get(seat, {})
    return {
        "seed": seed,
        "seat": seat,
        "reward": rewards[seat] - rewards[1 - seat],
        "gradient": stats.get("gradient", [0.0] * len(weights)),
        "choices": stats.get("choices", 0),
        "entropy": stats.get("entropy", 0.0),
    }


def _batch(weights, mode, temperature, opponent, candidate, lands, seeds, workers, iteration):
    jobs = [
        (
            weights, mode, temperature, opponent, candidate,
            lands[index % len(lands)], seed, seat,
            iteration * 100_000 + seed * 2 + seat,
        )
        for index, seed in enumerate(seeds) for seat in (0, 1)
    ]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_episode, jobs, chunksize=1))
    grouped = {}
    for result in results:
        grouped.setdefault(result["seed"], []).append(result)
    paired = {
        seed: statistics.mean(result["reward"] for result in rows)
        for seed, rows in grouped.items()
    }
    centre = statistics.mean(paired.values())
    spread = statistics.pstdev(paired.values()) or 1.0
    gradient = [0.0] * len(weights)
    choices = 0
    entropy = 0.0
    for seed, rows in grouped.items():
        advantage = max(-3.0, min(3.0, (paired[seed] - centre) / spread))
        for result in rows:
            count = max(1, result["choices"])
            for index, value in enumerate(result["gradient"]):
                gradient[index] += advantage * value / count
            choices += result["choices"]
            entropy += result["entropy"]
    scale = max(1, len(results))
    gradient = [value / scale for value in gradient]
    return centre, spread, gradient, choices, entropy / max(1, choices)


def _arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", default="main.py")
    parser.add_argument("--mode", choices=("basic", "zones", "memory"), required=True)
    parser.add_argument("--opponent", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--iterations", type=int, default=8)
    parser.add_argument("--batch", type=int, default=12)
    parser.add_argument("--seed-start", type=int, default=740_000)
    parser.add_argument("--workers", type=int, default=os.cpu_count())
    parser.add_argument("--learning-rate", type=float, default=0.08)
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--initial-weights")
    parser.add_argument("--land-curriculum", default="2")
    return parser.parse_args()


def main():
    args = _arguments()
    weights = (
        [float(value) for value in args.initial_weights.split(",")]
        if args.initial_weights else list(INITIAL_WEIGHTS)
    )
    lands = [int(value) for value in args.land_curriculum.split(",")]
    first_moment = [0.0] * len(weights)
    second_moment = [0.0] * len(weights)
    history = []
    cursor = args.seed_start
    for iteration in range(1, args.iterations + 1):
        seeds = list(range(cursor, cursor + args.batch))
        cursor += args.batch
        reward, spread, gradient, choices, entropy = _batch(
            weights, args.mode, args.temperature, args.opponent, args.candidate,
            lands, seeds, args.workers, iteration,
        )
        for index, value in enumerate(gradient):
            first_moment[index] = 0.9 * first_moment[index] + 0.1 * value
            second_moment[index] = 0.999 * second_moment[index] + 0.001 * value * value
            corrected_first = first_moment[index] / (1 - 0.9 ** iteration)
            corrected_second = second_moment[index] / (1 - 0.999 ** iteration)
            weights[index] += args.learning_rate * corrected_first / (math.sqrt(corrected_second) + 1e-8)
            weights[index] = max(-8.0, min(8.0, weights[index]))
        row = {
            "iteration": iteration,
            "seed_start": seeds[0],
            "seed_end": seeds[-1],
            "paired_reward": reward,
            "reward_spread": spread,
            "choices": choices,
            "entropy": entropy,
            "weights": list(weights),
        }
        history.append(row)
        print(
            f"iteration {iteration:>2d} reward={reward:>+9.0f} "
            f"spread={spread:>8.0f} choices={choices:>6d} entropy={entropy:.3f}"
        )
        pathlib.Path(args.output).write_text(
            json.dumps(
                {
                    "mode": args.mode,
                    "candidate": args.candidate,
                    "land_curriculum": lands,
                    "weights": weights,
                    "history": history,
                },
                indent=2,
            ) + "\n"
        )
    print("weights=" + ",".join(f"{value:.8f}" for value in weights))


if __name__ == "__main__":
    main()
