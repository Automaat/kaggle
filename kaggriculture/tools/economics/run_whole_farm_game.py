import argparse
import gzip
import hashlib
import importlib.metadata
import json
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from kaggle_environments import make

from kaggriculture.tools.artifact import load_artifact
from kaggriculture.tools.economics.animal_milp import ANIMALS
from kaggriculture.tools.economics.market_ledger import CROPS
from kaggriculture.tools.economics.rolling_coordinator import canonical_sha256
from kaggriculture.tools.economics.whole_farm_backend import PlanningHorizonConfig
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
    _write_bytes_atomic(compressed, path)
    return hashlib.sha256(compressed).hexdigest(), len(compressed)


def _write_bytes_atomic(value, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(value)
    temporary.replace(path)


def _write_json_atomic(value, path):
    _write_bytes_atomic(_encoded(value) + b"\n", path)


def _planning_horizon(exact_horizon_days=None, strategic_tail=False):
    return PlanningHorizonConfig(
        exact_horizon_days=exact_horizon_days,
        strategic_tail=strategic_tail,
    )


def _planning_horizon_document(horizon=None):
    if horizon is None:
        horizon = _planning_horizon()
    if type(horizon) is not PlanningHorizonConfig:
        raise TypeError("horizon must be a PlanningHorizonConfig")
    return asdict(horizon)


def _file_metadata(path):
    path = Path(path)
    if not path.exists():
        return None
    return {
        "bytes": path.stat().st_size,
        "path": str(path),
        "sha256": _sha256(path),
    }


def _failure_result(arguments, error):
    horizon = _planning_horizon(
        arguments.exact_horizon_days,
        arguments.strategic_tail,
    )
    result = {
        "arm": arguments.arm,
        "candidate_seat": arguments.candidate_seat,
        "error": {
            "text": str(error),
            "type": type(error).__name__,
        },
        "planning_horizon": _planning_horizon_document(horizon),
        "schema": "whole-farm-offline-game-attempt-v1",
        "seed": arguments.seed,
        "status": "failed",
    }
    replay_metadata = _file_metadata(arguments.replay)
    if replay_metadata is not None:
        try:
            with gzip.open(arguments.replay, "rt") as stream:
                replay = json.load(stream)
            replay_metadata["steps"] = len(replay["steps"])
        except (KeyError, OSError, TypeError, ValueError):
            pass
        result["partial_replay"] = replay_metadata
    html_metadata = _file_metadata(arguments.html) if arguments.html else None
    if html_metadata is not None:
        result["html"] = html_metadata
    progress_path = arguments.progress or arguments.output.with_name(
        f"{arguments.output.stem}_progress.json"
    )
    progress_metadata = _file_metadata(progress_path)
    if progress_metadata is not None:
        result["progress"] = progress_metadata
        try:
            progress_document = json.loads(progress_path.read_text())
            progress_metadata["records"] = len(progress_document["records"])
            latest = progress_document.get("latest")
            if latest is not None:
                result["last_checkpoint"] = latest
                result["source_step"] = latest["source_step"]
        except (KeyError, OSError, TypeError, ValueError):
            pass
    trace_metadata = _file_metadata(arguments.trace)
    if trace_metadata is not None:
        result["decision_trace"] = trace_metadata
    result["result_sha256"] = canonical_sha256(
        "whole-farm-game-attempt",
        result,
    )
    return result


def _write_failure_result(arguments, error):
    result = _failure_result(arguments, error)
    _write_json_atomic(result, arguments.output)
    return result


def _checkpoint_artifacts(
    environment,
    replay_path,
    html_path,
    arm,
    seed,
    candidate_seat,
    horizon=None,
):
    planning_horizon = _planning_horizon_document(horizon)
    if type(environment.info) is not dict:
        raise TypeError("environment info must be a dictionary")
    environment.info["planning_horizon"] = planning_horizon
    replay = environment.toJSON()
    replay["planning_horizon"] = planning_horizon
    replay["id"] = canonical_sha256(
        "whole-farm-game",
        (arm, seed, candidate_seat, planning_horizon),
    )
    replay_hash, replay_bytes = _write_gzip(replay, replay_path)
    result = {
        "replay": {
            "bytes": replay_bytes,
            "path": str(replay_path),
            "sha256": replay_hash,
            "steps": len(replay["steps"]),
        }
    }
    if html_path is not None:
        html = environment.render(
            mode="html",
            width=1200,
            height=800,
            controls=True,
        )
        if not isinstance(html, str):
            raise TypeError("html renderer returned no document")
        encoded = html.encode()
        _write_bytes_atomic(encoded, html_path)
        result["html"] = {
            "bytes": len(encoded),
            "path": str(html_path),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }
    return replay, result


def _candidate_farm(observation, candidate_seat):
    farm = observation["farms"][candidate_seat]
    crops = {crop: 0 for crop in CROPS}
    animals = {animal: 0 for animal in ANIMALS}
    structures = {structure: 0 for structure in ("COOP", "PASTURE")}
    for row in farm["tiles"]:
        for tile in row:
            if not isinstance(tile, dict):
                continue
            kind = tile.get("kind")
            crop = tile.get("crop")
            animal = tile.get("animal")
            if kind == "PLANT" and crop in crops:
                crops[crop] += 1
            if kind in structures and animal in animals:
                animals[animal] += 1
            if kind in structures and animal is None:
                structures[kind] += 1
    private = observation["private"]
    shed = private["shed"]
    seeds = private["seeds"]
    unlocked = tuple(farm["unlocked_quadrants"])
    return {
        "empty_structures": structures,
        "placed_animals": animals,
        "seeds": {crop: seeds.get(crop, 0) for crop in CROPS},
        "shed_animals": {animal: shed.get(animal, 0) for animal in ANIMALS},
        "standing_crops": crops,
        "unlocked_quadrant_count": len(unlocked),
        "unlocked_quadrants": unlocked,
    }


class _DailyProgress:
    def __init__(self, path, arm, seed, candidate_seat, horizon=None):
        self.path = None if path is None else Path(path)
        self.arm = arm
        self.seed = seed
        self.candidate_seat = candidate_seat
        self.planning_horizon = _planning_horizon_document(horizon)
        self.started = time.monotonic()
        self.records = []
        self.completed_days = set()

    def record(self, completed_day, source_step, observation, states, source):
        if self.path is None or completed_day in self.completed_days:
            return
        traces = source.traces
        trace = traces[-1] if traces else None
        solve = source.backend.last_solve
        farms = observation["farms"]
        players = tuple(
            {
                "cash": farms[seat]["money"],
                "reward": states[seat].reward,
                "seat": seat,
                "status": states[seat].status,
            }
            for seat in range(2)
        )
        record = {
            "candidate_farm": _candidate_farm(
                observation,
                self.candidate_seat,
            ),
            "completed_day": completed_day,
            "elapsed_seconds": round(time.monotonic() - self.started, 6),
            "fingerprint": None if trace is None else trace.fingerprint,
            "last_epoch": None if trace is None else trace.epoch,
            "last_solve": None
            if solve is None
            else {
                "animal_decisions": len(solve.animal_result.animals),
                "crop_decisions": len(solve.crop_result.decisions),
                "iterations": solve.ledger.iterations,
                "terminal_cash": solve.ledger.terminal_cash,
            },
            "planning_day": min(29, source_step // 24),
            "players": players,
            "source_step": source_step,
            "wall_time_utc": datetime.now(UTC).isoformat(),
        }
        self.records.append(record)
        self.completed_days.add(completed_day)
        document = {
            "arm": self.arm,
            "candidate_seat": self.candidate_seat,
            "latest": record,
            "planning_horizon": self.planning_horizon,
            "records": tuple(self.records),
            "schema": "whole-farm-progress-v1",
            "seed": self.seed,
        }
        _write_json_atomic(document, self.path)


def _final_money(replay, seat):
    observation = replay["steps"][-1][seat]["observation"]
    return observation["farms"][seat]["money"]


def _provider(arm, seed, time_limit, mip_rel_gap, horizon):
    if arm == "control-1.14":
        provider = WholeFarmControlProvider(
            seed,
            time_limit,
            mip_rel_gap,
            5,
            horizon=horizon,
        )
        return provider, lambda: provider.source
    source = WholeFarmHandoffSource(
        seed,
        time_limit,
        mip_rel_gap,
        5,
        horizon=horizon,
    )
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
    progress_path=None,
    html_path=None,
    exact_horizon_days=None,
    strategic_tail=False,
):
    horizon = _planning_horizon(exact_horizon_days, strategic_tail)
    if _sha256(COMPARATOR) != COMPARATOR_SHA256:
        raise ValueError("frozen comparator hash changed")
    provider, source_getter = _provider(
        arm,
        seed,
        time_limit,
        mip_rel_gap,
        horizon,
    )
    candidate_agent = make_provider_agent(lambda: provider)
    comparator = load_artifact(COMPARATOR)
    progress = _DailyProgress(
        progress_path,
        arm,
        seed,
        candidate_seat,
        horizon,
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

    def candidate(observation, configuration=None):
        action = candidate_agent(observation, configuration)
        source_step = observation["step"]
        if source_step > 0 and source_step % 24 == 0:
            progress.record(
                source_step // 24 - 1,
                source_step,
                observation,
                environment.steps[-1],
                source_getter(),
            )
            _checkpoint_artifacts(
                environment,
                replay_path,
                html_path,
                arm,
                seed,
                candidate_seat,
                horizon,
            )
        return action

    agents = (candidate, comparator) if candidate_seat == 0 else (comparator, candidate)
    try:
        environment.run(list(agents))
    except Exception:
        try:
            _checkpoint_artifacts(
                environment,
                replay_path,
                html_path,
                arm,
                seed,
                candidate_seat,
                horizon,
            )
        except Exception:
            pass
        raise
    replay, artifacts = _checkpoint_artifacts(
        environment,
        replay_path,
        html_path,
        arm,
        seed,
        candidate_seat,
        horizon,
    )
    if len(replay["steps"]) != 720:
        raise ValueError("full game must contain 720 steps")
    statuses = tuple(state.status for state in environment.steps[-1])
    rewards = tuple(state.reward for state in environment.steps[-1])
    if statuses != ("DONE", "DONE"):
        raise ValueError(f"game did not finish: {statuses}")
    source = source_getter()
    progress.record(
        29,
        719,
        replay["steps"][-1][candidate_seat]["observation"],
        environment.steps[-1],
        source,
    )
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
        "planning_horizon": _planning_horizon_document(horizon),
        "seed": seed,
    }
    trace_hash, trace_bytes = _write_gzip(trace_document, trace_path)
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
        "planning_horizon": _planning_horizon_document(horizon),
        "replay": artifacts["replay"],
        "schema": "whole-farm-offline-game-v1",
        "seed": seed,
    }
    if progress.path is not None:
        result["progress"] = {
            "path": str(progress.path),
            "records": len(progress.records),
            "sha256": _sha256(progress.path),
        }
    if "html" in artifacts:
        result["html"] = artifacts["html"]
    result["result_sha256"] = canonical_sha256("whole-farm-game-result", result)
    return result


