import copy
import json
import os
import pathlib
import shutil
import subprocess
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from artifact import load_artifact
from package_agent import build_archive
from runner import CHAMPION, load_agent, run_match


ROOT = pathlib.Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "agents_2.0.x/round37_0_shell"
BASELINE = ROOT / "agents_1.0.x/v1_14_0_central_herd.py"
REPLAY = ROOT / "replays/main_vs_champion_42.json"
RELEASE = ROOT / "agents_1.0.x/v1_15_0_staged_field"
RELEASE_ARCHIVE = ROOT / "agents_1.0.x/v1_15_0_staged_field.tar.gz"


def _observations(seat, limit=720):
    replay = json.loads(REPLAY.read_text())
    return [step[seat]["observation"] for step in replay["steps"][:limit]]


def _policy(loaded):
    return loaded.module._policy_agent.policy


def test_saved_replay_actions_equal_frozen_baseline():
    for seat in (0, 1):
        candidate = load_agent(str(CANDIDATE))
        baseline = load_agent(str(BASELINE))
        for observation in _observations(seat):
            candidate_input = copy.deepcopy(observation)
            baseline_input = copy.deepcopy(observation)
            assert candidate(candidate_input) == baseline(baseline_input)
            assert candidate_input == observation
            assert baseline_input == observation


def test_market_parameter_override_equals_frozen_baseline():
    observation = copy.deepcopy(_observations(0, 1)[0])
    observation["market"]["params"] = {
        "MELON": {
            "base": 300,
            "T": 250,
            "below_func": "sqrt",
            "below_target": 0.3,
            "above_func": "sq",
            "above_target": 2.1,
        }
    }
    candidate = load_agent(str(CANDIDATE))
    baseline = load_agent(str(BASELINE))
    assert candidate(copy.deepcopy(observation)) == baseline(copy.deepcopy(observation))


def test_duplicate_observation_does_not_advance_baseline():
    first, second = _observations(0, 2)
    candidate = load_agent(str(CANDIDATE))
    baseline = load_agent(str(BASELINE))
    action = candidate(copy.deepcopy(first))
    action["farmer"].append("MUTATED")
    duplicate = candidate(copy.deepcopy(first))
    assert duplicate == baseline(copy.deepcopy(first))
    duplicate["farmer"].append("MUTATED")
    assert candidate(copy.deepcopy(first)) == baseline(copy.deepcopy(first))
    assert candidate(copy.deepcopy(second)) == baseline(copy.deepcopy(second))


def test_step_rollback_replaces_private_baseline_module():
    first, second = _observations(0, 2)
    candidate = load_agent(str(CANDIDATE))
    fresh = load_agent(str(CANDIDATE))
    candidate(copy.deepcopy(first))
    initial_module = _policy(candidate).baseline.module
    candidate(copy.deepcopy(second))
    reset_action = candidate(copy.deepcopy(first))
    assert _policy(candidate).baseline.module is not initial_module
    assert reset_action == fresh(copy.deepcopy(first))


def test_two_loaded_candidates_have_isolated_packages_and_state():
    first = load_agent(str(CANDIDATE))
    second = load_agent(str(CANDIDATE))
    first_modules = {id(module) for module in first.package_modules}
    second_modules = {id(module) for module in second.package_modules}
    assert first_modules
    assert first_modules.isdisjoint(second_modules)
    assert _policy(first) is not _policy(second)
    assert not any(name == "agent_2" or name.startswith("agent_2.") for name in sys.modules)
    assert first(copy.deepcopy(_observations(0, 1)[0]))
    assert second(copy.deepcopy(_observations(1, 1)[0]))


def test_loader_restores_existing_agent_package():
    sentinel = types.ModuleType("agent_2")
    previous = sys.modules.get("agent_2")
    sys.modules["agent_2"] = sentinel
    try:
        load_agent(str(CANDIDATE))
        assert sys.modules["agent_2"] is sentinel
    finally:
        if previous is None:
            sys.modules.pop("agent_2", None)
        else:
            sys.modules["agent_2"] = previous


