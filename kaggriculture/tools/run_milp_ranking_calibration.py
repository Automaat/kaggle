import argparse
import gzip
import hashlib
import importlib.metadata
import json
import math
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from economics.market_ledger import CROPS
from economics.milp_oracle import solve_oracle, verify_result
from economics.run_milp_oracle import registered_input
from runner import ROOT, load_agent, run_match


RUNTIME = "agents_2.0.x/round37_1_task_graph"
CHAMPION = "agents_1.0.x/v1_14_0_central_herd.py"
SEEDS = (3_980_101, 3_980_102, 3_980_103)
PORTFOLIOS = (
    ("frozen-melon", (0, 0, 0, 0, 13)),
    ("milp-carrot-melon", (0, 9, 0, 0, 4)),
    ("all-carrot", (0, 13, 0, 0, 0)),
    ("all-strawberry", (0, 0, 0, 13, 0)),
    ("mixed", (3, 4, 0, 3, 3)),
)
FROZEN_HERD_TILES = 12


class PortfolioPlanner:
    def __init__(self, strategy_type, counts):
        self._strategy_type = strategy_type
        self._counts = counts
        self._player = None
        self._targets = None

    def reset(self):
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
        return self._strategy_type(targets) if targets else None

    def _build_targets(self, tiles):
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
            for crop, count in zip(CROPS, self._counts)
            for _ in range(count)
        )
        return tuple(zip(available[: len(crops)], crops))


def _module(loaded, name):
    return next(module for module in loaded.package_modules if module.__name__ == name)


def _portfolio_agent(counts):
    loaded = load_agent(RUNTIME)
    adapter = _module(loaded, "agent_2.adapter")
    strategy = _module(loaded, "agent_2.strategy")
    planner = PortfolioPlanner(strategy.CropStrategy, counts)
    return adapter.create_agent(strategy_factory=lambda: planner)


def _file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _reported_path(path):
    path = Path(path)
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def _tree_hash(path):
    digest = hashlib.sha256()
    path = Path(path)
    for source in sorted(path.rglob("*.py")):
        digest.update(str(source.relative_to(path)).encode())
        digest.update(b"\0")
        digest.update(source.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _crop_counts(replay, seat, step_index=24):
    if len(replay["steps"]) <= step_index:
        return {}
    observation = replay["steps"][step_index][seat]["observation"]
    counts = Counter()
    for row in observation["farms"][seat]["tiles"]:
        for tile in row:
            if isinstance(tile, dict) and tile.get("kind") == "PLANT":
                counts[tile["crop"]] += 1
    return dict(sorted(counts.items()))


def _requested_counts(counts):
    return {crop: count for crop, count in zip(CROPS, counts) if count}


def _average_ranks(values):
    result = [0.0] * len(values)
    ordered = sorted(range(len(values)), key=lambda index: values[index])
    position = 0
    while position < len(ordered):
        end = position + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[position]]:
            end += 1
        rank = (position + end - 1) / 2 + 1
        for index in ordered[position:end]:
            result[index] = rank
        position = end
    return tuple(result)


def spearman(values, outcomes):
    if len(values) != len(outcomes) or len(values) < 2:
        raise ValueError("rank vectors must have equal length above one")
    left = _average_ranks(values)
    right = _average_ranks(outcomes)
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum(
        (a - left_mean) * (b - right_mean) for a, b in zip(left, right)
    )
    left_scale = sum((value - left_mean) ** 2 for value in left)
    right_scale = sum((value - right_mean) ** 2 for value in right)
    if left_scale == 0 or right_scale == 0:
        return None
    return numerator / math.sqrt(left_scale * right_scale)


