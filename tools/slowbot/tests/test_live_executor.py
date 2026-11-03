"""Tests-only for tools/slowbot/live_executor.py: fake Jupiter, fake signer, fake chain. No network, no key, no send."""
import base64
import json
import urllib.parse

import pytest

from tools import h5_lane_executor as lane
from tools.slowbot import intents as itn, live_executor as lx
from tools.slowbot.positions import PositionManager
from tools.slowbot.quotes import WSOL

NOW = 1_800_000_000.0
MINT = "MintAAAA"
MINT2, MINT3 = "MintBBBB", "MintCCCC"
STAKE = 20_000_000
WALLET = "LaneWallet1111"
RSHA, PSHA = "r" * 40, "p" * 64


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeJup:
    """Buy: tokens = amount // buy_unit. Sell: lamports = tokens * sell_unit. quote/post are the JupiterSwap get/post."""

    def __init__(self):
        self.buy_unit, self.sell_unit, self.urls, self.fail = 20.0, 20.0, [], False  # 1 SOL lamport buys 1/20 token units

    def get(self, url):
        self.urls.append(url)
        if self.fail:
            raise OSError("down")
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.split("?", 1)[1]).items()}
        amt = int(q["amount"])
        out = int(amt / self.buy_unit) if q["inputMint"] == WSOL else int(amt * self.sell_unit)
        return {"inputMint": q["inputMint"], "outputMint": q["outputMint"], "inAmount": str(amt), "outAmount": str(out), "priceImpactPct": "0.01",
                "slippageBps": int(q["slippageBps"])}

    def post(self, url, body):
        q = body["quoteResponse"]
        kind = "buy" if q["inputMint"] == WSOL else "sell"
        tx = f"{kind}:{q['inAmount']}:{q['outAmount']}"
        return {"swapTransaction": base64.b64encode(tx.encode()).decode(), "lastValidBlockHeight": 999}


class FakeSigner:
    def __init__(self):
        self.signed = []

    def sign(self, b64):
        sig = f"SIG{len(self.signed) + 1}"
        self.signed.append((sig, base64.b64decode(b64).decode()))
        return b64, sig


class FakeChain:
    """confirm=True: every sent transaction lands at once. confirm=False: it lands only after land(sig). fail_sigs land with an error (fee only)."""

    def __init__(self, signer, ledger_probe=None):
        self.signer, self.sent, self.confirm, self.balance_lamports, self.probe = signer, [], True, 5_000_000_000, ledger_probe
        self.landed, self.fail_sigs, self.balance_override, self.send_log = set(), set(), {}, []
        self.height, self.result_fails = 0, False

    def send(self, b64):
        sig = next(s for s, t in reversed(self.signer.signed) if base64.b64encode(t.encode()).decode() == b64)
        if self.probe:
            self.probe(sig)  # asserts the write-ahead row is already on disk at send time
        self.sent.append(sig)
        self.send_log.append(dict(self.signer.signed)[sig])
        return sig

    def land(self, sig):
        self.landed.add(sig)

    def _has_landed(self, sig):
        return sig in self.sent and (self.confirm or sig in self.landed)

    def status(self, sig):
        if not self._has_landed(sig):
            return None
        return {"confirmationStatus": "confirmed", "err": {"InstructionError": 1} if sig in self.fail_sigs else None}

    def token_balance(self, mint):
        if mint in self.balance_override:
            return self.balance_override[mint]
        bal = 0
        for sig, tx in self.signer.signed:
            if self._has_landed(sig) and sig not in self.fail_sigs:
                kind, a, b = tx.split(":")
                bal += int(b) if kind == "buy" else -int(a)
        return bal

    def balance(self):
        return self.balance_lamports

    def block_height(self):
        return self.height

    def result(self, sig, mint):
        if self.result_fails:
            raise OSError("getTransaction failed")
        kind, a, b = dict(self.signer.signed)[sig].split(":")
        if sig in self.fail_sigs:
            return {"sol_delta": -5_000, "fee": 5_000, "err": {"InstructionError": 1}, "token_delta": 0, "ata_created": False}
        if kind == "buy":
            return {"sol_delta": -(int(a) + 5_000 + lx.ATA_RENT), "fee": 5_000, "err": None, "token_delta": int(b), "ata_created": True}
        return {"sol_delta": int(b) - 5_000, "fee": 5_000, "err": None, "token_delta": -int(a), "ata_created": False}


