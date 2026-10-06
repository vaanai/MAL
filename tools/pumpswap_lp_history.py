"""PumpSwap LP history and the V0 law (DEC-016 Amendment 5 section 7).

A pool's V0 (`v_base` = V + A + B) changes only at a liquidity Deposit or Withdraw, to
`floor(V0 * S_after / S_before)` where S is the LP mint supply. This module:

  - decodes `DepositEvent` / `WithdrawEvent` from `Program data:` log lines,
  - lists a pool set's LP events over a time range (`fetch_lp_history`, RPC injected),
  - replays V0 forward (`replay_forward`, exact ints) and backward (`v0_before`, nearest, <= 1 lamport per event),
  - decides whether two V0 reads are explained by the events between them (`consistent`).

No network call is made unless the caller passes a real `rpc`; the RPC URL is never printed or stored.
`rpc(method, params) -> result` is `tools.pumpswap_simulate.Rpc`'s call shape.
"""

from __future__ import annotations

import base64
import itertools
import time
from typing import Any, Callable, Sequence

PROGRAM_ID = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
DEPOSIT_DISC = bytes([120, 248, 61, 83, 31, 142, 107, 144])
WITHDRAW_DISC = bytes([22, 9, 133, 26, 160, 44, 71, 192])
EVENT_LEN = 8 + 8 + 10 * 8 + 5 * 32  # disc, i64 ts, ten u64, five pubkeys
PROGRAM_DATA = "Program data: "
SIG_PAGE = 1000
BATCH = 100
MAX_PAGES_DEFAULT = 200
MAX_AMBIGUOUS = 10  # 2^10 placements at most; more is unresolved
_U64 = ("lp_token_amount", "max_or_min_base", "max_or_min_quote", "user_base_token_reserves", "user_quote_token_reserves", "pool_base_token_reserves", "pool_quote_token_reserves", "base_amount", "quote_amount", "lp_mint_supply")
_KEYS = ("pool", "user", "user_base_token_account", "user_quote_token_account", "user_pool_token_account")


def _b58(raw: bytes) -> str:
    from solders.pubkey import Pubkey

    return str(Pubkey.from_bytes(raw))


def decode_event(data: bytes) -> dict[str, Any] | None:
    """One Deposit/Withdraw event from raw bytes (discriminator first), or None for anything else."""
    if len(data) < EVENT_LEN:
        return None
    disc = data[:8]
    if disc == DEPOSIT_DISC:
        kind = "deposit"
    elif disc == WITHDRAW_DISC:
        kind = "withdraw"
    else:
        return None
    out: dict[str, Any] = {"kind": kind, "timestamp": int.from_bytes(data[8:16], "little", signed=True)}
    o = 16
    for name in _U64:
        out[name] = int.from_bytes(data[o : o + 8], "little")
        o += 8
    for name in _KEYS:
        out[name] = _b58(data[o : o + 32])
        o += 32
    return out


def events_from_logs(logs: Sequence[str], pool: str) -> list[dict[str, Any]]:
    """Decoded events of `pool` from `Program data: <base64>` lines, in log order, with s_before and the signed LP delta."""
    out: list[dict[str, Any]] = []
    for line in logs:
        if not isinstance(line, str) or not line.startswith(PROGRAM_DATA):
            continue
        try:
            raw = base64.b64decode(line[len(PROGRAM_DATA) :].strip(), validate=False)
        except ValueError:
            continue
        ev = decode_event(raw)
        if ev is None or ev["pool"] != pool:
            continue
        lp = ev["lp_token_amount"]
        out.append({"kind": ev["kind"], "s_before": ev["lp_mint_supply"], "lp_delta": lp if ev["kind"] == "deposit" else -lp, "event_ts": ev["timestamp"]})
    return out