def test_same_stem_files_use_distinct_root_modules(tmp_path):
    first_path = tmp_path / "first/main.py"
    second_path = tmp_path / "second/main.py"
    first_path.parent.mkdir()
    second_path.parent.mkdir()
    first_path.write_text("def agent(obs):\n    return {'value': 1}\n")
    second_path.write_text("def agent(obs):\n    return {'value': 2}\n")
    first = load_artifact(first_path)
    second = load_artifact(second_path)
    assert first.module is not second.module
    assert first({}) == {"value": 1}
    assert second({}) == {"value": 2}


def test_two_package_versions_remain_isolated(tmp_path):
    loaded = []
    for value in (1, 2):
        root = tmp_path / f"version-{value}"
        package = root / "agent_2"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        (package / "value.py").write_text(f"VALUE = {value}\n")
        (root / "main.py").write_text(
            "from agent_2.value import VALUE\n"
            "def agent(obs):\n"
            "    return {'value': VALUE}\n"
        )
        loaded.append(load_artifact(root))
    assert loaded[0]({}) == {"value": 1}
    assert loaded[1]({}) == {"value": 2}
    assert {id(module) for module in loaded[0].package_modules}.isdisjoint(
        {id(module) for module in loaded[1].package_modules}
    )


def test_failed_import_restores_path_and_package(tmp_path):
    root = tmp_path / "broken"
    package = root / "agent_2"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (root / "main.py").write_text("raise RuntimeError('broken')\n")
    previous_path = list(sys.path)
    sentinel = types.ModuleType("agent_2")
    previous = sys.modules.get("agent_2")
    sys.modules["agent_2"] = sentinel
    try:
        try:
            load_artifact(root)
        except RuntimeError:
            pass
        else:
            raise AssertionError("load must fail")
        assert sys.path == previous_path
        assert sys.modules["agent_2"] is sentinel
    finally:
        if previous is None:
            sys.modules.pop("agent_2", None)
        else:
            sys.modules["agent_2"] = previous


def test_existing_loader_forms_remain_available():
    def marker(obs):
        return obs

    assert load_agent(marker) is marker
    assert load_agent("pass") == "pass"
    assert callable(load_agent("champion"))
    assert callable(load_agent(str(BASELINE.resolve())))
    assert callable(load_agent("specialist:melon"))
    assert callable(load_agent("current:melon"))
    assert callable(load_agent("variant:KAGG_LAND=1"))


def test_configured_loader_isolates_environment(monkeypatch):
    monkeypatch.delenv("AGENT2_PLANT_CAP", raising=False)
    configured = load_agent(f"configured:{CANDIDATE};AGENT2_PLANT_CAP=38")
    assert callable(configured)
    assert "AGENT2_PLANT_CAP" not in os.environ


def test_runner_executes_two_loaded_file_agents():
    opponent = ROOT / "agents_1.0.x/v1_13_0_rl_routing.py"
    _environment, rewards, statuses = run_match(str(BASELINE), str(opponent), seed=42)
    assert statuses == ["DONE", "DONE"]
    assert rewards[0] > 3000
    assert rewards[1] > 3000


def test_packed_artifact_is_deterministic_and_executable(tmp_path):
    first_path = tmp_path / "first.tar.gz"
    second_path = tmp_path / "second.tar.gz"
    first_manifest = build_archive(CANDIDATE, first_path, source_commit="test")
    second_manifest = build_archive(CANDIDATE, second_path, source_commit="test")
    assert first_path.read_bytes() == second_path.read_bytes()
    assert first_manifest == second_manifest
    assert first_manifest["baseline_sha256"] == (
        "86951703eac27253938500eac664650c1e927d1b86b26ed84be008f24739d699"
    )
    assert first_manifest["kaggle_environments_version"] == "1.32.7"
    assert first_manifest["configuration"]["townCenterSellInterval"] == 24
    candidate = load_agent(str(first_path))
    baseline = load_agent(str(BASELINE))
    observation = _observations(0, 1)[0]
    assert candidate(copy.deepcopy(observation)) == baseline(copy.deepcopy(observation))


