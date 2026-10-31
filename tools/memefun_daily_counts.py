"""meme.fun daily counts (lane P45, P36 proposal R2). Counts only: no price, no P&L, no edge claim.

For one UTC day, from the tape written by tools/memefun_watch.py (<tape>/memefun/tape-YYYY-MM-DDTHH.jsonl):
  * creates/day, buys/day, sells/day, swap-instruction rows, claims, failed-tx rows, undecoded events, distinct mints/creators/traders;
  * coverage (hours with any row, gap rows, start/stop rows) so a short day is flagged `complete: false`;
  * a seeded sample of N launches (default 20) measured the P36 way through PUBLIC getTransaction (hard budget, default 190 calls/day):
      for each launch (create at slot s0, ts t0): list the bonding curve's signatures between the launch tx and a signature just past the window
      (the cursor is the first tape signature at slot > s0 + slots_per_30s, so no getBlock is needed), keep successful txs with blockTime - t0 <= 30 s,
      fetch a seeded sample of those in slots s0..s0+1 (<= EARLY_CAP) and of the later ones (<= LATE_CAP), each weighted n / n_sampled,
      read buys from the TradeEvent in the logs (buyer = the event's `user`, amount = sol_amount in lamports, exact; P36 had to infer both),
      and report the share of the first-30 s buy volume taken in slots 0-1 excluding the launch signer and the creator (pooled), the number of other
      wallets that bought in slots 0-1, the failed txs in the window and the first other buy slot. The launch tx's own buys are never "other".
  * a free cross-check from the tape alone (no RPC): the same share from the recorded trade rows (needs no gap around the launch).
Slot 0 is the launch slot. t0 is the CreateEvent timestamp (the cluster clock; blockTime differs by <= 1 s).

Rules (P36 R2, fixed text): kill if creates/day < 50 on 5 of the 7 days, or the pooled slot-0-1 share >= 30%.
Promote to a decoder/pricing lane only at >= 200 creates/day AND share < 10%. `evaluate_rules` reads the daily files and applies them to COMPLETE days only.
Reading used for "creates/day >= 200": every complete day in the window. The promote branch needs 7 complete days; before that the status is `open`.

Seal notes: reads only this lane's own tape. Public RPC only (api.mainnet-beta), never a keyed url. Budget file per day: <out>/daily/ledger-<day>.jsonl.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Iterator

from tools import memefun_watch as mw

DEFAULT_ROOT = "/data/mal/memefun-watch"
RPC_URL = "https://api.mainnet-beta.solana.com"
WINDOW_S = 30
EARLY_CAP = 5
LATE_CAP = 3
BUDGET = 190
SPACING_S = 1.0
DEFAULT_SLOT_S = 0.2119  # measured 2026-10-26 (getRecentPerformanceSamples); refreshed from RPC when available
KILL_CREATES = 50
KILL_DAYS = 5
KILL_SHARE = 0.30
PROMOTE_CREATES = 200
PROMOTE_SHARE = 0.10
RULE_DAYS = 7


# ---------------------------------------------------------------------------------------------------------------- tape reading
def day_files(root: Path, day: str) -> list[Path]:
    return [root / mw.VENUE / f"tape-{day}T{h:02d}.jsonl" for h in range(24)]


def read_day(root: Path, day: str) -> Iterator[dict[str, Any]]:
    for p in day_files(root, day):
        if not p.exists():
            continue
        with open(p, encoding="utf-8") as fh:
            for ln in fh:
                try:
                    yield json.loads(ln)
                except ValueError:
                    continue  # a torn line


def count_rows(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    c: Counter = Counter()
    mints_created, creators, traders, mints_traded = set(), set(), set(), set()
    hours, gaps, starts, stops = set(), 0, 0, 0
    seen: set[tuple[str, int]] = set()
    for r in rows:
        t = r.get("type")
        if t in ("start", "stop", "gap", "gap_end", "hb", "disk_pause", "disk_resume"):
            if t == "gap":
                gaps += 1
            elif t == "start":
                starts += 1
            elif t == "stop":
                stops += 1
            c[t] += 1
            hours.add(int(r["t_recv_ms"] // 3_600_000))
            continue
        key = (r.get("signature"), r.get("event_index"), t)
        if key in seen:
            c["duplicate_rows_dropped"] += 1
            continue
        seen.add(key)
        hours.add(int(r["t_recv_ms"] // 3_600_000))
        c["rows"] += 1
        if r.get("failed"):
            c["failed_rows"] += 1
        if t == "create":
            c["creates"] += 1
            mints_created.add(r["mint"])
            creators.add(r["creator"])
        elif t == "trade":
            c["buys" if r["side"] == "buy" else "sells"] += 1
            traders.add(r["trader"])
            mints_traded.add(r["mint"])
        elif t == "claim":
            c["claims"] += 1
        elif t == "event_undecoded":
            c["event_undecoded"] += 1
        elif t == "ix":
            c["ix_rows"] += 1
            names = r.get("ix") or []
            if "Swap" in names:
                c["swap_ix_rows"] += 1
            if r.get("failed") and "Buy" in names:
                c["failed_buy_ix_rows"] += 1
            if r.get("failed") and "Sell" in names:
                c["failed_sell_ix_rows"] += 1
    out = {k: c.get(k, 0) for k in ("creates", "buys", "sells", "claims", "swap_ix_rows", "ix_rows", "failed_rows", "failed_buy_ix_rows",
                                   "failed_sell_ix_rows", "event_undecoded", "duplicate_rows_dropped", "rows")}
    out.update({"distinct_mints_created": len(mints_created), "distinct_creators": len(creators), "distinct_traders": len(traders),
                "distinct_mints_traded": len(mints_traded), "gap_rows": gaps, "start_rows": starts, "stop_rows": stops,
                "hours_with_rows": len(hours)})
    return out


def coverage(day: str, counts: dict[str, Any], now: datetime) -> dict[str, Any]:
    d0 = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    over = now >= d0 + timedelta(days=1)
    full_hours = counts["hours_with_rows"] >= 24
    return {"day_over": over, "hours_with_rows": counts["hours_with_rows"], "gap_rows": counts["gap_rows"],
            "complete": bool(over and full_hours and counts["gap_rows"] == 0),
            "note": "complete = day over, a row in each of the 24 hours, and no gap row"}


# --------------------------------------------------------------------------------------------------------------------- RPC
class Rpc:
    """Public getTransaction/getSignaturesForAddress client with a hard per-day budget and a call ledger (every attempt counts)."""

    def __init__(self, ledger: Path, budget: int = BUDGET, url: str = RPC_URL, spacing_s: float = SPACING_S,
                 opener: Callable[..., Any] | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        if "helius" in url.lower() or "?" in url or "@" in url:
            raise SystemExit("public unkeyed RPC only")
        self.ledger, self.budget, self.url, self.spacing_s = ledger, budget, url, spacing_s
        self._open = opener or (lambda req, timeout: urllib.request.urlopen(req, timeout=timeout))
        self._sleep = sleep
        self._last = 0.0
        self.used = self._count()
        ledger.parent.mkdir(parents=True, exist_ok=True)

    def _count(self) -> int:
        try:
            with open(self.ledger) as f:
                return sum(1 for _ in f)
        except FileNotFoundError:
            return 0

    @property
    def remaining(self) -> int:
        return self.budget - self.used

    def call(self, method: str, params: list, tag: str = "") -> Any:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        err: Any = None
        for attempt in range(4):
            if self.used >= self.budget:
                raise RuntimeError(f"RPC budget {self.budget} reached")
            wait = self.spacing_s - (time.time() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = time.time()
            self.used += 1
            with open(self.ledger, "a") as f:
                f.write(json.dumps({"t": int(time.time()), "m": method, "tag": tag, "attempt": attempt}) + "\n")
            try:
                req = urllib.request.Request(self.url, data=body, headers={"content-type": "application/json", "user-agent": "mal-p45"})
                with self._open(req, 40) as r:
                    d = json.load(r)
                if "error" in d:
                    err = d["error"]
                    if d["error"].get("code") in (429, -32429) or "rate" in str(d["error"]).lower():
                        self._sleep(5 * (attempt + 1))
                        continue
                    return {"error": d["error"]}
                return d.get("result")
            except Exception as e:  # noqa: BLE001 - HTTP 429 arrives as an exception
                err = str(e)[:200]
                self._sleep(5 * (attempt + 1))
        return {"error": err}

    def get_tx(self, sig: str, tag: str = "") -> Any:
        return self.call("getTransaction", [sig, {"encoding": "json", "maxSupportedTransactionVersion": 1, "commitment": "confirmed"}], tag)

    def sigs(self, addr: str, tag: str = "", **opt: Any) -> Any:
        o = {"limit": 1000, "commitment": "confirmed"}
        o.update(opt)
        return self.call("getSignaturesForAddress", [addr, o], tag)


# ------------------------------------------------------------------------------------------------------------------ sampling
def tx_notice(tx: dict[str, Any], sig: str) -> SimpleNamespace:
    return SimpleNamespace(slot=tx["slot"], signature=sig, failed=tx["meta"]["err"] is not None, logs=tuple(tx["meta"]["logMessages"] or ()),
                           t_recv_ms=(tx.get("blockTime") or 0) * 1000)


def buys_in_tx(tx: dict[str, Any], sig: str, mint: str) -> list[dict[str, Any]]:
    if tx["meta"]["err"] is not None:
        return []
    return [{"trader": r["trader"], "amt": r["sol_amount"]} for r in mw.rows_for_notice(tx_notice(tx, sig))
            if r["type"] == "trade" and r["side"] == "buy" and r["mint"] == mint]


def pick_launches(creates: list[dict[str, Any]], n: int, seed: int) -> list[dict[str, Any]]:
    return random.Random(seed).sample(creates, min(n, len(creates)))


def cursor_after(slot_index: list[tuple[int, str]], slot: int) -> str | None:
    """First recorded signature at a slot strictly after `slot` (slot_index sorted by slot)."""
    for s, sig in slot_index:
        if s > slot:
            return sig
    return None


def summarize_buys(buys: list[dict[str, Any]], excl: set[str]) -> dict[str, Any]:
    """buys: {slot_off, amt, trader, w}. Pure."""
    tot_x = sum(b["amt"] * b["w"] for b in buys if b["trader"] not in excl)
    e01_x = sum(b["amt"] * b["w"] for b in buys if b["slot_off"] <= 1 and b["trader"] not in excl)
    tot = sum(b["amt"] * b["w"] for b in buys)
    e01 = sum(b["amt"] * b["w"] for b in buys if b["slot_off"] <= 1)
    w01_x = {b["trader"] for b in buys if b["slot_off"] <= 1 and b["trader"] not in excl}
    return {"buy_amt_30s": tot, "buy_amt_slot01": e01, "buy_amt_30s_ex_launcher": tot_x, "buy_amt_slot01_ex_launcher": e01_x,
            "share_slot01": (e01 / tot) if tot else None, "share_slot01_ex_launcher": (e01_x / tot_x) if tot_x else None,
            "wallets_slot01_ex_launcher": len(w01_x),
            "first_other_buy_slot": min((b["slot_off"] for b in buys if b["trader"] not in excl), default=None)}


def tape_crosscheck(trades_by_mint: dict[str, list[dict[str, Any]]], launch: dict[str, Any]) -> dict[str, Any]:
    """The same measure from the recorded trade rows alone (no RPC)."""
    excl = {launch["creator"]}
    buys = [{"slot_off": r["slot"] - launch["slot"], "amt": r["sol_amount"], "trader": r["trader"], "w": 1.0}
            for r in trades_by_mint.get(launch["mint"], []) if r["side"] == "buy" and not r.get("failed")
            and r["slot"] >= launch["slot"] and r["ts"] - launch["ts"] <= WINDOW_S]
    out = summarize_buys(buys, excl)
    out["n_buys"] = len(buys)
    return out


def sample_launch(rpc: Rpc, launch: dict[str, Any], slot_index: list[tuple[int, str]], spw: int, rng: random.Random, tx_allow: int) -> dict[str, Any]:
    info: dict[str, Any] = {"mint": launch["mint"], "curve": launch["bonding_curve"], "launch_sig": launch["signature"], "slot0": launch["slot"],
                            "t0": launch["ts"], "creator": launch["creator"]}
    cur = cursor_after(slot_index, launch["slot"] + spw)
    if cur is None:
        return {**info, "error": "no recorded signature past the window (cursor)"}
    lst = rpc.sigs(launch["bonding_curve"], "window sigs", before=cur, until=launch["signature"])
    if not isinstance(lst, list):
        return {**info, "error": f"sigs failed {str(lst)[:120]}"}
    if len(lst) >= 1000 and rpc.remaining > 20:
        more = rpc.sigs(launch["bonding_curve"], "window sigs p1", before=lst[-1]["signature"], until=launch["signature"])
        if isinstance(more, list):
            lst += more
    win = [x for x in lst if x["slot"] >= launch["slot"] and x.get("blockTime") is not None and x["blockTime"] - launch["ts"] <= WINDOW_S]
    ok = [x for x in win if x["err"] is None]
    early = [x for x in ok if x["slot"] <= launch["slot"] + 1]
    late = [x for x in ok if x["slot"] > launch["slot"] + 1]
    e_cap = min(EARLY_CAP, tx_allow)
    l_cap = min(LATE_CAP, max(0, tx_allow - min(len(early), e_cap)))
    early_s = early if len(early) <= e_cap else rng.sample(early, e_cap)
    late_s = late if len(late) <= l_cap else rng.sample(late, l_cap)
    w_e = (len(early) / len(early_s)) if early_s else 0.0
    w_l = (len(late) / len(late_s)) if late_s else 0.0
    info.update({"sigs_listed": len(lst), "sigs_truncated": len(lst) >= 1000, "n_ok_window": len(ok), "n_failed_window": len(win) - len(ok),
                 "n_failed_slot01": sum(1 for x in win if x["err"] is not None and x["slot"] <= launch["slot"] + 1),
                 "n_early_ok": len(early), "n_early_fetched": len(early_s), "n_late_ok": len(late), "n_late_fetched": len(late_s)})
    buys: list[dict[str, Any]] = []
    for x, w in [(x, w_e) for x in early_s] + [(x, w_l) for x in late_s]:
        t = rpc.get_tx(x["signature"], "window tx")
        if not isinstance(t, dict) or "meta" not in t:
            info["fetch_fail"] = info.get("fetch_fail", 0) + 1
            continue
        for b in buys_in_tx(t, x["signature"], launch["mint"]):
            buys.append({**b, "slot_off": t["slot"] - launch["slot"], "w": w})
    info.update(summarize_buys(buys, {launch["creator"]}))
    info["n_buys_fetched"] = len(buys)
    return info


def pool_samples(rows: list[dict[str, Any]], key: str = "") -> dict[str, Any]:
    ok = [r for r in rows if "error" not in r]
    p = key
    e = sum(r[f"buy_amt_slot01_ex_launcher{p}"] for r in ok)
    t = sum(r[f"buy_amt_30s_ex_launcher{p}"] for r in ok)
    return {"n_launches": len(rows), "n_measured": len(ok), "pooled_share_slot01_ex_launcher": (e / t) if t else None,
            "launches_with_other_buyer_slot01": sum(1 for r in ok if r["wallets_slot01_ex_launcher"] > 0),
            "launches_with_any_buy": sum(1 for r in ok if r["buy_amt_30s_ex_launcher"] > 0),
            "failed_txs_in_windows": sum(r.get("n_failed_window", 0) for r in ok),
            "ok_txs_in_windows": sum(r.get("n_ok_window", 0) for r in ok)}


# ------------------------------------------------------------------------------------------------------------------ rules
def evaluate_rules(days: list[dict[str, Any]]) -> dict[str, Any]:
    """days: the per-day summaries (daily file bodies). Applies the fixed P36 R2 kill / promote text to COMPLETE days only."""
    comp = [d for d in days if d["coverage"]["complete"]]
    low = [d["day"] for d in comp if d["counts"]["creates"] < KILL_CREATES]
    num = den = 0.0
    for d in days:
        s = d.get("sample", {}).get("rpc_pooled")
        if s and s.get("pooled_share_slot01_ex_launcher") is not None:
            num += d["sample"]["rpc_sums"]["slot01"]
            den += d["sample"]["rpc_sums"]["total"]
    share = (num / den) if den else None
    kill_c = len(low) >= KILL_DAYS
    kill_s = share is not None and share >= KILL_SHARE
    promote = (len(comp) >= RULE_DAYS and all(d["counts"]["creates"] >= PROMOTE_CREATES for d in comp[:RULE_DAYS])
               and share is not None and share < PROMOTE_SHARE)
    status = "kill" if (kill_c or kill_s) else "promote" if promote else "open"
    return {"status": status, "complete_days": len(comp), "days_below_50": low, "pooled_share_all_days": share,
            "kill_creates_rule": kill_c, "kill_share_rule": kill_s, "promote_rule": promote,
            "reading": "kill: >=5 complete days with <50 creates, or pooled slot-0-1 share >= 0.30; promote: 7 complete days all >=200 creates and share < 0.10; else open"}


# --------------------------------------------------------------------------------------------------------------------- main
def run_day(root: Path, day: str, n: int, seed: int, budget: int, do_rpc: bool, rpc: Rpc | None = None, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    rows = list(read_day(root, day))
    counts = count_rows(rows)
    out: dict[str, Any] = {"day": day, "generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "counts": counts, "coverage": coverage(day, counts, now),
                           "decoder": "IDL events (CreateEvent, TradeEvent, ClaimEvent); docs/research/p45-memefun-watch/idl.json"}
    creates = [r for r in rows if r.get("type") == "create" and not r.get("failed") and r.get("signature")]
    uniq = {r["mint"]: r for r in creates}
    creates = sorted(uniq.values(), key=lambda r: (r["slot"], r["signature"]))
    trades: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r.get("type") == "trade":
            trades.setdefault(r["mint"], []).append(r)
    slot_index = sorted({(r["slot"], r["signature"]) for r in rows if r.get("signature") and not r.get("failed")})
    launches = pick_launches(creates, n, seed)
    sample: dict[str, Any] = {"seed": seed, "n_requested": n, "n_picked": len(launches), "creates_available": len(creates)}
    sample["tape_rows"] = [{"mint": l["mint"], "slot0": l["slot"], **tape_crosscheck(trades, l)} for l in launches]
    sample["tape_pooled"] = {
        "pooled_share_slot01_ex_launcher": (lambda e, t: (e / t) if t else None)(
            sum(r["buy_amt_slot01_ex_launcher"] for r in sample["tape_rows"]), sum(r["buy_amt_30s_ex_launcher"] for r in sample["tape_rows"])),
        "launches_with_other_buyer_slot01": sum(1 for r in sample["tape_rows"] if r["wallets_slot01_ex_launcher"] > 0),
        "launches_with_any_buy": sum(1 for r in sample["tape_rows"] if r["buy_amt_30s_ex_launcher"] > 0),
        "note": "from the recorded trade rows; undercounts if the recorder had a gap near the launch"}
    if do_rpc and launches:
        rpc = rpc or Rpc(root / "daily" / f"ledger-{day}.jsonl", budget=budget)
        slot_s = DEFAULT_SLOT_S
        perf = rpc.call("getRecentPerformanceSamples", [5], "slot rate")
        if isinstance(perf, list) and perf:
            tot_slots = sum(p["numSlots"] for p in perf)
            if tot_slots:
                slot_s = sum(p["samplePeriodSecs"] for p in perf) / tot_slots
        spw = int(WINDOW_S / slot_s) + 2
        rng = random.Random(seed)
        res: list[dict[str, Any]] = []
        for i, l in enumerate(launches):
            left = len(launches) - i
            allow = (rpc.remaining - 2 * left) // left  # keep ~2 listing calls per remaining launch
            if allow < 1:
                res.append({"mint": l["mint"], "error": "budget exhausted before this launch"})
                continue
            try:
                res.append(sample_launch(rpc, l, slot_index, spw, rng, allow))
            except RuntimeError as e:
                res.append({"mint": l["mint"], "error": str(e)})
                break
        sample["slot_s"], sample["slots_per_30s"] = slot_s, spw
        sample["rpc_rows"] = res
        sample["rpc_pooled"] = pool_samples(res)
        sample["rpc_sums"] = {"slot01": sum(r["buy_amt_slot01_ex_launcher"] for r in res if "error" not in r),
                              "total": sum(r["buy_amt_30s_ex_launcher"] for r in res if "error" not in r)}
        sample["rpc_calls_used"], sample["rpc_budget"] = rpc.used, rpc.budget
    out["sample"] = sample
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="meme.fun daily counts + 20-launch first-30-s sample (counts only)")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--day", default=None, help="UTC day YYYY-MM-DD; default = yesterday")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--budget", type=int, default=BUDGET, help="public RPC calls for the day's sample (hard)")
    ap.add_argument("--no-rpc", action="store_true", help="counts and the tape cross-check only")
    ap.add_argument("--evaluate", action="store_true", help="also apply the fixed kill/promote text to the daily files")
    a = ap.parse_args(argv)
    root = Path(a.root)
    day = a.day or (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    seed = int(day.replace("-", ""))
    out = run_day(root, day, a.n, seed, a.budget, not a.no_rpc)
    d = root / "daily"
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".counts-{day}.tmp"
    tmp.write_text(json.dumps(out, indent=1, sort_keys=True, allow_nan=False) + "\n")
    os.replace(tmp, d / f"counts-{day}.json")
    c = out["counts"]
    print(json.dumps({"day": day, "creates": c["creates"], "buys": c["buys"], "sells": c["sells"], "claims": c["claims"], "failed_rows": c["failed_rows"],
                      "complete": out["coverage"]["complete"], "tape_pooled": out["sample"]["tape_pooled"],
                      "rpc_pooled": out["sample"].get("rpc_pooled"), "rpc_calls_used": out["sample"].get("rpc_calls_used")}, sort_keys=True))
    if a.evaluate:
        days = [json.loads(p.read_text()) for p in sorted(d.glob("counts-*.json"))]
        print(json.dumps({"rules": evaluate_rules(days)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
