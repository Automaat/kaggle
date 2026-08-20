# Agent 1.0.0 — implementation plan

**Status: shipped as `agents_1.0.x/v1_0_0_land.py` on 2026-08-19, then `v1_1_0_herd.py` on 2026-08-20.** What the plan
got right, what it got wrong, and what is left is in the results section at the
bottom; round 8 of [EXPERIMENTS.md](../EXPERIMENTS.md) has the numbers.

Written 2026-08-19, after rounds 7 to 7d, and rewritten the same day after an adversarial review. Every claim below that the review falsified has been removed rather than reworded; the record of what it caught is at the end.

[EXPERIMENTS.md](../EXPERIMENTS.md) is what was already tried. [research/01-shared-market-games.md](../research/01-shared-market-games.md) is the theory. This file is the forward plan.

## Context, and how much of it is actually evidence

Version 0.22.0 lost its first real episode, 94619184, by $2,752 with 25 tiles against 100.

That margin is **half a standard deviation of one unpaired, unswapped game**: the per-seed spread of our own benchmark is about $5,600, which is why `bench.py` needs 200 seeds to resolve $800. The loss is not evidence that our agent is worse, and "per tile we are four times better" is a ratio of two noisy numbers. Do not build on either.

What the episode does prove, because existence needs no interval, is that a bot can hold 100 tiles on 12 hands and finish the season with them productive. Our agent cannot do that at any setting. That single fact is the whole reason for a major version.

The three tuning rounds since then are much stronger evidence, because they are paired and repeated:

- Land loses at every quadrant count, every labour budget and every payback gate, monotonically in how hard the gate is (7b, 7c, 7d). The cause is delivery: 41 of the new tiles stand as weeds while `_land_profit` prices them as tended.
- The winner's engine copied directly, premium crops banned and geese run, loses $48,000 before any land is bought (7c). The goose pipeline needs wheat carried to 25 animals daily, and our logistics were built for seven animals beside the shed.
- Land competes with the herd for the same early cash, and the herd wins by a wide margin.

And one older result the first draft of this plan ignored: **routing was already tried and lost**. A task-centric assignment cut movement from 56.1% to 48.5% and scored 7/20, because at 25 tiles the freed turns had nothing to do (EXPERIMENTS.md, round 5). Movement being 65% of unit-turns is not by itself a reason to route.

## What that changes about the plan's shape

Round 7's own conclusion was that land, hands and mix must move together or all three read as losses. The same argument applies to routing: its only hypothesised payoff is making distant tiles productive, so measuring it alone on 25 tiles measures nothing.

So the expansion is **one phase, swept jointly**, not four phases in a chain. The two changes that stand alone — the sell schedule and the objective — go first, because they can be falsified in a day each.

## Coverage of the research note

| Research action                     | Where     | Honest status                                                                                                               |
|:------------------------------------|:----------|:----------------------------------------------------------------------------------------------------------------------------|
| 1. Objective is the difference      | Phase 2   | The benchmark already scores the difference; the planner does not, and the opponent-supply split it needs does not exist yet |
| 2. Selling as an execution schedule | Phase 1   | Planned, independent of everything else                                                                                     |
| 3. Capacity as commitment           | Phase 3   | Re-priced only; signalling to the opponent stays out of scope                                                                |
| 4. Products by curve shape          | Phase 3   | Only meaningful with scale, so it moves with land                                                                            |
| 5. Search where it is cheap         | Phase 3   | Macro-actions only; the receding horizon is deferred                                                                         |
| 6. Mine replays before training     | Phase 0.3 | Separate track, starts now                                                                                                   |
| 7. Best-arm identification          | Phase 0.1 | Redesigned after review: the first draft's budget could not have worked                                                     |

## Phase 0 — Instruments, and a ladder habit

### 0.1 `tools/bandit.py` — joint parameter selection, with a floor

Round 7's core test error was sweeping one knob at a time: `KAGG_LAND` alone loses, `MAX_HANDS` alone loses, and only the pair can win. The fix is best-arm identification over whole configurations, because we keep only the survivor and do not care what the rejected arms cost on the way.

The first draft then specified a budget that cannot work, and the review's simulation is the reason this section is rewritten. At 16 arms and 400 paired seeds, sequential halving gives 6 seeds per arm in round 1, whose half-width is about $4,481. It returns the true best arm 16% of the time for a $500 effect and 23% for an $800 one. Every effect this project has ever kept is in that range.

Constraints, all of them binding:

- **A floor of 40 paired seeds per arm in round one.** Below that the round is a coin flip, and a coin flip that eliminates arms is worse than no sweep.
- **At most 8 arms**, pre-screened by hand or by an earlier cheap round. The cartesian product of three knobs is not a field, it is a budget request.
- **Rank on the paired money difference between arms on common seeds**, with its own interval. Match points dichotomise the paired statistic at zero and cost roughly 57% more seeds for the same power; keep them as a reported diagnostic only.
- **Score every round against `--pool default`, not against `champion`.** `champion` is `main.py` byte for byte apart from its docstring, so a mirror sweep selects for exploiting our own glut curve. The log already has that failure: a herd tournament winner scored 96% points across seven specialists and then 38% on a fresh range, and was rejected as league overfitting.
- **No claim that the default arm is safe.** It is eliminated by noise like any other arm. If the survivor does not beat today's default in a direct paired run, nothing ships.
- **A per-run held-out offset**, not the fixed `HELD_OUT_START = 100_000` block that rounds 5 through 7d have already consumed. State the abort rule before the run: if confirmation fails, the arm is dropped and the round is not re-cut.

