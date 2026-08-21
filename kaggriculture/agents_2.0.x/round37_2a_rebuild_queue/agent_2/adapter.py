from .policy import Agent2Policy


def create_agent(baseline_path=None):
    policy = Agent2Policy(baseline_path)

    def call(obs):
        return policy.act(obs)

    call.policy = policy
    return call