def mkcfg(tmp_path, **kw):
    base = {"intents_path": str(tmp_path / "intents.jsonl"), "state_dir": str(tmp_path / "state"), "ledger_path": str(tmp_path / "lane" / "ledger.jsonl"),
            "stop_path": str(tmp_path / "lane" / "STOP"), "dry_run": False, "user": WALLET, "pin_runner_sha": RSHA, "pin_prompt_sha256": PSHA}
    base.update(kw)
    return lx.parse_config(base)


def mkexec(tmp_path, dry=False, clock=None, jup=None, probe=False, **kw):
    clock = clock or Clock()
    jup = jup or FakeJup()
    cfg = mkcfg(tmp_path, dry_run=dry, **kw)
    signer = FakeSigner()
    ex_ref = {}

    def check(sig):
        rows = ex_ref["ex"].ledger.read()
        assert any(r["ev"] in ("send_intent", "sell_intent") and r.get("signature") == sig for r in rows), "send_intent must be on disk before the send"
    chain = FakeChain(signer, check if probe else None)
    ex = lx.SlowbotLive(cfg, jup=lx.JupiterSwap(jup.get, jup.post), chain=chain, signer=None if dry else signer, liq_fn=lambda m: 50_000.0,
                        user=WALLET, now_fn=clock, sleep=clock.sleep)
    ex_ref["ex"] = ex
    return ex, jup, signer, chain, clock


def put_intent(tmp_path, mint=MINT, age=5.0, clock_t=NOW, paper_in=25_000_000, paper_out=1_250_000, n=[0]):
    n[0] += 1
    dts = clock_t - age
    row = {"schema": itn.SCHEMA, "intent_id": f"{mint}:{int(dts)}:{n[0]}", "arm": "H", "mint": mint, "pool": "P", "decision_ts": dts, "conviction": 4, "runner_sha": RSHA, "prompt_sha256": PSHA,
           "paper_entry": {"in_lamports": paper_in, "out_tokens": paper_out, "liq_usd": 50_000.0}}
    itn.append_intent(tmp_path, {**row}) if False else None
    with open(tmp_path / "intents.jsonl", "a") as f:
        f.write(json.dumps(row) + "\n")
    return row


def other_bot_ledger(ex, bot, rows):
    lc = lane.LaneConfig.parse({"ledger_path": str(ex.lc.ledger_path), "stop_path": str(ex.lc.stop_path), "bot_id": bot, "stake_lamports": STAKE,
                                "fee_reserve_lamports": lane.LANE_MIN_FEE_RESERVE})
    led = lane.LaneLedger(lc)
    with led.locked():
        for r in rows:
            led.append(r)


def open_row(mint, filled=True):
    r = [{"ev": "send_intent", "ts_ms": 1, "mint": mint, "signature": "X" + mint, "stake_lamports": STAKE, "sol_in_lamports": 0, "sol_out_lamports": 0}]
    if filled:
        r.append({"ev": "fill", "ts_ms": 2, "mint": mint, "ok": True, "sol_in_lamports": 0, "sol_out_lamports": STAKE})
    return r


def outcomes(ex):
    return list(ex.st["done"].values())


