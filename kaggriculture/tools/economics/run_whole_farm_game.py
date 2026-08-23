import argparse
import gzip
import hashlib
import importlib.metadata
import json
from dataclasses import asdict
from pathlib import Path

from kaggle_environments import make

from kaggriculture.tools.artifact import load_artifact
from kaggriculture.tools.economics.rolling_coordinator import canonical_sha256
from kaggriculture.tools.economics.whole_farm_hybrid_provider import (
    WholeFarmControlProvider,
    WholeFarmHandoffSource,
)
from kaggriculture.tools.offline_executor import make_provider_agent
from kaggriculture.tools.routing.execution_provider import ExecutionRouteProvider


ROOT = Path(__file__).resolve().parents[2]


COMPARATOR = ROOT / "agents_1.0.x/v1_14_0_central_herd.py"
COMPARATOR_COMMIT = "b74a3ea"
COMPARATOR_SHA256 = "86951703eac27253938500eac664650c1e927d1b86b26ed84be008f24739d699"
DEFAULT_SEED = 3_980_000


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _encoded(value):
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _write_gzip(value, path):
    encoded = _encoded(value)
    compressed = gzip.compress(encoded, mtime=0)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(compressed)
    return hashlib.sha256(compressed).hexdigest(), len(compressed)


def _final_money(replay, seat):
    observation = replay["steps"][-1][seat]["observation"]
    return observation["farms"][seat]["money"]


def _provider(arm, seed, time_limit, mip_rel_gap):
    if arm == "control-1.14":
        provider = WholeFarmControlProvider(seed, time_limit, mip_rel_gap, 5)
        return provider, lambda: provider.source
    source = WholeFarmHandoffSource(seed, time_limit, mip_rel_gap, 5)
    provider = ExecutionRouteProvider(source)
    return provider, lambda: source


def run(
    arm,
    seed,
    candidate_seat,
    replay_path,
    trace_path,
    time_limit=30.0,
    mip_rel_gap=0.0,
):
    if _sha256(COMPARATOR) != COMPARATOR_SHA256:
        raise ValueError("frozen comparator hash changed")
    provider, source_getter = _provider(arm, seed, time_limit, mip_rel_gap)
    candidate = make_provider_agent(lambda: provider)
    comparator = load_artifact(COMPARATOR)
    agents = (
        (candidate, comparator)
        if candidate_seat == 0
        else (comparator, candidate)
    )
    environment = make(
        "kaggriculture",
        configuration={
            "actTimeout": 300,
            "episodeSteps": 720,
            "runTimeout": 7200,
            "seed": seed,
        },
        debug=True,
    )
    environment.run(list(agents))
    replay = environment.toJSON()
    replay["id"] = canonical_sha256(
        "whole-farm-game",
        (arm, seed, candidate_seat),
    )
    if len(replay["steps"]) != 720:
        raise ValueError("full game must contain 720 steps")
    statuses = tuple(state.status for state in environment.steps[-1])
    rewards = tuple(state.reward for state in environment.steps[-1])
    if statuses != ("DONE", "DONE"):
        raise ValueError(f"game did not finish: {statuses}")
    source = source_getter()
    source.verify_daily_epochs()
    traces = tuple(source.traces)
    daily_steps = tuple(
        trace.observed.source_step
        for trace in traces
        if trace.observed.source_step % 24 == 0
    )
    if daily_steps != tuple(range(0, 697, 24)):
        raise ValueError("daily decision traces are incomplete")
    trace_document = {
        "arm": arm,
        "candidate_seat": candidate_seat,
        "decision_traces": tuple(asdict(trace) for trace in traces),
        "seed": seed,
    }
    trace_hash, trace_bytes = _write_gzip(trace_document, trace_path)
    replay_hash, replay_bytes = _write_gzip(replay, replay_path)
    opponent_seat = 1 - candidate_seat
    result = {
        "arm": arm,
        "candidate": {
            "final_money": _final_money(replay, candidate_seat),
            "reward": rewards[candidate_seat],
            "seat": candidate_seat,
            "status": statuses[candidate_seat],
        },
        "comparator": {
            "commit": COMPARATOR_COMMIT,
            "final_money": _final_money(replay, opponent_seat),
            "reward": rewards[opponent_seat],
            "seat": opponent_seat,
            "sha256": COMPARATOR_SHA256,
            "status": statuses[opponent_seat],
        },
        "daily_epochs": len(daily_steps),
        "decision_trace": {
            "bytes": trace_bytes,
            "path": str(trace_path),
            "sha256": trace_hash,
            "total_epochs": len(traces),
        },
        "execution_label": (
            "strategy-2.0-execution-1.14"
            if arm == "control-1.14"
            else "strategy-2.0-execution-route-2.0"
        ),
        "game_days": 30,
        "kaggle_environments_version": importlib.metadata.version(
            "kaggle-environments"
        ),
        "replay": {
            "bytes": replay_bytes,
            "path": str(replay_path),
            "sha256": replay_hash,
            "steps": len(replay["steps"]),
        },
        "schema": "whole-farm-offline-game-v1",
        "seed": seed,
    }
    result["result_sha256"] = canonical_sha256("whole-farm-game-result", result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--arm",
        choices=("control-1.14", "route-2.0"),
        default="control-1.14",
    )
    parser.add_argument("--candidate-seat", type=int, choices=(0, 1), default=0)
    parser.add_argument("--mip-rel-gap", type=float, default=0.0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--time-limit", type=float, default=30.0)
    parser.add_argument("--trace", type=Path, required=True)
    arguments = parser.parse_args()
    result = run(
        arguments.arm,
        arguments.seed,
        arguments.candidate_seat,
        arguments.replay,
        arguments.trace,
        arguments.time_limit,
        arguments.mip_rel_gap,
    )
    text = json.dumps(result, allow_nan=False, indent=2, sort_keys=True) + "\n"
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
