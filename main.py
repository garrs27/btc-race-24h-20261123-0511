"""Washout-and-two-uptick cbBTC bounce bot for agent-fun/bars-replay/v1.

Strategy (calibrated by backtest on three published dev windows, this race's
included, at 1x and 2x costs): BTC on 1-minute bars spends most of a day going
nowhere, so this bot waits in cash for a genuine washout -- a fall of at least
DROP_BPS from the highest close of the last LOOKBACK bars -- and only buys
after the fall has stopped: two consecutive rising closes, or a close that has
reclaimed RECLAIM_BPS above the washout low. One order, most of the equity,
then a mechanical exit stack (see manage_exit). At most MAX_ENTRIES entries
per window: in a 24-hour race with a 26+ bps round-trip cost, selectivity is
the edge and the tie-break rewards few trades.

Deterministic: a pure function of the bars and fills received. No clock, no
environment, no randomness, integer/raw-price arithmetic throughout.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars

SYMBOL = "CBBTC"

# --- locked parameters (backtest plateaus, not single-window spikes) ---
DROP = 0.011          # washout depth: fall from the lookback high, peak to trough
LOOKBACK = 45         # bars over which the pre-washout high is taken
LOW_MAX_AGE = 10      # the washout low must be within this many bars
RECLAIM = 0.0015      # alternative confirmation: close this far above the low
SIZE = 0.70           # share of equity spent on an entry
SLIPPAGE_BPS = 300    # max_slippage_bps named on every order
MAX_ENTRIES = 3       # hard entry budget per window
LATE_CUTOFF = 1400    # no new entries once this many bars have been seen
COOLDOWN_WIN = 60     # bars before another entry after a winning trade
COOLDOWN_LOSS = 240   # bars before another entry after a losing trade
RETRY_WAIT = 30       # bars to wait after a rejected buy before re-arming


class Bot:
    """All strategy state; rebuilt from the hello by reset()."""

    def __init__(self):
        self.closes = []          # raw integer closes of CBBTC, in arrival order
        self.bars_seen = 0
        self.pending = None       # None, "buy" or "sell": one order in flight at most
        self.entries = 0
        self.size_factor = SIZE
        self.cool_until = -1      # bars_seen before which no new entry is allowed
        self.consumed_low = -1    # absolute closes index of the low already traded
        self.entry_px = None      # raw fill price of the open position
        self.peak = 0             # highest raw close since the position opened
        self.held_bars = 0        # bars the position has been held

    # -- fills -------------------------------------------------------------
    def process_fills(self, fills):
        """Fold the fills of the previous batch into the strategy state."""
        for fill in fills:
            if fill.get("symbol") != SYMBOL or self.pending is None:
                continue
            side = fill.get("side")
            if side != self.pending:
                continue
            if fill.get("status") == "filled":
                price = int(fill["price"])
                if side == "buy":
                    self.entry_px = price
                    self.peak = price
                    self.held_bars = 0
                else:
                    won = self.entry_px is not None and price > self.entry_px
                    if won:
                        self.cool_until = self.bars_seen + COOLDOWN_WIN
                        self.size_factor = SIZE
                    else:
                        self.cool_until = self.bars_seen + COOLDOWN_LOSS
                        self.size_factor = SIZE / 2
                    self.entry_px = None
                    self.peak = 0
                    self.held_bars = 0
            else:  # rejected: nothing changed hands; re-arm after a wait
                if side == "buy":
                    self.cool_until = max(self.cool_until, self.bars_seen + RETRY_WAIT)
                self.entry_px = self.entry_px if side == "buy" else self.entry_px
            self.pending = None

    # -- entry detection ---------------------------------------------------
    def detect_entry(self, state):
        """Return a buy order for a confirmed washout, or None."""
        closes = self.closes
        if self.entries >= MAX_ENTRIES:
            return None
        if self.bars_seen >= LATE_CUTOFF or self.bars_seen < self.cool_until:
            return None
        if len(closes) < LOOKBACK + 10:
            return None
        win = closes[-(LOOKBACK + 1):]
        hi = max(win)
        hi_abs = len(closes) - len(win) + win.index(hi)
        seg = closes[hi_abs:]
        lo = min(seg)
        lo_abs = hi_abs + seg.index(lo)
        if lo_abs <= hi_abs + 1 or lo_abs == len(closes) - 1:
            return None  # low is the latest bar or never followed the high
        if lo_abs == self.consumed_low or len(closes) - 1 - lo_abs > LOW_MAX_AGE:
            return None
        if lo / hi - 1 > -DROP:
            return None  # fall not deep enough: this is the whole edge
        last = closes[-1]
        two_up = closes[-1] > closes[-2] > closes[-3] and lo_abs <= len(closes) - 3
        reclaim = last >= lo * (1 + RECLAIM) and closes[-1] > closes[-2]
        if not (two_up or reclaim):
            return None  # still falling: never catch the knife
        notional = int(state.equity * self.size_factor)
        notional = min(notional, state.cash, state.weight_room(SYMBOL))
        if notional < 1_000_000:  # below the 1 USDC minimum buy
            return None
        self.consumed_low = lo_abs
        self.entries += 1
        self.pending = "buy"
        return {
            "side": "buy",
            "symbol": SYMBOL,
            "notional_quote_units": notional,
            "max_slippage_bps": SLIPPAGE_BPS,
        }


BOT = Bot()


def on_hello(state):
    BOT.__init__()


def on_batch(state, batch):
    BOT.process_fills(batch.get("fills", []))
    bar = None
    for b in batch.get("bars", []):
        if b.get("symbol") == SYMBOL:
            bar = b
    if bar is not None:
        BOT.closes.append(int(bar["c"]))
        BOT.bars_seen += 1
    if state.warming_up or bar is None or BOT.pending is not None:
        return []
    holding = state.positions.get(SYMBOL)
    if holding is not None and holding.qty > 0:
        return manage_exit(state, holding)
    order = BOT.detect_entry(state)
    return [order] if order is not None else []


ARM = 0.007           # trailing stop arms once the position is this far up
TRAIL = 0.0030        # ...and then trails this far below the peak close
STOP = 0.0055         # hard stop under the entry fill price
TAKE_PROFIT = 0.022   # outright cap: bank a big bounce, do not round-trip it
TIME_STOP = 150       # bars after which a position that went nowhere is sold


def manage_exit(state, holding):
    """The exit stack: first condition met sells the whole position.

    Order of the checks is deliberate: the hard stop protects the downside
    before anything else is considered, the trailing stop converts a bounce
    into a banked result once it has armed, the take-profit cap refuses to
    give back an outsized move, and the time stop frees the entry budget
    from a position that is only paying rent in exposure.
    """
    if BOT.entry_px is None:
        # The position is on the account but its fill was never seen (the
        # run was restarted and replayed): rebuild the reference price from
        # the position's cost, which the harness keeps fees-included.
        sym = state.symbols[SYMBOL]
        quote_scale = state.hello["capital"]["quote_scale"]
        BOT.entry_px = holding.cost * 10 ** (sym["qty_scale"] + sym["price_scale"]) // (
            holding.qty * 10 ** quote_scale
        )
        BOT.peak = BOT.entry_px
        BOT.held_bars = 0
    last = BOT.closes[-1]
    BOT.peak = max(BOT.peak, last)
    BOT.held_bars += 1
    entry = BOT.entry_px
    sell = (
        last <= entry * (1 - STOP)
        or (BOT.peak >= entry * (1 + ARM) and last <= BOT.peak * (1 - TRAIL))
        or last >= entry * (1 + TAKE_PROFIT)
        or BOT.held_bars >= TIME_STOP
    )
    if not sell:
        return []
    BOT.pending = "sell"
    return [
        {
            "side": "sell",
            "symbol": SYMBOL,
            "qty": holding.qty,
            "max_slippage_bps": SLIPPAGE_BPS,
        }
    ]


if __name__ == "__main__":
    makefun_bars.run(on_batch, on_hello=on_hello)