# --- config ----------------------------------------------------------------------------------------------------------------------------
def test_config_defaults_and_bounds(tmp_path):
    c = mkcfg(tmp_path)
    assert (c["bot_id"], c["stake_lamports"], c["max_open"], c["bot_budget_lamports"], c["max_intent_age_s"]) == ("slowbot-h-1", 20_000_000, 2, 60_000_000, 90)
    assert (c["lane_budget_lamports"], c["lane_daily_loss_lamports"], c["lane_max_open"]) == (300_000_000, 100_000_000, 3)
    for bad in ({"stake_lamports": 50_000_000}, {"max_open": 3}, {"bot_budget_lamports": 61_000_000}, {"lane_budget_lamports": 400_000_000},
                {"lane_daily_loss_lamports": 200_000_000}, {"lane_max_open": 4}, {"max_slippage_bps": 900}, {"bot_id": "other"}, {"bogus": 1}):
        with pytest.raises(ValueError):
            mkcfg(tmp_path, **bad)


# --- intents: stale, dry-run, happy path -----------------------------------------------------------------------------------------------
def test_stale_intent_refused_and_fresh_accepted(tmp_path):
    ex, jup, signer, chain, clock = mkexec(tmp_path, dry=True)
    put_intent(tmp_path, age=91)
    put_intent(tmp_path, mint=MINT2, age=89)
    ex.process_intents()
    assert outcomes(ex) == ["refused:stale_intent", "dry_would_send"]
    assert not any(MINT in u for u in jup.urls)  # a stale intent never reaches Jupiter


def test_dry_run_signs_nothing_sends_nothing_writes_no_ledger_row(tmp_path):
    ex, jup, signer, chain, clock = mkexec(tmp_path, dry=True)
    put_intent(tmp_path)
    ex.process_intents()
    assert outcomes(ex) == ["dry_would_send"] and signer.signed == [] and chain.sent == []
    assert ex.ledger.read() == []
    assert ex.pm.open_positions() == []


def test_intent_processed_once(tmp_path):
    ex, *_ = mkexec(tmp_path, dry=True)
    put_intent(tmp_path)
    assert ex.process_intents() == 1 and ex.process_intents() == 0


def test_live_buy_write_ahead_fill_and_position(tmp_path):
    ex, jup, signer, chain, clock = mkexec(tmp_path, probe=True)
    put_intent(tmp_path)
    ex.process_intents()
    assert outcomes(ex) == ["sent"] and chain.sent == ["SIG1"]
    rows = ex.ledger.read()
    assert [r["ev"] for r in rows if r["ev"] != "snap"] == ["send_intent", "fill"]
    fill = [r for r in rows if r["ev"] == "fill"][0]
    assert fill["ok"] is True and fill["bot_id"] == "slowbot-h-1" and fill["tokens"] == STAKE // 20 and fill["fee_network_lamports"] == 5_000
    assert fill["sol_out_lamports"] == STAKE + 5_000 + lx.ATA_RENT
    (p,) = ex.pm.open_positions()
    assert p["cost_lamports"] == STAKE and p["tokens_left"] == STAKE // 20 and p["entry_unit"] == STAKE / (STAKE // 20)
    assert not ex.pm.open_positions("T") and ex.st["pending"] == {}


def test_slippage_cap_goes_to_jupiter_and_entry_worse_than_paper_refused(tmp_path):
    ex, jup, *_ = mkexec(tmp_path, dry=True, max_slippage_bps=120)
    put_intent(tmp_path)
    ex.process_intents()
    assert "slippageBps=120" in jup.urls[0]
    jup.buy_unit = 40.0  # fewer tokens for the same SOL: the price is 2x the paper's
    ex2, jup2, *_ = mkexec(tmp_path / "b", dry=True)
    jup2.buy_unit = 10_000.0
    put_intent(tmp_path / "b")
    ex2.process_intents()
    assert outcomes(ex2) == ["refused:entry_worse_than_paper"]


