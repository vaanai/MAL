"""floor_arena_executor: the DRY executor of the FLOOR book `l1-floor-v2` (tools/arena_rule_l1.py). PAPER / DRY ONLY: LIVE_BUILT is False, no key, no wallet, no send.

FLOOR is a rule book. DEC-032 section 1 item 5 names its live path: tools/arena_live_bridge.py over tools/probe_live.LiveExecutor, NOT the H5 arena executor
(tools/h5_arena_executor.py is an H5-trigger executor: boost slices, a 330 s exit plan, slot-timed stages). This module is a thin, pinned subclass of the bridge's `Bridge`.
It edits no frozen or pinned file (arena_live_bridge.py, arena_rule_l1.py, arena_rules*.py, arena_paper.py, arena_score.py, arena_feed.py, arena_stack.py, the H5 executor).

The decisions are the frozen rule's, read from the hash-chained decision log the v2 host writes (`entry` and `exit` rows). Nothing is re-decided here.

WHAT THIS CHANGES ON TOP OF THE BRIDGE (each item is a difference between a 24 h-hold, take-profit +50 % book and the H5 / probe-profile defaults the bridge was written for)
  1 BOOK PIN       bot_id, the frozen sha (5e12f8f5, DEC-031 Am.9), max hold HOLD_MS (24 h + the 1.9 s leg), stake and the C0 table are code constants. Config can only tighten.
                   The bridge's own TIERS["T0"] (0.02 SOL, 10 trades a day) is the old L6 proposal; the DEC-032 C0 table (0.05 SOL, 2 open, 30 a day, 0.10 / 0.20 SOL stops) is used here.
  2 FEES           155,000 priority + 5,000 base = 160,000 per send (DEC-031 Am.8). The bridge's reserves assume 500,000 priority.
  3 NO STOP LOSS   The book has none: it holds 24 h or takes profit at +50 %. The bridge's sl_backstop (default -50 %) would sell positions the paper book keeps, so `backstops`
                   is replaced: only the book's own clock exit remains.
  4 CLOCK EXIT     The position carries its own deadline, decision_ms + HOLD_MS (the rule's `on_tick`). It is stored in the persisted order, so a restart of the bridge or of the host
                   does not move it. If the host is down at the deadline the bridge sells itself; the sell is flagged `late` beyond EXIT_LATE_MS and excluded from parity as a gap.
  5 EXITS FOLLOWED Only the rule's own reasons (tp_1.5x, time_24h) are followed. The v2 host re-opens its book after a restart and sells those positions at the FIRST tick
                   (reason `resume_reopen`, booked `engine_restart` in the paper ledger). A live position is not an engine position: that exit is NOT followed (counted, logged).
  6 RE-ENTRY       The rule allows a re-entry 60 s after an exit decision. The bridge never re-buys a mint; here the mint is released when its sell has landed.
  7 GATES          committed-loss stop (DEC-032 s3), wallet floor with p155 reserves, V band and meta pin on the decision row, 10-minute hold after any host restart or re-open
                   (the decision log's only gap evidence), canary duration (14 days from the first send), max concurrent positions 2.
  8 DRY REPLAY     `replay_equivalence` feeds a closed decision log through the bridge with capacity gates off (dry only) and compares the orders it would send with the log's decisions.

Parity, twin writer and tie-out are NOT wired here (they are the live-entry lane, P1): this build only records what they need (`gap`, `late` flags on the persisted order).
Nothing here says the rule makes money. FLOOR has 0 out-of-sample trades and has not cleared the promotion gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tools import arena_live_bridge as B
from tools import arena_rule_l1 as L
from tools.arena_declog import DecisionLog, utc_day
from tools.arena_paper import canon
from tools.arena_pool_table import V_HI, V_LO

LIVE_BUILT = False  # this PR builds no live entry point; a live config is refused at start
BOT_ID = L.BOT_ID
CELL = L.CELL
FROZEN_SHA = "5e12f8f597571d6d5191a7ba38125c67ad723b7d"  # DEC-031 Am.9: FLOOR's frozen sha (git: "FLOOR v2 host: trim engine print history (#796)"). A config must equal it.
FROZEN_SHA_PREFIX = FROZEN_SHA[:8]
# Config `rule_id` picks the decision-log arm this bridge follows. Default (key absent) is `l1-floor-v2`, whose behaviour is byte-identical to before this key existed. `l1-floor-v3`
# is l1-floor-v2 with a pinned vault cap q_star (tools/arena_rule_l1_v3.py); following it needs the start row's q_star to be one of Q_STAR_ALLOWED (a test pins the equality with the
# rule's own table) and, when config `expected_q_star_lamports` is given, to equal it. Anything else latches Q_STAR_LATCH (no entries). Plumbing only: nothing here says any arm makes money.
RULE_V2 = BOT_ID
RULE_V3 = "l1-floor-v3"
# `l1-floor-v4` = v3 at vault 1.0 SOL with the take-profit multiple pinned by config `expected_tp` (REQUIRED for v4, one of {1.3, 1.5}; v2 / v3 refuse the key). For v4 the entry meta `tp`
# must equal that pinned value (v2 / v3: still L.TP), the start row's binding.tp must equal it (else TP_LATCH, no entries), and the followed take-profit exit reason is `tp_<tp>x`.
RULE_V4 = "l1-floor-v4"
RULE_IDS = (RULE_V2, RULE_V3, RULE_V4)
TP_ALLOWED: tuple[float, ...] = (1.3, 1.5)
TP_LATCH = "tp_mismatch"
Q_STAR_ALLOWED: tuple[int, ...] = (500_000_000, 750_000_000, 1_000_000_000)
Q_STAR_LATCH = "q_star_mismatch"
HOLD_MS = L.HOLD_MS  # 24 h + 1.9 s, the rule's own constant (never config)
FOLLOWED_EXIT_REASONS = (L.REASON_TP, L.REASON_TIME)
PAPER_STAKE_SOL = L.STAKE_SOL  # the paper book's deciding stake (0.25); the canary stake is lower and the twin is repriced at it

# --- fees: DEC-031 Am.8 profile p155
PRIORITY_LAMPORTS = 155_000
BASE_FEE_LAMPORTS = 5_000
SEND_FEE_LAMPORTS = PRIORITY_LAMPORTS + BASE_FEE_LAMPORTS  # 160,000
UNLANDED_BUY_STATUSES = frozenset({"buy_sending", "buy_sent"})  # bridge order statuses whose buy has not landed (arena_live_bridge.open_orders)
SELL_RESERVE_SENDS = 10  # the worst case of one exit: the sell ladder's sends, each at the top priority (as in h5_arena_executor.SELL_RESERVE_SENDS)
SELL_RESERVE_LAMPORTS = SELL_RESERVE_SENDS * SEND_FEE_LAMPORTS  # 1,600,000
RENT_RESERVE_LAMPORTS = B.RENT_RESERVE_LAMPORTS  # 2,100,000, paid on the buy and refunded by the closing sell

# --- the DEC-032 section 3 C0 table, pinned in code; config may only lower it
C0 = {"stake_lamports": 50_000_000, "max_open": 2, "max_trades_per_day": 30, "daily_loss_lamports": 100_000_000, "total_loss_lamports": 200_000_000}
FUNDED_MIN_LAMPORTS, FUNDED_MAX_LAMPORTS = 350_000_000, 1_000_000_000
FLOOR_BELOW_FUNDED_LAMPORTS = 250_000_000
MIN_WALLET_FLOOR_LAMPORTS = 50_000_000
CANARY_MAX_MS = 14 * 86_400_000
CANARY_MAX_MS_CEILING = 60 * 86_400_000  # code ceiling for the optional config key canary_max_ms (DEC-035 Am.15)
GAP_HOLD_MS = 600_000  # DEC-032 s5 item 2: buys restart only after 10 gap-free minutes
EXIT_LATE_MS = 600_000  # a clock exit sent this long after its deadline is `late` (DEC-032 s5 item 4: stuck position = exit instant + 600 s)

FS_SCHEMA = "floor_executor_state_v1"


def wallet_floor_lamports(funded: int | None, configured: int = 0) -> int:
    """DEC-032 s3: floor = max(0.05 SOL, funded - 0.25 SOL); config may raise it, never lower it."""
    base = MIN_WALLET_FLOOR_LAMPORTS if funded is None else max(MIN_WALLET_FLOOR_LAMPORTS, int(funded) - FLOOR_BELOW_FUNDED_LAMPORTS)
    return max(base, int(configured))


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def followed_exit_reasons(tp: float | None = None) -> tuple[str, str]:
    """v2 / v3 (tp None): the module constants. v4: the pinned TP's reason."""
    return FOLLOWED_EXIT_REASONS if tp is None else (f"tp_{tp}x", L.REASON_TIME)


