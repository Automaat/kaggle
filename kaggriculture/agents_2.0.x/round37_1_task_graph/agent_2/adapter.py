from .policy import Agent2Policy


def create_agent(baseline_path=None, economy_factory=None):
    policy = Agent2Policy(baseline_path, economy_factory)

    def call(obs):
        return policy.act(obs)

    call.policy = policy
    return call