def test_requote_degrade_refused_before_send(tmp_path):
    ex, jup, signer, chain, clock = mkexec(tmp_path)
    orig = jup.get
    calls = []

    def get(url):
        d = orig(url)
        calls.append(1)
        if len(calls) == 2:  # the re-check quote just before the send
            d["outAmount"] = str(int(int(d["outAmount"]) * 0.9))
        return d
    ex.jup = lx.JupiterSwap(get, jup.post)
    put_intent(tmp_path)
    ex.process_intents()
    assert outcomes(ex) == ["refused:requote_degraded"] and chain.sent == [] and ex.ledger.read() == []


def test_fee_payer_check_in_real_signer_refuses_foreign_tx():
    pytest.importorskip("solders")
    from solders.keypair import Keypair
    from solders.message import MessageV0
    from solders.hash import Hash
    from solders.instruction import Instruction
    from solders.pubkey import Pubkey
    from solders.transaction import VersionedTransaction
    mine, other = Keypair(), Keypair()
    ix = Instruction(Pubkey.default(), b"", [])
    msg = MessageV0.try_compile(other.pubkey(), [ix], [], Hash.default())
    tx = VersionedTransaction(msg, [other])
    with pytest.raises(lx.Refused):
        lx.KeySigner(mine).sign(base64.b64encode(bytes(tx)).decode())


# --- limits ------------------------------------------------------------------------------------------------------------------------------
def test_bot_max_open_2(tmp_path):
    ex, jup, signer, chain, clock = mkexec(tmp_path)
    for m in (MINT, MINT2, MINT3):
        put_intent(tmp_path, mint=m)
    ex.process_intents()
    assert outcomes(ex) == ["sent", "sent", "refused:bot_max_open"]


def test_shared_lane_max_open_3(tmp_path):
    ex, *_ = mkexec(tmp_path, dry=True)
    other_bot_ledger(ex, "h5-lane-2", open_row("OtherMint1") + open_row("OtherMint2") + open_row("OtherMint3"))
    put_intent(tmp_path)
    ex.process_intents()
    assert outcomes(ex) == ["refused:lane_max_open"]


def test_mint_held_by_other_lane_bot_refused(tmp_path):
    ex, *_ = mkexec(tmp_path, dry=True)
    other_bot_ledger(ex, "h5-lane-2", open_row(MINT))
    put_intent(tmp_path)
    ex.process_intents()
    assert outcomes(ex) == ["refused:mint_held_by_other_bot"]


def test_shared_total_budget_0_30_and_daily_0_10(tmp_path):
    ex, *_ = mkexec(tmp_path / "a", dry=True)
    other_bot_ledger(ex, "h5-lane-2", [{"ev": "fill", "ts_ms": 1, "mint": "Z", "ok": False, "sol_in_lamports": 0, "sol_out_lamports": 300_000_000}])
    put_intent(tmp_path / "a")
    ex.process_intents()
    assert outcomes(ex) in (["refused:lane_budget_stop"], ["refused:lane_budget_presend"])
    ex2, *_ = mkexec(tmp_path / "b", dry=True)
    day = lane.day_of(int(NOW * 1000))
    other_bot_ledger(ex2, "h5-lane-2", [{"ev": "snap", "ts_ms": 1, "day": day, "pnl_lamports": 0, "pnl_cost_lamports": 0},
                                        {"ev": "fill", "ts_ms": 2, "mint": "Z", "ok": False, "sol_in_lamports": 0, "sol_out_lamports": 100_000_000}])
    put_intent(tmp_path / "b")
    ex2.process_intents()
    assert outcomes(ex2) == ["refused:lane_daily_stop"]


