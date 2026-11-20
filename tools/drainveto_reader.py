"""DRAIN-VETO step 0: the pinned reader (QP-RULING-1120b Q1).

What it reads: pumpfun curve rows and PumpSwap rows of each trigger pool's mint, ONLY inside the trigger's own exploration block, from the pool's create hour to the FLOOR
decision instant. Fields kept per row: trader (crc32), side, token amount, slot, event_index, block time, venue/prog, mint. Nothing else is ever kept (no SOL or quote amount,
reserve, price, fee, creator, name). No hour before the block's first hour, none after the decision hour, none at or after 2026-10-01T00Z, none in the D2/D3 hours.
Rows after the decision cut (slot, event_index) are dropped before they are counted.

What it writes: counts and per-pool flags. No pool or mint or trader id, no price, no P&L, no exit outcome. The per-pool file is keyed by the ordinal of the trigger in the input
file only.

Modes:
  --schema-count  counts rows by venue and key name per block (names and counts only; runs first, per the ruling);
  (default)       the reader proper; needs --triggers.

Trigger input (JSONL, exactly these keys, any other key is refused): blk, mint, slot, event_index, block_time, Q, B, V, split (optional: D, C1 or C2).
Q, B, V are the decision-time post-buy state (PART1 S2 algebra, same units as token amounts). D = k/V - B with k = (Q+V)*B, i.e. D = Q*B/V: the token amount a holder must
add to the pool to drive the real quote reserve to 0. The trigger file is made by the caller from the allowed grid fields (pool, blk, tdec, Qe, Be, VMap V); the reader never
opens grid.pkl.

Holder balance: per crc32 trader, sum(buy token) - sum(sell token) over the curve and PumpSwap rows read; negative balances (tokens that arrived by transfer) count as 0.
No pool, burn or known-program exclusion is done (a crc32 id cannot be recognised); transfers in and out are invisible; crc32 ids can collide. Direction of the bias is unknown.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import subprocess
import sys
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from tools import arena_screen as AS

ALLOWED_ROW_FIELDS = ("trader", "side", "token_raw", "slot", "event_index", "tx_index", "block_time", "event_ts", "venue", "prog", "mint")
ALLOWED_CREATE_FIELDS = ("mint", "slot", "block_time", "event_ts")
TRIGGER_KEYS = ("blk", "mint", "slot", "event_index", "block_time", "Q", "B", "V", "split")
SPLITS = ("D", "C1", "C2")
HARD_END = datetime(2026, 10, 1, tzinfo=timezone.utc)  # no hour at or after this instant, ever
VENUES = ("pumpfun", "pumpswap")
REASONS = ("block_not_measurable", "no_create_in_block", "bad_trigger_row")
_MINT_RE = re.compile(rb'"mint"\s*:\s*"([^"]+)"')


class ReaderRefused(Exception):
    pass


def drain_tokens(Q: float, B: float, V: float) -> float:
    """D = k/V - B with k = (Q+V)*B  (= Q*B/V)."""
    if V <= 0 or Q < 0 or B < 0:
        raise ReaderRefused("bad_state: V must be > 0, Q and B >= 0")
    return (Q + V) * B / V - B


# ----------------------------------------------------------------------------------------------------------------------------------------------- guards
def _hour_of(name: str) -> datetime | None:
    return AS.hour_of_name(name)


def _check_hour(t: datetime, f: str) -> None:
    if t >= HARD_END:
        raise ReaderRefused(f"hour_at_or_after_2026-10-01: {f}")
    if AS.SEALED_HOURS[0] <= t < AS.SEALED_HOURS[1]:
        raise ReaderRefused(f"sealed_hour: {f}")


def block_root(blk: str) -> str:
    """The canonical block root. Only the four OUTCOMES blocks pass (exact name match, then arena_screen.check_block_path before any open)."""
    if blk not in AS.CANONICAL_BLOCKS:
        raise ReaderRefused(f"not_one_of_the_four_blocks: {blk!r}")
    root = f"{AS.CLEAN_VIEW}/{blk}"
    if AS.check_block_path(root, True) != "clean":
        raise ReaderRefused(f"not_clean: {root}")
    return root


def kind_files(root: str, kind: str) -> list[tuple[datetime, str]]:
    """Hour files of kind 'trades' | 'creates' | 'migrations' in one block (block root, or its w*/ worker dirs), sorted. Every file is realpath-checked inside the root."""
    if kind not in ("trades", "creates", "migrations"):
        raise ReaderRefused(f"bad_kind: {kind}")
    rr = os.path.realpath(root)
    p = Path(root)
    fs = sorted(set(p.glob(f"{kind}/*.jsonl.zst")) | set(p.glob(f"w*/{kind}/*.jsonl.zst")))
    out = []
    for f in fs:
        t = _hour_of(f.name)
        if t is None:
            raise ReaderRefused(f"unexpected_file_name: {f}")
        real = os.path.realpath(f)
        if not (real == rr or real.startswith(rr + "/")):
            raise ReaderRefused(f"file_outside_block_root: {f}")
        for w in AS.FORBIDDEN:
            if w in str(f).lower() or w in real.lower():
                raise ReaderRefused(f"path_forbidden_word:{w}: {f}")
        _check_hour(t, str(f))
        out.append((t, str(f)))
    out.sort(key=lambda x: x[0])
    hrs = [t for t, _ in out]
    if len(set(hrs)) != len(hrs):
        raise ReaderRefused(f"duplicate_hour: {root} {kind}")
    return out


def read_lines(path: str) -> Iterator[bytes]:
    z = subprocess.Popen(["zstd", "-dc", "-q", path], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
    assert z.stdout is not None
    try:
        yield from z.stdout
    finally:
        z.stdout.close()
        rc = z.wait()
    if rc != 0:
        raise RuntimeError(f"zstd failed ({rc}) on {path}")


def project(r: dict, allowed: tuple[str, ...]) -> dict:
    """The field allowlist: everything outside it is dropped here and never stored or counted."""
    return {k: r[k] for k in allowed if k in r}


def venue_of(r: dict) -> str | None:
    return r.get("venue") or r.get("prog")


def row_time(r: dict):
    return r.get("block_time") or r.get("event_ts")


# ----------------------------------------------------------------------------------------------------------------------------------------------- schema count
def schema_count(blk: str) -> dict:
    """Per block: hour counts, rows by venue, rows carrying trader / token_raw / slot per venue, and the key NAMES seen on trades rows (names and counts only)."""
    root = block_root(blk)
    out: dict = {"block": blk, "trades_hours": 0, "creates_hours": 0, "migrations_hours": 0, "trades_rows": 0, "rows_by_venue": collections.Counter(),
                 "trader_rows_by_venue": collections.Counter(), "token_raw_rows_by_venue": collections.Counter(), "slot_rows_by_venue": collections.Counter(),
                 "key_names": collections.Counter(), "creates_rows": 0, "migrations_rows": 0, "bad_json_rows": 0}
    for kind in ("trades", "creates", "migrations"):
        files = kind_files(root, kind)
        out[f"{kind}_hours"] = len(files)
        for _, f in files:
            for line in read_lines(f):
                try:
                    r = json.loads(line)
                except ValueError:
                    out["bad_json_rows"] += 1
                    continue
                if not isinstance(r, dict):
                    out["bad_json_rows"] += 1
                    continue
                if kind != "trades":
                    out[f"{kind}_rows"] += 1
                    continue
                out["trades_rows"] += 1
                v = venue_of(r) or "none"
                out["rows_by_venue"][v] += 1
                if r.get("trader"):
                    out["trader_rows_by_venue"][v] += 1
                if r.get("token_raw") is not None:
                    out["token_raw_rows_by_venue"][v] += 1
                if r.get("slot") is not None:
                    out["slot_rows_by_venue"][v] += 1
                for k in r:
                    out["key_names"][k] += 1
    return {k: dict(v) if isinstance(v, collections.Counter) else v for k, v in out.items()}


def curve_trader_rows(sc: dict) -> int:
    return int(sc["trader_rows_by_venue"].get("pumpfun", 0)) if sc["token_raw_rows_by_venue"].get("pumpfun", 0) else 0


# ----------------------------------------------------------------------------------------------------------------------------------------------- triggers
def load_triggers(path: str) -> list[dict]:
    out = []
    with open(path, "rb") as fh:
        for n, line in enumerate(fh):
            if not line.strip():
                continue
            r = json.loads(line)
            extra = sorted(set(r) - set(TRIGGER_KEYS))
            if extra:
                raise ReaderRefused(f"trigger_key_not_allowed (line {n + 1}): {extra}")
            if r.get("split") is not None and r["split"] not in SPLITS:
                raise ReaderRefused(f"bad_split (line {n + 1})")
            out.append(r)
    return out


def trigger_ok(t: dict) -> bool:
    try:
        return (t["blk"] in AS.CANONICAL_BLOCKS and isinstance(t["mint"], str) and t["mint"] != "" and int(t["slot"]) >= 0 and int(t["event_index"]) >= 0
                and float(t["block_time"]) > 0 and float(t["Q"]) >= 0 and float(t["B"]) >= 0 and float(t["V"]) > 0)
    except (KeyError, TypeError, ValueError):
        return False


# ----------------------------------------------------------------------------------------------------------------------------------------------- reader
def hour_floor(ts: float) -> datetime:
    return datetime.fromtimestamp(int(ts) - int(ts) % 3600, tz=timezone.utc)


def find_creates(root: str, mints: set) -> dict:
    """mint -> create block time (earliest), from the block's own creates hours; fields kept: mint, time."""
    found: dict = {}
    for _, f in kind_files(root, "creates"):
        for line in read_lines(f):
            m = _MINT_RE.search(line)
            if m is None or m.group(1).decode() not in mints:
                continue
            try:
                r = project(json.loads(line), ALLOWED_CREATE_FIELDS)
            except ValueError:
                continue
            ts = row_time(r)
            if isinstance(ts, (int, float)) and ts > 0:
                mt = r["mint"]
                found[mt] = min(found.get(mt, ts), ts)
    return found