def summarize(forecasts, games):
    names = tuple(name for name, _counts in PORTFOLIOS)
    forecast_values = tuple(forecasts[name]["terminal_cash"] for name in names)
    groups = []
    for seed in SEEDS:
        for seat in (0, 1):
            selected = [
                game
                for game in games
                if game["seed"] == seed and game["candidate_seat"] == seat
            ]
            indexed = {game["portfolio"]: game for game in selected}
            outcomes = tuple(indexed[name]["candidate_reward"] for name in names)
            top_index = max(range(len(names)), key=lambda index: forecast_values[index])
            best = max(outcomes)
            worst = min(outcomes)
            regret = best - outcomes[top_index]
            groups.append(
                {
                    "seed": seed,
                    "candidate_seat": seat,
                    "spearman": spearman(forecast_values, outcomes),
                    "forecast_top": names[top_index],
                    "realized_top": names[max(range(len(names)), key=outcomes.__getitem__)],
                    "top_regret": regret,
                    "normalized_top_regret": regret / (best - worst) if best > worst else 0.0,
                    "forecast_top_minus_frozen": outcomes[top_index]
                    - outcomes[names.index("frozen-melon")],
                }
            )
    realized_means = {
        name: sum(
            game["candidate_reward"]
            for game in games
            if game["portfolio"] == name
        )
        / sum(game["portfolio"] == name for game in games)
        for name in names
    }
    absolute_errors = tuple(
        abs(forecasts[game["portfolio"]]["terminal_cash"] - game["candidate_reward"])
        for game in games
    )
    planned_units = sum(sum(counts) for _name, counts in PORTFOLIOS) * len(SEEDS) * 2
    executed_units = 0
    exact_executions = 0
    for game in games:
        requested = game["requested_day_1_crops"]
        executed = game["executed_day_1_crops"]
        executed_units += sum(
            min(count, executed.get(crop, 0)) for crop, count in requested.items()
        )
        exact_executions += requested == executed
    group_spearman = tuple(
        group["spearman"] for group in groups if group["spearman"] is not None
    )
    aggregate_spearman = spearman(
        forecast_values,
        tuple(realized_means[name] for name in names),
    )
    solver_failures = sum(not value["success"] for value in forecasts.values())
    simulator_failures = sum(
        game["candidate_status"] != "DONE" or game["champion_status"] != "DONE"
        for game in games
    )
    metrics = {
        "initial_regime_mean_absolute_terminal_cash_error": sum(absolute_errors)
        / len(absolute_errors),
        "mean_group_spearman": sum(group_spearman) / len(group_spearman)
        if group_spearman
        else None,
        "portfolio_mean_spearman": aggregate_spearman,
        "mean_top_regret": sum(group["top_regret"] for group in groups) / len(groups),
        "mean_normalized_top_regret": sum(
            group["normalized_top_regret"] for group in groups
        )
        / len(groups),
        "mean_forecast_top_minus_frozen": sum(
            group["forecast_top_minus_frozen"] for group in groups
        )
        / len(groups),
        "planned_units": planned_units,
        "executed_units": executed_units,
        "planned_versus_executed_ratio": executed_units / planned_units,
        "exact_portfolio_executions": exact_executions,
        "solver_failures": solver_failures,
        "simulator_failures": simulator_failures,
        "solver_timeouts": sum(
            not value["success"] and "time" in value["message"].lower()
            for value in forecasts.values()
        ),
    }
    gates = {
        "all_solver_runs_optimal": solver_failures == 0
        and all(value["mip_gap"] == 0 for value in forecasts.values()),
        "all_simulator_games_done": simulator_failures == 0,
        "planned_versus_executed_at_least_0_90": metrics[
            "planned_versus_executed_ratio"
        ]
        >= 0.90,
        "mean_group_spearman_at_least_0_50": metrics["mean_group_spearman"]
        is not None
        and metrics["mean_group_spearman"] >= 0.50,
        "portfolio_mean_spearman_at_least_0_50": aggregate_spearman is not None
        and aggregate_spearman >= 0.50,
        "mean_normalized_regret_at_most_0_25": metrics[
            "mean_normalized_top_regret"
        ]
        <= 0.25,
        "forecast_top_beats_frozen": metrics["mean_forecast_top_minus_frozen"] > 0,
        "intermediate_day_calibration_complete": False,
        "varied_market_regime_calibration_complete": False,
    }
    return {
        "metrics": metrics,
        "groups": groups,
        "realized_portfolio_means": realized_means,
        "gates": gates,
        "a2b_promoted": all(gates.values()),
    }


