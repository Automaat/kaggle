# Kaggle replays — fetch, summarize, view

**Status: all three steps built and run on 2026-08-19.** 26 episodes are stored gzipped in `episodes/`, summarised in `summaries/`, and `view.py --ladder` prints the cross-episode table that refuted round 7's product theory.

Phase 0.3 of [agents_1.0.x/PLAN.md](../agents_1.0.x/PLAN.md), as its own track. It changes no agent code and blocks nothing.

Local self-play replays stay in `replays/`. Everything downloaded from Kaggle lives here.

## What the ladder already gives us

Checked on 2026-08-19 against the live competition, not from memory:

- Submission `55630506` already has **16 episodes**, one validation and the rest public, produced in about one hour of laddering.
- The Kaggle CLI covers the whole path with no scraping: `kaggle competitions submissions -c kaggriculture`, then `kaggle competitions episodes <submission_id>`, then `kaggle competitions replay <episode_id> -p <dir>`.
- `kaggle competitions logs <episode_id> <agent_index>` returns our agent's own logs from a real ladder game. Nothing else in the repo can see that.
- `kaggle competitions leaderboard kaggriculture --show` gives team ids, and `kaggle competitions team-submissions <team_id>` gives their active submissions — which is the route to a top player's episodes.

A replay is a single JSON of **22 MB** with `steps` of 720 entries. Each step holds both seats' `observation` and `action`, so every tile, price, shop and order of both players is readable turn by turn. `info` carries the team names and, importantly, **`seed`** — the same seed our own `tools/runner.py` accepts, so any ladder game can be re-run locally against a candidate agent.

## Layout

| Path           | Role                                                               |
|:---------------|:-------------------------------------------------------------------|
| `episodes/`    | Downloads, gzipped: 0.4 MB each, 22 MB raw. Git-ignored for now    |
| `summaries/`   | Derived per-episode JSON and the cross-episode roll-up. Committed  |
| `fetch.py`     | Sync new episodes for our submissions and for a watchlist of teams |
| `summarize.py` | Raw replay to one small committed record; the raw stays gzipped    |
| `view.py`      | Read one episode day by day                                        |

## Step 1 — `fetch.py`

- Resolve our submission ids from `kaggle competitions submissions`, and watchlist teams from a checked-in `teams.json` of leaderboard team ids.
- List episodes per submission, skip every id already in `episodes/` or already summarized, download the rest.
- Write an `index.json`: episode id, submission id, both team names, both final rewards, our seat, the seed, the download time.
- Idempotent by construction, so a re-run costs one listing call per submission and nothing else.

## Step 2 — `summarize.py`

One record per episode, small enough to commit, holding per player and per day:

- Tiles owned, tiles planted by crop, animals by kind, hands hired.
- Units sold per product, revenue per product, and the market price per product at day end.
- Money at day end, and the final result.

What the roll-up can and cannot answer, because the sample is not a sample of the field:

- Sixteen episodes from one submission, paired by rating, with a provisional rating for most of them. That is our own rating neighbourhood, so the prevalence of any strategy in it is not the prevalence in the competition. Treat every strategy observation as an existence proof, never as a frequency.
- The price curves in these episodes contain our own melon dump, so the shared curve seen there is conditional on our policy, not a property of the market.
- What the sample does support: reconstructing a specific opponent's behaviour turn by turn, and rebuilding it as a bench opponent. That is the highest-value output of this track, because `DEFAULT_POOL` is currently our own lineage end to end.

Keep the raws. The first draft said summarize then delete, on the argument that the seed reproduces the map — which is false in the part that matters: the seed reproduces the **world**, not the opponent's policy, and the opponent's per-turn actions are the only reason to hold a ladder replay at all. The storage premise was also wrong by 55 times: `gzip` takes the measured 22,452,841-byte replay down to **405,865 bytes**, so sixteen episodes are 6.5 MB compressed. Store them gzipped, and delete a raw only once a turn-level schema exists that provably captures what it held.

## Step 3 — `view.py`

- Replay one stored episode day by day: money, tiles, herd, hands, prices, units sold, in the shape `tools/trace.py` already prints, so both read alike.
- A board snapshot per day, both farms side by side, to see where the opponent's tiles are and when they go to weeds.
- Filter by player, by day range and by product.

## Do we automate it?

**Yes for fetching, no for conclusions.**

Fetch is idempotent, cheap and time-sensitive: the ladder produced 16 episodes in about an hour, and episodes are the only source of real-opponent evidence we have. A missed window is data we cannot recover later.

- A **daily** sync is enough. Polling every few minutes buys freshness we have no use for, and every game is preserved on Kaggle in the meantime.
- Run it also on demand right after a new submission, because that is when a new strategy is being priced by real opponents.
- Storage is not the constraint it looked like: gzipped, a replay is 0.4 MB, so a week of unattended syncing is tens of megabytes. `fetch.py` gzips on download and keeps everything.
- Do not automate the reading. The roll-up is a report; the decision about what it means belongs in `EXPERIMENTS.md` with everything else.

Mechanism, once the three scripts exist and have run by hand at least twice: a scheduled daily run of `fetch.py --summarize`, which appends to `summaries/` and leaves a one-line diff when nothing new arrived. Not before then — a scheduler around an unproven script produces silent failure, not data.

## Open questions

- What is the episode rate per day once the submission settles, and does it fall off after the first hours?
- Are opponent episodes reachable through `team-submissions` for every leaderboard team, or only for teams with a public active submission?
- Is there a rate limit on `replay` worth respecting, and does a 22 MB download count against the same budget as a listing call?
- Do agent logs from `competitions logs` include our own stderr from the ladder run? If they do, that is the only crash evidence we will ever get from a real match.