Realistic cost at the floor: 8 arms, 40 seeds, four rounds is roughly 2,000 paired seeds, about 2.5 hours on all cores at 1.5 s a match. That is the price of a decision that is not noise.

### 0.2 Labour cost per crop — build it, do not claim it exists

`tools/opstats.py` counts operation types globally, over one match at `seed=3`, with no attribution to a tile or a crop. It cannot produce a per-crop table, and the first draft was wrong to imply otherwise.

Round 7c also already built a labour estimator and reverted it, at +$576 +/- $1,282, indistinguishable from zero. Rebuilding it as a **measurement** is still worth it, because `KAGG_HANDS_PER_TILE = 0.34` is an assumption every land argument in the log rests on and the opponent's realised figure was `0.12`. Rebuilding it as a **planner gate** is not, until Phase 3 says otherwise.

### 0.3 Replay mining — separate track, start now

Own folder and own plan: [replays_kaggle/PLAN.md](../replays_kaggle/PLAN.md).

### 0.4 Submit weekly, starting now

The entry deadline is 2026-09-23, about five weeks out, and we have exactly one real episode of feedback. Freezing every gate to a mirror of ourselves for five weeks and then submitting is the same error round 7 exposed, repeated at a larger scale.

Submit the current best every week whatever its state, and treat the ladder result as the outer gate. The mirror and the pool stay as inner gates, because they are fast, not because they are the truth.

## Phase 1 — Selling as a schedule

First, because it touches only `_sell_orders` and can be falsified in a day.

- Per product, a schedule over the days left instead of a hold-or-dump flag. Town drain, own supply and the price curve are all known, so the optimum is a small dynamic program rather than a sign test on `scarcity`.
- Respect the order budget. Only the first 10 orders are processed and `HIRE` and `BUY_LAND` consume slots, so a per-product schedule competes for the same ten indices. A schedule that needs eleven orders is not a schedule.
- Use the floor deliberately. Units sold at $1 never enter market inventory, so floor sales carry no permanent impact.
- The melon race is a candidate, not free money. With both farms holding 112 melons the log prices selling first at $26,883 against $7,822 for selling second, but the one implementation tried scored +$994 +/- $1,369 and `KAGG_MELON_RACE` is off. The prize is in the timing rule, and a schedule is a better shape for it than a flag.

Gate: paired money difference against `--pool default` excludes zero at 200 seeds.

## Phase 2 — The objective becomes the difference

The game scores a ranking, so taking $10,000 off the opponent is worth what earning $10,000 is worth. Three parts, and only the first is done.

- **Measurement.** `bench.py` already reports the seat-pair difference and match points rather than our mean money. Two caveats found by review: `_binomial_ci` is dead code, the reported interval is a normal approximation, and the pool aggregate prints no interval at all. Fix the aggregate before leaning on it.
- **The opponent-supply split does not exist.** `_supply_forecast` sums both farms into one dict and uses its `player` argument only to decide whether to assume our own future fertilizer. `KAGG_OPPONENT_STOCK` is off by default and estimates their hidden shed, not their standing supply. Splitting the forecast by farm is the actual work of this phase.
- **Then the planner term.** With their supply separated, our marginal unit's effect on their realised price is computable. Weight it behind one knob and let the bandit choose the weight.

Two limits to state up front. Within one order index both players receive the same quote, so denial is never instantaneous; it works only through inventory that persists into later turns. And denial value is largest exactly where the curve is steepest — melon at `sq 3.60`, wool at `sq 3.20` — which is where our own worst historical mistake lives: "melon fell from 256 to 13 by day 15, and that crash is ours".

Gate: `--pool default` with an interval, not `champion`. A mirror rewards self-harm that a varied pool punishes.

## Phase 3 — The joint expansion: routing, land, hands, mix

One phase because the log says these cannot be measured apart. Routing alone lost 7/20 at 25 tiles. Land alone lost at every count. The winner's mix alone lost $48,000. The hypothesis is that they only pay together.

Order of work inside the phase:

1. **Capacity pre-check, before any code.** The shed holds 100 and overflow is discarded at the end-of-day drop. Twenty-five geese need 25 wheat resident per day and produce about 25 eggs a day, against a `KAGG_SHED_TARGET` of 70 and ten order slots a turn. If the arithmetic does not close, the goose line is dead on arrival and this phase is only routing plus land.
2. **Routing as macro-actions.** Replace the per-unit nearest-job search (`_tile_task`, `_priority`, `_step_toward`) with a daily assignment of tile clusters to units, walked as trips. Unit-to-cluster assignment is an assignment problem with an exact solution.
3. **Feed logistics that scale**, batched rather than one animal per trip.
4. **The joint bandit sweep** over land, hands, mix and goose count, at the Phase 0.1 floor, scored on the pool.
5. **The payback gate from 7d**, rebuilt in about forty lines from the log, only once tiles are actually being tended.

