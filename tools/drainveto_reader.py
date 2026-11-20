"""DRAIN-VETO step 0: the pinned reader (QP-RULING-1120b Q1).

What it reads: pumpfun curve rows and PumpSwap rows of each trigger pool's mint, ONLY inside the trigger's own exploration block, from the pool's create hour to the FLOOR
decision instant. Fields kept per row: trader (crc32), side, token amount, slot, event_index, block time, venue/prog, mint. Nothing else is ever kept (no SOL or quote amount,
reserve, price, fee, creator, name). No hour before the block's first hour, none after the decision hour, none at or after 2026-10-01T00Z, none in the D2/D3 hours.
The decision cut is (slot, tx_index, event_index) up to AND INCLUDING the trigger print (the kit's print order, 1120b "up to and including"); only rows with a greater key are dropped
before they are counted. The trigger print is a sell, so counting it removes the tokens already sold; per block the reader counts trigger_print_seen (a sell row with exactly the locator key was read). A row in the trigger's slot lacking tx_index or event_index is dropped and counted, never defaulted. Creates hours are read only up to the latest decision hour.

What it writes: counts and per-pool flags. No pool or mint or trader id, no price, no P&L, no exit outcome. The per-pool file is keyed by the ordinal of the trigger in the input
file only.

Modes:
  --schema-count  counts rows by venue and key name per block (names and counts only; runs first, per the ruling);
  (default)       the reader proper; needs --triggers.

Trigger input: the pinned locator file of tools/drainveto_triggers.py (JSONL, exactly LOCATOR_KEYS), passed with --triggers-sha256; the reader refuses to run if the file's sha256
differs. Decision state comes from the builder's drainveto_dstate.jsonl via --dstate with --dstate-sha256 (exactly DSTATE_KEYS; its ordinal set must equal the locator's). --schema-json needs
--schema-sha256. A trigger with locator_ok false is censored
(locator_incomplete); the key is never defaulted.
Q, B, V are the TRIGGER PRINT's own post-trade state from the pinned builder (not grid Qe/Be): Q = the rule's qpost, real quote reserve in lamports, V excluded; B = base reserve after the
sell in token_raw; V = VMap V in lamports. Dr = k/V - B with k = (Q+V)*B, i.e. Dr = Q*B/V in token_raw: the token amount a holder must add to drive the real quote reserve to 0.
Ranges (never clipped; outside -> censored dstate_invalid): V in [17.5e9, 17.7e9], Q in [0, 0.5e9], B > 0. The reader never opens grid.pkl and does not derive triggers from tape. The dstate file is written by the same pinned builder.
Measurability: a block is measurable only if >= 99% of its pumpfun rows AND of its pumpswap rows carry mint, trader, token_raw, slot, tx_index and event_index (MIN_FIELD_SHARE, fixed
before any schema output). The reader proper takes the filed --schema-count output via --schema-json, or else re-runs the licensed schema pass itself.
Field allowlist: rows are JSON-parsed and projected to the allowlist as the first operation (QP-accepted at 0d0e3a5a while projection stays first).

Holder balance: per crc32 trader, sum(buy token) - sum(sell token) over the curve and PumpSwap rows read; negative balances (tokens that arrived by transfer) count as 0.
No pool, burn or known-program exclusion is done (a crc32 id cannot be recognised); transfers in and out are invisible; crc32 ids can collide. Direction of the bias is unknown.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
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
LOCATOR_KEYS = ("ordinal", "block", "blk", "pool", "pool_crc32", "mint", "hour", "slot", "tx_index", "event_index", "locator_ok", "locator_problem", "n_qual_in_slot")  # tools/drainveto_triggers.py OUT_KEYS
DSTATE_KEYS = ("ordinal", "Q", "B", "V", "split")  # decision-time state from the licensed grid fields (Qe, Be, VMap V); its own pinned step, not built here
SPLITS = ("D", "C1", "C2")
HARD_END = datetime(2026, 10, 1, tzinfo=timezone.utc)  # no hour at or after this instant, ever
VENUES = ("pumpfun", "pumpswap")
REASONS = ("locator_incomplete", "dstate_invalid", "block_not_measurable", "oracle_live_proxy", "no_create_in_block", "create_after_decision", "no_usable_rows_before_cut", "bad_trigger_row")
# no_create_in_block merges "created before the block start" with "no create row found" (the creates dir is the block's own; the two cannot be told apart).
SIX = ("mint", "trader", "token_raw", "slot", "tx_index", "event_index")
MIN_FIELD_SHARE = 0.99
V_RANGE = (17.5e9, 17.7e9)  # lamports, VMap V
Q_RANGE = (0.0, 0.5e9)  # real quote after the trigger print, lamports, V excluded
ORACLE_LIVE = "oracle-live-2026-09-25_27"
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
    """Per block: hour counts, and per venue: rows, rows carrying each of the six fields, side value counts, key NAMES (names and counts only, never ids or amounts)."""
    root = block_root(blk)
    out: dict = {"block": blk, "trades_hours": 0, "creates_hours": 0, "migrations_hours": 0, "trades_rows": 0, "creates_rows": 0, "migrations_rows": 0, "bad_json_rows": 0, "venues": {}}
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
                v = out["venues"].setdefault(venue_of(r) or "none", {"rows": 0, "has": {k: 0 for k in SIX}, "all_six": 0, "side_values": {}, "key_names": {}})
                v["rows"] += 1
                ok = True
                for k in SIX:
                    if r.get(k) not in (None, ""):
                        v["has"][k] += 1
                    else:
                        ok = False
                v["all_six"] += int(ok)
                sv = str(r.get("side"))[:12]
                v["side_values"][sv] = v["side_values"].get(sv, 0) + 1
                for k in r:
                    v["key_names"][k] = v["key_names"].get(k, 0) + 1
    return out


def measurable(sc: dict) -> bool:
    """>= MIN_FIELD_SHARE of pumpfun rows AND of pumpswap rows carry all six fields (a venue with no rows fails)."""
    for ven in VENUES:
        v = sc["venues"].get(ven)
        if not v or v["rows"] == 0 or v["all_six"] / v["rows"] < MIN_FIELD_SHARE:
            return False
    return True


# ----------------------------------------------------------------------------------------------------------------------------------------------- triggers
def _jsonl(path: str, keys: tuple, what: str, required: bool = True) -> list[dict]:
    out = []
    with open(path, "rb") as fh:
        for n, line in enumerate(fh):
            if not line.strip():
                continue
            r = json.loads(line)
            extra = sorted(set(r) - set(keys))
            missing = sorted(set(keys) - set(r)) if required else []
            if extra or missing:
                raise ReaderRefused(f"{what}_keys_refused (line {n + 1}): extra {extra} missing {missing}")
            out.append(r)
    return out


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_triggers(path: str, sha256: str, dstate_path: str, dstate_sha256: str = "") -> list[dict]:
    """Locator file (sha256-pinned) merged by ordinal with the decision-state file. Refuses on any sha or key mismatch."""
    if not sha256 or sha256_file(path) != sha256.lower():
        raise ReaderRefused("trigger_file_sha256_mismatch")
    if not dstate_sha256 or sha256_file(dstate_path) != dstate_sha256.lower():
        raise ReaderRefused("dstate_file_sha256_mismatch")
    loc = _jsonl(path, LOCATOR_KEYS, "locator")
    dl = _jsonl(dstate_path, DSTATE_KEYS, "dstate")
    st = {r["ordinal"]: r for r in dl}
    if len({r["ordinal"] for r in loc}) != len(loc) or len(st) != len(dl):
        raise ReaderRefused("ordinals_not_unique")
    if set(st) != {r["ordinal"] for r in loc}:
        raise ReaderRefused("dstate_ordinals_differ_from_locator")
    out = []
    for r in loc:
        d = st.get(r["ordinal"])
        if d is None:
            raise ReaderRefused("dstate_missing_ordinal")
        if d.get("split") is not None and d["split"] not in SPLITS:
            raise ReaderRefused("bad_split")
        out.append({"ordinal": r["ordinal"], "blk": r["block"], "mint": r["mint"], "hour": r["hour"], "slot": r["slot"], "tx_index": r["tx_index"], "event_index": r["event_index"],
                    "locator_ok": r["locator_ok"], "Q": d["Q"], "B": d["B"], "V": d["V"], "split": d.get("split")})
    return out


def trigger_ok(t: dict) -> bool:
    try:
        datetime.strptime(t["hour"], "%Y-%m-%dT%H")
        return (t["blk"] in AS.CANONICAL_BLOCKS and isinstance(t["mint"], str) and t["mint"] != "" and int(t["slot"]) >= 0 and int(t["tx_index"]) >= 0 and int(t["event_index"]) >= 0
                and float(t["Q"]) == float(t["Q"]) and float(t["B"]) == float(t["B"]) and float(t["V"]) == float(t["V"]))
    except (KeyError, TypeError, ValueError):
        return False


def dstate_ok(t: dict) -> bool:
    return V_RANGE[0] <= float(t["V"]) <= V_RANGE[1] and Q_RANGE[0] <= float(t["Q"]) <= Q_RANGE[1] and float(t["B"]) > 0


def decision_hour(t: dict) -> datetime:
    return datetime.strptime(t["hour"], "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)


# ----------------------------------------------------------------------------------------------------------------------------------------------- reader
def hour_floor(ts: float) -> datetime:
    return datetime.fromtimestamp(int(ts) - int(ts) % 3600, tz=timezone.utc)


def find_creates(root: str, mints: set, last_hour: datetime) -> dict:
    """mint -> create block time (earliest), from the block's own creates hours; fields kept: mint, time."""
    found: dict = {}
    for ch, f in kind_files(root, "creates"):
        if ch > last_hour:  # never an hour after the latest decision
            break
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


