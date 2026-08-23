"""Entrypoint for the staged-field agent.

The harness loads this file by path, which does not put its directory on
`sys.path`, so the package import below has to arrange that itself.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from agent_2 import adapter as _adapter

_policy_agent = _adapter.create_agent()


def agent(obs):
    return _policy_agent(obs)