def _forecast(name, counts, time_limit, mip_rel_gap):
    data = registered_input()
    result = solve_oracle(data, time_limit, mip_rel_gap, counts)
    errors = verify_result(data, result, counts)
    return name, {
        "counts": _requested_counts(counts),
        "success": result.success,
        "status": result.status,
        "message": result.message,
        "mip_gap": result.mip_gap,
        "wall_seconds": result.wall_seconds,
        "terminal_cash": result.terminal_cash,
        "incremental_crop_profit": result.incremental_crop_profit,
        "verification_errors": errors,
        "decisions": tuple(asdict(value) for value in result.decisions),
    }


def _game(name, counts, seed, candidate_seat, replay_dir):
    candidate = _portfolio_agent(counts)
    agents = (candidate, CHAMPION) if candidate_seat == 0 else (CHAMPION, candidate)
    env, rewards, statuses = run_match(*agents, seed=seed, debug=True)
    replay = env.toJSON()
    replay_name = f"round39_9a_{name}_{seed}_seat_{candidate_seat}.json.gz"
    replay_path = replay_dir / replay_name
    encoded = json.dumps(replay, separators=(",", ":")).encode()
    replay_path.write_bytes(gzip.compress(encoded, mtime=0))
    champion_seat = 1 - candidate_seat
    return {
        "portfolio": name,
        "seed": seed,
        "candidate_seat": candidate_seat,
        "candidate_reward": rewards[candidate_seat],
        "champion_reward": rewards[champion_seat],
        "candidate_status": statuses[candidate_seat],
        "champion_status": statuses[champion_seat],
        "requested_day_1_crops": _requested_counts(counts),
        "executed_day_1_crops": _crop_counts(replay, candidate_seat),
        "replay": _reported_path(replay_path),
        "replay_sha256": _file_hash(replay_path),
    }


def run(output, replay_dir, time_limit=120.0, mip_rel_gap=0.0):
    if any(sum(counts) != 13 for _name, counts in PORTFOLIOS):
        raise ValueError("every portfolio must contain 13 crops")
    forecasts = dict(
        _forecast(name, counts, time_limit, mip_rel_gap)
        for name, counts in PORTFOLIOS
    )
    games = tuple(
        _game(name, counts, seed, seat, replay_dir)
        for seed in SEEDS
        for seat in (0, 1)
        for name, counts in PORTFOLIOS
    )
    summary = summarize(forecasts, games)
    document = {
        "schema": 1,
        "scope": "a2a-initial-state-ranking-calibration",
        "source_commit": "ea73017",
        "runtime": RUNTIME,
        "runtime_sha256": _tree_hash(ROOT / RUNTIME),
        "champion": CHAMPION,
        "champion_sha256": _file_hash(ROOT / CHAMPION),
        "model_sha256": _file_hash(ROOT / "tools/economics/milp_oracle.py"),
        "tool_sha256": _file_hash(Path(__file__)),
        "kaggle_environments_version": importlib.metadata.version(
            "kaggle-environments"
        ),
        "seeds": SEEDS,
        "portfolios": {
            name: _requested_counts(counts) for name, counts in PORTFOLIOS
        },
        "forecasts": forecasts,
        "games": games,
        "calibration_coverage": {
            "initial_day": True,
            "intermediate_days": False,
            "market_regimes": ("initial-default",),
            "ledger_transition_error_measured": False,
        },
        **summary,
    }
    encoded = json.dumps(document, indent=2, sort_keys=True)
    Path(output).write_text(encoded + "\n")
    return document


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--replay-dir", default=str(ROOT / "replays"))
    parser.add_argument("--time-limit", type=float, default=120.0)
    parser.add_argument("--mip-rel-gap", type=float, default=0.0)
    args = parser.parse_args()
    replay_dir = Path(args.replay_dir)
    replay_dir.mkdir(parents=True, exist_ok=True)
    result = run(args.output, replay_dir, args.time_limit, args.mip_rel_gap)
    print(json.dumps({"metrics": result["metrics"], "gates": result["gates"]}, indent=2))
    if result["metrics"]["solver_failures"] or result["metrics"][
        "simulator_failures"
    ]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