def read_block(blk: str, trig: list[tuple[int, dict]], block_measurable: bool) -> tuple[dict, dict]:
    """Returns (ordinal -> record, skipped-row counts by reason). trig is [(ordinal, trigger)] of this block."""
    res: dict = {}
    skips: collections.Counter = collections.Counter()
    good = []
    for i, t in trig:
        if not t.get("locator_ok"):  # first: an empty mint or missing key is locator_incomplete, whatever else is wrong
            res[i] = {"status": "censored", "reason": "locator_incomplete"}
        elif not trigger_ok(t):
            res[i] = {"status": "censored", "reason": "bad_trigger_row"}
        elif not dstate_ok(t):
            res[i] = {"status": "censored", "reason": "dstate_invalid"}
        else:
            good.append((i, t))
    if not block_measurable:
        for i, _ in good:
            res[i] = {"status": "censored", "reason": "block_not_measurable"}
        return res, dict(skips)
    if blk == ORACLE_LIVE:  # no creates or migrations rows exist; the complete time can only be proxied by the first print: censored before any creates lookup
        for i, _ in good:
            res[i] = {"status": "censored", "reason": "oracle_live_proxy"}
        return res, dict(skips)
    root = block_root(blk)
    files = kind_files(root, "trades")
    if not files or not good:
        for i, _ in good:
            res[i] = {"status": "censored", "reason": "block_not_measurable"}
        return res, dict(skips)
    block_start = files[0][0]
    last_hour = max(decision_hour(t) for _, t in good)
    created = find_creates(root, {t["mint"] for _, t in good}, last_hour)
    act: dict = {}
    for i, t in good:
        ct = created.get(t["mint"])
        if ct is None or hour_floor(ct) < block_start:
            res[i] = {"status": "censored", "reason": "no_create_in_block"}
            continue
        h0, h1 = hour_floor(ct), decision_hour(t)
        if h1 < h0:
            res[i] = {"status": "censored", "reason": "create_after_decision"}
            continue
        act.setdefault(t["mint"], []).append((i, t, h0, h1))
    state = {i: {"bal": collections.defaultdict(float), "n_curve": 0, "n_ps": 0, "seen": False} for lst in act.values() for i, *_ in lst}
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
                r = project(json.loads(line), ALLOWED_ROW_FIELDS)  # first operation after parse
            except ValueError:
                skips["bad_json"] += 1
                continue
            if r.get("mint") != mint:
                skips["mint_mismatch"] += 1
                continue
            v = venue_of(r)
            if v not in VENUES:
                skips["bad_venue"] += 1
                continue
            if r.get("side") not in ("buy", "sell"):
                skips["bad_side"] += 1
                continue
            if r.get("slot") is None or r.get("token_raw") is None or not r.get("trader"):
                skips["missing_slot_token_or_trader"] += 1
                continue
            slot = int(r["slot"])
            tid = zlib.crc32(str(r["trader"]).encode())
            amt = float(r["token_raw"]) * (1.0 if r["side"] == "buy" else -1.0)
            for i, t, h0, h1 in lv:
                ts = int(t["slot"])
                if slot > ts:  # after the decision cut
                    skips["after_cut"] += 1
                    continue
                if slot == ts:
                    if r.get("tx_index") is None or r.get("event_index") is None:  # never defaulted
                        skips["same_slot_missing_key"] += 1
                        continue
                    if (int(r["tx_index"]), int(r["event_index"])) > (int(t["tx_index"]), int(t["event_index"])):  # after the trigger print; the print itself counts
                        skips["after_cut"] += 1
                        continue
                    if (int(r["tx_index"]), int(r["event_index"])) == (int(t["tx_index"]), int(t["event_index"])) and r["side"] == "sell":
                        state[i]["seen"] = True
                s = state[i]
                s["bal"][tid] += amt
                s["n_curve" if v == "pumpfun" else "n_ps"] += 1
    for lst in act.values():
        for i, t, _, _ in lst:
            s = state[i]
            pos = sorted((b for b in s["bal"].values() if b > 0), reverse=True)
            tot = sum(pos)
            if s["n_curve"] + s["n_ps"] == 0 or tot <= 0:
                res[i] = {"status": "censored", "reason": "no_usable_rows_before_cut"}
                continue
            D = drain_tokens(float(t["Q"]), float(t["B"]), float(t["V"]))
            top1, top3 = pos[0], sum(pos[:3])
            res[i] = {"status": "measured", "n_curve_rows": s["n_curve"], "n_pumpswap_rows": s["n_ps"], "n_holders": len(pos),
                      "trigger_print_seen": s["seen"], "top1_share": top1 / tot, "top3_share": top3 / tot, "top1_ge_D": bool(top1 >= D), "top3_ge_D": bool(top3 >= D)}
    return res, dict(skips)


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
                c["trigger_print_seen"] += int(r["trigger_print_seen"])
                c["flag_top1"] += int(r["top1_ge_D"])
                c["flag_top3"] += int(r["top3_ge_D"])
    out = []
    for (b, s), c in sorted(rows.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        d = {"block": b, "split": s, **{k: c.get(k, 0) for k in ("triggers", "censored", "measured", "trigger_print_seen", "flag_top1", "flag_top3")}}
        for rs in REASONS:
            d["censored_" + rs] = c.get("censored_" + rs, 0)
        m = d["measured"]
        d["flagged_share_top1"] = (d["flag_top1"] / m) if m else None
        d["flagged_share_top3"] = (d["flag_top3"] / m) if m else None
        out.append(d)
    return out


def run(triggers_path: str, out_dir: str, schema_json: str | None = None, *, triggers_sha256: str = "", dstate_path: str = "", dstate_sha256: str = "", schema_sha256: str = "") -> dict:
    out = AS.check_out_dir(out_dir)
    trigs = load_triggers(triggers_path, triggers_sha256, dstate_path, dstate_sha256)
    by_blk: dict = collections.defaultdict(list)
    res: dict = {}
    for i, t in enumerate(trigs):
        if t.get("blk") in AS.CANONICAL_BLOCKS:
            by_blk[t["blk"]].append((i, t))
        else:
            raise ReaderRefused(f"not_one_of_the_four_blocks: {t.get('blk')!r}")
    if schema_json and (not schema_sha256 or sha256_file(schema_json) != schema_sha256.lower()):
        raise ReaderRefused("schema_file_sha256_mismatch")
    filed = load_schema_json(schema_json) if schema_json else {}
    skip_by_blk: dict = {}
    for blk, lst in by_blk.items():
        sc = filed.get(blk) or schema_count(blk)
        r, sk = read_block(blk, lst, measurable(sc))
        res.update(r)
        skip_by_blk[blk] = sk
    agg = aggregate(trigs, res)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "per_pool.jsonl"), "w") as fh:
        for i, t in enumerate(trigs):
            fh.write(json.dumps({"i": i, "blk": t["blk"], "split": t.get("split"), **res[i]}, sort_keys=True) + "\n")
    with open(os.path.join(out, "counts.json"), "w") as fh:
        json.dump({"aggregate": agg, "skipped_rows_by_block": skip_by_blk}, fh, indent=1, sort_keys=True)
    return {"aggregate": agg, "skipped_rows_by_block": skip_by_blk}