def replay_forward(v0: int, events: Sequence[dict[str, Any]]) -> int:
    """floor(v0 * (s + d) / s) per event, in order. Exact integers. s_before == 0 raises ValueError."""
    v = v0
    for e in events:
        s, d = e["s_before"], e["lp_delta"]
        if s <= 0:
            raise ValueError("s_before must be positive")
        v = v * (s + d) // s
    return v


def v0_before(v0_after: int, events: Sequence[dict[str, Any]]) -> int:
    """Inverse of replay_forward, events given in forward order. Each step is v * s / (s + d) rounded to
    nearest (half up), so the result is within 1 lamport per event of a V0 that replays to v0_after
    (floor loses < 1 lamport per event forward; the inverse is not unique, and is exact only to that)."""
    v = v0_after
    for e in reversed(list(events)):
        s, d = e["s_before"], e["lp_delta"]
        t = s + d
        if s <= 0 or t <= 0:
            raise ValueError("supply must stay positive")
        v = (2 * v * s + t) // (2 * t)
    return v


def _order(events: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(events, key=lambda e: (e.get("slot", 0), e.get("idx", 0)))


def consistent(v0_a: int, v0_b: int, events_between: Sequence[dict[str, Any]], events_ambiguous: Sequence[dict[str, Any]]) -> bool | None:
    """True iff v0_a == v0_b, or some placement of the ambiguous events (each before the first read, or after it) lets
    the definite events plus the chosen ambiguous ones, replayed in order from v0_a, reproduce v0_b within 1 lamport
    per applied event. False when no placement does. None (unresolved) when more than MAX_AMBIGUOUS are ambiguous.
    Events carry slot and (optionally) idx for ordering."""
    if v0_a == v0_b:
        return True
    k = len(events_ambiguous)
    if k > MAX_AMBIGUOUS:
        return None
    for mask in itertools.product((False, True), repeat=k):
        chosen = [e for e, m in zip(events_ambiguous, mask) if m]
        applied = _order(list(events_between) + chosen)
        if not applied:
            continue
        try:
            got = replay_forward(v0_a, applied)
        except ValueError:
            continue
        if abs(got - v0_b) <= len(applied):
            return True
    return False


class _Limiter:
    def __init__(self, rps: float, sleep: Callable[[float], None], clock: Callable[[], float]):
        self.gap = 1.0 / rps
        self.sleep, self.clock = sleep, clock
        self.last = -1e18
        self.calls = 0

    def wait(self) -> None:
        now = self.clock()
        if now - self.last < self.gap:
            self.sleep(self.gap - (now - self.last))
        self.last = self.clock()
        self.calls += 1


def fetch_lp_history(rpc: Callable[[str, list], Any], pools: Sequence[str], t_from_unix: int, t_to_unix: int, *, rps: float = 5.0, max_pages: int = MAX_PAGES_DEFAULT, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> tuple[dict[str, dict[str, Any]], int]:
    """({pool: {"lp_mint", "events", "resolved", "reason"}}, n_rpc_calls).

    Events are those of the pool with t_from <= block_time <= t_to, chronological (slot, then position in the
    signature listing, which is block order). A pool is unresolved (reason set) if its account is missing or
    unparseable, the signature paging stopped (max_pages, or an RPC error) before reaching t_from, any transaction
    in range could not be fetched, or the events do not chain (s_before + lp_delta == next s_before)."""
    from tools import pumpswap_tx as tx

    if rps > 5.0:
        raise ValueError("rps must be <= 5: walkers share Helius")
    lim = _Limiter(rps, sleep, clock)
    uniq = sorted({p for p in pools if p})
    res: dict[str, dict[str, Any]] = {}
    mints: dict[str, str] = {}
    for i in range(0, len(uniq), BATCH):
        chunk = uniq[i : i + BATCH]
        lim.wait()
        try:
            vals = rpc("getMultipleAccounts", [chunk, {"encoding": "base64"}])["value"]
        except BaseException as exc:  # noqa: BLE001 -- Rpc raises SystemExit; the message may hold a URL, keep the type only
            if isinstance(exc, KeyboardInterrupt):
                raise
            for p in chunk:
                res[p] = {"lp_mint": None, "events": [], "resolved": False, "reason": f"accounts_fetch_failed:{type(exc).__name__}"}
            continue
        for p, acc in zip(chunk, vals):
            if not acc:
                res[p] = {"lp_mint": None, "events": [], "resolved": False, "reason": "account_missing"}
                continue
            try:
                mints[p] = str(tx.parse_pool_account(base64.b64decode(acc["data"][0]))["lp_mint"])
            except (ValueError, KeyError, TypeError, IndexError):
                res[p] = {"lp_mint": None, "events": [], "resolved": False, "reason": "account_unparseable"}
    for p in uniq:
        if p in res:
            continue
        res[p] = _history_one(rpc, lim, p, mints[p], t_from_unix, t_to_unix, max_pages)
    return {p: res[p] for p in uniq}, lim.calls


def _history_one(rpc: Callable[[str, list], Any], lim: _Limiter, pool: str, lp_mint: str, t_from: int, t_to: int, max_pages: int) -> dict[str, Any]:
    ent: dict[str, Any] = {"lp_mint": lp_mint, "events": [], "resolved": True, "reason": None}

    def fail(reason: str) -> dict[str, Any]:
        ent["resolved"], ent["reason"] = False, reason
        return ent

    sigs: list[dict[str, Any]] = []  # newest first, as listed
    before: str | None = None
    reached = False
    for _ in range(max_pages):
        params: dict[str, Any] = {"limit": SIG_PAGE}
        if before:
            params["before"] = before
        lim.wait()
        try:
            page = rpc("getSignaturesForAddress", [lp_mint, params])
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, KeyboardInterrupt):
                raise
            return fail(f"signatures_fetch_failed:{type(exc).__name__}")
        if not isinstance(page, list):
            return fail("signatures_bad_response")
        sigs.extend(page)
        if len(page) < SIG_PAGE:
            reached = True  # the listing ended: all of the mint's history is in hand
            break
        oldest = page[-1].get("blockTime")
        if oldest is not None and oldest < t_from:
            reached = True
            break
        before = page[-1].get("signature")
        if not before:
            return fail("signatures_bad_response")
    if not reached:
        return fail("paging_did_not_reach_t_from")
    events: list[dict[str, Any]] = []
    n = len(sigs)
    for pos, s in enumerate(sigs):  # pos 0 is the newest
        if s.get("err") is not None:
            continue
        bt = s.get("blockTime")
        if bt is not None and (bt < t_from or bt > t_to):
            continue
        sig = s.get("signature")
        lim.wait()
        try:
            tr = rpc("getTransaction", [sig, {"encoding": "json", "maxSupportedTransactionVersion": 1}])
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, KeyboardInterrupt):
                raise
            return fail(f"tx_fetch_failed:{type(exc).__name__}")
        if not isinstance(tr, dict):
            return fail("tx_fetch_failed:null")
        if (tr.get("meta") or {}).get("err") is not None:
            continue
        block_time = tr.get("blockTime", bt)
        if block_time is not None and (block_time < t_from or block_time > t_to):
            continue
        logs = (tr.get("meta") or {}).get("logMessages") or []
        for j, ev in enumerate(events_from_logs(logs, pool)):
            events.append({"slot": tr.get("slot", s.get("slot")), "block_time": block_time, "sig": sig, "kind": ev["kind"], "s_before": ev["s_before"], "lp_delta": ev["lp_delta"], "_k": (n - pos, j)})
    events.sort(key=lambda e: (e["slot"] if e["slot"] is not None else 0, e["_k"]))
    for i, e in enumerate(events):
        e.pop("_k")
        e["idx"] = i
    for a, b in zip(events, events[1:]):
        if a["s_before"] + a["lp_delta"] != b["s_before"]:
            ent["events"] = events
            return fail("supply_chain_break")
    ent["events"] = events
    return ent
