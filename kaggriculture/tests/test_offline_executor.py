import copy
import gzip
import hashlib
import importlib
import json
import pathlib
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

executor = importlib.import_module("offline_executor")
DEFAULT_ACTION = object()


def _action():
    return {"farmer": ["PASS"], "hands": [], "market": []}


def _observation(step=0, value="a", overage=60):
    return {
        "step": step,
        "day": step // 24,
        "hour": step % 24,
        "player": 0,
        "value": value,
        "remainingOverageTime": overage,
    }


class RecordingProvider:
    def __init__(self, action=DEFAULT_ACTION):
        self.action = _action() if action is DEFAULT_ACTION else action
        self.reset_calls = 0
        self.observations = []
        self.fail_reset = False
        self.fail_act = False

    def reset(self):
        self.reset_calls += 1
        if self.fail_reset:
            raise RuntimeError("reset failed")

    def act(self, observation):
        self.observations.append(observation)
        if self.fail_act:
            raise RuntimeError("act failed")
        return self.action


@pytest.mark.parametrize(
    "provider",
    (
        object(),
        type("MissingAct", (), {"reset": lambda self: None})(),
        type("MissingReset", (), {"act": lambda self, obs: _action()})(),
    ),
)
def test_provider_agent_rejects_incomplete_provider(provider):
    with pytest.raises(TypeError):
        executor.ProviderAgent(provider)


def test_duplicate_ignores_overage_and_returns_isolated_action():
    provider = RecordingProvider()
    agent = executor.ProviderAgent(provider)
    first = agent(_observation(overage=60))
    first["farmer"].append("MUTATED")
    second = agent(_observation(overage=1))
    assert second == _action()
    assert provider.reset_calls == 1
    assert len(provider.observations) == 1


def test_changed_same_step_and_regression_reset_provider():
    provider = RecordingProvider()
    agent = executor.ProviderAgent(provider)
    agent(_observation(step=2, value="a"))
    agent(_observation(step=2, value="b"))
    agent(_observation(step=1, value="c"))
    assert provider.reset_calls == 3
    assert len(provider.observations) == 3


def test_step_falls_back_to_day_and_hour():
    observation = {"day": 2, "hour": 3}
    assert executor.source_step(observation) == 51
    assert len(executor.observation_identity(observation)) == 64


def test_provider_receives_deep_copy():
    class MutatingProvider(RecordingProvider):
        def act(self, observation):
            observation["nested"]["value"] = 2
            return super().act(observation)

    provider = MutatingProvider()
    agent = executor.ProviderAgent(provider)
    observation = _observation()
    observation["nested"] = {"value": 1}
    agent(observation)
    assert observation["nested"]["value"] == 1


@pytest.mark.parametrize("phase", ("reset", "act"))
def test_provider_errors_keep_phase_step_and_cause(phase):
    provider = RecordingProvider()
    setattr(provider, f"fail_{phase}", True)
    agent = executor.ProviderAgent(provider)
    with pytest.raises(executor.ProviderExecutionError) as captured:
        agent(_observation(step=7))
    assert captured.value.phase == phase
    assert captured.value.source_step == 7
    assert isinstance(captured.value.__cause__, RuntimeError)


@pytest.mark.parametrize(
    "action",
    (
        None,
        {},
        {"farmer": [], "hands": [], "market": []},
        {"farmer": ["PASS"], "hands": [()], "market": []},
        {"farmer": ["PASS"], "hands": [], "market": [[]]},
        {"farmer": ["PASS"], "hands": [], "market": [], "extra": []},
    ),
)
def test_invalid_action_envelope_is_typed_failure(action):
    agent = executor.ProviderAgent(RecordingProvider(action=action))
    with pytest.raises(executor.ProviderExecutionError) as captured:
        agent(_observation())
    assert captured.value.phase == "validate"


def test_factory_creates_fresh_provider():
    providers = []

    def factory():
        provider = RecordingProvider()
        providers.append(provider)
        return provider

    first = executor.make_provider_agent(factory)
    second = executor.make_provider_agent(factory)
    first(_observation())
    second(_observation())
    assert len(providers) == 2
    assert providers[0] is not providers[1]
    assert [provider.reset_calls for provider in providers] == [1, 1]


def test_registered_full_match_and_replays_are_deterministic(tmp_path):
    runner = importlib.import_module("run_offline_executor_smoke")
    replay_dir = tmp_path / "replays"
    result = runner.run(replay_dir)
    assert result["status"] == "accepted-harness-only"
    assert result["repeated_run_equal"] is True
    assert result["provider"]["real_agent_2_backend"] is False
    assert len(result["games"]) == 2
    assert {game["candidate_seat"] for game in result["games"]} == {0, 1}
    for game in result["games"]:
        assert game["candidate_status"] == "DONE"
        assert game["comparator_status"] == "DONE"
        assert game["steps"] == 720
        assert game["candidate_reward"] == game["candidate_final_money"]
        assert game["comparator_reward"] == game["comparator_final_money"]
        path = replay_dir / game["replay"]
        assert len(gzip.decompress(path.read_bytes())) > 1_000_000
        replay = json.loads(gzip.decompress(path.read_bytes()))
        assert len(replay["steps"]) == 720
        assert len(replay["id"]) == 64
    canonical = copy.deepcopy(result)
    deterministic = canonical.pop("deterministic_sha256")
    encoded = json.dumps(canonical, allow_nan=False, sort_keys=True)
    assert deterministic == hashlib.sha256(encoded.encode()).hexdigest()


def test_registered_run_rejects_changed_comparator(tmp_path, monkeypatch):
    runner = importlib.import_module("run_offline_executor_smoke")
    monkeypatch.setattr(runner, "COMPARATOR_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="comparator hash"):
        runner.run(tmp_path)