def read_block(blk: str, trig: list[tuple[int, dict]], block_measurable: bool) -> dict:
    """Returns ordinal -> record. trig is [(ordinal, trigger)] of this block."""
    res: dict = {}
    good = []
    for i, t in trig:
        if not trigger_ok(t):
            res[i] = {"status": "censored", "reason": "bad_trigger_row"}
        else:
            good.append((i, t))
    if not block_measurable:
        for i, _ in good:
            res[i] = {"status": "censored", "reason": "block_not_measurable"}
        return res
    root = block_root(blk)
    files = kind_files(root, "trades")
    if not files:
        for i, _ in good:
            res[i] = {"status": "censored", "reason": "block_not_measurable"}
        return res
    block_start = files[0][0]
    created = find_creates(root, {t["mint"] for _, t in good})
    # active window per trigger: create hour .. decision hour; create before block start cannot occur (the creates dir is the block's own) but is still censored
    act: dict = {}
    for i, t in good:
        ct = created.get(t["mint"])
        if ct is None or hour_floor(ct) < block_start:
            res[i] = {"status": "censored", "reason": "no_create_in_block"}
            continue
        h0, h1 = hour_floor(ct), hour_floor(float(t["block_time"]))
        if h1 < h0:
            res[i] = {"status": "censored", "reason": "bad_trigger_row"}
            continue
        act.setdefault(t["mint"], []).append((i, t, h0, h1))
    state = {i: {"bal": collections.defaultdict(float), "n_curve": 0, "n_ps": 0} for lst in act.values() for i, *_ in lst}
    for hr, f in files:
        live = {m: [a for a in lst if a[2] <= hr <= a[3]] for m, lst in act.items()}
        live = {m: v for m, v in live.items() if v}
        if not live:
            continue
        for line in read_lines(f):
            m = _MINT_RE.search(line)
            if m is None:
                continue
            mint = m.group(1).decode()
            lv = live.get(mint)
            if lv is None:
                continue
            try:
                r = project(json.loads(line), ALLOWED_ROW_FIELDS)
            except ValueError:
                continue
            v = venue_of(r)
            if v not in VENUES or r.get("side") not in ("buy", "sell") or r.get("slot") is None or r.get("token_raw") is None or not r.get("trader"):
                continue
            key = (int(r["slot"]), int(r.get("event_index") or 0))
            tid = zlib.crc32(str(r["trader"]).encode())
            amt = float(r["token_raw"]) * (1.0 if r["side"] == "buy" else -1.0)
            for i, t, h0, h1 in lv:
                if key > (int(t["slot"]), int(t["event_index"])):  # the decision cut: rows after it are dropped
                    continue
                s = state[i]
                s["bal"][tid] += amt
                s["n_curve" if v == "pumpfun" else "n_ps"] += 1
    for lst in act.values():
        for i, t, _, _ in lst:
            s = state[i]
            pos = sorted((b for b in s["bal"].values() if b > 0), reverse=True)
            tot = sum(pos)
            D = drain_tokens(float(t["Q"]), float(t["B"]), float(t["V"]))
            top1, top3 = (pos[0] if pos else 0.0), sum(pos[:3])
            res[i] = {"status": "measured", "n_curve_rows": s["n_curve"], "n_pumpswap_rows": s["n_ps"], "n_holders": len(pos),
                      "top1_share": (top1 / tot) if tot > 0 else None, "top3_share": (top3 / tot) if tot > 0 else None,
                      "top1_ge_D": bool(tot > 0 and top1 >= D), "top3_ge_D": bool(tot > 0 and top3 >= D)}
    return res


