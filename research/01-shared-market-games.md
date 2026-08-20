# How to play a two-player shared-market game

What kind of game Kaggriculture is, what that classification already tells us, and which agent architectures people actually use for it. Written 2026-08-19, after the first real-opponent loss recorded in [EXPERIMENTS.md](../EXPERIMENTS.md).

## 1. The game has a name — several, from different fields

No single label covers it. Four fields name it four ways, and each name comes with its own toolbox.

### Game theory: a general-sum simultaneous-move stochastic game

The formal class is a **stochastic game**, also called a **Markov game**: several players, a state, both players choose an action at the same time each round, the state moves on, everyone collects a reward. Kaggriculture is the two-player case with 720 rounds and a small amount of randomness (weeds, shop draws).

Two qualifiers matter:

- **Simultaneous-move.** Neither player sees this turn's action before committing. This makes it an imperfect-information game in the technical sense, even though almost everything about the board is public.
- **Near-perfect information.** Both farms, the market and the town are fully visible. Only the opponent's shed, seeds and carried inventory are hidden.

The equilibrium concept for this class is **Markov perfect equilibrium**. Computing one in a general-sum stochastic game is PPAD-hard even for small games, so "just solve it" is not on the table. What survives is approximation.

### The payoff twist: general-sum rewards, zero-sum outcome

This is the single most useful structural fact about the game and it is easy to miss.

The **reward** is money, and money is general-sum. Both players can get richer together — that is what happens when the town drains a product neither of them has glutted. Both can get poorer together — that is a melon crash.

The **outcome** is a ranking. Only "who has more" is scored. So the game we are actually playing is **zero-sum over the difference in bank balances**, not over the balances themselves.

Everything follows from that:

- Destroying $10,000 of the opponent's revenue is worth exactly as much as earning $10,000. Both move the difference by $10,000.
- Dumping a product into the floor is not automatically a mistake. It is a mistake only if it costs us more than it costs them.
- Maximising our own money is the wrong objective function. Our own benchmark already warns about this from the empirical side: mean money is not the score, win rate is, because a weak opponent lifts both means. The game-theoretic reason is that the two players share a price curve.

### Economics: a dynamic Cournot oligopoly with capacity investment

The market coupling is exactly **Cournot competition**: firms choose quantities, a shared inverse-demand curve turns total quantity into a price, and each firm's output lowers the price the other one receives. Kaggriculture adds two layers on top:

- **Capacity investment.** Land, animals and structures are capacity bought in advance. The literature on Cournot with capacity precommitment finds that commitment helps the *smaller* firm and hurts the *larger* one, and that repeated capacity investment destabilises the Cournot equilibrium and pushes the industry toward collusive output levels.
- **Multi-period supply.** Products keep their price curve across the whole season, so a sale today permanently changes the price available tomorrow. The curve is stateful, not per-turn.

The practical reading: capacity decisions are strategic commitments visible to the opponent, and per-product depth (`T` in the price table) is the real capacity constraint, not tiles.

### Trading: an optimal execution problem

Selling into a curve that moves against you as you sell is the **optimal execution** problem from algorithmic trading, whose standard treatment is the **Almgren–Chriss** model. It trades off market impact (sell fast, get a worse price) against timing risk (sell slow, the price may move first) and yields a schedule rather than a single decision.

Two components in that model map onto the game directly:

- **Temporary impact** — the price we get for the units in this one order block.
- **Permanent impact** — market inventory rises and stays risen, so the price for every later sale is lower.

In Kaggriculture there is no bid-ask spread and no recovery: every sale is permanent impact. That makes the problem *harder* than the trading version, not easier, and it means "sell the shed when full" is a schedule chosen by accident.

The one place the game gives impact back is the $1 floor: units sold at the floor are not added to market inventory. Selling at the floor is free of permanent impact, which is a genuine, exploitable asymmetry.

### Genre: an economic engine-builder with a shared dynamic market

In game-design terms this is an **engine-building economic game with a dynamic commodity market** — the family that includes Power Grid, Container, Arkwright and Food Chain Magnate. The defining mechanic is exactly ours: prices move on a fixed supply-demand curve driven by what players actually do, so a commodity everybody produces crashes and a commodity nobody produces spikes.

The genre's known strategic staples are worth stating because they are testable claims about our game:

- Take the product nobody else is taking, even at a worse base rate.
- Early compounding beats late optimisation.
- Watch the opponent's engine, not the score.

### Kaggle: a "simulation competition"

Kaggle runs one RTS-like simulation competition a year — Halite, Lux AI, Kore, Lux AI Season 2, Orbit Wars, and now Kaggriculture. Scoring is a skill rating from pairwise matches, not a metric on a fixed dataset.

## 2. What the classification already tells us

Before choosing any architecture, the taxonomy pays out:

| Fact about the class | Consequence for the agent |
| :--- | :--- |
| Zero-sum outcome over general-sum rewards | Optimise the **difference**, not our own money |
| Simultaneous moves | A pure best response to a predicted opponent is exploitable; some hedging is correct |
| Cournot coupling | Our own marginal unit lowers our own price — price every allocation at the margin |
| Capacity precommitment | Land and animals are visible commitments; the opponent can and will react |
| Permanent price impact | Every sale is a permanent decision; sale *timing* is a first-class strategy |
| Price floor absorbs impact | Sales at $1 do not deepen the glut — the floor is a free disposal channel |
| MPE is PPAD-hard | Approximate. Do not look for the "right" strategy |

## 3. The architectures people actually use

### Rule-based / heuristic

Hand-written policy, usually with a hand-tuned evaluation of what each action is worth.

Kaggle's own record is the strongest argument for it. **Halite, Kore and Lux AI Season 2 were all won by rules-based agents.** Only Lux AI Season 1 was won by deep RL. In a game with a fully known forward model and a hard-to-explore action space, hand-written domain logic remains competitive at the top of the ladder.

The weakness is the one that has already bitten this project: heuristics encode assumptions, and assumptions get validated only against whatever you test them on. Our land rejection survived six rounds of self-play and died on contact with the first real opponent.

### Search with a forward model (MCTS, beam search, MPC)

We have a perfect simulator, so we can search. The blocker is the branching factor: 9 units × ~15 ops × 10 market orders per turn is astronomically wide, and the horizon is 720 turns.

The standard fixes are all abstractions:

- **Macro-actions / options.** Search over "plant this tile with melon and tend it to harvest" instead of over `NORTH`. A macro-action is treated as a single action, which deepens the tree and cuts branching. This is the main lever in RTS-style domains.
- **State and action abstraction.** Group equivalent tiles and units; search the abstract game.
- **Informed / CMAB-based sampling.** Bias the search with a hand-written prior instead of sampling uniformly.
- **Receding horizon.** Plan the next few days, execute one, replan. This is model predictive control, and it fits a game where the market state drifts under you.

Our current planner is already a degenerate case of this: a one-step greedy allocation with a hand-rolled price forecast. Making it a real search means macro-actions plus replanning, not a deeper tree over raw ops.

### Deep reinforcement learning by self-play

Feasible but expensive, and the class of game is the hard one — general-sum, simultaneous-move, long-horizon, sparse terminal reward.

What worked for the one DRL win on Kaggle (Lux AI S1): a **GridNet action space** (one action head per board cell, so the output does not blow up combinatorially), **reward shaping** early then sparse win-loss reward later, curriculum from a small map to full size, and an actor-critic algorithm (IMPALA with UPGO and TD(λ) terms) rather than plain PPO.

That is a serious engineering programme. The honest read for a side project: not the first thing to try, and it needs the reward shaped around the *difference* in money, not our own money, or it will learn to farm rather than to compete.

### Imitation learning from replays

The cheapest way to import strategy that we do not have.

Concrete recent evidence from Kaggle's Orbit Wars: a behavioural clone trained on one top player's replays reached **49th of about 5,000 teams**, with a reported 8 minutes of training; a stronger version was a ~2.3M-parameter attention network trained on pooled winner-states from the top ~10 ladder players, conditioned on a teacher identity plus a recency scalar.

For us the value is not necessarily a cloned policy. It is that replays are downloadable, and one replay already overturned a conclusion six rounds of self-play could not. Mining replays for *what the field does* is a research tool before it is a model.

The known failure mode is **causal confusion**: a clone trained on observation histories learns to copy its own previous action rather than the expert's reason for it.

### Inner optimisation

Independent of the strategy layer: within a turn, assigning N units to M tasks is an assignment problem, and choosing what to sell against a stateful curve is a scheduling problem. Both have exact solutions and neither needs learning. This is where hand-written agents leak value quietly.

## 4. What this says for Kaggriculture specifically