def test_bot_sub_budget_0_06_stops_and_never_resets(tmp_path):
    ex, jup, signer, chain, clock = mkexec(tmp_path)
    other_bot_ledger(ex, "slowbot-h-1", [{"ev": "fill", "ts_ms": 1, "mint": "Z", "ok": False, "sol_in_lamports": 0, "sol_out_lamports": 60_000_000}])
    put_intent(tmp_path)
    ex.process_intents()
    assert outcomes(ex) == ["refused:bot_budget_stop"]
    import os
    assert os.path.exists(ex.cfg["bot_stop_path"])
    # a later win does not reopen it: the STOP file stays until the manager removes it
    other_bot_ledger(ex, "slowbot-h-1", [{"ev": "exit", "ts_ms": 3, "mint": "Z", "sol_in_lamports": 90_000_000, "sol_out_lamports": 0}])
    put_intent(tmp_path, mint=MINT2)
    ex.process_intents()
    assert outcomes(ex)[-1] == "refused:bot_stop_file" and chain.sent == []


def test_bot_presend_budget_counts_open_stakes_and_reserve(tmp_path):
    ex, *_ = mkexec(tmp_path, dry=True)
    other_bot_ledger(ex, "slowbot-h-1", [{"ev": "fill", "ts_ms": 1, "mint": "Z", "ok": False, "sol_in_lamports": 0, "sol_out_lamports": 36_000_000}])
    put_intent(tmp_path)  # pnl -36M; -36M - (0 + 20M + reserve) <= -60M
    ex.process_intents()
    assert outcomes(ex) == ["refused:bot_budget_presend"]


def test_lane_stop_file_refuses(tmp_path):
    ex, *_ = mkexec(tmp_path, dry=True)
    ex.ledger.write_stop("manual", 1)
    put_intent(tmp_path)
    ex.process_intents()
    assert outcomes(ex) == ["refused:lane_stop_file"]


def test_wallet_floor_refuses(tmp_path):
    ex, jup, signer, chain, clock = mkexec(tmp_path)
    chain.balance_lamports = 60_000_000
    put_intent(tmp_path)
    ex.process_intents()
    assert outcomes(ex) == ["refused:wallet_floor"] and chain.sent == []


# --- exit-rule parity with positions.py ------------------------------------------------------------------------------------------------
ENTRY = 25_000_000
TOK = 1_000_000


def script(steps):
    """steps: (dt_s, out_lamports|None|'raise', liq). The quote function returns the scripted value for the tokens asked (scaled by tokens/TOK)."""
    it = {"i": 0}

    def fn(mint, amount):
        dt, out, liq = steps[it["i"]]
        if out == "raise":
            raise OSError("x")
        if out is None:
            return None
        return {"out_lamports": int(out * amount / TOK), "liq_usd": liq}
    return fn, it


SCENARIOS = {
    "stop": [(30, 24_000_000, 50_000.0), (30, 20_000_000, 50_000.0), (30, 20_000_000, 50_000.0)],
    "take_then_trail": [(30, 26_000_000, 50_000.0), (30, 36_000_000, 50_000.0), (30, 50_000_000, 50_000.0), (30, 39_000_000, 50_000.0), (30, 39_000_000, 50_000.0)],
    "time_stop": [(30, 25_000_000, 50_000.0), (4 * 3600, 25_500_000, 50_000.0), (30, 25_500_000, 50_000.0)],
    "liq_collapse": [(30, 25_000_000, 50_000.0), (30, 25_000_000, 29_000.0), (30, 25_000_000, 29_000.0)],
    "liq_unknown": [(30, 25_000_000, None), (30, 25_000_000, None), (30, 25_000_000, None), (30, 25_000_000, None)],
    "writeoff": [(30, None, None), (300, "raise", None), (300, None, None), (300, None, None)],
    "stop_beats_take_same_poll_order": [(30, 40_000_000, 50_000.0), (30, 20_000_000, 50_000.0), (30, 20_000_000, 50_000.0)],
}


def run_paper(steps, tmp):
    clk = Clock()
    fn, it = script(steps)
    pm = PositionManager(tmp / "s.json", tmp / "l.jsonl", fn, buy_quote_fn=lambda m, l, a: {"in_lamports": ENTRY, "out_lamports": TOK, "liq_usd": 50_000.0},
                         now_fn=clk)
    assert pm.open({"mint": MINT, "arm": "H"}, "H")[0]
    return trace(pm, clk, steps, it)