def expected_tp(d: Mapping[str, Any]) -> float | None:
    """Config `expected_tp`. v4: REQUIRED, exactly one of TP_ALLOWED (a float; no bool / int / string). v2 / v3: the key is refused (even null)."""
    if d.get("rule_id", RULE_V2) != RULE_V4:
        if "expected_tp" in d:
            raise ValueError(f"expected_tp is only for {RULE_V4!r} (remove the key, even if null)")
        return None
    tp = d.get("expected_tp")
    if isinstance(tp, bool) or not isinstance(tp, float) or tp not in TP_ALLOWED:
        raise ValueError(f"expected_tp is required for {RULE_V4!r} and must be one of {list(TP_ALLOWED)} (got {tp!r})")
    return tp


def expected_q_star(d: Mapping[str, Any]) -> int | None:
    """Optional config `expected_q_star_lamports` (v3 only; a pinned value, exact int). Absent or null = any pinned value. v2 refuses the key (a stray key must not look like it did something)."""
    if d.get("rule_id", RULE_V2) not in (RULE_V3, RULE_V4):
        if "expected_q_star_lamports" in d:
            raise ValueError(f"expected_q_star_lamports is only for {RULE_V3!r} / {RULE_V4!r} (remove the key, even if null)")
        return None
    q = d.get("expected_q_star_lamports")
    if q is None:
        return None
    if d.get("rule_id") == RULE_V4 and (isinstance(q, bool) or q != 1_000_000_000):
        raise ValueError(f"expected_q_star_lamports for {RULE_V4!r} must be 1000000000 (got {q!r})")
    if isinstance(q, bool) or not isinstance(q, int) or q not in Q_STAR_ALLOWED:
        raise ValueError(f"expected_q_star_lamports must be one of {list(Q_STAR_ALLOWED)} (got {q!r})")
    return q


