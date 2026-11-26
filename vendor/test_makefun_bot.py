"""Unit tests for makefun_bot.py (stdlib unittest). They run only inside python:3.13-slim:
tests/integration/docker-python-sdk.test.ts executes ``python3 -P -s /sdk/test_makefun_bot.py`` with
``PYTHONHASHSEED=0``, as the grader runs a bot.
"""

import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import makefun_bot as sdk  # noqa: E402

SOL = sdk.LAMPORTS_PER_SOL
INITIAL_VQ = 30_000_000_000
INITIAL_VT = 1_073_000_000_000_000
INITIAL_RT = 793_100_000_000_000
VT_MINUS_RT = INITIAL_VT - INITIAL_RT


def key(label):
    """A valid-looking base58 address for a label made of base58 letters."""
    return (label + "x" * 44)[:44]


def sig(label):
    return (label + "z" * 88)[:88]


def hello(**over):
    msg = {
        "type": "hello",
        "protocol": sdk.PROTOCOL,
        "seed": "42",
        "universe": {
            "program": sdk.PUMP_PROGRAM_ID,
            "quote": "SOL",
            "mayhem": "excluded",
            "exclude_cashback": True,
            "sample": {"hash": "sha256", "modulus": 1, "residue": 0},
        },
        "capital": {"lamports": str(10 * SOL)},
        "caps": {
            "max_open_positions": 5,
            "max_order_bps_of_real_reserve": 100,
            "max_orders_per_batch": 16,
            "shorts": False,
            "leverage": False,
        },
        "latency_slots": 2,
        "fees": {
            "protocol_bps_fallback": 95,
            "creator_bps_fallback": 30,
            "base_fee_lamports": "5000",
            "priority_fee_lamports": "100000",
            "failed_tx_bps": 200,
        },
    }
    msg.update(over)
    return msg


def create(mint, slot):
    return {
        "event": "CreateEvent",
        "mint": mint,
        "slot": slot,
        "virtual_token_reserves": str(INITIAL_VT),
        "virtual_quote_reserves": str(INITIAL_VQ),
        "virtual_sol_reserves": str(INITIAL_VQ),
        "real_token_reserves": str(INITIAL_RT),
    }


def trade(mint, slot, real_quote):
    vq = INITIAL_VQ + real_quote
    vt = INITIAL_VQ * INITIAL_VT // vq
    return {
        "event": "TradeEvent",
        "mint": mint,
        "slot": slot,
        "virtual_token_reserves": str(vt),
        "virtual_quote_reserves": str(vq),
        "real_token_reserves": str(vt - VT_MINUS_RT),
        "real_quote_reserves": str(real_quote),
    }


def complete(mint, slot):
    return {"event": "CompleteEvent", "mint": mint, "slot": slot}


def account(cash=10 * SOL, positions=()):
    return {
        "cash_lamports": str(cash),
        "positions": [{"mint": m, "token_amount": str(t), "cost_lamports": str(c)} for m, t, c in positions],
    }


def batch(seq, slot, events=(), marks=(), fills=(), acct=None):
    return {
        "type": "batch",
        "seq": seq,
        "slot": slot,
        "events": list(events),
        "marks": list(marks),
        "fills": list(fills),
        "account": acct if acct is not None else account(),
    }


def done(seq, acct=None, reason="end_of_data"):
    return {"type": "done", "seq": seq, "reason": reason, "account": acct if acct is not None else account()}


def fill(client_id, mint, status="filled", side="buy", slot=0):
    return {
        "client_id": client_id,
        "mint": mint,
        "side": side,
        "status": status,
        "reason": "max_slippage" if status == "rejected" else None,
        "decided_slot": slot,
        "slot": slot,
        "quote_lamports": "0",
        "token_amount": "0",
        "fee_lamports": "0",
        "tx_fee_lamports": "0",
        "fee_fallback": False,
    }


def stream(*messages, tail=b""):
    return io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in messages) + tail)


class FlushCountingWriter(io.StringIO):
    def __init__(self):
        super().__init__()
        self.flushes = 0
        self.lines_at_flush = []

    def flush(self):
        self.flushes += 1
        self.lines_at_flush.append(self.getvalue().count("\n"))
        super().flush()


def drive(messages, on_batch, **kw):
    out = FlushCountingWriter()
    result = sdk.run(on_batch, input=stream(*messages), output=out, **kw)
    return result, out


A, B, C = key("AAA"), key("BBB"), key("CCC")