def run_live(steps, tmp):
    clk = Clock()
    fn, it = script(steps)

    def sell(p, tokens, reason):
        dt, out, liq = steps[it["i"]]
        return {"proceeds": int(out * tokens / TOK) if isinstance(out, int) else 0, "fee": 5_000, "signature": "S"}
    pm = lx.LivePositions(tmp / "s.json", tmp / "l.jsonl", fn, sell_fn=sell, now_fn=clk)
    pm.adopt(MINT, "i", TOK, ENTRY, 50_000.0, clk())
    return trace(pm, clk, steps, it)


def trace(pm, clk, steps, it):
    out = []
    for i in range(len(steps)):
        it["i"] = i
        clk.t += steps[i][0]
        for ex in pm.tick():
            out.append((i, "exit", ex["reason"]))
        for p in pm.open_positions():
            if p["taken"] and (i, "take", None) not in out:
                out.append((i, "take", None))
    return out


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_exit_rules_match_positions_py(name, tmp_path):
    (tmp_path / "p").mkdir()
    (tmp_path / "l").mkdir()
    paper = run_paper(SCENARIOS[name], tmp_path / "p")
    live = run_live(SCENARIOS[name], tmp_path / "l")
    assert paper == live and paper, name


def test_parity_constants_are_the_paper_constants():
    from tools.slowbot import positions as p
    assert lx.LivePositions.__mro__[1] is PositionManager
    assert {"_step", "tick", "_miss"}.isdisjoint(vars(lx.LivePositions))  # the decision code is inherited, not copied
    assert (p.STOP, p.TAKE, p.TAKE_FRACTION, p.TRAIL, p.TIME_STOP_S, p.LIQ_COLLAPSE, p.LIQ_UNKNOWN_MAX, p.NO_QUOTE_WRITEOFF_S) == \
        (0.15, 0.40, 0.5, 0.20, 14400, 0.40, 3, 600)


def test_failed_live_sell_keeps_position_open_and_retries(tmp_path):
    clk = Clock()
    calls = []
    results = [None, {"proceeds": 10, "fee": 1, "signature": "S"}]

    def sell(p, tokens, reason):
        calls.append(reason)
        return results.pop(0)
    pm = lx.LivePositions(tmp_path / "s.json", tmp_path / "l.jsonl", lambda m, a: {"out_lamports": 1_000_000, "liq_usd": 50_000.0}, sell_fn=sell, now_fn=clk)
    pm.adopt(MINT, "i", TOK, ENTRY, 50_000.0, clk())
    clk.t += 30
    assert pm.tick() == [] and len(pm.open_positions()) == 1
    clk.t += 30
    assert [e["reason"] for e in pm.tick()] == ["stop"] and calls == ["stop", "stop"] and pm.open_positions() == []


# --- live exits through the executor and the ledger --------------------------------------------------------------------------------------
def test_take_half_keeps_position_open_in_ledger_then_trail_exit_closes_it(tmp_path):
    ex, jup, signer, chain, clock = mkexec(tmp_path)
    put_intent(tmp_path)
    ex.process_intents()
    clock.t += 30
    jup.sell_unit = 25.0  # +25% on... entry unit is 20 lamports per token; 28 -> +40%
    jup.sell_unit = 28.0
    ex.pm.tick()
    rows = ex.ledger.read()
    assert [r["ev"] for r in rows if r["ev"] in ("partial_exit", "exit")] == ["partial_exit"]
    assert lane.lane_fold(rows, {}, 0, 60, "slowbot-h-1").open.keys() == {"slowbot-h-1:" + MINT}
    (p,) = ex.pm.open_positions()
    assert p["taken"] and p["tokens_left"] == STAKE // 20 // 2
    clock.t += 30
    jup.sell_unit = 20.0  # below the 20% trail from the high
    ex.pm.tick()
    rows = ex.ledger.read()
    assert [r["ev"] for r in rows if r["ev"] in ("partial_exit", "exit")] == ["partial_exit", "exit"]
    assert lane.lane_fold(rows, {}, 0, 60, "slowbot-h-1").open == {} and ex.pm.open_positions() == []


