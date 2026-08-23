"""Entrypoint for the staged-field agent.

The harness compiles this file and execs it with a bare globals dict, so
`__file__` is not defined. It also appends the file's directory to `sys.path`
itself, which is what makes the package import below work.
"""

from agent_2 import adapter as _adapter

_policy_agent = _adapter.create_agent()


def agent(obs):
    return _policy_agent(obs)
