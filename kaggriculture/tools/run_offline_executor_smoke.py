import argparse
import copy
import gzip
import hashlib
import importlib.metadata
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from offline_executor import CallableActionProvider, make_provider_agent
from runner import load_agent, run_match


COMPARATOR = "agents_1.0.x/v1_14_0_central_herd.py"
COMPARATOR_COMMIT = "b74a3ea"
COMPARATOR_SHA256 = "86951703eac27253938500eac664650c1e927d1b86b26ed84be008f24739d699"
DEFAULT_SEED = 3_980_000


def _file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _provider_factory():
    agent = load_agent(COMPARATOR)
    return CallableActionProvider(agent)


def _stable_replay(replay, seed, candidate_seat):
    result = copy.deepcopy(replay)
    identity = hashlib.sha256(
        f"round39-16b:{seed}:{candidate_seat}".encode()
    ).hexdigest()
    result["id"] = identity
    return result


def _encoded_replay(replay):
    return json.dumps(
        replay,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _write_replay(replay, path):
    encoded = _encoded_replay(replay)
    compressed = gzip.compress(encoded, mtime=0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(compressed)
    decoded = gzip.decompress(path.read_bytes())
    if decoded != encoded or json.loads(decoded) != replay:
        raise ValueError("replay round trip failed")
    return hashlib.sha256(compressed).hexdigest(), len(compressed)


def _final_money(replay, seat):
    observation = replay["steps"][-1][seat]["observation"]
    return observation["farms"][seat]["money"]


def _game(seed, candidate_seat, replay_dir):
    candidate = make_provider_agent(_provider_factory)
    agents = (
        (candidate, COMPARATOR)
        if candidate_seat == 0
        else (COMPARATOR, candidate)
    )
    environment, rewards, statuses = run_match(*agents, seed=seed, debug=True)
    replay = _stable_replay(environment.toJSON(), seed, candidate_seat)
    if len(replay["steps"]) != 720:
        raise ValueError("registered replay must contain 720 steps")
    if statuses != ["DONE", "DONE"]:
        raise ValueError("registered game did not finish")
    final_money = tuple(_final_money(replay, seat) for seat in (0, 1))
    if tuple(rewards) != final_money:
        raise ValueError("reward differs from final farm money")
    name = (
        f"round39_16b_smoke_vs_v1_14_0_{seed}_seat_"
        f"{candidate_seat}.json.gz"
    )
    replay_path = replay_dir / name
    replay_hash, replay_bytes = _write_replay(replay, replay_path)
    comparator_seat = 1 - candidate_seat
    return {
        "candidate_seat": candidate_seat,
        "candidate_reward": rewards[candidate_seat],
        "comparator_reward": rewards[comparator_seat],
        "candidate_status": statuses[candidate_seat],
        "comparator_status": statuses[comparator_seat],
        "candidate_final_money": final_money[candidate_seat],
        "comparator_final_money": final_money[comparator_seat],
        "replay": name,
        "replay_bytes": replay_bytes,
        "replay_sha256": replay_hash,
        "steps": len(replay["steps"]),
    }


def _pair(seed, replay_dir):
    return tuple(_game(seed, seat, replay_dir) for seat in (0, 1))


def run(replay_dir, seed=DEFAULT_SEED):
    replay_dir = Path(replay_dir)
    games = _pair(seed, replay_dir)
    with tempfile.TemporaryDirectory(prefix="round39-16b-repeat-") as directory:
        repeated = _pair(seed, Path(directory))
    if games != repeated:
        raise ValueError("registered replay run is not deterministic")
    executor = Path(__file__).with_name("offline_executor.py")
    runner = Path(__file__)
    payload = {
        "experiment": "round39_16b_offline_executor",
        "status": "accepted-harness-only",
        "seed": seed,
        "provider": {
            "identity": "frozen-1.14-delegating-smoke",
            "real_agent_2_backend": False,
        },
        "comparator": {
            "path": COMPARATOR,
            "commit": COMPARATOR_COMMIT,
            "sha256": COMPARATOR_SHA256,
        },
        "kaggle_environments_version": importlib.metadata.version(
            "kaggle-environments"
        ),
        "games": games,
        "repeated_run_equal": True,
        "executor_sha256": _file_hash(executor),
        "runner_sha256": _file_hash(runner),
    }
    encoded = json.dumps(payload, allow_nan=False, sort_keys=True)
    payload["deterministic_sha256"] = hashlib.sha256(encoded.encode()).hexdigest()
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--replay-dir", required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    arguments = parser.parse_args()
    payload = run(arguments.replay_dir, arguments.seed)
    encoded = json.dumps(payload, allow_nan=False, indent=2, sort_keys=True) + "\n"
    Path(arguments.output).write_text(encoded)
    print(encoded, end="")


if __name__ == "__main__":
    main()
