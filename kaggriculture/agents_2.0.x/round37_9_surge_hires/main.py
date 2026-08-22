from agent_2 import adapter as _adapter

_policy_agent = _adapter.create_agent()


def agent(obs):
    return _policy_agent(obs)