Gate: the joint configuration beats today's default on `--pool default --held-out`, and its improvement survives one ladder week. Nothing here ships on a mirror result alone.

## Phase 4 — Tune, validate, freeze

1. One joint bandit run over the survivors, at the floor.
2. Validation on a fresh held-out offset and on `--pool default`, with the abort rule stated before the run.
3. Freeze as `agents_1.0.x/v1_0_0_*.py`, update `README.md` and `EXPERIMENTS.md`, submit.

## Acceptance rules

- Two hundred paired seeds by default, four hundred before a version bump.
- A change is kept only when the paired money interval excludes zero **and** match points agree. The first draft allowed either, which let a 6-of-6 sweep report a 100% interval of zero width and clear the gate on no evidence.
- An interval of zero width means the sample is too small, never that the result is certain.
- `champion` is a mirror of `main.py`, so it is a smoke test, not a gate. The gates are `--pool default`, a fresh held-out offset, and the weekly ladder.
- The regression pool is entirely our own lineage. Add at least one opponent reconstructed from a real ladder replay before trusting it as a proxy for the field.

## What the adversarial review changed

Kept for the next reader, because the first draft read as confident on all of it:

- The plan rested on one game and called it a diagnosis.
- Phase 1 was routing alone, which the log had already run and lost.
- Phase 3 claimed `_supply_forecast` separated the opponent's supply. It does not.
- Phase 0.2 claimed `opstats.py` already had the accounting. It does not.
- The bandit's budget made round one a coin flip, and the "cannot pick something worse" claim was false.
- The acceptance rule's `or` was a hole that a zero-width interval walks straight through.
- `--held-out` is a fixed constant that six rounds have already used.
- The above-target figures were wrong: melon is 3.60 and wool 3.20, not 1.60.
- Shed capacity, the ten-order cap and the five-week deadline were absent.

## Open questions

- Does the capacity arithmetic in Phase 3.1 close at all? If it does not, the field's strategy is not available to us and the plan needs a different answer to scale.
- Can a real opponent be reconstructed from a ladder replay well enough to sit in `DEFAULT_POOL`?
- What is the actual per-crop labour cost, and does it move the land answer once routing exists?
- Is the herd still correct at 100 tiles, or does the pasture belong to wheat once routing works?
- Does the floor's free disposal enable deliberate denial, given that denial only acts across turns and not within an order index?

## Results

Run on 2026-08-19, in one session, with `tools/bandit.py` as the instrument.

| Phase | Outcome |
|:------|:--------|
| 0.1 bandit | Built. Floor of 40 paired seeds, ranked on paired money, pool-scored, confirmation on fresh seeds. It caught one league-overfit arm and dropped it |
| 0.2 labour | Built as `tools/labour.py`, and it turned out to be the whole game: 0.34 hands per tile was wrong by a factor of five for crops |
| 0.3 replays | Built. 26 episodes fetched, summarised and read. They refuted round 7's wheat-and-eggs reading |
| 1 sell schedule | **Falsified.** 62% points against 91% for the default, 84% with an absorption gate. Reverted |
| 2 difference objective | **Falsified.** Loses monotonically in the weight, and is exactly neutral in a mirror. Reverted |
| 3 routing | **Confirmed.** Trip radius 2, movement 65.0% to 56.8%, frozen as 0.23.0 |
| 3 land | **Confirmed, on the third attempt.** One quadrant, 12 hands, herd of 6 and 4, +$5,614 +/- $2,208 |
| 4 freeze | Done. 1.0.0 beats the submitted 0.22.0 by 80% points and +$6,698 +/- $1,365 |

What the plan got wrong: it treated products as the thing to change, on the
evidence of one lost episode. The replays say the opposite — the field sells the
same premium goods we do and simply farms more board. Banning melon scored 15%
points and -$33,366.

What is still open: the second quadrant loses at every hand count tried, and the
weed count is the clearest single gap against the ladder — zero to two on 75
tiles for the top players, three to eight on 25 for us.

## Round 9 addendum

The land question was reopened because the ladder replays show every strong
player buying quadrants. It produced one bug fix and one clear diagnosis.

The bug: `PLACE` ranked below watering, so bought livestock was carried all day
and never put down, and the buy logic counted the carried animals as stock. The
herd froze at five. Fixing the priority is worth +$3,408 +/- $2,099 and shipped
as 1.1.0.

The diagnosis: at 75 tiles plants die of thirst at 8% per tile-day against 3% at
home, and 99 of 127 weeds are dead plants rather than overgrown ground. Five
candidate fixes were measured and all lost — alternate-day watering, quadrant
zoning, a higher hiring ceiling, a smaller seed reserve, and a cash-crop opening.
The remaining lever is the one Phase 3 named and did not deliver: a unit's day
planned as a route over a cluster, not as a sequence of nearest jobs.
