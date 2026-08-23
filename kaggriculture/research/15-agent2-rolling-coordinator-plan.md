# Agent 2.0 rolling coordinator contract plan

Status: replacement execution plan for standalone experiment 39.15.

## Decision and base

Build a typed, atomic rolling coordinator on the final accepted Round 39.14
shop-forecast commit. It schedules whole-farm planning daily, after a changed
shop multiset or explicit economic divergence. It repairs space and routes only
after unexpected execution changes. This experiment validates the contract and
does not change live actions.

Frozen comparator remains 1.14.0 commit `b74a3ea`, SHA-256
`86951703eac27253938500eac664650c1e927d1b86b26ed84be008f24739d699`.

## Files

1. Add `tools/economics/rolling_coordinator.py`.
2. Add `tests/test_economic_rolling_coordinator.py`.
3. Add `tools/economics/run_rolling_coordinator.py`.
4. Add this plan as `research/15-agent2-rolling-coordinator-plan.md`.
5. Add `research/round39_15_rolling_coordinator.json` after validation.
6. Append the registered result to `EXPERIMENTS.md`.

The accepted shop module is a base dependency, not a changed file. Crop,
animal, land/hire, space, route and live-agent files remain unchanged.

## Canonical input

`canonical_sha256(domain, value)` serializes a JSON-compatible frozen value
with `allow_nan=False`, `sort_keys=True`, compact separators and a domain field.
It returns 64 lowercase hexadecimal characters. Lists and mappings are copied
to canonical tuples before storage; unsupported values and nonfinite numbers
are rejected.

`ObservedDelta` is frozen and stores domain (`economy`, `topology` or `route`),
canonical target key, exact pre-state fingerprint and exact post-state
fingerprint. `ExpectedEffectRef` stores the same fields plus planning epoch and
an identifier equal to canonical SHA-256 of `(domain, target_key,
pre_state_fingerprint, post_state_fingerprint, planning_epoch)`, excluding the
identifier itself. Thus an effect is bound to one epoch and one exact state
transition.

`ExecutionSignal` is frozen and contains exact observed deltas, completed
effect identifiers and `route_precondition_failed: bool`. Identifiers and
deltas are nonempty and unique. Every completed identifier must match one
pending effect and its exact observed delta. Extra deltas remain unexplained.
One planned `PLANT` therefore cannot hide a weed that appears elsewhere in the
same observation.

`RollingObservation` is frozen and contains source step `0..718`, the exact
open-shop tuple, canonical economy, topology, route-precondition and progress
fingerprints, and `ExecutionSignal`. It derives a sorted `(shop, count)`
signature that preserves duplicates. Its identity hash covers every field.

Economy state includes money, private goods and seeds, market inventory and
prices, open shops and production-relevant public farm state. Topology includes
tiles and unlocked land. Route preconditions include actionable target state,
but exclude ordinary unit positions. Progress includes positions and task
cursors. The later live adapter must build these domains from canonical
`World.data`; 39.15 tests use exact frozen fixtures. The adapter must emit one
delta for every changed economy, topology or route target and no delta for an
unchanged target. A changed domain fingerprint without corresponding deltas is
an invalid observation. Any unmatched economy delta is a divergence in this
correctness-first contract. Later measurement can add a registered tolerance;
39.15 does not silently ignore opponent market changes.

Equal identity and equal step is a duplicate. Any different observation with
`source_step <= previous.source_step` resets the coordinator and backend before
planning epoch 0, matching existing `EpisodeState.observe()` behavior.

## Typed plans

Every reference below is a frozen dataclass. Every fingerprint is canonical
SHA-256. Every collection is a tuple of validated immutable scalar values.

- `EconomicPlanRef` stores its fingerprint, crop-result fingerprint,
  animal-result fingerprint, investment-result fingerprint, resource-profile
  fingerprint, order intent identifiers and animal-purchase intent identifiers.
- `SpacePlanRef` stores its fingerprint, economic fingerprint, spatial task
  identifiers and rejected animal intent identifiers.
- `RoutePlanRef` stores its fingerprint, economic and space fingerprints, route
  identifiers and pending `ExpectedEffectRef` values.
- `PlanningWindow` stores inclusive `action_end_step`, `strategy_end_step` and
  `investment_end_step` copied from `forecast_shops()`. The first ends at the
  current day, the second at `next_shop_replan_step - 1`, and the third at 718.
- `WholeFarmIntent` stores epoch, creation step, window, three plan references
  and a sorted tuple of planning reasons.
- `PlanFailure` stores failed phase, exception text, attempted epoch and the
  last committed epoch. It never exposes a retained executable intent.

Reference validation enforces parent fingerprints. Space must name its economy
parent. Routes must name both economy and space parents.

