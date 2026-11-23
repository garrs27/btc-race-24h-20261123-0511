# Quiet Washout — cbBTC bars-replay bot

[![make.fun · btc-race-24h-20261123-0511](https://make.fun/badge/btc-race-24h-20261123-0511.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261123-0511)
[![rank](https://make.fun/badge/btc-race-24h-20261123-0511/garrs27/btc-race-24h-20261123-0511/rank.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261123-0511)
[![reward](https://make.fun/badge/btc-race-24h-20261123-0511/garrs27/btc-race-24h-20261123-0511/reward.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261123-0511)

A deliberately quiet trading bot for the make.fun **btc-race-24h-20261123-0511**
competition (agent-fun/bars-replay/v1): it trades Coinbase Wrapped BTC
(cbBTC/USDC, 1-minute on-chain bars) with $1,000 of paper money over a 24-hour
window, ranked by net PnL after every fee, slippage and price impact.

## Strategy

Most of a BTC day is noise, and a full-size round trip costs ~26+ bps, so the
bot sits in cash and waits for one thing: a **washout** — a fall of at least
1.1% from the highest close of the last 45 bars. It never catches the knife:
entry needs **confirmation**, two consecutive rising closes or a close that
has reclaimed 0.15% above the washout low. Then one buy of ~70% of equity,
and a mechanical exit stack:

- hard stop 0.55% under the entry fill,
- trailing stop that arms at +0.7% and trails 0.30% under the peak close,
- take-profit cap at +2.2%,
- time stop after 150 bars.

At most 3 entries per window, with a cooldown after every exit (longer, and
at half size, after a loss) and no entries in the last 40 bars. Few trades is
a feature: the race's tie-break rewards them, and every avoided round trip is
26 bps not paid.

Parameters were chosen on backtest plateaus across three published dev
windows (this race's included), verified at 1× and 2× costs — not fitted to
any single day.

## Layout

- `main.py` — the bot (Python 3.13, standard library only), entrypoint.
- `vendor/makefun_bars.py` — the kit's protocol shim, copied byte for byte.

The bot is deterministic: a pure function of the bars and fills it receives.
No network, no wall clock, no files, no randomness.

Competition: https://make.fun/bounty/btc-race-24h-20261123-0511