def make_config(d: Mapping[str, Any]) -> B.Config:
    """The bridge Config for FLOOR. Fail closed: any pin that does not hold raises ValueError. Caps are min(config, C0); the hold is the rule's."""
    if LIVE_BUILT is False and d.get("mode", B.DRY) != B.DRY:
        raise ValueError("live_not_built: LIVE_BUILT is False, only mode 'dry' exists")
    rule_id = d.get("rule_id", RULE_V2)
    if not isinstance(rule_id, str) or rule_id not in RULE_IDS:
        raise ValueError(f"rule_id must be one of {list(RULE_IDS)}")
    if d.get("bot_id", rule_id) != rule_id:
        raise ValueError(f"bot_id must be {rule_id}")
    expected_q_star(d)
    expected_tp(d)
    sha = d.get("frozen_sha")
    if sha != FROZEN_SHA:
        raise ValueError("frozen_sha must be FLOOR's frozen sha")
    if "max_hold_ms" in d and d["max_hold_ms"] != HOLD_MS:
        raise ValueError("max_hold_ms is the rule's constant")
    for k in ("root", "state_dir"):
        if not d.get(k):
            raise ValueError(f"config {k} is required")
    end = d.get("end_ms")
    if not _num(end) or end <= 0:
        raise ValueError("config end_ms is required (explicit, positive)")
    kw: dict[str, Any] = {}
    if "canary_max_ms" in d:
        cm = d["canary_max_ms"]
        if isinstance(cm, bool) or not isinstance(cm, int) or not 0 < cm <= CANARY_MAX_MS_CEILING:
            raise ValueError(f"config canary_max_ms must be an int in (0, {CANARY_MAX_MS_CEILING}] milliseconds")
        kw["canary_max_ms"] = cm
    for k, cap in C0.items():
        v = d.get(k, cap)
        if not _num(v) or v <= 0:
            raise ValueError(f"config {k} must be a positive number")
        kw[k] = min(int(v), cap)
    funded = d.get("funded_lamports")
    if funded is not None and not (_num(funded) and FUNDED_MIN_LAMPORTS <= funded <= FUNDED_MAX_LAMPORTS):
        raise ValueError("funded_lamports must be 0.35 to 1.00 SOL")
    kw["wallet_floor_lamports"] = wallet_floor_lamports(None if funded is None else int(funded), int(d.get("wallet_floor_lamports", 0) or 0))
    ptl = d.get("per_trade_loss_lamports", 15_000_000)
    if not _num(ptl) or ptl <= 0:
        raise ValueError("config per_trade_loss_lamports must be a positive number")
    # A stake-sized loss is the whole position. FLOOR has no stop, so a total loss of one position is a legal outcome and trips the cap latch; keep the bridge default but never above the stake.
    kw["per_trade_loss_lamports"] = min(int(ptl), kw["stake_lamports"])
    for k in ("stop_file", "halt_file"):
        if d.get(k):
            kw[k] = str(d[k])
    if "dry_balance_lamports" in d:
        kw["dry_balance_lamports"] = int(d["dry_balance_lamports"])
    once = d.get("wallet_floor_count_held_once", False)
    if not isinstance(once, bool):
        raise ValueError("config wallet_floor_count_held_once must be a boolean")
    kw["wallet_floor_count_held_once"] = once
    return B.Config(bot_id=rule_id, frozen_sha=sha, root=str(d["root"]), state_dir=str(d["state_dir"]), end_ms=int(end), max_hold_ms=HOLD_MS, mode=B.DRY,
                    sl_backstop=B.MAX_SL_BACKSTOP, **kw)


