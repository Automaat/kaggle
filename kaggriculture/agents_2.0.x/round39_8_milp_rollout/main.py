from agent_2 import adapter as _adapter
from agent_2.strategy import MilpFirstDayStrategy

_policy_agent = _adapter.create_agent(strategy_factory=MilpFirstDayStrategy)


def agent(obs):
    return _policy_agent(obs)