def _buys(n):
    return [{"side": "buy", "mint": A, "quote_lamports": 1, "max_slippage_bps": 1} for _ in range(n)]


class ReadLines(unittest.TestCase):
    def test_splits_lines_drops_cr_and_yields_an_unterminated_tail(self):
        src = io.BytesIO('{"a":1}\n{"b":"é"}\r\n\nlast'.encode())
        self.assertEqual(list(sdk.read_lines(src)), ['{"a":1}', '{"b":"é"}', "", "last"])

    def test_a_line_of_exactly_max_bytes_passes_and_one_more_byte_fails(self):
        ok = b"a" * sdk.MAX_HARNESS_LINE_BYTES
        self.assertEqual([len(x) for x in sdk.read_lines(io.BytesIO(ok + b"\n" + ok))], [sdk.MAX_HARNESS_LINE_BYTES] * 2)
        with self.assertRaisesRegex(sdk.ProtocolError, "over 1048576 bytes"):
            list(sdk.read_lines(io.BytesIO(ok + b"a\n")))
        with self.assertRaisesRegex(sdk.ProtocolError, "over 1048576 bytes"):
            list(sdk.read_lines(io.BytesIO(ok + b"a")))

    def test_invalid_utf8_is_a_protocol_error(self):
        with self.assertRaisesRegex(sdk.ProtocolError, "not valid UTF-8"):
            list(sdk.read_lines(io.BytesIO(b'{"a":"\xff"}\n')))