class FloorBridge(B.Bridge):
    """Bridge pinned to FLOOR. `dry_replay_caps=True` (dry only) switches the capacity gates off so a replay of a whole decision log shows every decision going through."""

    def __init__(self, cfg: B.Config, port: B.OrderPort, *, now_ms: Callable[[], int] | None = None, dry_replay_caps: bool = False,
                 live_ok_path: Path = B.LIVE_OK_PATH, tier_path: Path = B.TIER_PATH, root_checks: bool = True,
                 close_blocked: Callable[[str], bool | None] | None = None, expected_q_star: int | None = None, expected_tp: float | None = None) -> None:
        if cfg.bot_id not in RULE_IDS or cfg.max_hold_ms != HOLD_MS or cfg.frozen_sha != FROZEN_SHA:
            raise SystemExit("floor executor start refused: book pin")
        if expected_q_star is not None and (cfg.bot_id not in (RULE_V3, RULE_V4) or isinstance(expected_q_star, bool) or expected_q_star not in Q_STAR_ALLOWED
                                            or (cfg.bot_id == RULE_V4 and expected_q_star != 1_000_000_000)):
            raise SystemExit("floor executor start refused: expected_q_star")
        if (cfg.bot_id == RULE_V4) != (expected_tp is not None) or (expected_tp is not None and (isinstance(expected_tp, bool) or expected_tp not in TP_ALLOWED)):
            raise SystemExit("floor executor start refused: expected_tp")  # v4 needs a pinned TP, v2 / v3 take none
        self.expected_q_star = expected_q_star
        self.expected_tp = expected_tp
        self.followed_exits = followed_exit_reasons(expected_tp)
        self._start_guard(cfg, port)
        self.dry_replay_caps = bool(dry_replay_caps)
        sd = Path(cfg.state_dir)
        self.fs_path = sd / f"floor-state-{cfg.mode}.json"
        self.fs = self._load_fs()
        if self.expected_tp is not None and self.fs.get("tp") not in (None, self.expected_tp):
            raise SystemExit("floor executor start refused: expected_tp")  # same state_dir, other TP: open positions would lose their take-profit sell
        super().__init__(cfg, port, now_ms=now_ms, live_ok_path=live_ok_path, tier_path=tier_path, root_checks=root_checks, close_blocked=close_blocked)

    def _start_guard(self, cfg: B.Config, port: B.OrderPort) -> None:
        """This dry executor never takes a live config or port. The lane subclass (tools/floor_lane_executor.py) replaces this one method; nothing else here changes."""
        if cfg.mode != B.DRY or port.live or LIVE_BUILT:
            raise SystemExit("floor executor start refused: live_not_built")

    # -- sidecar state (the bridge's BridgeState has a fixed field set; FLOOR's own facts live beside it, atomic like it)
    def _load_fs(self) -> dict[str, Any]:
        if self.fs_path.exists():
            raw = json.loads(self.fs_path.read_text())
            if raw.get("schema") != FS_SCHEMA:
                raise SystemExit("floor executor start refused: floor state schema")
            return raw
        return {"schema": FS_SCHEMA, "first_send_ms": None, "gap_hold_until_ms": 0, "starts_seen": 0, "gaps": [], "exits_not_followed": {}, "late_exits": []}

    def _save(self) -> None:
        super()._save()
        B._atomic_write(self.fs_path, self.fs)

    # -- limits: the C0 table, never the bridge's older T0
    def tier_limits(self) -> tuple[str, dict[str, int], str | None]:
        if self.dry_replay_caps:
            big = 10 ** 12
            return "T0", {"stake_lamports": min(self.cfg.stake_lamports, C0["stake_lamports"]), "max_open": big, "max_trades_per_day": big, "daily_loss_lamports": big,
                          "total_loss_lamports": big}, "dry_replay_caps"
        lim = {k: min(getattr(self.cfg, k), v) for k, v in C0.items()}
        return "T0", lim, None

    # -- money
    def _position_worst_case(self, stake: int) -> int:
        return stake + SEND_FEE_LAMPORTS + RENT_RESERVE_LAMPORTS + SELL_RESERVE_LAMPORTS

    def committed_loss_lamports(self, scope: bool = True) -> int:
        """realized loss since the first send (floored at 0) + the worst case of every open or in-flight position (DEC-032 s3)."""
        total = max(0, -int(self.state.realized_lamports))
        return total + sum(self._position_worst_case(int(o["stake_lamports"])) for o in self.open_orders(scope).values())

    def floor_problem(self, stake: int, bal: int | None, scope: bool = True) -> str | None:
        if self.dry_replay_caps:
            return None
        if bal is None:
            return "balance_unreadable"
        opens = self.open_orders(scope)
        if self.committed_loss_lamports(scope) + self._position_worst_case(stake) > self.cfg.total_loss_lamports:
            return "committed_loss_stop"
        if getattr(self.cfg, "wallet_floor_count_held_once", False):
            # DEC-035 Am.4 item 5: a landed buy's stake has already left the balance; a buy still sending/sent (not yet landed) has not, so it stays counted
            held = sum(int(o["stake_lamports"]) for o in opens.values() if o.get("status") in UNLANDED_BUY_STATUSES)
        else:
            held = sum(int(o["stake_lamports"]) for o in opens.values())
        need = held + stake + SEND_FEE_LAMPORTS + RENT_RESERVE_LAMPORTS + (len(opens) + 1) * SELL_RESERVE_LAMPORTS + self.cfg.wallet_floor_lamports
        return "wallet_floor" if bal < need else None

    # -- entry
    def entry_refusal(self, row: Mapping[str, Any], now: int, lim: dict[str, int], scope: bool = True) -> str | None:
        why = super().entry_refusal(row, now, lim, scope)
        if why:
            return why
        meta = row.get("meta") or {}
        if row.get("stake_sol") != PAPER_STAKE_SOL:
            return "paper_stake_pin"
        if meta.get("rule") != self.cfg.bot_id or meta.get("cell") != CELL or meta.get("hold_s") != L.HOLD_S or meta.get("tp") != (L.TP if self.expected_tp is None else self.expected_tp):
            return "meta_pin"
        if self.cfg.bot_id in (RULE_V3, RULE_V4) and (self.fs.get("q_star_lamports") is None or meta.get("q_star_lamports") != self.fs["q_star_lamports"]):
            return "q_star_pin"  # v3 / v4 only: the entry was decided at the cap the start row pinned
        v, age = meta.get("v"), meta.get("age_s")
        if not _num(v) or not V_LO <= v <= V_HI:
            return "v_band"  # the rule tested it on the deciding row; restated here because the canary universe is the band (QP 10-23 item 5)
        if not _num(age) or not L.AGE_LO <= age <= L.AGE_HI:
            return "age_pin"
        if self.dry_replay_caps:
            return None
        if now < int(self.fs["gap_hold_until_ms"]):
            return "gap_hold"
        first = self.fs["first_send_ms"]
        if first is not None and now > int(first) + int(getattr(self.cfg, "canary_max_ms", CANARY_MAX_MS)):
            return "canary_expired"
        return None

    def handle_entry(self, row: dict[str, Any]) -> None:
        before = self.state.attempts
        super().handle_entry(row)
        if self.state.attempts > before and self.fs["first_send_ms"] is None:
            self.fs["first_send_ms"] = self.now_ms()  # the 14 days run from the first buy attempt

    def _check_start_q_star(self, row: Mapping[str, Any]) -> None:
        """v3 only: the start row's binding.q_star_lamports must be a pinned value, equal config expected_q_star_lamports when given, and equal the value of an earlier start row."""
        b = row.get("binding")
        q = b.get("q_star_lamports") if isinstance(b, dict) else None
        if isinstance(q, bool) or not isinstance(q, int) or q not in Q_STAR_ALLOWED or (self.cfg.bot_id == RULE_V4 and q != 1_000_000_000):
            self._latch(Q_STAR_LATCH, f"start row q_star_lamports {q!r} is not a pinned value")
        elif self.expected_q_star is not None and q != self.expected_q_star:
            self._latch(Q_STAR_LATCH, f"start row q_star_lamports {q} != expected {self.expected_q_star}")
        elif self.fs.get("q_star_lamports") not in (None, q):
            self._latch(Q_STAR_LATCH, f"start row q_star_lamports {q} != earlier start row {self.fs['q_star_lamports']}")
        else:
            self.fs["q_star_lamports"] = q

    def _check_start_tp(self, row: Mapping[str, Any]) -> None:
        """v4 only: the start row's binding.tp must equal config expected_tp and the value of an earlier start row."""
        b = row.get("binding")
        tp = b.get("tp") if isinstance(b, dict) else None
        if isinstance(tp, bool) or not isinstance(tp, float) or tp != self.expected_tp:
            self._latch(TP_LATCH, f"start row tp {tp!r} != expected {self.expected_tp}")
        elif self.fs.get("tp") not in (None, tp):
            self._latch(TP_LATCH, f"start row tp {tp} != earlier start row {self.fs['tp']}")
        else:
            self.fs["tp"] = tp

    # -- decision rows
    def _gap(self, ms: int, why: str) -> None:
        self.fs["gap_hold_until_ms"] = max(int(self.fs["gap_hold_until_ms"]), int(ms) + GAP_HOLD_MS)
        self.fs["gaps"] = (self.fs["gaps"] + [{"ms": int(ms), "why": why}])[-200:]
        self._log("gap", why=why, decision_ms=int(ms), hold_until_ms=self.fs["gap_hold_until_ms"])

    def handle_row(self, row: dict[str, Any]) -> None:
        k = row.get("kind")
        if k == "start":
            if self.cfg.bot_id in (RULE_V3, RULE_V4):
                self._check_start_q_star(row)
            if self.cfg.bot_id == RULE_V4:
                self._check_start_tp(row)
            self.fs["starts_seen"] += 1
            if self.fs["starts_seen"] > 1:
                self._gap(row["ms"], "host_restart")
        elif k in ("resume_reopen", "resume_dropped"):
            self._gap(row["ms"], k)
            o = self.state.orders.get(str(row.get("order_id")))
            if o is not None and o["status"] not in ("closed", "refused"):
                o["gap"] = True  # the paper side is excluded (engine_restart); the live trip is a gap trip, excluded from parity and counted
            self._save()
            return
        elif k == "exit":
            reason = row.get("reason")
            if reason not in self.followed_exits:
                n = self.fs["exits_not_followed"]
                n[str(reason)] = n.get(str(reason), 0) + 1
                self._count("exit_not_followed")
                self._log("exit_not_followed", order_id=row.get("order_id"), reason=reason, seq=row.get("seq"))
                self._save()
                return
        super().handle_row(row)

    # -- exits: the rule's own clock, no stop loss
    def backstops(self) -> None:
        now = self.now_ms()
        self.reconcile(only_stale=True)
        for oid, o in list(self.state.orders.items()):
            if o["status"] != "open":
                continue
            due = int(o["decision_ms"]) + HOLD_MS
            if now >= due:
                o.setdefault("exit_decision_ms", due)  # the rule's own instant, not the instant we noticed
                self._try_exit(oid, L.REASON_TIME)

    def _try_exit(self, oid: str, reason: str) -> None:
        super()._try_exit(oid, reason)
        o = self.state.orders.get(oid)
        if o is None or reason != L.REASON_TIME or not o.get("sell_send_ms"):
            return
        lag = int(o["sell_send_ms"]) - (int(o["decision_ms"]) + HOLD_MS)
        if lag > EXIT_LATE_MS and not o.get("late"):
            o["late"] = True
            o["gap"] = True
            self.fs["late_exits"] = (self.fs["late_exits"] + [{"order_id": oid, "lag_ms": lag}])[-200:]
            self._count("exit_late")
            self._log("exit_late", order_id=oid, lag_ms=lag)
            self._save()

    # -- re-entry after a landed sell (rule: 60 s after the exit decision, enforced by the rule's own pool table)
    def apply_events(self, events: list[dict[str, Any]]) -> None:
        by_mint = {o["mint"]: k for k, o in self.state.orders.items()}
        sold = [(ev.get("order_id") or by_mint.get(ev.get("mint"))) for ev in events if ev.get("event") == "sell_landed"]
        super().apply_events(events)
        for oid in sold:
            o = self.state.orders.get(oid) if oid else None
            if o is not None and o["status"] == "closed" and o["mint"] in self.state.bought:
                self.state.bought.remove(o["mint"])
        if sold:
            self._save()


