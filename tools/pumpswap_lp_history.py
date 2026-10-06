"""PumpSwap LP history and the V0 law (DEC-016 Amendment 5 section 7).

A pool's V0 (`v_base` = V + A + B) changes only at a liquidity Deposit or Withdraw, to
`floor(V0 * S_after / S_before)` where S is the LP mint supply. This module:

  - decodes `DepositEvent` / `WithdrawEvent` from `Program data:` log lines,
  - lists a pool set's LP events from a start time to the chain tip (`fetch_lp_history`, RPC injected),
  - replays V0 forward (`replay_forward`, exact ints) and backward (`v0_before`: the smallest preimage),
  - decides whether two V0 reads are explained by the events between them (`consistent`),
  - lists every V0 a pool could have under the ambiguous placements (`possible_values`).

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
RETRY_PASSES = 3  # further passes, after the first, over pools unresolved by a failed tx fetch or failed signature paging
MAX_PASSES = 1 + RETRY_PASSES
TRANSIENT_PREFIXES = ("accounts_fetch_failed", "supply_read_failed", "signatures_fetch_failed", "signatures_bad_response", "tx_fetch_failed")  # failed RPC reads; chain, truncation and supply mismatches are never retried
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


EVENT_IX_TAG = bytes.fromhex("e445a52e51cb9a1d")  # self-CPI event instruction: tag + 8-byte event disc + payload
DEPOSIT_IX = bytes.fromhex("f223c68952e1f2b6")
WITHDRAW_IX = bytes.fromhex("b712469c946da122")
# deposit / withdraw instructions put the pool account FIRST (IDL: pool, global_config, user, ...). If an invocation has no
# readable accounts[0], tx_events falls back to counting every deposit/withdraw invocation in the tx.


def _account_keys(tr: dict[str, Any]) -> list[str]:
    msg = (tr.get("transaction") or {}).get("message") or {}
    keys = [k for k in (msg.get("accountKeys") or []) if isinstance(k, str)]
    la = (tr.get("meta") or {}).get("loadedAddresses") or {}
    return keys + [k for k in (la.get("writable") or []) if isinstance(k, str)] + [k for k in (la.get("readonly") or []) if isinstance(k, str)]


def _all_instructions(tr: dict[str, Any]) -> list[dict[str, Any]]:
    """Outer instructions with their inner instructions after each, in execution order."""
    msg = (tr.get("transaction") or {}).get("message") or {}
    inner: dict[int, list] = {}
    for grp in (tr.get("meta") or {}).get("innerInstructions") or []:
        if isinstance(grp, dict) and isinstance(grp.get("index"), int):
            inner.setdefault(grp["index"], []).extend(grp.get("instructions") or [])
    out: list[dict[str, Any]] = []
    for i, ix in enumerate(msg.get("instructions") or []):
        out.append(ix)
        out.extend(inner.get(i, []))
    return out


def tx_events(tr: dict[str, Any], pool: str) -> tuple[list[dict[str, Any]], str | None]:
    """(events of `pool` in this transaction, error reason or None).

    Events come from `Program data:` logs and from PumpSwap self-CPI inner instructions (program PROGRAM_ID, data =
    EVENT_IX_TAG + event disc + payload; inner instructions are never truncated). Logs not truncated: the log events are
    used, and if self-CPI events exist they must be identical (else "event_source_mismatch"). Logs truncated: the self-CPI
    events are used, and they must be complete: the number of PumpSwap deposit / withdraw instruction invocations (outer or
    inner) on this pool must equal the decoded Deposit / Withdraw events of that kind, else "logs_truncated_unrecoverable".
    A truncated migration transaction with only create_pool resolves with no events."""
    from tools.exp003_rpc_backfill import _b58decode

    logs = (tr.get("meta") or {}).get("logMessages") or []
    truncated = any(isinstance(x, str) and "Log truncated" in x for x in logs)
    keys = _account_keys(tr)
    cpi_all: list[dict[str, Any]] = []
    n_dep = n_wd = 0
    n_dep_all = n_wd_all = 0
    pool_unknown = False
    for ix in _all_instructions(tr):
        pi = ix.get("programIdIndex")
        if not isinstance(pi, int) or pi >= len(keys) or keys[pi] != PROGRAM_ID:
            continue
        data = _b58decode(ix.get("data") or "") or b""
        if data[:8] == EVENT_IX_TAG:
            ev = decode_event(data[8:])
            if ev is not None:
                cpi_all.append(ev)
        elif data[:8] in (DEPOSIT_IX, WITHDRAW_IX):
            is_dep = data[:8] == DEPOSIT_IX
            n_dep_all += is_dep
            n_wd_all += not is_dep
            accts = ix.get("accounts") or []
            if not accts or not isinstance(accts[0], int) or accts[0] >= len(keys):
                pool_unknown = True
            elif keys[accts[0]] == pool:
                n_dep += is_dep
                n_wd += not is_dep

    def conv(ev: dict[str, Any]) -> dict[str, Any]:
        lp = ev["lp_token_amount"]
        return {"kind": ev["kind"], "s_before": ev["lp_mint_supply"], "lp_delta": lp if ev["kind"] == "deposit" else -lp, "event_ts": ev["timestamp"]}

    cpi = [conv(e) for e in cpi_all if e["pool"] == pool]
    key = lambda e: (e["kind"], e["s_before"], e["lp_delta"])  # noqa: E731
    if not truncated:
        evs = events_from_logs(logs, pool)
        if cpi and [key(e) for e in cpi] != [key(e) for e in evs]:
            return [], "event_source_mismatch"
        return evs, None
    if pool_unknown:  # cannot tell whose invocation it is: every deposit/withdraw in the tx must have a decoded event
        ok = len(cpi_all) and sum(e["kind"] == "deposit" for e in cpi_all) == n_dep_all and sum(e["kind"] == "withdraw" for e in cpi_all) == n_wd_all
        ok = ok or (n_dep_all + n_wd_all == 0)
    else:
        ok = sum(e["kind"] == "deposit" for e in cpi) == n_dep and sum(e["kind"] == "withdraw" for e in cpi) == n_wd
    if not ok:
        return [], "logs_truncated_unrecoverable"
    return cpi, None


def replay_forward(v0: int, events: Sequence[dict[str, Any]]) -> int:
    """floor(v0 * (s + d) / s) per event, in order. Exact integers. s_before <= 0 raises ValueError."""
    v = v0
    for e in events:
        s, d = e["s_before"], e["lp_delta"]
        if s <= 0:
            raise ValueError("s_before must be positive")
        v = v * (s + d) // s
    return v


def v0_before_interval(v0_after: int, events: Sequence[dict[str, Any]]) -> tuple[int, int] | None:
    """The exact set of integers v with replay_forward(v, events) == v0_after, as (smallest, largest), or None when no
    integer maps to v0_after. Each step is monotone, so its preimage of an interval is an interval:
    [ceil(lo*s/t), floor(((hi+1)*s - 1)/t)] with s = S_before, t = S_after. A step with t <= 0 raises ValueError."""
    lo = hi = v0_after
    for e in reversed(list(events)):
        s, t = e["s_before"], e["s_before"] + e["lp_delta"]
        if s <= 0 or t <= 0:
            raise ValueError("supply must stay positive")
        lo, hi = -((-lo * s) // t), ((hi + 1) * s - 1) // t
        if lo > hi:
            return None
    return lo, hi


def v0_before(v0_after: int, events: Sequence[dict[str, Any]]) -> int | None:
    """The SMALLEST integer V0_before with replay_forward(V0_before, events) == v0_after (events in forward order), or
    None (inconsistent: no integer replays to v0_after). The true value lies in v0_before_interval's (smallest, largest);
    the width is about S_before/S_after per event, so it is the smallest member, not an estimate, that is returned."""
    r = v0_before_interval(v0_after, events)
    return None if r is None else r[0]


def _order(events: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    # idx is the chronological position fetch_lp_history assigns: slot, then the signature listing's block order within a slot
    return sorted(events, key=lambda e: (e.get("slot", 0), e.get("idx", 0)))


def _placements(between: Sequence[dict[str, Any]], ambiguous: Sequence[dict[str, Any]]):
    for mask in itertools.product((False, True), repeat=len(ambiguous)):
        yield _order(list(between) + [e for e, m in zip(ambiguous, mask) if m])


def consistent(v0_a: int, v0_b: int, events_between: Sequence[dict[str, Any]], events_ambiguous: Sequence[dict[str, Any]]) -> bool | None:
    """True iff v0_a == v0_b, or ANY placement of the ambiguous events (each before the first read, or after it) lets
    the definite events plus the chosen ambiguous ones, replayed in order from v0_a, reproduce v0_b within 1 lamport
    per applied event. False when no placement does. None (unresolved) when more than MAX_AMBIGUOUS are ambiguous."""
    if v0_a == v0_b:
        return True
    if len(events_ambiguous) > MAX_AMBIGUOUS:
        return None
    for applied in _placements(events_between, events_ambiguous):
        if not applied:
            continue
        try:
            got = replay_forward(v0_a, applied)
        except ValueError:
            continue
        if abs(got - v0_b) <= len(applied):
            return True
    return False


def possible_values(v0: int, events_definite: Sequence[dict[str, Any]], events_ambiguous: Sequence[dict[str, Any]], direction: str = "forward") -> set[int] | None:
    """Every V0 reachable from `v0` under the placements of the ambiguous events: forward = replay_forward, backward =
    v0_before (smallest preimage; a placement with no preimage contributes nothing). None when too many are ambiguous.
    For pricing, a pool is resolved only if the set has one member; more than one means the placements disagree."""
    if len(events_ambiguous) > MAX_AMBIGUOUS:
        return None
    out: set[int] = set()
    for applied in _placements(events_definite, events_ambiguous):
        try:
            v = replay_forward(v0, applied) if direction == "forward" else v0_before(v0, applied)
        except ValueError:
            continue
        if v is not None:
            out.add(v)
    return out


def v0_at(slot: int, anchor_v0: int, anchor_slot_span: tuple[int, int], events: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """The V0 values the pool could have had at `slot`, given V0 = anchor_v0 read at some slot in anchor_slot_span = (lo, hi)
    (the fetch's context-slot range) and the pool's LP events. The anchor reflects the events before its read; the
    target reflects the events before `slot`. Both are chronological prefixes, so the answer is the anchor moved across the
    events between the two cuts: forward (replay_forward) if the target cut is later, backward (v0_before, smallest
    preimage; a cut with no preimage contributes nothing) if earlier. An event in [lo, hi] may be on either side of the
    anchor read; an event in `slot` itself may be on either side of the trade.

    Returns {"values": set, "anchor_ambiguous": bool, "target_ambiguous": bool} or None (more than MAX_AMBIGUOUS events
    ambiguous on one side). anchor_ambiguous: for some target placement, the anchor-span placements give different values
    (vbook: unresolved). target_ambiguous: for some anchor placement, the target-slot placements differ (vbook: worse-of).
    An empty `values` means no placement is consistent."""
    lo, hi = anchor_slot_span
    ev = _order(events)
    sl = [e.get("slot", 0) for e in ev]
    ca_min, ca_max = sum(1 for x in sl if x < lo), sum(1 for x in sl if x <= hi)
    ct_min, ct_max = sum(1 for x in sl if x < slot), sum(1 for x in sl if x <= slot)
    if ca_max - ca_min > MAX_AMBIGUOUS or ct_max - ct_min > MAX_AMBIGUOUS:
        return None
    grid: dict[tuple[int, int], int | None] = {}
    for ca in range(ca_min, ca_max + 1):
        for ct in range(ct_min, ct_max + 1):
            try:
                grid[(ca, ct)] = replay_forward(anchor_v0, ev[ca:ct]) if ct >= ca else v0_before(anchor_v0, ev[ct:ca])
            except ValueError:
                grid[(ca, ct)] = None
    values = {v for v in grid.values() if v is not None}
    anchor_amb = any(len({grid[(ca, ct)] for ca in range(ca_min, ca_max + 1)}) > 1 for ct in range(ct_min, ct_max + 1))
    target_amb = any(len({grid[(ca, ct)] for ct in range(ct_min, ct_max + 1)}) > 1 for ca in range(ca_min, ca_max + 1))
    return {"values": values, "anchor_ambiguous": anchor_amb, "target_ambiguous": target_amb}


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


def _is_transient(reason: str | None) -> bool:
    return bool(reason) and reason.startswith(TRANSIENT_PREFIXES)


def _read_accounts(rpc: Callable[[str, list], Any], lim: _Limiter, pools: Sequence[str]) -> dict[str, dict[str, Any]]:
    """pool -> {"lp_mint", "lp_supply", "slot"} or {"error": reason}."""
    from tools import pumpswap_tx as tx

    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(pools), BATCH):
        chunk = list(pools[i : i + BATCH])
        lim.wait()
        try:
            r = rpc("getMultipleAccounts", [chunk, {"encoding": "base64"}])
            vals = r["value"]
            slot = (r.get("context") or {}).get("slot")
        except BaseException as exc:  # noqa: BLE001 -- Rpc raises SystemExit; its message may hold a URL, keep the type only
            if isinstance(exc, KeyboardInterrupt):
                raise
            for p in chunk:
                out[p] = {"error": f"accounts_fetch_failed:{type(exc).__name__}"}
            continue
        for p, acc in zip(chunk, vals):
            if not acc:
                out[p] = {"error": "account_missing"}
                continue
            try:
                parsed = tx.parse_pool_account(base64.b64decode(acc["data"][0]))
                out[p] = {"lp_mint": str(parsed["lp_mint"]), "lp_supply": int(parsed["lp_supply"]), "slot": slot}
            except (ValueError, KeyError, TypeError, IndexError):
                out[p] = {"error": "account_unparseable"}
    return out


def fetch_lp_history(rpc: Callable[[str, list], Any], pools: Sequence[str], t_from_unix: int, t_to_unix: int, *, rps: float = 5.0, max_pages: int = MAX_PAGES_DEFAULT, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic, passes: int = MAX_PASSES, attempts: list[dict[str, Any]] | None = None) -> tuple[dict[str, dict[str, Any]], int]:
    """({pool: {"lp_mint", "events", "resolved", "reason", "lp_supply", "attempts"}}, n_rpc_calls).

    Events are those of the pool from t_from to the chain tip (events after t_to are kept: the end-supply check needs
    them), chronological: by slot, then by position in the signature listing (getTransaction gives no transaction
    index; getSignaturesForAddress lists a slot's transactions in block order), recorded as "idx".

    A pool is UNRESOLVED (reason recorded) if: its account is missing/unparseable; paging stopped before t_from;
    a transaction fetch failed; a transaction's logs are truncated and its self-CPI events do not account for every
    deposit/withdraw invocation on the pool ("logs_truncated_unrecoverable"), or its log and self-CPI events disagree
    ("event_source_mismatch") (see tx_events); the S sequence does not chain (S_after = s_before + lp_delta of each event must equal the next
    s_before); or the last S_after differs from the pool account's lp_supply, read again after the history (recorded as
    "lp_supply" with its context slot "supply_slot", for every resolved pool; with zero events there is nothing to chain).
    A creation transaction (no event for the pool) is ignored. Up to RETRY_PASSES further passes retry pools unresolved by
    a failed RPC read (account, supply, signature paging or transaction; never chain, truncation or supply mismatch); every pass is appended to `attempts` if given."""
    if rps > 5.0:
        raise ValueError("rps must be <= 5: walkers share Helius")
    lim = _Limiter(rps, sleep, clock)
    uniq = sorted({p for p in pools if p})
    res: dict[str, dict[str, Any]] = {}
    todo = list(uniq)
    log: dict[str, list[dict[str, Any]]] = {}
    for n in range(1, passes + 1):
        if not todo:
            break
        accts = _read_accounts(rpc, lim, todo)
        for p in todo:
            a = accts[p]
            if "error" in a:
                res[p] = {"lp_mint": None, "events": [], "resolved": False, "reason": a["error"], "lp_supply": None, "supply_slot": None}
            else:
                res[p] = _history_one(rpc, lim, p, a["lp_mint"], t_from_unix, max_pages)
                res[p]["lp_supply_first"] = a["lp_supply"]  # read BEFORE the signature listing
            res[p]["attempts"] = n
        ok_ids = [p for p in todo if res[p]["resolved"]]
        end = _read_accounts(rpc, lim, ok_ids) if ok_ids else {}  # the supply AFTER the history (every resolved pool, zero events included)
        for p in ok_ids:
            e, a = res[p], end[p]
            if "error" in a:  # an errored read is retried; a read that succeeded but shows no/odd account is not
                transient = a["error"].startswith("accounts_fetch_failed")
                e["resolved"], e["reason"] = False, ("supply_read_failed:" if transient else "supply_") + a["error"]
                continue
            e["lp_supply"], e["supply_slot"] = a["lp_supply"], a["slot"]  # the history is complete up to this slot
            if e["events"]:
                last = e["events"][-1]
                if last["s_before"] + last["lp_delta"] != a["lp_supply"]:
                    e["resolved"], e["reason"] = False, "last_supply_mismatch"
            elif e.get("lp_supply_first") != a["lp_supply"]:  # zero events: nothing may have moved between the first read and the end read
                e["resolved"], e["reason"] = False, "last_supply_mismatch"
        for p in todo:  # every attempt, per pool, with its pass and reason (earlier reasons are kept)
            log.setdefault(p, []).append({"pass": n, "resolved": res[p]["resolved"], "reason": res[p]["reason"]})
        if attempts is not None:
            attempts.append({"pass": n, "n_pools": len(todo), "n_resolved": sum(1 for p in todo if res[p]["resolved"])})
        todo = [p for p in todo if not res[p]["resolved"] and _is_transient(res[p]["reason"])]
    for p in uniq:
        res[p]["attempt_log"] = log.get(p, [])
    return {p: res[p] for p in uniq}, lim.calls


def _history_one(rpc: Callable[[str, list], Any], lim: _Limiter, pool: str, lp_mint: str, t_from: int, max_pages: int) -> dict[str, Any]:
    ent: dict[str, Any] = {"lp_mint": lp_mint, "events": [], "resolved": True, "reason": None, "lp_supply": None, "supply_slot": None}

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
        if bt is not None and bt < t_from:
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
        if block_time is not None and block_time < t_from:
            continue
        evs, why = tx_events(tr, pool)
        if why:
            return fail(why)
        # a transaction with no Deposit/Withdraw event for this pool (pool creation, SPL token logs) is ignored
        for j, ev in enumerate(evs):
            events.append({"slot": tr.get("slot", s.get("slot")), "block_time": block_time, "sig": sig, "kind": ev["kind"], "s_before": ev["s_before"], "lp_delta": ev["lp_delta"], "_k": (n - pos, j)})
    events.sort(key=lambda e: (e["slot"] if e["slot"] is not None else 0, e["_k"]))
    for i, e in enumerate(events):
        e.pop("_k")
        e["idx"] = i
    ent["events"] = events
    for a, b in zip(events, events[1:]):
        if a["s_before"] + a["lp_delta"] != b["s_before"]:
            return fail("supply_chain_break")
    return ent
