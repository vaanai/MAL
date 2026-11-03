"""slowbot H-arm LIVE executor, DEC-035 live-learning lane (s2). Runs on mal-fast-0 only; the paper runner stays on research-0.

NOT STARTED, NOT INSTALLED by this module. Only the manager starts a lane book. The wallet key is loaded in `--live` mode only, from the systemd
credential (tools.probe_live.load_probe_key / credential_path: CREDENTIALS_DIRECTORY, LoadCredential=probe-wallet:... in the unit); this file
never opens any other key path and never prints key material. Default mode is DRY: quotes and swap transactions are built and every limit is
checked, nothing is signed or sent, and the lane ledger is only read.

Flow per intent (a row of the pushed live-intents.jsonl, see tools/slowbot/intents.py):
  refuse if older than max_intent_age_s (default 90) -> refuse if the lane STOP / the bot STOP is present -> own limits (max_open 2, mint held by
  another lane bot, sub-budget 0.06 SOL never reset) -> Jupiter quote (slippage cap) -> entry vs paper entry -> swap transaction built, fee payer
  checked, signed -> re-quote just before the send (on-chain route state; refuse if it degraded beyond requote_degrade_bps) -> LaneLedger.reserve
  (shared DEC-035 limits: 0.30 total, 0.10 daily, max 3 open; writes and fsyncs the `send_intent` row BEFORE the send) -> send -> confirm by
  signature -> `fill` row. Unresolved signatures are reconciled by signature on every loop and after a restart.

Exits mirror tools/slowbot/positions.py EXACTLY by construction: LivePositions subclasses PositionManager and inherits `_step`, `tick`, `_miss`
and the constants unchanged (positions.py: STOP 0.15 :42, TAKE 0.40 :43, TAKE_FRACTION 0.5 :44, TRAIL 0.20 :45, TIME_STOP_S 4 h :46, LIQ_COLLAPSE
0.40 :47, LIQ_UNKNOWN_MAX 3 :48, NO_QUOTE_WRITEOFF_S 600 :49, POLL_S 30 :41; the order liq_collapse, liq_unknown_exit, stop, trail, time_stop,
then take-half, then no_quote_writeoff is `_step` / `_miss`). Only the three places that book a SELL are overridden to do the sell for real and book
the actual proceeds: `_fresh_fill` (positions.py :`_fresh_fill`), `_take_half` (same mutations as positions.py `_take_half`) and `_exit`. A failed
live sell leaves the position open and is retried at the next poll.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

from tools import h5_lane_executor as lane
from tools.slowbot import intents as itn
from tools.slowbot import positions as pos
from tools.slowbot.positions import PositionManager, atomic_write_json
from tools.slowbot.quotes import WSOL

BOT_ID = "slowbot-h-1"
DEFAULTS = {
    "bot_id": BOT_ID, "stake_lamports": 20_000_000, "max_open": 2, "bot_budget_lamports": 60_000_000,
    "lane_budget_lamports": 300_000_000, "lane_daily_loss_lamports": 100_000_000, "lane_max_open": 3,
    "fee_reserve_lamports": lane.LANE_MIN_FEE_RESERVE, "mark_stale_s": 60,
    "max_intent_age_s": 90, "max_slippage_bps": 300, "sell_slippage_bps": 1500, "max_entry_vs_paper_bps": 1500,
    "requote_degrade_bps": 200, "max_price_impact_bps": 500, "buy_priority_lamports": 200_000, "sell_priority_lamports": 200_000,
    "wallet_floor_lamports": 50_000_000, "confirm_timeout_s": 45, "intent_poll_s": 2.0, "reconcile_every_s": 5.0,
    "unresolved_s": 600, "dry_run": True,
}
HARD_MAX_SLIPPAGE_BPS = 500
ATA_RENT = pos.ATA_RENT_LAMPORTS


class Refused(Exception):
    pass


def _now_ms(now: float) -> int:
    return int(now * 1000)


# --- config ------------------------------------------------------------------------------------------------------------------------------
def parse_config(raw: Any) -> dict[str, Any]:
    """Config can only LOWER the DEC-035 maxima. Unknown keys are refused. Keyless."""
    if not isinstance(raw, dict):
        raise ValueError("config must be an object")
    allowed = set(DEFAULTS) | {"intents_path", "state_dir", "ledger_path", "stop_path", "bot_stop_path", "live_ok_path", "user", "rpc_url_env",
                               "jupiter_quote_url", "jupiter_swap_url", "dexscreener_url"}
    extra = set(raw) - allowed
    if extra:
        raise ValueError(f"unknown config keys {sorted(extra)}")
    c = {**DEFAULTS, **raw}
    for k in ("intents_path", "state_dir", "ledger_path", "stop_path"):
        if not isinstance(c.get(k), str) or not c[k]:
            raise ValueError(f"{k} is required")
    c.setdefault("bot_stop_path", str(Path(c["state_dir"]) / "STOP-slowbot-h-1"))
    if c["bot_id"] != BOT_ID:
        raise ValueError(f"bot_id must be {BOT_ID}")
    if not lane.LANE_STAKE_MIN_LAMPORTS <= c["stake_lamports"] <= lane.LANE_STAKE_MAX_LAMPORTS:
        raise ValueError("stake_lamports outside the DEC-035 band")
    if not 1 <= c["max_open"] <= 2:
        raise ValueError("max_open must be 1..2")
    if not 1 <= c["bot_budget_lamports"] <= 60_000_000:
        raise ValueError("bot_budget_lamports must be 1..60000000")
    if not 1 <= c["lane_max_open"] <= lane.LANE_MAX_OPEN or not 1 <= c["lane_budget_lamports"] <= lane.LANE_BUDGET_MAX_LAMPORTS \
            or not 1 <= c["lane_daily_loss_lamports"] <= lane.LANE_DAILY_MAX_LAMPORTS:
        raise ValueError("shared lane limits above DEC-035 maxima")
    if not 1 <= c["max_slippage_bps"] <= HARD_MAX_SLIPPAGE_BPS or not 1 <= c["sell_slippage_bps"] <= 3000:
        raise ValueError("slippage cap out of range")
    if not 0 < c["max_intent_age_s"] <= 600:
        raise ValueError("max_intent_age_s must be in (0, 600]")
    return c


def lane_config(c: dict[str, Any]) -> lane.LaneConfig:
    return lane.LaneConfig.parse({
        "ledger_path": c["ledger_path"], "stop_path": c["stop_path"], "bot_id": c["bot_id"], "stake_lamports": c["stake_lamports"],
        "max_open": c["lane_max_open"], "budget_lamports": c["lane_budget_lamports"], "daily_loss_lamports": c["lane_daily_loss_lamports"],
        "fee_reserve_lamports": c["fee_reserve_lamports"], "mark_stale_s": c["mark_stale_s"]})


# --- external clients (all injectable; tests use fakes) ----------------------------------------------------------------------------------
class JupiterSwap:
    """Jupiter lite-api quote + swap. `get(url)` and `post(url, body)` return parsed JSON and raise on failure."""

    def __init__(self, get: Callable[[str], dict], post: Callable[[str, dict], dict],
                 quote_url="https://lite-api.jup.ag/swap/v1/quote", swap_url="https://lite-api.jup.ag/swap/v1/swap"):
        self.get, self.post, self.quote_url, self.swap_url = get, post, quote_url, swap_url

    def quote(self, in_mint: str, out_mint: str, amount: int, slippage_bps: int) -> dict:
        return self.get(f"{self.quote_url}?inputMint={in_mint}&outputMint={out_mint}&amount={int(amount)}&slippageBps={int(slippage_bps)}")

    def swap_tx(self, quote: dict, user: str, priority_lamports: int) -> dict:
        return self.post(self.swap_url, {"quoteResponse": quote, "userPublicKey": user, "wrapAndUnwrapSol": True,
                                         "dynamicComputeUnitLimit": True, "prioritizationFeeLamports": int(priority_lamports)})


class KeySigner:
    """Signs a Jupiter-built transaction with the lane key. Refuses a transaction whose fee payer is not the lane wallet."""

    def __init__(self, keypair):
        self._kp = keypair

    def __repr__(self) -> str:  # never reveal the key
        return "KeySigner(<redacted>)"

    @property
    def pubkey(self) -> str:
        return str(self._kp.pubkey())

    def sign(self, tx_b64: str) -> tuple[str, str]:
        from solders.transaction import VersionedTransaction
        vt = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
        if str(vt.message.account_keys[0]) != self.pubkey:
            raise Refused("tx_fee_payer_not_lane_wallet")
        signed = VersionedTransaction(vt.message, [self._kp])
        return base64.b64encode(bytes(signed)).decode(), str(signed.signatures[0])


class Chain:
    """Solana JSON-RPC reads and the send. `rpc(method, params)` returns the `result` or raises."""

    def __init__(self, rpc: Callable[[str, list], Any], wallet: str):
        self.rpc, self.wallet = rpc, wallet

    def send(self, signed_b64: str) -> str:
        return self.rpc("sendTransaction", [signed_b64, {"encoding": "base64", "skipPreflight": False, "maxRetries": 3,
                                                          "preflightCommitment": "confirmed"}])

    def status(self, sig: str) -> dict | None:
        v = self.rpc("getSignatureStatuses", [[sig], {"searchTransactionHistory": True}])["value"][0]
        return v

    def block_height(self) -> int:
        return int(self.rpc("getBlockHeight", [{"commitment": "confirmed"}]))

    def balance(self) -> int:
        return int(self.rpc("getBalance", [self.wallet, {"commitment": "confirmed"}])["value"])

    def result(self, sig: str, mint: str) -> dict:
        """Wallet SOL delta, network fee, token delta and whether the token account was created, from the confirmed transaction."""
        tx = self.rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}])
        meta = tx["meta"]
        keys = [k["pubkey"] if isinstance(k, dict) else k for k in tx["transaction"]["message"]["accountKeys"]]
        i = keys.index(self.wallet)

        def tok(rows):
            return sum(int(b["uiTokenAmount"]["amount"]) for b in (rows or []) if b.get("owner") == self.wallet and b.get("mint") == mint)
        pre_has = any(b.get("owner") == self.wallet and b.get("mint") == mint for b in (meta.get("preTokenBalances") or []))
        return {"sol_delta": int(meta["postBalances"][i]) - int(meta["preBalances"][i]), "fee": int(meta["fee"]), "err": meta.get("err"),
                "token_delta": tok(meta.get("postTokenBalances")) - tok(meta.get("preTokenBalances")), "ata_created": not pre_has}


# --- exits: positions.py mirrored by subclassing ---------------------------------------------------------------------------------------
class LivePositions(PositionManager):
    """Inherits positions.py `_step` / `tick` / `_miss` UNCHANGED: every exit threshold and its order is the paper H arm's. Overrides only the
    sell bookings. `sell_fn(pos, tokens, reason) -> dict|None` performs the live sell and returns {"proceeds", "fee"} or None on failure."""

    def __init__(self, state_path, log_path, quote_fn, *, sell_fn, on_exit=None, on_partial=None, now_fn=time.time):
        super().__init__(state_path, log_path, quote_fn, now_fn=now_fn)
        self.sell_fn, self.on_exit, self.on_partial = sell_fn, on_exit, on_partial

    def adopt(self, mint: str, intent_id: str, tokens: int, cost_lamports: int, liq_usd: float | None, opened_t: float, extra: dict | None = None) -> dict:
        """The same position record positions.py `open` builds (its field set), for a live fill."""
        self.state["seq"] += 1
        pid = f"H-{self.state['seq']:06d}"
        p = {"id": pid, "arm": "H", "mint": mint, "pool": None, "symbol": None, "opened_t": opened_t, "cost_lamports": cost_lamports,
             "tokens": tokens, "tokens_left": tokens, "entry_unit": cost_lamports / tokens, "entry_liq_usd": liq_usd, "taken": False,
             "high_ratio": None, "proceeds_lamports": 0, "fees_lamports": 0, "legs": 0, "misses": 0, "miss_since_t": None,
             "liq_unknown_n": 0, "last_ratio": None, "last_value_lamports": None, "intent_id": intent_id, **(extra or {})}
        self.state["open"][pid] = p
        self._log("open", opened_t, p, cost_lamports=cost_lamports, tokens=tokens, entry_liq_usd=liq_usd, live=True)
        self._save()
        return p

    def _fresh_fill(self, p, amount, trigger_out):  # positions.py _fresh_fill re-quotes for the paper fill; live books the real proceeds in _exit
        return trigger_out, "live_pending"

    def _take_half(self, p, now, ratio, trigger_out):  # same mutations as positions.py _take_half, with the real fill
        sell = int(p["tokens_left"] * pos.TAKE_FRACTION)
        r = self.sell_fn(p, sell, "take_half")
        if r is None:
            self._save()
            return  # not taken: the trigger is re-evaluated at the next poll
        fill = r["proceeds"]
        p["proceeds_lamports"] += fill
        p["fees_lamports"] += r["fee"]
        p["tokens_left"] -= sell
        p["taken"] = True
        p["high_ratio"] = max(ratio, (fill / sell) / p["entry_unit"])
        p["legs"] += 1
        self._log("leg", now, p, leg="take_half", tokens_sold=sell, trigger_ratio=ratio, fill_out_lamports=fill,
                  fill_ratio=(fill / sell) / p["entry_unit"], fill_basis="live", leg_no=p["legs"], signature=r.get("signature"))
        if self.on_partial:
            self.on_partial(p, r)
        self._save()

    def _exit(self, p, now, fill, reason, *, trigger_out, fill_basis, trigger_ratio=None):
        if reason == "no_quote_writeoff":  # positions.py: booked at 0 proceeds, no sell is possible
            r = {"proceeds": 0, "fee": 0, "signature": None}
        else:
            r = self.sell_fn(p, p["tokens_left"], reason)
            if r is None:
                self._save()
                return None  # the position stays open; the trigger fires again at the next poll
        row = super()._exit(p, now, r["proceeds"], reason, trigger_out=trigger_out, fill_basis="live" if reason != "no_quote_writeoff" else fill_basis,
                            trigger_ratio=trigger_ratio)
        if self.on_exit:
            self.on_exit(p, r, row)
        return row


# --- the executor ----------------------------------------------------------------------------------------------------------------------
class SlowbotLive:
    def __init__(self, cfg: dict[str, Any], *, jup: JupiterSwap, chain: Chain | None, signer: KeySigner | Any | None, liq_fn: Callable[[str], float | None],
                 user: str, now_fn: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep):
        self.cfg, self.jup, self.chain, self.signer, self.liq_fn, self.user = cfg, jup, chain, signer, liq_fn, user
        self.now_fn, self.sleep = now_fn, sleep
        self.dry = bool(cfg["dry_run"]) or signer is None
        self.lc = lane_config(cfg)
        self.ledger = lane.LaneLedger(self.lc)
        sd = Path(cfg["state_dir"])
        sd.mkdir(parents=True, exist_ok=True)
        self.state_path, self.log_path = sd / "executor-state.json", sd / "executor-log.jsonl"
        self.st = self._load()
        self.marks: dict[str, tuple[int, int]] = {}
        self._last_reconcile = 0.0
        self.pm = LivePositions(sd / "positions-state.json", sd / "positions-log.jsonl", self._sell_quote, sell_fn=self._live_sell,
                                on_exit=self._on_exit, on_partial=self._on_partial, now_fn=now_fn)

    # -- state and log
    def _load(self) -> dict:
        try:
            st = json.loads(self.state_path.read_text())
        except FileNotFoundError:
            return {"done": {}, "pending": {}}
        if not isinstance(st, dict) or not {"done", "pending"} <= set(st):
            raise ValueError("executor state is malformed; refusing to start")
        return st

    def _save(self) -> None:
        atomic_write_json(self.state_path, self.st)

    def log(self, kind: str, **kw: Any) -> None:
        pos._append_jsonl(self.log_path, {"kind": kind, "t": self.now_fn(), "bot_id": BOT_ID, "dry": self.dry, **kw})

    # -- ledger views
    def _view(self) -> lane.LaneView:
        with self.ledger.locked():
            return lane.lane_fold(self.ledger.read(), {}, _now_ms(self.now_fn()), self.lc.mark_stale_s, BOT_ID)

    def bot_stop_present(self) -> bool:
        try:
            os.stat(self.cfg["bot_stop_path"])
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return True

    def _write_bot_stop(self, reason: str) -> None:
        p = Path(self.cfg["bot_stop_path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        except FileExistsError:
            return
        with os.fdopen(fd, "w") as f:
            f.write(f"{reason} ts_ms={_now_ms(self.now_fn())} bot_id={BOT_ID}\n")  # never reset by this process: only the manager removes it

    def own_limits(self, mint: str, stake: int) -> str | None:
        """The slowbot-h-1 limits, on top of the shared lane limits LaneLedger.reserve enforces. Fail closed."""
        if self.bot_stop_present():
            return "bot_stop_file"
        try:
            with self.ledger.locked():
                rows = self.ledger.read()
            allv = lane.lane_fold(rows, self.marks, _now_ms(self.now_fn()), self.lc.mark_stale_s, BOT_ID)
            own = lane.lane_fold([r for r in rows if isinstance(r, dict) and r.get("bot_id") == BOT_ID], self.marks, _now_ms(self.now_fn()),
                                 self.lc.mark_stale_s, BOT_ID)
        except Exception:
            return "lane_ledger_error"
        for key in allv.open:
            owner, m = key.split(":", 1)
            if m == mint:
                return "mint_open_own" if owner == BOT_ID else "mint_held_by_other_bot"
        budget = self.cfg["bot_budget_lamports"]
        if own.pnl <= -budget:
            self._write_bot_stop(f"bot_budget_stop pnl_lamports={own.pnl} budget_lamports={budget}")
            return "bot_budget_stop"
        if len(own.open) >= self.cfg["max_open"]:
            return "bot_max_open"
        reserve = self.cfg["fee_reserve_lamports"] * (len(own.open) + 1)
        if own.pnl - (own.open_stakes + stake + reserve) <= -budget:
            return "bot_budget_presend"
        return None

    # -- intents
    def process_intents(self) -> int:
        n = 0
        for row in itn.read_intents(self.cfg["intents_path"]):
            iid = row.get("intent_id")
            if not isinstance(iid, str) or iid in self.st["done"]:
                continue
            n += 1
            try:
                out = self.handle(row)
            except Refused as e:
                out = f"refused:{e}"
            except Exception as e:  # never kill the loop; the intent is closed and logged
                out = f"error:{type(e).__name__}"
            self.st["done"][iid] = out
            self._save()
            self.log("intent", intent_id=iid, mint=row.get("mint"), outcome=out)
        return n

    def handle(self, row: dict) -> str:
        now = self.now_fn()
        mint, stake = row["mint"], self.cfg["stake_lamports"]
        dts = row.get("decision_ts")
        if not isinstance(dts, (int, float)) or isinstance(dts, bool):
            raise Refused("no_decision_ts")
        age = now - dts
        if age > self.cfg["max_intent_age_s"] or age < -5:
            raise Refused("stale_intent")
        pe = row.get("paper_entry") or {}
        if not isinstance(pe.get("in_lamports"), int) or not isinstance(pe.get("out_tokens"), int) or pe["out_tokens"] <= 0:
            raise Refused("bad_paper_entry")
        why = self.ledger.check(self.marks, _now_ms(now), stake)
        if why:
            raise Refused(why)
        why = self.own_limits(mint, stake)
        if why:
            raise Refused(why)
        if not self.dry and self.chain is not None:
            need = self.cfg["wallet_floor_lamports"] + stake + self.cfg["fee_reserve_lamports"]
            if self.chain.balance() < need:
                raise Refused("wallet_floor")
        q1 = self.jup.quote(WSOL, mint, stake, self.cfg["max_slippage_bps"])
        out1 = self._check_quote(q1, stake, pe)
        swap = self.jup.swap_tx(q1, self.user, self.cfg["buy_priority_lamports"])
        tx_b64 = swap["swapTransaction"]
        if self.dry:
            self.log("dry_would_send", intent_id=row["intent_id"], mint=mint, stake_lamports=stake, quote_out=out1, tx_built=bool(tx_b64))
            return "dry_would_send"
        signed_b64, sig = self.signer.sign(tx_b64)
        q2 = self.jup.quote(WSOL, mint, stake, self.cfg["max_slippage_bps"])  # re-check immediately before the send
        out2 = self._check_quote(q2, stake, pe)
        if out2 < out1 * (1 - self.cfg["requote_degrade_bps"] / 1e4):
            raise Refused("requote_degraded")
        why = self.ledger.reserve(mint, sig, self.marks, _now_ms(self.now_fn()), stake)  # write-ahead send_intent row, fsynced, under the lock
        if why:
            raise Refused(why)
        self.st["pending"][sig] = {"kind": "buy", "mint": mint, "intent_id": row["intent_id"], "t": self.now_fn(), "stake": stake,
                                   "liq_usd": pe.get("liq_usd"), "last_valid": swap.get("lastValidBlockHeight")}
        self._save()
        self.chain.send(signed_b64)
        self.log("sent", intent_id=row["intent_id"], mint=mint, signature=sig)
        self.reconcile(force=True)
        return "sent"

    def _check_quote(self, q: dict, stake: int, pe: dict) -> int:
        try:
            out, inn = int(q["outAmount"]), int(q["inAmount"])
        except (KeyError, TypeError, ValueError):
            raise Refused("bad_quote") from None
        if inn != stake or out <= 0:
            raise Refused("quote_not_stake_size")
        try:
            impact = abs(float(q.get("priceImpactPct") or 0)) * 1e4
        except ValueError:
            raise Refused("bad_quote") from None
        if impact > self.cfg["max_price_impact_bps"]:
            raise Refused("price_impact")
        paper_unit = pe["in_lamports"] / pe["out_tokens"]
        if (inn / out) > paper_unit * (1 + self.cfg["max_entry_vs_paper_bps"] / 1e4):
            raise Refused("entry_worse_than_paper")
        return out

    # -- reconcile by signature
    def reconcile(self, force: bool = False) -> None:
        now = self.now_fn()
        if not force and now - self._last_reconcile < self.cfg["reconcile_every_s"]:
            return
        self._last_reconcile = now
        if self.dry or self.chain is None:
            return
        try:
            self.ledger.drain()
        except Exception:
            pass
        # a send_intent in the ledger with no pending entry (crash between the ledger row and the state write) is adopted from the ledger
        try:
            for key, p in self._view().open.items():
                if key.startswith(BOT_ID + ":") and not p["filled"] and p.get("signature") and p["signature"] not in self.st["pending"]:
                    self.st["pending"][p["signature"]] = {"kind": "buy", "mint": key.split(":", 1)[1], "intent_id": None, "t": p["ts_ms"] / 1000.0,
                                                          "stake": p["stake"], "liq_usd": None, "last_valid": None}
        except Exception:
            pass
        for sig, e in list(self.st["pending"].items()):
            try:
                st = self.chain.status(sig)
            except Exception:
                continue
            if st is not None and st.get("confirmationStatus") in ("confirmed", "finalized"):
                self._settle(sig, e, st)
            elif now - e["t"] > self.cfg["unresolved_s"]:
                self._expire(sig, e)
        self._recover_positions()
        self._save()

    def _settle(self, sig: str, e: dict, st: dict) -> None:
        mint, ms = e["mint"], _now_ms(self.now_fn())
        res = self.chain.result(sig, mint)
        if st.get("err") or res["err"] or res["token_delta"] <= 0:
            self.ledger.record("fill", mint, self.marks, ms, ok=False, signature=sig, sol_out=max(res["fee"], -res["sol_delta"]), fee_network=res["fee"],
                               reason="tx_error")
            self.log("buy_failed", mint=mint, signature=sig)
        else:
            spent = -res["sol_delta"]
            rent = ATA_RENT if res["ata_created"] else 0
            self.ledger.record("fill", mint, self.marks, ms, ok=True, signature=sig, sol_out=spent, fee_network=res["fee"], rent_unreclaimed=rent,
                               tokens=res["token_delta"], cost_lamports=e["stake"], liq_usd=e.get("liq_usd"), opened_t=self.now_fn(),
                               intent_id=e.get("intent_id"))
            self._adopt(mint, e.get("intent_id"), res["token_delta"], e["stake"], e.get("liq_usd"), self.now_fn())
            self.log("buy_filled", mint=mint, signature=sig, tokens=res["token_delta"], spent_lamports=spent)
        self.st["pending"].pop(sig, None)

    def _expire(self, sig: str, e: dict) -> None:
        """No outcome after unresolved_s: closed at its FULL cost (lane_executor LANE_UNRESOLVED_MS convention). If it lands later the manager
        finds untracked tokens; that is logged as a loud event."""
        self.ledger.record("writeoff", e["mint"], self.marks, _now_ms(self.now_fn()), signature=sig, sol_out=e["stake"], reason="unresolved_full_cost")
        self.log("unresolved_writeoff", mint=e["mint"], signature=sig)
        self.st["pending"].pop(sig, None)

    def _adopt(self, mint, intent_id, tokens, cost, liq, opened_t) -> None:
        if not any(p["mint"] == mint for p in self.pm.open_positions()):
            self.pm.adopt(mint, intent_id, tokens, cost, liq, opened_t)

    def _recover_positions(self) -> None:
        """A filled own position in the ledger with no live position record (crash between the fill row and the state write) is rebuilt from its fill row."""
        try:
            with self.ledger.locked():
                rows = self.ledger.read()
            view = lane.lane_fold([r for r in rows if isinstance(r, dict) and r.get("bot_id") == BOT_ID], {}, _now_ms(self.now_fn()), 9, BOT_ID)
        except Exception:
            return
        for key, p in view.open.items():
            mint = key.split(":", 1)[1]
            if not key.startswith(BOT_ID + ":") or not p["filled"] or any(x["mint"] == mint for x in self.pm.open_positions()):
                continue
            fills = [r for r in rows if r.get("bot_id") == BOT_ID and r.get("ev") == "fill" and r.get("mint") == mint and r.get("ok") is True]
            if fills and fills[-1].get("tokens"):
                f = fills[-1]
                self.pm.adopt(mint, f.get("intent_id"), int(f["tokens"]), int(f.get("cost_lamports") or p["stake"]), f.get("liq_usd"),
                              float(f.get("opened_t") or self.now_fn()))

    # -- exits
    def _sell_quote(self, mint: str, amount: int) -> dict:
        q = self.jup.quote(mint, WSOL, amount, self.cfg["sell_slippage_bps"])
        out = int(q["outAmount"])
        self.marks[mint] = (out, _now_ms(self.now_fn()))
        return {"out_lamports": out, "liq_usd": self.liq_fn(mint)}

    def _live_sell(self, p: dict, tokens: int, reason: str) -> dict | None:
        mint = p["mint"]
        if self.dry or self.chain is None:
            return {"proceeds": 0, "fee": 0, "signature": None}
        try:
            q = self.jup.quote(mint, WSOL, tokens, self.cfg["sell_slippage_bps"])
            swap = self.jup.swap_tx(q, self.user, self.cfg["sell_priority_lamports"])
            signed_b64, sig = self.signer.sign(swap["swapTransaction"])
            self.chain.send(signed_b64)
        except Exception as e:
            self.log("sell_failed", mint=mint, reason=reason, error_class=type(e).__name__)
            return None
        deadline = self.now_fn() + self.cfg["confirm_timeout_s"]
        while self.now_fn() < deadline:
            try:
                st = self.chain.status(sig)
            except Exception:
                st = None
            if st is not None and st.get("confirmationStatus") in ("confirmed", "finalized"):
                res = self.chain.result(sig, mint)
                if st.get("err") or res["err"]:
                    self.ledger.record("fee", mint, self.marks, _now_ms(self.now_fn()), signature=sig, sol_out=res["fee"], fee_network=res["fee"], reason="sell_failed_tx")
                    return None
                return {"proceeds": res["sol_delta"] + res["fee"], "fee": res["fee"], "signature": sig}
            self.sleep(1.0)
        self.log("sell_unconfirmed", mint=mint, signature=sig, reason=reason)
        return None  # the position stays open; a landed sell is seen as a zero balance at the next sell quote and logged

    def _on_exit(self, p: dict, r: dict, row: dict) -> None:
        ms = _now_ms(self.now_fn())
        if row["reason"] == "no_quote_writeoff":
            self.ledger.record("writeoff", p["mint"], self.marks, ms, reason="no_quote_writeoff")
        else:
            self.ledger.record("exit", p["mint"], self.marks, ms, sol_in=r["proceeds"], sol_out=r["fee"], fee_network=r["fee"], signature=r.get("signature"),
                               reason=row["reason"])
        self.marks.pop(p["mint"], None)

    def _on_partial(self, p: dict, r: dict) -> None:
        self.ledger.record("partial_exit", p["mint"], self.marks, _now_ms(self.now_fn()), sol_in=r["proceeds"], sol_out=r["fee"],
                           fee_network=r["fee"], signature=r.get("signature"), reason="take_half")

    # -- loop
    def step(self) -> None:
        self.process_intents()
        self.reconcile()

    def run(self, duration: float | None = None, poll_s: float = pos.POLL_S) -> None:
        t0, last_tick = self.now_fn(), 0.0
        while duration is None or self.now_fn() - t0 < duration:
            self.step()
            if self.now_fn() - last_tick >= poll_s:
                last_tick = self.now_fn()
                self.pm.tick()
            self.sleep(self.cfg["intent_poll_s"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--live", action="store_true", help="sign and send; needs dry_run=false, the live_ok_path file and the systemd credential")
    ap.add_argument("--duration", type=float, default=None)
    a = ap.parse_args(argv)
    cfg = parse_config(json.loads(Path(a.config).read_text()))
    live = a.live
    if live:
        if cfg["dry_run"]:
            raise SystemExit("--live with dry_run=true in the config: refusing")
        if not cfg.get("live_ok_path") or not Path(cfg["live_ok_path"]).exists():
            raise SystemExit("live_ok_path missing: refusing to go live")
    else:
        cfg["dry_run"] = True
    import urllib.request

    def get(url):
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read())

    def post(url, body):
        req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    rpc_url = os.environ.get(cfg.get("rpc_url_env") or "SLOWBOT_RPC_URL") or (
        f"https://mainnet.helius-rpc.com/?api-key={os.environ['HELIUS_API_KEY']}" if os.environ.get("HELIUS_API_KEY") else None)
    chain = signer = None
    user = cfg.get("user")
    if live:
        from tools import probe_live as pl
        kp = pl.load_probe_key()
        signer, user = KeySigner(kp), str(kp.pubkey())

    def rpc(method, params):
        r = post(rpc_url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if "error" in r:
            raise RuntimeError(method)
        return r["result"]
    if live:
        if not rpc_url:
            raise SystemExit("live mode needs an RPC url (rpc_url_env or HELIUS_API_KEY)")
        chain = Chain(rpc, user)
    if not user:
        raise SystemExit("config `user` (the lane wallet's public address) is required in dry mode")

    def liq(mint):
        try:
            d = get(f"{cfg.get('dexscreener_url', 'https://api.dexscreener.com/tokens/v1/solana')}/{mint}")
            vals = [p.get("liquidity", {}).get("usd") for p in d if isinstance(p, dict)]
            vals = [v for v in vals if isinstance(v, (int, float))]
            return max(vals) if vals else None
        except Exception:
            return None
    ex = SlowbotLive(cfg, jup=JupiterSwap(get, post, cfg.get("jupiter_quote_url", "https://lite-api.jup.ag/swap/v1/quote"),
                                          cfg.get("jupiter_swap_url", "https://lite-api.jup.ag/swap/v1/swap")),
                     chain=chain, signer=signer, liq_fn=liq, user=user)
    ex.run(duration=a.duration)
    return 0


if __name__ == "__main__":
    sys.exit(main())
