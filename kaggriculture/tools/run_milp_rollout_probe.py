import argparse
import gzip
import hashlib
import importlib.metadata
import json
from collections import Counter
from pathlib import Path

from runner import ROOT, run_match


DEFAULT_CANDIDATE = "agents_2.0.x/round39_8_milp_rollout"
DEFAULT_CHAMPION = "agents_1.0.x/v1_14_0_central_herd.py"


def _file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_hash(path):
    digest = hashlib.sha256()
    for source in sorted(path.rglob("*.py")):
        digest.update(str(source.relative_to(path)).encode())
        digest.update(b"\0")
        digest.update(source.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _crop_counts(replay, seat, step_index):
    observation = replay["steps"][step_index][seat]["observation"]
    counts = Counter()
    for row in observation["farms"][seat]["tiles"]:
        for tile in row:
            if isinstance(tile, dict) and tile.get("kind") == "PLANT":
                counts[tile["crop"]] += 1
    return dict(sorted(counts.items()))


def _game(candidate, champion, seed, candidate_seat, replay_dir):
    agents = (candidate, champion) if candidate_seat == 0 else (champion, candidate)
    env, rewards, statuses = run_match(*agents, seed=seed, debug=True)
    replay = env.toJSON()
    name = f"round39_8_milp_vs_v1_14_0_{seed}_seat_{candidate_seat}.json.gz"
    replay_path = replay_dir / name
    encoded_replay = json.dumps(replay, separators=(",", ":")).encode()
    replay_path.write_bytes(gzip.compress(encoded_replay, mtime=0))
    candidate_index = candidate_seat
    champion_index = 1 - candidate_seat
    return {
        "candidate_seat": candidate_seat,
        "candidate_reward": rewards[candidate_index],
        "champion_reward": rewards[champion_index],
        "candidate_status": statuses[candidate_index],
        "champion_status": statuses[champion_index],
        "candidate_day_1_crops": _crop_counts(replay, candidate_index, 24),
        "candidate_final_crops": _crop_counts(
            replay,
            candidate_index,
            len(replay["steps"]) - 1,
        ),
        "replay": str(replay_path.relative_to(ROOT)),
        "replay_sha256": _file_hash(replay_path),
    }


def run(candidate, champion, seed, replay_dir):
    candidate_path = ROOT / candidate
    champion_path = ROOT / champion
    games = tuple(
        _game(candidate, champion, seed, seat, replay_dir) for seat in (0, 1)
    )
    differences = tuple(
        game["candidate_reward"] - game["champion_reward"] for game in games
    )
    return {
        "schema": 1,
        "scope": "milp-first-day-rollout-probe",
        "seed": seed,
        "candidate": candidate,
        "candidate_sha256": _tree_hash(candidate_path),
        "champion": champion,
        "champion_sha256": _file_hash(champion_path),
        "kaggle_environments_version": importlib.metadata.version(
            "kaggle-environments"
        ),
        "games": games,
        "candidate_mean_reward": sum(
            game["candidate_reward"] for game in games
        )
        / len(games),
        "champion_mean_reward": sum(game["champion_reward"] for game in games)
        / len(games),
        "mean_difference": sum(differences) / len(differences),
        "wins": sum(value > 0 for value in differences),
        "ties": sum(value == 0 for value in differences),
        "statistical_score_claim": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", default=DEFAULT_CANDIDATE)
    parser.add_argument("--champion", default=DEFAULT_CHAMPION)
    parser.add_argument("--seed", type=int, default=3_980_000)
    parser.add_argument("--output", required=True)
    parser.add_argument("--replay-dir", default=str(ROOT / "replays"))
    args = parser.parse_args()
    replay_dir = Path(args.replay_dir)
    replay_dir.mkdir(parents=True, exist_ok=True)
    result = run(args.candidate, args.champion, args.seed, replay_dir)
    encoded = json.dumps(result, indent=2, sort_keys=True)
    Path(args.output).write_text(encoded + "\n")
    print(encoded)
    if any(
        game["candidate_status"] != "DONE"
        or game["champion_status"] != "DONE"
        for game in result["games"]
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