# --- dry replay of a closed decision log ---------------------------------------------------------------------------------------------


class _Clock:
    def __init__(self, t: int = 0) -> None:
        self.t = t

    def __call__(self) -> int:
        return self.t


REPLAY_LAG_MS = 50  # the bridge reads each row this long after its decision instant (inside the 5 s age limit)


def decisions_from_log(root: str, bot_id: str = BOT_ID, followed: Sequence[str] = FOLLOWED_EXIT_REASONS) -> list[tuple[str, str]]:
    """The decisions the bridge must act on: one buy per entry row, one sell per order with at least one followed exit row. Sorted for comparison."""
    out, sold = [], set()
    for r in DecisionLog(root).rows(bot_id):
        if r.get("kind") == "entry":
            out.append(("buy", str(r["order_id"])))
        elif r.get("kind") == "exit" and r.get("reason") in followed and r["order_id"] not in sold:
            sold.add(r["order_id"])
            out.append(("sell", str(r["order_id"])))
    return sorted(out)


def replay_equivalence(cfg_dict: Mapping[str, Any]) -> dict[str, Any]:
    """Run a closed decision log through a dry FloorBridge (capacity gates off, dry only) and compare the orders it would send with the log's decisions.
    Result `equal` needs md5(orders) == md5(decisions) and no entry refused. No price, fill or P&L is read or produced."""
    cfg = make_config(cfg_dict)
    port = B.DryPort(cfg.dry_balance_lamports)
    clk = _Clock(0)
    br = FloorBridge(cfg, port, now_ms=clk, dry_replay_caps=True, expected_q_star=expected_q_star(cfg_dict), expected_tp=expected_tp(cfg_dict))
    for row in br.read_new_rows():
        clk.t = int(row["ms"]) + REPLAY_LAG_MS
        br.handle_row(row)
        br._poll_ports()
        br.backstops()
        br._poll_ports()
    br._save()
    orders = sorted((w["side"], str(w["order_id"])) for w in port.would_send)
    want = decisions_from_log(cfg.root, cfg.bot_id, followed_exit_reasons(expected_tp(cfg_dict)))
    md5 = lambda x: hashlib.md5(canon(x).encode()).hexdigest()  # noqa: E731
    refused = {k: v for k, v in br.state.counts.items() if k.startswith("refused_")}
    return {"orders": len(orders), "decisions": len(want), "md5_orders": md5(orders), "md5_decisions": md5(want), "refused": refused,
            "not_followed": dict(br.fs["exits_not_followed"]), "latches": dict(br.state.latches),
            "equal": md5(orders) == md5(want) and not refused and not br.state.latches}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FLOOR dry executor. DRY ONLY: live is not built.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("replay-check", help="dry replay of a closed decision log; prints the equivalence record")
    r.add_argument("--config", required=True)
    t = sub.add_parser("run", help="tail the decision log and log the orders it would send (dry)")
    t.add_argument("--config", required=True)
    t.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    raw = json.loads(Path(a.config).read_text())
    if a.cmd == "replay-check":
        res = replay_equivalence(raw)
        print(canon(res))
        return 0 if res["equal"] else 1
    cfg = make_config(raw)
    br = FloorBridge(cfg, B.DryPort(cfg.dry_balance_lamports), expected_q_star=expected_q_star(raw), expected_tp=expected_tp(raw))
    print(f"floor_arena_executor mode=dry bot={cfg.bot_id} stake={cfg.stake_lamports}", flush=True)
    br.run(once=a.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