class Run(unittest.TestCase):
    def test_one_flushed_reply_per_batch_echoing_seq_and_nothing_for_done(self):
        calls = []
        messages = [hello(), batch(0, 100), batch(1, 101), batch(2, 105), done(3)]
        result, out = drive(
            messages,
            lambda s, b: calls.append(("batch", b["seq"])),
            on_hello=lambda s: calls.append(("hello", s.seq)),
            on_done=lambda s, d: calls.append(("done", d["seq"])),
        )
        lines = out.getvalue().splitlines()
        self.assertEqual(lines, [f'{{"type":"reply","seq":{i},"orders":[]}}' for i in range(3)])
        self.assertEqual(out.lines_at_flush, [1, 2, 3])
        self.assertEqual(calls, [("hello", -1), ("batch", 0), ("batch", 1), ("batch", 2), ("done", 3)])
        self.assertEqual(result.batches, 3)
        self.assertEqual(result.done["reason"], "end_of_data")

    def test_stops_reading_at_done(self):
        out = FlushCountingWriter()
        src = stream(hello(), batch(0, 1), done(1), tail=b"not json at all\n")
        sdk.run(lambda s, b: [], input=src, output=out)
        self.assertEqual(src.read(), b"not json at all\n")

    def test_amounts_become_decimal_strings_and_client_ids_are_filled_in(self):
        def on_batch(state, b):
            reserved = state.client_id()
            return [
                {"side": "buy", "mint": A, "quote_lamports": 50_000_000, "max_slippage_bps": 500},
                {"side": "buy", "mint": B, "quote_lamports": "7", "max_slippage_bps": 0, "client_id": "o2"},
                {"side": "sell", "mint": C, "token_amount": sdk.U64_MAX, "max_slippage_bps": 10_000},
                {"side": "sell", "mint": A, "token_amount": 1, "max_slippage_bps": 1, "client_id": reserved},
            ]

        _, out = drive([hello(), batch(0, 1), batch(1, 2), done(2)], on_batch)
        first, second = out.getvalue().splitlines()
        self.assertEqual(
            first,
            '{"type":"reply","seq":0,"orders":['
            f'{{"side":"buy","client_id":"o3","mint":"{A}","quote_lamports":"50000000","max_slippage_bps":500}},'
            f'{{"side":"buy","client_id":"o2","mint":"{B}","quote_lamports":"7","max_slippage_bps":0}},'
            f'{{"side":"sell","client_id":"o4","mint":"{C}","token_amount":"18446744073709551615","max_slippage_bps":10000}},'
            f'{{"side":"sell","client_id":"o1","mint":"{A}","token_amount":"1","max_slippage_bps":1}}]}}',
        )
        # Fresh ids skip every id used so far; a reused explicit id ("o2") is passed through for the
        # harness to reject as duplicate_client_id (a rejection, not a protocol violation).
        ids = [o["client_id"] for o in json.loads(second)["orders"]]
        self.assertEqual(ids, ["o6", "o2", "o7", "o5"])

    def test_state_follows_events_marks_account_and_fills(self):
        seen = {}

        def on_batch(state, b):
            if b["seq"] == 0:
                return [
                    {"side": "buy", "mint": A, "quote_lamports": 40_000_000, "max_slippage_bps": 100},
                    {"side": "buy", "mint": B, "quote_lamports": 10_000_000, "max_slippage_bps": 100},
                ]
            if b["seq"] == 1:
                seen["pending_after_fills"] = list(state.pending)
                seen["committed"] = state.committed_position_count()
                seen["free"] = state.free_cash_lamports()
                seen["cash"] = state.cash
                seen["positions"] = {m: (h.token_amount, h.cost_lamports) for m, h in state.positions.items()}
                seen["curve_a"] = state.curves[A]
                seen["max_a"] = state.max_buy_lamports(A)
                seen["curve_b_complete"] = state.curves[B].complete
                seen["max_b"] = state.max_buy_lamports(B)
                seen["curve_c"] = state.curves[C]
            return []

        b0 = batch(0, 100, events=[create(A, 100), trade(A, 100, 6 * SOL), create(B, 100), create(C, 100)])
        mark_a = {
            "mint": A,
            "slot": 101,
            "virtual_token_reserves": "1",
            "virtual_quote_reserves": "2",
            "real_token_reserves": "3",
            "real_quote_reserves": str(8 * SOL),
        }
        stale_mark_c = dict(mark_a, mint=C, slot=99, real_quote_reserves="5")
        cash_after = 10 * SOL - 40_000_000 - 105_000
        b1 = batch(
            1,
            102,
            events=[complete(B, 102)],
            marks=[mark_a, stale_mark_c],
            fills=[fill("o1", A, slot=102)],
            acct=account(cash_after, [(A, 123_456, 40_000_000), (C, 0, 0)]),
        )
        result, _ = drive([hello(), b0, b1, done(2, account(9 * SOL))], on_batch)
        self.assertEqual(seen["pending_after_fills"], ["o2"])
        self.assertEqual(seen["committed"], 2)
        self.assertEqual(seen["free"], cash_after - 10_000_000 - 105_000)
        self.assertEqual(seen["cash"], cash_after)
        self.assertEqual(seen["positions"], {A: (123_456, 40_000_000)})
        curve_a = seen["curve_a"]
        self.assertEqual(
            (curve_a.created_slot, curve_a.trades, curve_a.reserves_slot, curve_a.real_quote_reserves, curve_a.virtual_token_reserves),
            (100, 1, 101, 8 * SOL, 1),
        )
        self.assertEqual(seen["max_a"], 8 * SOL // 100)
        self.assertTrue(seen["curve_b_complete"])
        self.assertEqual(seen["max_b"], 0)
        self.assertEqual((seen["curve_c"].real_quote_reserves, seen["curve_c"].reserves_slot), (0, 100))
        self.assertEqual(result.state.cash, 9 * SOL)
        self.assertEqual(result.state.positions, {})
        self.assertEqual(result.state.seq, 2)
        self.assertEqual(result.state.tx_fee_lamports(), 105_000)

    def test_orders_the_harness_would_refuse_raise_order_error_and_write_nothing(self):
        good = {"side": "buy", "mint": A, "quote_lamports": 1, "max_slippage_bps": 1}
        cases = [
            ({**good, "side": "short"}, "side must be"),
            ({**good, "limit": 5}, 'unknown key "limit"'),
            ({**good, "token_amount": 5}, 'unknown key "token_amount"'),
            ({**good, "mint": "0OIl" * 10}, "mint must be"),
            ({**good, "quote_lamports": -1}, "quote_lamports must be a u64"),
            ({**good, "quote_lamports": sdk.U64_MAX + 1}, "quote_lamports must be a u64"),
            ({**good, "quote_lamports": True}, "quote_lamports must be a u64"),
            ({**good, "quote_lamports": 1.0}, "quote_lamports must be a u64"),
            ({**good, "quote_lamports": "01"}, "quote_lamports must be a u64"),
            ({**good, "quote_lamports": "1_000"}, "quote_lamports must be a u64"),
            ({**good, "max_slippage_bps": 10_001}, "max_slippage_bps must be"),
            ({**good, "max_slippage_bps": False}, "max_slippage_bps must be"),
            ({**good, "client_id": "has space"}, "client_id must be"),
            ({**good, "client_id": "x" * 65}, "client_id must be"),
            ({"side": "sell", "mint": A, "max_slippage_bps": 1}, "token_amount must be a u64"),
        ]
        for order, message in cases:
            with self.subTest(message=message):
                out = FlushCountingWriter()
                with self.assertRaisesRegex(sdk.OrderError, message):
                    sdk.run(lambda s, b, o=order: [o], input=stream(hello(), batch(0, 1), done(1)), output=out)
                self.assertEqual(out.getvalue(), "")
        with self.assertRaisesRegex(sdk.OrderError, "must return a list"):
            sdk.run(lambda s, b: good, input=stream(hello(), batch(0, 1), done(1)), output=FlushCountingWriter())

    def test_a_reply_over_the_line_limit_raises_before_writing(self):
        many = [
            {"side": "buy", "mint": A, "quote_lamports": 1, "max_slippage_bps": 1, "client_id": f"{i:064d}"}
            for i in range(400)
        ]
        out = FlushCountingWriter()
        # A cap of 400 lets all 400 orders through the order-count check, so the line size is what fails.
        roomy = hello(caps=dict(hello()["caps"], max_orders_per_batch=400))
        with self.assertRaisesRegex(sdk.OrderError, r"the reply is \d+ bytes \(max 65536\)"):
            sdk.run(lambda s, b: many, input=stream(roomy, batch(0, 1), done(1)), output=out)
        self.assertEqual(out.getvalue(), "")

    def test_more_orders_than_max_orders_per_batch_raise_naming_both_numbers_before_writing(self):
        for cap in (16, 3, 0):
            with self.subTest(cap=cap):
                h = hello(caps=dict(hello()["caps"], max_orders_per_batch=cap))
                out = FlushCountingWriter()
                with self.assertRaises(sdk.OrderError) as raised:
                    sdk.run(
                        lambda s, b, cap=cap: _buys(cap if b["seq"] == 0 else cap + 1),
                        input=stream(h, batch(0, 1), batch(1, 2), done(2)),
                        output=out,
                    )
                self.assertEqual(
                    str(raised.exception),
                    f"batch 1: on_batch returned {cap + 1} orders, over hello.caps.max_orders_per_batch ({cap}); "
                    f"the harness rejects the orders past the cap and fails the run on a reply far over it, "
                    f"so send at most {cap} per batch",
                )
                # Batch 0's reply (exactly the cap) went out; batch 1's never did.
                lines = out.getvalue().splitlines()
                self.assertEqual([json.loads(x)["seq"] for x in lines], [0])
                # BotState.to_orders, which a strategy's own unit tests may call, refuses it the same way.
                with self.assertRaisesRegex(sdk.OrderError, rf"returned {cap + 1} orders, over hello\.caps\.max_orders_per_batch \({cap}\)"):
                    sdk.BotState(h).to_orders(_buys(cap + 1))

    def test_exactly_max_orders_per_batch_orders_are_sent_whole(self):
        for cap in (16, 3, 0):
            with self.subTest(cap=cap):
                h = hello(caps=dict(hello()["caps"], max_orders_per_batch=cap))
                _, out = drive([h, batch(0, 1), done(1)], lambda s, b, cap=cap: _buys(cap))
                orders = json.loads(out.getvalue())["orders"]
                self.assertEqual([o["client_id"] for o in orders], [f"o{i + 1}" for i in range(cap)])

    def test_protocol_errors(self):
        cases = [
            ([batch(0, 1)], "a batch before hello"),
            ([done(0)], "done before hello"),
            ([hello(protocol="agent-fun/replay/v1")], 'speaks "agent-fun/replay/v1"'),
            ([hello(), hello()], "a second hello"),
            ([hello(), {"type": "tick"}], 'unknown message type "tick"'),
            ([hello(), [1, 2]], "unknown message type None"),
            ([hello(), dict(batch(0, 1), events=None)], "malformed batch"),
            ([hello(), batch(0, 1)], r"input ended before done \(after 1 batches\)"),
            ([], "input ended before hello"),
        ]
        for messages, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(sdk.ProtocolError, message):
                    sdk.run(lambda s, b: [], input=stream(*messages), output=FlushCountingWriter())
        with self.assertRaisesRegex(sdk.ProtocolError, "input line 2 is not JSON"):
            sdk.run(lambda s, b: [], input=io.BytesIO(json.dumps(hello()).encode() + b"\n{nope\n"), output=FlushCountingWriter())


if __name__ == "__main__":
    unittest.main(verbosity=2)
