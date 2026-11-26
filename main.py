"""Ridge Runner — a selective pump.fun momentum rider for agent-fun/replay/v2.

The bot watches every coin from its CreateEvent and buys only the ones that
have already shown genuine demand: old enough that the creation frenzy is
over, bought by many distinct wallets other than the creator, carrying
several SOL of net buy flow, sitting on deep real reserves, up a healthy
multiple from its creation price but not a vertical spike, with a creator
who has not sold and has not bought much either. One fixed-size buy per
coin, at most five positions open, and then the coin is left alone: there
is no take-profit. Winners ride until the curve completes (the replay
closes them there) or a stop rule fires; losers are cut by a stop-loss, a
hold-time cap, a staleness rule, or a creator exit.

Why no take-profit: on this tape the profit lives in the handful of coins
that run all the way to curve completion. Every fixed take-profit tried in
earlier work on the same dev window finished negative, because capping a
winner removes the one payoff that covers the slow bleed of the other ~85%
of trades. The stop-loss is the only price exit, and it is wide on purpose.

Wire protocol in one paragraph (hello, batch, orders, slot): the harness
opens with a single hello, then sends one batch per slot carrying that
slot's events, reserve marks, fills and account snapshot, and the bot
answers every batch with at most max_orders_per_batch orders. Exits are
chosen before entries, so a sale's proceeds are available to the next
batch rather than the current one. The run is fully deterministic — no
clock, no randomness, no environment reads, no I/O besides the protocol
lines on stdin/stdout. Dicts are only ever iterated in insertion order
and sets are only ever measured by length, so the two graded executions
produce byte-identical action logs.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bot  # noqa: E402  (after the sys.path line: -P leaves the script directory off sys.path)

SOL = makefun_bot.LAMPORTS_PER_SOL

# --- entry filters (tuned on the dev window; see the sweep log) ---
STAKE_SOL = 0.12
MIN_AGE = 20
MAX_AGE = 40
MIN_BUYERS = 12
MIN_NET_SOL = 3.0
MIN_RQ_SOL = 5.0
MIN_RUNUP = 2.5
MAX_RUNUP = 4.0
MAX_DEV_BUY_SOL = 2.5
MIN_BUY_SOL = 0.03

# --- exits ---
STOP_LOSS = 0.1
MAX_HOLD = 500
STALE_SLOTS = 300
EXIT_DEV_SELL = True        # exit when the creator sells while we hold

MAX_SLIPPAGE_BPS = 10_000   # wide open: a 2-slot latency move must not reject us


class Coin:
    """Tape state for one coin, from its CreateEvent onward."""

    __slots__ = (
        "mint", "create_slot", "creator", "vq0", "vt0",
        "dev_buy", "dev_sold", "buyers", "net_flow", "trades",
        "last_trade_slot", "decided",
    )

    def __init__(self, mint, create_slot, creator, vq0, vt0):
        self.mint = mint
        self.create_slot = create_slot
        self.creator = creator
        self.vq0 = vq0            # creation virtual quote reserves
        self.vt0 = vt0            # creation virtual token reserves
        self.dev_buy = 0          # creator buys in the create slot (lamports)
        self.dev_sold = False     # creator has sold at least once
        self.buyers = set()       # distinct non-creator buyers
        self.net_flow = 0         # non-creator buys minus sells (lamports)
        self.trades = 0
        self.last_trade_slot = create_slot
        self.decided = False      # bought, or dropped for good


class Held:
    """Bookkeeping for one open position."""

    __slots__ = ("opened_slot", "tx_paid", "dev_sold_at_open")

    def __init__(self, opened_slot, tx_paid, dev_sold_at_open):
        self.opened_slot = opened_slot
        self.tx_paid = tx_paid
        self.dev_sold_at_open = dev_sold_at_open


class RidgeRunner:
    """The strategy. One instance per run; all state lives here."""

    def __init__(self):
        self.coins = {}           # mint -> Coin, insertion order
        self.held = {}            # mint -> Held
        self.selling = set()      # mints with a sell in flight
        self.proto_bps = 95
        self.creator_bps = 30

    # -- pricing ---------------------------------------------------------
    def sell_value(self, tokens, vq, vt):
        """Lamports a sell of `tokens` would pay after curve fees (no tx fee)."""
        if tokens <= 0 or vt <= 0:
            return 0
        q = tokens * vq // (vt + tokens)
        fee = -(-q * self.proto_bps // 10_000)
        cfee = -(-q * self.creator_bps // 10_000)
        return q - fee - cfee

    # -- tape ------------------------------------------------------------
    def read_events(self, batch):
        for e in batch["events"]:
            kind = e["event"]
            mint = e["mint"]
            if kind == "CreateEvent":
                self.coins[mint] = Coin(
                    mint, e["slot"], e["creator"],
                    int(e["virtual_quote_reserves"]),
                    int(e["virtual_token_reserves"]),
                )
            elif kind == "TradeEvent":
                c = self.coins.get(mint)
                if c is None:
                    continue
                sol = int(e["sol_amount"])
                c.trades += 1
                c.last_trade_slot = e["slot"]
                is_dev = e["user"] == c.creator
                if e["is_buy"]:
                    if is_dev:
                        if e["slot"] == c.create_slot:
                            c.dev_buy += sol
                    else:
                        c.buyers.add(e["user"])
                        c.net_flow += sol
                else:
                    if is_dev:
                        c.dev_sold = True
                    else:
                        c.net_flow -= sol
            else:  # CompleteEvent
                c = self.coins.get(mint)
                if c is not None:
                    c.decided = True

    def read_fills(self, batch):
        for f in batch["fills"]:
            mint = f["mint"]
            if f["client_id"].startswith("_"):
                # Harness close (complete / flatten / write-off): position gone.
                self.held.pop(mint, None)
                self.selling.discard(mint)
                continue
            if f["side"] == "buy" and f["status"] == "filled":
                c = self.coins.get(mint)
                self.held[mint] = Held(
                    f["slot"], 0,
                    c.dev_sold if c is not None else False,
                )
            elif f["side"] == "sell":
                self.selling.discard(mint)
            h = self.held.get(mint)
            if h is not None and f["status"] != "rejected":
                h.tx_paid += int(f["tx_fee_lamports"])

    # -- decisions -------------------------------------------------------
    def on_batch(self, state, batch):
        self.read_events(batch)
        self.read_fills(batch)
        # Drop held bookkeeping for positions the harness no longer reports
        # (and which have no buy in flight).
        for mint in list(self.held):
            if mint not in state.positions and mint not in self.selling:
                pending_buy = False
                for p in state.pending.values():
                    if p.order["mint"] == mint and p.order["side"] == "buy":
                        pending_buy = True
                        break
                if not pending_buy:
                    del self.held[mint]

        orders = []
        cap = state.hello["caps"]["max_orders_per_batch"]
        tx = state.tx_fee_lamports()
        marks = {}
        for m in batch["marks"]:
            marks[m["mint"]] = (int(m["virtual_quote_reserves"]),
                                int(m["virtual_token_reserves"]))
        slot = state.slot

        # Exits first.
        for mint, pos in state.positions.items():
            if len(orders) >= cap or mint in self.selling:
                continue
            h = self.held.get(mint)
            if h is None:
                c = self.coins.get(mint)
                h = Held(slot, tx, c.dev_sold if c is not None else False)
                self.held[mint] = h
            mk = marks.get(mint)
            if mk is None:
                curve = state.curves.get(mint)
                if curve is None:
                    continue
                mk = (curve.virtual_quote_reserves,
                      curve.virtual_token_reserves)
            value = self.sell_value(pos.token_amount, mk[0], mk[1]) - tx
            basis = pos.cost_lamports + h.tx_paid
            do_exit = False
            if basis > 0 and value * 10_000 <= basis * (10_000 - int(STOP_LOSS * 10_000)):
                do_exit = True
            if slot - h.opened_slot >= MAX_HOLD:
                do_exit = True
            c = self.coins.get(mint)
            if c is not None:
                if EXIT_DEV_SELL and c.dev_sold and not h.dev_sold_at_open:
                    do_exit = True
                if slot - c.last_trade_slot >= STALE_SLOTS:
                    do_exit = True
            if not do_exit:
                continue
            orders.append({"side": "sell", "mint": mint,
                           "token_amount": pos.token_amount,
                           "max_slippage_bps": MAX_SLIPPAGE_BPS})
            self.selling.add(mint)
            if c is not None:
                c.decided = True

        # Entries.
        free = state.free_cash_lamports() - tx * len(orders)
        open_slots = state.hello["caps"]["max_open_positions"] - state.committed_position_count()
        stake = int(STAKE_SOL * SOL)
        min_buy = int(MIN_BUY_SOL * SOL)
        for mint, c in list(self.coins.items()):
            if len(orders) >= cap or open_slots <= 0:
                break
            if c.decided:
                continue
            age = slot - c.create_slot
            if age > MAX_AGE:
                c.decided = True
                continue
            if age < MIN_AGE:
                continue
            if c.dev_sold or c.dev_buy > int(MAX_DEV_BUY_SOL * SOL):
                c.decided = True
                continue
            if len(c.buyers) < MIN_BUYERS or c.net_flow < int(MIN_NET_SOL * SOL):
                continue
            curve = state.curves.get(mint)
            if curve is None or curve.complete:
                c.decided = True
                continue
            if curve.real_quote_reserves < int(MIN_RQ_SOL * SOL):
                continue
            # Run-up: price now / creation price, as a ratio of vq/vt pairs.
            # runup = (vq/vt) / (vq0/vt0) = vq*vt0 / (vt*vq0)
            if c.vq0 <= 0 or c.vt0 <= 0:
                continue
            vq = curve.virtual_quote_reserves
            vt = curve.virtual_token_reserves
            if vt <= 0:
                continue
            runup_num = vq * c.vt0
            runup_den = vt * c.vq0
            if runup_num * 10 < runup_den * int(MIN_RUNUP * 10):
                continue
            if runup_num * 10 > runup_den * int(MAX_RUNUP * 10):
                c.decided = True
                continue
            amount = min(stake, state.max_buy_lamports(mint))
            if amount < min_buy or free < amount + tx:
                continue
            orders.append({"side": "buy", "mint": mint,
                           "quote_lamports": amount,
                           "max_slippage_bps": MAX_SLIPPAGE_BPS})
            free -= amount + tx
            open_slots -= 1
            c.decided = True
        return orders


_RUNNER = RidgeRunner()


def on_batch(state, batch):
    return _RUNNER.on_batch(state, batch)


if __name__ == "__main__":
    makefun_bot.run(on_batch)