def test_packed_artifact_is_path_independent(tmp_path):
    copied = tmp_path / "copied"
    shutil.copytree(CANDIDATE, copied)
    first_path = tmp_path / "original.tar.gz"
    second_path = tmp_path / "copied.tar.gz"
    build_archive(CANDIDATE, first_path, source_commit="test")
    build_archive(copied, second_path, source_commit="test")
    assert first_path.read_bytes() == second_path.read_bytes()


def test_extracted_artifact_runs_through_installed_loader(tmp_path):
    archive = tmp_path / "agent.tar.gz"
    build_archive(CANDIDATE, archive, source_commit="test")
    script = """
import sys
from pathlib import Path
from kaggle_environments import make
sys.path.insert(0, sys.argv[2])
from artifact import extract_archive
root = Path(sys.argv[3])
extract_archive(sys.argv[1], root)
environment = make('kaggriculture', configuration={'episodeSteps': 720, 'seed': 42})
environment.run([str(root / 'main.py'), 'random'])
states = environment.steps[-1]
assert [state.status for state in states] == ['DONE', 'DONE']
assert states[0].reward > 3000
"""
    extracted = tmp_path / "extracted"
    result = subprocess.run(
        [sys.executable, "-c", script, str(archive), str(ROOT / "tools"), str(extracted)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_release_archive_is_the_champion(tmp_path):
    generated = tmp_path / "release.tar.gz"
    manifest = build_archive(
        RELEASE,
        generated,
        source_commit="f2dd7895c05003c1ee0a90f2b96eda56dcbfd05b",
        stage="39",
        candidate="1.15.0",
    )
    assert CHAMPION == "agents_1.0.x/v1_15_0_staged_field"
    assert generated.read_bytes() == RELEASE_ARCHIVE.read_bytes()
    assert manifest["candidate"] == "1.15.0"
    assert manifest["stage"] == "39"


def test_release_source_matches_selected_candidate():
    selected = ROOT / "agents_2.0.x/round38_2_hire_batch"
    selected_files = {
        path.relative_to(selected): path.read_bytes()
        for path in selected.rglob("*.py")
    }
    release_files = {
        path.relative_to(RELEASE): path.read_bytes()
        for path in RELEASE.rglob("*.py")
    }
    assert release_files == selected_files


def test_release_champion_finishes_live_episode():
    assert callable(load_agent(str(RELEASE_ARCHIVE)))
    _environment, rewards, statuses = run_match("champion", str(BASELINE), seed=64)
    assert statuses == ["DONE", "DONE"]
    assert rewards[0] > 3000
    assert rewards[1] > 3000


def test_archive_entrypoint_loads_without_path_help(tmp_path):
    """Given an archive, When the harness loads main.py by path, Then it imports.

    Kaggle executes the entrypoint without putting its directory on `sys.path`,
    while `tools/artifact.py` inserts it. That gap passed every local gate and
    failed validation on the ladder.
    """
    import subprocess

    archive = tmp_path / "agent.tar.gz"
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "package_agent.py"),
         str(ROOT / "agents_1.0.x" / "v1_15_0_staged_field"), str(archive)],
        check=True, capture_output=True,
    )
    root = tmp_path / "unpacked"
    root.mkdir()
    subprocess.run(["tar", "xzf", str(archive), "-C", str(root)], check=True)
    probe = (
        "import importlib.util, sys;"
        f"spec = importlib.util.spec_from_file_location('probe', {str(root / 'main.py')!r});"
        "module = importlib.util.module_from_spec(spec);"
        "sys.modules['probe'] = module;"
        "spec.loader.exec_module(module);"
        "assert callable(module.agent)"
    )
    result = subprocess.run([sys.executable, "-c", probe], cwd="/", capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