1. **Change the objective to the difference.** Every evaluation we have measures our money. The game scores the gap. This is a one-line change in the benchmark and a deep change in the planner.
2. **Treat selling as an execution schedule.** We decide *whether* to hold or dump per product. Almgren–Chriss decides *how much per period*. The floor rule (no impact at $1) is a free disposal channel we currently do not exploit deliberately.
3. **Reconsider capacity as commitment, not cost.** Land was rejected on a labour argument that a real opponent refuted with 12 hands on 100 tiles. Cournot with capacity precommitment says the smaller producer benefits from committing — which is us.
4. **Pick products by curve shape, not base price.** Egg and wheat cannot be glutted (`log` above `I0`); strawberry, melon, milk and wool collapse (`above_target > 1`). Scaling is only available on the first group. This is the same conclusion the lost episode produced, arrived at from theory.
5. **Add search where it is cheap.** Macro-actions over tile plans with a few-day receding horizon, evaluated on the money difference, is a realistic upgrade to the current greedy planner. Full MCTS over raw ops is not.
6. **Mine replays before training on them.** Start this now, as a track of its own: it changes no agent code and blocks nothing, and one replay already overturned a conclusion six rounds of self-play could not. The immediate value of leaderboard replays is diagnostic. Behavioural cloning is a later option and brings its own failure modes.
7. **Choose parameters by best-arm identification, not by sweeping one knob at a time.** Land, hands and mix only win together, so the arm is the whole configuration and the field is the cartesian product. Because we only keep the survivor, the objective is best-arm identification under a fixed budget, not the regret of the arms tested on the way — which is sequential halving, not UCB. Each round plays the live arms on one fresh block of paired seeds and drops the worse half, so the budget concentrates where the decision is still open — with the caveat that the first round needs enough seeds to separate the arms at all, or halving is just an expensive coin flip. This is the cheapest form of learning available to the agent, and the only one already justified by our own test errors.

## 5. Open questions

- Is full-land wheat-and-eggs the field's dominant strategy, or one player's idea? Needs more replays.
- Does the floor's free-disposal rule enable a deliberate denial strategy — crash a product we do not sell, to strand an opponent who does?
- What is the actual actions-per-tile-per-day cost by crop? Every labour conclusion in the log rests on an assumed flat 0.34.

## Sources

- [A Competition Winning Deep Reinforcement Learning Agent in microRTS](https://arxiv.org/html/2402.08112v1) — Kaggle simulation competition history; Halite, Kore and Lux S2 won by rules-based agents, Lux S1 by DRL
- [Kaggle_Lux_AI_2021](https://github.com/IsaiahPressman/Kaggle_Lux_AI_2021) — the winning DRL agent: GridNet action space, IMPALA with UPGO and TD(λ), reward shaping and map curriculum
- [Top 50 Imitation Learning Solution, Orbit Wars](https://www.kaggle.com/competitions/orbit-wars/writeups/top-50-imitation-learning-solution) — behavioural cloning from top-ladder replays, 49th of ~5,000
- [Fighting Copycat Agents in Behavioral Cloning from Observation Histories](https://proceedings.neurips.cc/paper/2020/file/1b113258af3968aaf3969ca67e744ff8-Paper.pdf) — causal confusion in behavioural cloning
- [On the complexity of computing Markov perfect equilibrium in general-sum stochastic games](https://academic.oup.com/nsr/article/10/1/nwac256/6840228) — stochastic/Markov game definitions, MPE, hardness
- [Monte Carlo Tree Search in Simultaneous Move Games](https://dke.maastrichtuniversity.nl/m.winands/documents/wcg13-smmcts.pdf) — MCTS for simultaneous-move games
- [Monte Carlo Tree Search: a review of recent modifications and applications](https://link.springer.com/article/10.1007/s10462-022-10228-y) — action abstraction, macro-actions, CMAB sampling for large action spaces
- [Evolving MCTS Macro-actions in Real-Time Domains](https://link.springer.com/chapter/10.1007/978-981-95-4972-6_29) — macro-actions deepen search and cut branching
- [Optimal Execution Strategies — Almgren-Chriss Model](https://questdb.com/glossary/optimal-execution-strategies-almgren-chriss-model/) — temporary vs permanent impact, execution schedules
- [Capacity precommitment and price competition yield the Cournot outcome](https://www.sciencedirect.com/science/article/abs/pii/S0899825605001090) — capacity precommitment
- [Dynamic Cournot duopoly with intertemporal capacity constraints](https://www.sciencedirect.com/science/article/abs/pii/S0167718711000798) — commitment helps the smaller firm
- [Capacity Constraints and Investment Decisions under Cournot Competition](https://www.researchgate.net/publication/23754857_Capacity_Constraints_and_Investment_Decisions_under_Cournot_Competition) — repeated capacity investment destabilises Cournot equilibrium
- [Best of Both Worlds: Regret Minimization versus Minimax Play](https://arxiv.org/abs/2502.11673) — best response is exploitable; regret minimisation bounds the loss
- [Almost Optimal Exploration in Multi-Armed Bandits](https://proceedings.mlr.press/v28/karnin13.pdf) — sequential halving: fixed-budget best-arm identification, no tuning parameters
- [Non-stochastic Best Arm Identification and Hyperparameter Optimization](https://proceedings.mlr.press/v51/jamieson16.pdf) — successive halving as the allocation rule for expensive noisy evaluations
- [Economic Board Games: The Complete Guide](https://www.smoothiewars.com/blog/431-economic-board-games-guide) — engine-building with dynamic commodity markets as a genre