# --- reconcile by signature ------------------------------------------------------------------------------------------------------------
def test_restart_with_pending_buy_reconciles_by_signature(tmp_path):
    ex, jup, signer, chain, clock = mkexec(tmp_path)
    chain.confirm = False
    put_intent(tmp_path)
    ex.process_intents()
    assert list(ex.st["pending"]) == ["SIG1"] and ex.pm.open_positions() == []
    # new process: same state dir and ledger; the signature now confirms
    ex2, jup2, signer2, chain2, _ = mkexec(tmp_path, clock=clock)
    signer2.signed, chain2.sent = list(signer.signed), ["SIG1"]
    ex2.reconcile(force=True)
    assert ex2.st["pending"] == {} and len(ex2.pm.open_positions()) == 1
    assert [r["ev"] for r in ex2.ledger.read() if r["ev"] != "snap"] == ["send_intent", "fill"]


def test_crash_between_ledger_row_and_state_is_adopted_from_ledger(tmp_path):
    ex, jup, signer, chain, clock = mkexec(tmp_path)
    assert ex.ledger.reserve(MINT, "SIG1", {}, int(NOW * 1000), STAKE) is None  # send_intent on disk, no pending entry in the state
    signer.signed.append(("SIG1", f"buy:{STAKE}:{STAKE // 20}"))
    chain.sent.append("SIG1")
    ex.reconcile(force=True)
    assert len(ex.pm.open_positions()) == 1


def test_unresolved_signature_written_off_at_full_cost(tmp_path):
    ex, jup, signer, chain, clock = mkexec(tmp_path)
    chain.confirm = False
    put_intent(tmp_path)
    ex.process_intents()
    clock.t += 601
    ex.reconcile(force=True)
    rows = ex.ledger.read()
    w = [r for r in rows if r["ev"] == "writeoff"]
    assert len(w) == 1 and w[0]["sol_out_lamports"] == STAKE and ex.st["pending"] == {}
    assert lane.lane_fold(rows, {}, 0, 60, "slowbot-h-1").open == {}


def test_filled_position_rebuilt_from_ledger_after_state_loss(tmp_path):
    ex, jup, signer, chain, clock = mkexec(tmp_path)
    put_intent(tmp_path)
    ex.process_intents()
    (tmp_path / "state" / "positions-state.json").unlink()
    ex2, *_ = mkexec(tmp_path, clock=clock)
    assert ex2.pm.open_positions() == []
    ex2._recover_positions()
    (p,) = ex2.pm.open_positions()
    assert p["tokens_left"] == STAKE // 20


def test_main_refuses_live_without_ok_file_and_keeps_dry_default(tmp_path):
    cfg = {"pin_runner_sha": "a", "pin_prompt_sha256": "b", "intents_path": "i", "state_dir": str(tmp_path / "s"), "ledger_path": str(tmp_path / "l"), "stop_path": str(tmp_path / "x"), "dry_run": False}
    p = tmp_path / "c.json"
    p.write_text(json.dumps(cfg))
    with pytest.raises(SystemExit):
        lx.main(["--config", str(p), "--live"])  # no live_ok_path
    assert lx.DEFAULTS["dry_run"] is True


def test_example_config_parses_and_is_dry_and_keyless():
    from pathlib import Path
    raw = (Path(lx.__file__).resolve().parents[2] / "scripts" / "mal-fast" / "slowbot-live.json.example").read_text()
    c = lx.parse_config(json.loads(raw))
    assert c["dry_run"] is True and "probe-wallet" not in raw and "keypair" not in raw.lower()
