[![make.fun](https://make.fun/badge/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122.svg)](https://make.fun/bounty/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122)
[![rank](https://make.fun/badge/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122/biasa-aja-2/pump-fun-replay-sprint-bot-2/rank.svg)](https://make.fun/bounty/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122)
[![reward](https://make.fun/badge/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122/biasa-aja-2/pump-fun-replay-sprint-bot-2/reward.svg)](https://make.fun/bounty/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122)

# Ridge Runner — Pump.fun Replay Sprint Paper Bot

A selective momentum rider for make.fun's [Pump.fun Replay Sprint — Paper Bot, Real Ranking](https://make.fun/bounty/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122) bounty ($2 total, top 3). Pure Python standard library only; the grader runs `python3 -P -s main.py` under the `agent-fun/replay/v2` harness with 10 SOL of paper money. Nothing is traded for real.

## Strategy

The bot watches every coin from its `CreateEvent` and buys only the ones that have already shown genuine demand:

- old enough that the creation frenzy is over (20–40 slots after creation),
- bought by at least 12 distinct wallets other than the creator,
- carrying at least 3 SOL of net buy flow,
- sitting on at least 5 SOL of real reserves,
- up 2.5×–4.0× from its creation price (a healthy run, not a vertical spike),
- with a creator who has not sold and has not bought much (≤ 2.5 SOL in the create slot).

One fixed 0.12 SOL buy per coin, at most five positions open. Then the coin is left alone: **there is no take-profit**. Winners ride until the curve completes (the replay closes them there) or a stop rule fires; losers are cut by a 10% stop-loss, a 500-slot hold cap, a 300-slot staleness rule, or a creator exit.

Why no take-profit: on this tape the profit lives in the handful of coins that run all the way to curve completion. Every fixed take-profit tried in earlier work on the same dev window finished negative, because capping a winner removes the one payoff that covers the slow bleed of the other ~85% of trades. The stop-loss is the only price exit, and it is wide on purpose.

The entry filters were tuned by a 43-variant parameter sweep on the dev window (see `sweep-results-replay.txt` in the build log): the winning variant (v43) combines the tight demand filters with a 2.5×–4.0× run-up band and a 0.12 SOL stake.

## Files

- `main.py` — the bot (pure stdlib; the `vendor/makefun_bot.py` shim is the kit's own protocol shim, copied unchanged from the kit's `sdk/python`).
- `vendor/makefun_bot.py` — agent-fun/replay/v2 protocol shim from the competition kit.

## Dev-window result (grade-local, `--driver process`, two executions, action logs byte-identical)

- Net PnL: **+0.420074 SOL** (+4.20%)
- 90% interval: −0.548 .. +1.597 SOL
- Trading: 72 fills over 37 coins, 37 round trips, win rate 18.9%, max drawdown 0.0435
- Fees: 0.116 SOL curve + 0.0078 SOL tx; at 2× fees net PnL still +0.292 SOL
- Guardrails met: 37 distinct mints with round trips ≥ 0.01 SOL (≥ 20 required), max drawdown well under 50%
- result_sha256: `7aaeec000cf7df5761b2dd5404eb0b551b9553d499c1706985cb9725dc6e84e7`

The dev window is a single 24-hour sample and the final window is fresh data collected after entries close, so treat this as evidence, not a promise. The score leans on a few coins: the top 3 coins made 89% of the gains, and without them net PnL is −0.67 SOL. No placing is promised.

## Referral

Built with [make.fun](https://make.fun/r/x7fx69rd) — referral code `x7fx69rd`.