def _argument_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--arm",
        choices=("control-1.14", "route-2.0"),
        default="control-1.14",
    )
    parser.add_argument("--candidate-seat", type=int, choices=(0, 1), default=0)
    parser.add_argument(
        "--exact-horizon-days",
        type=int,
        choices=range(1, 31),
    )
    parser.add_argument("--html", type=Path)
    parser.add_argument("--mip-rel-gap", type=float, default=0.0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--progress", type=Path)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--time-limit", type=float, default=30.0)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--strategic-tail", action="store_true")
    return parser


def main():
    parser = _argument_parser()
    arguments = parser.parse_args()
    try:
        _planning_horizon(
            arguments.exact_horizon_days,
            arguments.strategic_tail,
        )
    except (TypeError, ValueError) as error:
        parser.error(str(error))
    try:
        result = run(
            arguments.arm,
            arguments.seed,
            arguments.candidate_seat,
            arguments.replay,
            arguments.trace,
            arguments.time_limit,
            arguments.mip_rel_gap,
            arguments.progress
            or arguments.output.with_name(f"{arguments.output.stem}_progress.json"),
            arguments.html,
            arguments.exact_horizon_days,
            arguments.strategic_tail,
        )
    except Exception as error:
        _write_failure_result(arguments, error)
        raise
    text = json.dumps(result, allow_nan=False, indent=2, sort_keys=True) + "\n"
    _write_bytes_atomic(text.encode(), arguments.output)
    print(text, end="")


if __name__ == "__main__":
    main()
