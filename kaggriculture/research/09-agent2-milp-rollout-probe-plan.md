# Agent 2.0 MILP first-day rollout probe

Status: execution plan for an exploratory paired game. This is not the A2a
ranking gate and cannot promote the MILP to live control.

## Objective

Execute the registered A2a day-0 crop portfolio against frozen 1.14.0 on seed
3,980,000 in both seats. Measure realized terminal money, failures and whether
the requested first-day crop targets were planted.

Source predecessor: `32d8fb7`. Frozen comparator: 1.14.0 commit `b74a3ea`,
SHA-256 `86951703eac27253938500eac664650c1e927d1b86b26ed84be008f24739d699`.

## Intervention

Copy the accepted Agent 2.0 task-graph package into a new experiment snapshot.
Keep its economy, herd, land, routing, task graph and fallback behavior frozen.

Add one deterministic strategy planner. During day 0 it reserves the same 12
central animal positions as 1.14.0 and assigns the remaining 13 initial tiles
to the A2a optimum: nine carrot and four melon. Carrot gets the nearer crop
positions because it turns over more often. The planner returns only targets
that are still empty. From day 1 it returns no override, so 1.14.0 controls all
later crop choices.

The existing strategy seam protects animal targets and makes the baseline seed
purchase code react to the new crop targets. SciPy and the MILP implementation
remain outside the runtime package.

## Registered run

Run seed 3,980,000 twice:

1. Agent 2.0 MILP probe in seat 0, frozen 1.14.0 in seat 1;
2. frozen 1.14.0 in seat 0, Agent 2.0 MILP probe in seat 1.

Save both complete replays and one canonical result JSON. Record rewards,
statuses, first-day accepted plant counts and final crop state.

## Gates

- default Agent 2.0 snapshot remains unchanged;
- runtime snapshot imports no SciPy or economics research tool;
- exact target count is nine carrot and four melon on an empty initial board;
- animal positions are not overridden;
- day 1 and later return to frozen strategy;
- both games finish with status `DONE`;
- all tests and Ruff for changed files pass;
- result states that two games do not establish a score improvement.

## Non-goals

- A2a portfolio-ranking calibration;
- daily MILP replanning;
- animal, land, hire or feed optimization;
- release of Agent 2.0;
- statistical score claim.

## Unresolved questions

None.