def aggregate(trigs: list[dict], res: dict) -> list[dict]:
    rows: dict = collections.defaultdict(lambda: collections.Counter())
    for i, t in enumerate(trigs):
        r = res[i]
        for key in ((t.get("blk"), "all"), (t.get("blk"), t.get("split") or "nosplit")):
            c = rows[key]
            c["triggers"] += 1
            if r["status"] == "censored":
                c["censored"] += 1
                c["censored_" + r["reason"]] += 1
            else:
                c["measured"] += 1
                c["flag_top1"] += int(r["top1_ge_D"])
                c["flag_top3"] += int(r["top3_ge_D"])
    out = []
    for (b, s), c in sorted(rows.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        d = {"block": b, "split": s, **{k: c.get(k, 0) for k in ("triggers", "censored", "measured", "flag_top1", "flag_top3")}}
        for rs in REASONS:
            d["censored_" + rs] = c.get("censored_" + rs, 0)
        m = d["measured"]
        d["flagged_share_top1"] = (d["flag_top1"] / m) if m else None
        d["flagged_share_top3"] = (d["flag_top3"] / m) if m else None
        out.append(d)
    return out


def run(triggers_path: str, out_dir: str) -> dict:
    out = AS.check_out_dir(out_dir)
    trigs = load_triggers(triggers_path)
    by_blk: dict = collections.defaultdict(list)
    res: dict = {}
    for i, t in enumerate(trigs):
        if t.get("blk") in AS.CANONICAL_BLOCKS:
            by_blk[t["blk"]].append((i, t))
        else:
            raise ReaderRefused(f"not_one_of_the_four_blocks: {t.get('blk')!r}")
    for blk, lst in by_blk.items():
        sc = schema_count(blk)
        res.update(read_block(blk, lst, curve_trader_rows(sc) > 0))
    agg = aggregate(trigs, res)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "per_pool.jsonl"), "w") as fh:
        for i, t in enumerate(trigs):
            fh.write(json.dumps({"i": i, "blk": t["blk"], "split": t.get("split"), **res[i]}, sort_keys=True) + "\n")
    with open(os.path.join(out, "counts.json"), "w") as fh:
        json.dump(agg, fh, indent=1, sort_keys=True)
    return {"aggregate": agg}


STAMP = "IN-SAMPLE EXPLORATION on the four OUTCOMES blocks; count-only; wallet history from same-block rows before the decision only (RULES Am.1 item 3); not a gate read."


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--schema-count", action="store_true", help="count rows and field names per block, nothing else")
    ap.add_argument("--blocks", nargs="*", default=list(AS.CANONICAL_BLOCKS))
    ap.add_argument("--triggers", help="trigger JSONL (allowed keys only)")
    ap.add_argument("--out", help="output dir (not inside the arena or clean view)")
    a = ap.parse_args(argv)
    try:
        if a.schema_count:
            print(STAMP)
            for b in a.blocks:
                print(json.dumps(schema_count(b), sort_keys=True))
            return 0
        if not (a.triggers and a.out):
            ap.error("--triggers and --out are required unless --schema-count")
        print(STAMP)
        print(json.dumps(run(a.triggers, a.out), sort_keys=True))
        return 0
    except (ReaderRefused, AS.Refused) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