The coordinator owns `_next_epoch`. It starts at 0 after initialization or
reset, is assigned to a candidate and increments only after atomic commit.
Duplicate, progress-only and expected-effect calls return the exact same
`WholeFarmIntent` object and do not change epoch. A no-backend progress call
still atomically advances `_last_observation` and records matched effect
acknowledgements. Failure changes neither observation nor epoch, so the same
observation can retry.

## Backend and feedback boundary

`PlannerBackend` is a `Protocol` with:

```text
reset() -> None
solve_whole_farm(epoch, observation, forecast, window) ->
    tuple[EconomicPlanRef, SpacePlanRef, RoutePlanRef]
repair_space(epoch, observation, economy, previous_space) -> SpacePlanRef
repair_routes(epoch, observation, economy, space, previous_routes) -> RoutePlanRef
```

`solve_whole_farm()` is the only daily economic entry point. Round 39.15B must
implement the five-model fixed point inside this call:

1. enumerate land/hire and animal macro candidates;
2. solve crops with candidate feed, cash, tile, storage, order and action use;
3. solve spatial placement only for selected animal purchase intents;
4. estimate complete route capacity;
5. add a feasibility cut and repeat when space rejects an intent or routes
   exceed capacity;
6. return only a mutually feasible bundle or raise a typed backend error after
   the registered iteration limit.

This is the explicit economy-space-route feedback boundary. The 39.15 recording
backend returns registered feasible references without running the MILPs.

## Invalidation and atomicity

The coordinator evaluates all dirty reasons first and calls each required phase
at most once in topological order.

`economy_dirty` is true on initialization/reset, a new day, changed shop
signature or any unexplained economy delta. It constructs
`ShopForecastInput(source_step, open_shops)`, calls `forecast_shops()`, requires
`verify_forecast()` to return no errors and then calls `solve_whole_farm()`.
The result is already a complete compatible bundle.

Without `economy_dirty`, `space_dirty` is true only when one or more topology
deltas remain after exact effect matching. It calls `repair_space()`, then
routes.

Without `economy_dirty`, `routes_dirty` is true on route-precondition failure,
an unmatched route delta or `space_dirty`. It calls `repair_routes()` once.

Progress fingerprint changes and fully matched effects call no backend. A
changed domain with an unknown, duplicate, stale-epoch, already acknowledged or
state-mismatched effect is unexplained. The coordinator tracks acknowledged
effect IDs until a new route commits.

At step 72, new-day and shop changes coalesce into one whole-farm solve. Source
step 718 has action, strategy and investment boundaries equal to 718 and cannot
schedule another solve.

All outputs are built in local variables. Effect epochs, parent fingerprints
and the complete
bundle are validated before `_last_intent`, `_last_observation`, epoch or
acknowledged effects change. Any backend exception, invalid output or parent
mismatch returns `PlanFailure`; no old intent is executable for that call.

`reset()` clears coordinator state, calls backend reset once and makes the next
successful intent epoch 0. Backend reset failure returns `PlanFailure` in the
reset phase and leaves the coordinator empty.

## Live ownership boundary

Existing `Agent2Policy` continues to own raw-observation duplicate caching and
episode reset. A later adapter will call this coordinator only for observations
that pass that gate. 39.15 adds a contract test proving that the frozen live
policy does not import or execute the standalone coordinator. Round 39.15B must
add a disabled adapter before any live arm; herd, spatial-task and route seams
do not yet exist in the live coordinator.

## Validation and stop conditions

Tests cover canonical hash stability and domain separation, invalid immutable
values, duplicate shop multiplicity, exact step 71/72/718 windows, exact object
identity for duplicate/progress, equal-step reset, step regression, every dirty
reason, simultaneous dirty reasons, expected and unexpected topology effects,
one expected spatial effect plus one simultaneous unexpected weed, unmatched
economy and route deltas, missing delta coverage, stale-epoch and state-mismatch
effects, sequential progress-state updates, acknowledgement replay, unchanged
and changed repair fingerprints, parent
mismatch, unsafe backend objects, all three backend exceptions, atomic retry,
exact `_next_epoch` transitions, reset failure and live nonintegration.

The registered runner uses a recording backend over a deterministic day 0 to 4
sequence. It records call order, epochs, reasons, windows, failures and a stable
hash. Required gates:

- every expected call count and order matches the contract;
- no partial state after injected failures;
- all result-parent fingerprints validate;
- two runs have the same canonical result after no elapsed field is included;
- full tests and Ruff pass;
- adversarial review returns `CLEAN`.

The result status is `accepted-contract-only`. It makes no economic ranking,
simulator score, runtime or 1.14.0 superiority claim. Stop before live games.
39.15B owns real backend coupling; 39.16 owns executable intents and full
30-day comparisons.

## Unresolved questions

- None for the coordinator contract.