def load_schema_json(path: str) -> dict:
    """The filed --schema-count output (one JSON line per block; the stamp line is ignored)."""
    out = {}
    for line in open(path):
        line = line.strip()
        if line.startswith("{"):
            d = json.loads(line)
            out[d["block"]] = d
    return out


STAMP = ("IN-SAMPLE EXPLORATION on the four OUTCOMES blocks; count-only; wallet history from same-block rows before the decision only (RULES Am.1 item 3); not a gate read. "
         "Flagged share is on the uncensored subset only; censored counts per block are in the output. Gaps in keep or decode between create and decision are not detected "
         "(balances incomplete, bias of unknown direction).")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--schema-count", action="store_true", help="count rows and field names per block, nothing else")
    ap.add_argument("--blocks", nargs="*", default=list(AS.CANONICAL_BLOCKS))
    ap.add_argument("--triggers", help="locator JSONL built by tools/drainveto_triggers.py")
    ap.add_argument("--triggers-sha256", help="the notebooked sha256 of the locator file; the reader refuses on any difference")
    ap.add_argument("--dstate", help="drainveto_dstate.jsonl from the pinned builder (ordinal, Q, B, V, split)")
    ap.add_argument("--dstate-sha256", help="the notebooked sha256 of the dstate file")
    ap.add_argument("--schema-sha256", help="the notebooked sha256 of the --schema-json file (required with it)")
    ap.add_argument("--schema-json", help="filed --schema-count output (counts only); measurability is read from it instead of re-running the schema pass")
    ap.add_argument("--out", help="output dir (not inside the arena or clean view)")
    a = ap.parse_args(argv)
    try:
        if a.schema_count:
            print(STAMP)
            for b in a.blocks:
                print(json.dumps(schema_count(b), sort_keys=True))
            return 0
        if not (a.triggers and a.out and a.triggers_sha256 and a.dstate and a.dstate_sha256):
            ap.error("--triggers, --triggers-sha256, --dstate, --dstate-sha256 and --out are required unless --schema-count")
        print(STAMP)
        print(json.dumps(run(a.triggers, a.out, a.schema_json, triggers_sha256=a.triggers_sha256, dstate_path=a.dstate, dstate_sha256=a.dstate_sha256, schema_sha256=a.schema_sha256 or ""), sort_keys=True))
        return 0
    except (ReaderRefused, AS.Refused) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
