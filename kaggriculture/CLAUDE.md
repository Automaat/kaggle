# Kaggriculture

Agent for the Kaggle [Kaggriculture](https://www.kaggle.com/competitions/kaggriculture) simulation competition.
Two farms share one 10x10 board and one market for 30 days (720 turns, 24 per day). Most coins wins.

`main.py` is the submission. It must keep a top-level `agent(obs)`. Nothing else is uploaded.

## Language

Reply in Polish in this repository. This overrides the global English rule.
Code, identifiers, commit messages, file names and every committed document stay in English.

## Read first

| File                       | Why                                                            |
|:---------------------------|:---------------------------------------------------------------|
| `README.md`                | Current agent, layout, findings from the simulator, open leads |
| `EXPERIMENTS.md`           | Every idea tried and how it scored. Check before proposing one |
| `RULES.md`                 | The rule set. Use it instead of reading `.venv`                |
| `agents_1.0.x/PLAN.md`     | The plan for the next version                                  |
| `replays_kaggle/README.md` | How ladder replays are pulled and summarised                   |

## Commands

```bash
mise run install                                     # uv sync
uv run python tools/play.py main.py champion 42      # one match, writes replays/*.json
uv run python tools/bench.py main.py                 # paired, seat-swapped, all cores
uv run python tools/bench.py main.py --pool default --held-out
uv run python tools/bandit.py --grid KAGG_LAND=1,2 --floor 40
uv run pytest -q tests/
```

Opponents: `pass`, `random`, `starter`, `champion`, or a path to a `.py` with `agent(obs)`.
`champion` is `runner.CHAMPION`, the frozen current best. Judge against it, never against `starter`.

## Measuring a change

- Judge on **win rate against a fixed opponent**, not mean money. Both farms share one market, so the opponent's strength moves both means together.
- `bench.py` is paired: same seeds, seats swapped, confidence interval on the per-seed difference. The interval is about $1,400 at 60 seeds, $800 at 200, $550 at 400. Read the interval, not the mean.
- 200 paired seeds by default. 400 before a version bump.
- The champion is a mirror, so a candidate can win it by exploiting the champion's own glut curve. Confirm every kept change on `--pool default --held-out` before freezing a version.
- Use `bandit.py` when knobs only pay together (land, hands, herd). It ranks on paired money difference against the pool and confirms the survivor on a fresh seed block.
- A result that does not survive the held-out confirmation gets reverted and written down, not kept.

## Versioning

Semantic. A **minor** bump is a tuning round. A **major** bump means the strategy itself changed.

Freezing a version: copy `main.py` to `agents_1.0.x/vX_Y_Z_<name>.py`, keep it as a benchmark opponent, and record the measurement in `EXPERIMENTS.md`.

## Knobs

Configuration is read from `KAGG_*` environment variables, which is what the sweep tools drive.
`KAGG_MIX_0` / `KAGG_MIX_1` only reach the fixed planner (`KAGG_PLANNER=fixed`). Under the default dynamic planner the crop choice is priced per tile, so bias it with `KAGG_BAN`.

## The ladder

The ladder is the only gate that is not our own mirror. Submit weekly.

```bash
uv run kaggle competitions submit kaggriculture -f main.py -m "<version> <what changed>"
uv run python replays_kaggle/fetch.py       # sync new episodes, idempotent
uv run python replays_kaggle/view.py --ladder
```

A submission starts near rating 600 and needs a few dozen episodes to settle, so do not read a fresh score as a verdict.
`kaggle competitions logs <episode_id> 0` returns our own agent's stdout, stderr and per-turn duration from a real ladder game. Nothing local can see that. Do not leave downloaded logs or raw replays in the repo; `replays_kaggle/episodes/` is git-ignored and raw replays are 22 MB each.

An episode `info` carries the `seed`, and `tools/runner.py` takes the same seed, so any ladder game can be re-run locally against a candidate.

## Writing agent code

- One file, standard library only at run time. `kaggle-environments` is for the harness, not for `main.py`.
- Keep the per-turn budget clear. Ladder turns run at about 1.5 ms mean and 70 ms on the first turn.
- The agent must never raise. A crash forfeits the episode.
