#!/usr/bin/env python3
"""EXP-025 P7 driver: writes the P7 record that `tools/exp025_look.py --p7` reads (R14).

    /data/mal/audit-1008/venv/bin/python tools/exp025_p7.py run --look 1 [--rps 5]   # after the DEC-016 FINAL, once per look
    /data/mal/audit-1008/venv/bin/python tools/exp025_p7.py e0 [--rps 5]              # exploration day 2026-09-20 only (plumbing E0)

Rule (EXP/EXP-025-c1nf-part1-prereg.md section 10 P7, Amendment 1, Amendment 6):
  Frame and draw. The canonical-pool PumpSwap prints of the pools with a non-null `tokens.v0_lamports` in the look's tokens.parquet, over the
    look's V-covered hours `[2026-10-09T00, look end)` (section 10 P7, written out), in (slot, tx_index, event_index) order. The sampled tape
    rows are the RAW block rows (the walker's JSONL: they carry signature, ix_name, zero_sol, V); Amendment 1 item 3. Frame, main draw of 1,000
    and the buy top-up come from p7_buy_amend.p7_raw_draw (event_v_map's frame and draw; top-up candidates per Amendment 6 item 1).
  Line 1 (constant product). Each comparable sampled print (main + top-up) is decided by p7_buy_amend.p7_raw_check: buys by the inverse law
    (cp_buy_quote_in, Amendment 6), sells by event_v_map's sell law unchanged. Raw event = getTransaction (encoding json, commitment
    confirmed, maxSupportedTransactionVersion 1; at most 3 attempts per transaction; one fetch per transaction) decoded by
    observe.trade_decode.records_from_logs(event_v=True) at blob 238942a6 (refused otherwise), keyed by event_index. The adapter row is the
    row of the look's materialised tape/trades/<hour>.parquet at (slot, tx_index, event_index). Unresolved = miss. Tally: p7_raw_tally.
  Line 2 (fee tier, section 10 P7 line 2: "(EXP-024 section 10 P7)", buy side as amended by Amendment 7), on the main 1,000 only, from the
    adapter row (the row pass A prices) and V0. Q = quote_reserve_mapped + V0, b = base_reserve, tok = token_raw, sol = sol_lamports.
      sell (unchanged, tools/boostfloor_inputs.py tier_lines' form): f = float(exp025_read.fee(Q, b)), BP = 1e-4 (1 bp),
            E = Q * tok / (b + tok) * (1 - f); hit iff E > 0 and |sol - E| <= BP * E
      buy  (Amendment 7 B, decided only through ARTIFACTS/exp025/p7_line2_amend.py, loaded after its sha256 check):
            1. excluded (in neither denominator; counted per cause and per name) unless the SAMPLED raw row is a comparable buy of line 1
               (p7_buy_amend.p7_raw_exclusion: zero_sol, buy_exact_quote_in, no_ix_name, ix_not_listed), before any adapter check;
            2. the adapter checks below; 3. skipped (not in buy_n; buy_skipped) if sol <= 0 or tok <= 0 or tok >= b;
            4. hit iff abs(sol * 10**6 - qin * (10**6 + ppm)) <= 100 * qin, qin = -((-Q * tok) // (b - tok)), ppm = the integer tier of
               exp025_read.fee(Q, b) (p7_line2_amend.tier_ppm). All in Python ints.
    no_adapter_row, identity_mismatch and field_missing are misses in the denominator (EXP-024: "a print without V is a miss"), counted per
    cause (LINE2_CAUSES); a sell whose formula is undefined (b <= 0 or b + tok <= 0: the tier divides by b) is a miss counted as `degenerate`.

Seal. `run` refuses (exit 2) before it opens any October, tape, token or block path unless exp025_read.LookGuard passes (the DEC-016 FINAL
marker and its ledger entry, and the look's last allowlisted hour has ended), and refuses if the look's tape/tokens are not materialised.
Every hour it opens passes the guard's allowlist check. Paths are fixed in code; the record is written once (an existing record refuses).

Output (in the look's O/p7/): P7.json {"schema", "look", "cp": [sell_ok, sell_n, buy_ok, buy_n], "fee": [...], counts, credits, pins,
"prints": path, "prints_sha256"} and p7_prints.jsonl (signatures and per-print outcomes). pins carry p7_line2_amend_sha256 and driver_blob (this
file's git blob), which Amendment 7 D pins. Stdout: counts only (per side, cause, ix_name).
No pick, label, fill, exit or P&L is computed anywhere here.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TOOLS = os.path.join(ROOT, "tools")
for _p in (ROOT, TOOLS):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import exp025_read as R  # noqa: E402

ART = os.path.join(ROOT, "ARTIFACTS", "exp025")
AMEND_PATH = os.path.join(ART, "p7_buy_amend.py")
AMEND_SHA256 = "ed3005f083d80bba768292a8ff6adf4b4220370e01760a540034c6d1bcace31b"   # SHA256SUMS line (git blob 24dc5ede)
LINE2_PATH = os.path.join(ART, "p7_line2_amend.py")
LINE2_SHA256 = "1908931c9ff1c88219d1276cfd8537f533073be7c97f8eb4ae4fa49994c19b2f"   # SHA256SUMS line; EXP-025 Amendment 7 D
DECODER_PATH = os.path.join(ROOT, "observe", "trade_decode.py")
DECODER_BLOB = "238942a6b3c5425389eddfde4d11268c300acbec"                            # Amendment 1 part 2 "Decode"
HELIUS_ENV = "/var/lib/mal/backfill/helius.env"                                       # the research walkers' key file; never printed
V_COVER_START = "2026-10-09T00"
SCHEMA = "exp025_p7_v1"
E0_SCHEMA = "exp025_p7_e0_v1"
BLOCK_OF_SOURCE = {"forward-1002": "forward-1002", "forward-1002ev": "forward-1002ev", "forward-1016": "walk2"}   # = exp025_look's
TOKENS_OF_LOOK = {1: "/data/mal/exp025/look1/hunt-shared/tokens.parquet",            # the SH line of patches/common2_look{1,2}.patch
                  2: "/data/mal/exp025/look2/hunt-shared/tokens.parquet"}
E0_DAY = "2026-09-20"
E0_OUT = "/data/mal/exp025/e0/p7-0920-am7"   # a new directory: P7.json is write-once and #607's record is /data/mal/exp025/e0/p7-0920
RPS_DEFAULT = 5.0
RAW_COLS = ("{venue:'VARCHAR',pool:'VARCHAR',side:'VARCHAR',slot:'BIGINT',tx_index:'INTEGER',event_index:'INTEGER',signature:'VARCHAR',"
            "sol_lamports:'BIGINT',token_raw:'HUGEINT',quote_reserve:'HUGEINT',base_reserve:'HUGEINT',virtual_quote_reserves:'HUGEINT',"
            "ix_name:'VARCHAR',zero_sol:'BOOLEAN'}")
RAW_FIELDS = ("pool", "side", "slot", "tx_index", "event_index", "signature", "sol_lamports", "token_raw", "quote_reserve", "base_reserve",
              "virtual_quote_reserves", "ix_name", "zero_sol")
ADAPTER_FIELDS = ("slot", "tx_index", "event_index", "pool", "side", "sol_lamports", "token_raw", "quote_reserve", "base_reserve")
LINE2_CAUSES = ("no_adapter_row", "identity_mismatch", "field_missing", "degenerate")
LINE2_SKIPPED = "buy_skipped"
LINE2_BP = 1e-4                                                                     # tier_lines BP: 1 bp
GET_TX_OPTS = {"encoding": "json", "commitment": "confirmed", "maxSupportedTransactionVersion": 1}


class P7Refusal(R.Refusal):
    pass


# ------------------------------------------------------------------------------------------------------------------- pins
def _sha256(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def git_blob(path: str) -> str:
    with open(path, "rb") as fh:
        data = fh.read()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def sums_line(rel: str) -> str | None:
    with open(os.path.join(ART, "SHA256SUMS")) as fh:
        for line in fh:
            parts = line.split()
            if len(parts) == 2 and parts[1] == rel:
                return parts[0]
    return None


def load_amend(path: str = AMEND_PATH):
    """ARTIFACTS/exp025/p7_buy_amend.py, only if its sha256 equals both the pinned constant and its SHA256SUMS line (refuse otherwise)."""
    got = _sha256(path)
    if got != AMEND_SHA256 or sums_line("p7_buy_amend.py") != AMEND_SHA256:
        raise P7Refusal("PIN", f"p7_buy_amend.py sha256 {got[:12]} is not the pinned {AMEND_SHA256[:12]} (SHA256SUMS)")
    spec = importlib.util.spec_from_file_location("exp025_p7_buy_amend_driver", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def load_line2_amend(path: str = LINE2_PATH):
    """ARTIFACTS/exp025/p7_line2_amend.py, only if its sha256 equals both the pinned constant and its SHA256SUMS line (refuse otherwise)."""
    got = _sha256(path)
    if got != LINE2_SHA256 or sums_line("p7_line2_amend.py") != LINE2_SHA256:
        raise P7Refusal("PIN", f"p7_line2_amend.py sha256 {got[:12]} is not the pinned {LINE2_SHA256[:12]} (SHA256SUMS)")
    spec = importlib.util.spec_from_file_location("exp025_p7_line2_amend_driver", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def check_decoder(path: str = DECODER_PATH) -> None:
    got = git_blob(path)
    if got != DECODER_BLOB:
        raise P7Refusal("PIN", f"observe/trade_decode.py is blob {got[:8]}, not the pinned {DECODER_BLOB[:8]}")


# ------------------------------------------------------------------------------------------------------------------- context
def look_context(look: int, now: int | None = None) -> dict:
    """The fixed paths of a look. The guard runs FIRST: nothing below it opens a path before the FINAL marker check passes."""
    guard = R.LookGuard(look, now)                         # SEAL: FINAL marker + ledger, and the look's last allowlisted hour has ended
    import exp025_adapter as AD
    L = R.LOOKS[look]
    O = L["O"]
    hours = []
    for src, h in R.allowlisted_hours(look):
        if not (R.ep(V_COVER_START) <= R.ep(h) < R.ep(L["end"])):
            continue
        guard.check_hour(src, h)
        p = AD.src_file(AD.SOURCES[BLOCK_OF_SOURCE[src]], "trades", h)
        if p is not None:
            R.assert_not_closed(str(p))
        hours.append((h, str(p) if p else None))
    return dict(mode="look", look=look, schema=SCHEMA, hours=hours, tape=os.path.join(O, "tape", "trades"), tokens=TOKENS_OF_LOOK[look],
                assembly=os.path.join(O, "look_assembly.json"), out=os.path.join(O, "p7"))


def e0_context() -> dict:
    """Exploration E0 (2026-09-20): the adapter's E0 source, the audit tape and the exploration tokens. Exploration hours only."""
    import exp025_adapter as AD
    hours = []
    for i in range(24):
        h = f"{E0_DAY}T{i:02d}"
        AD.check_exploration_hour(h, AD.E0_SRC)
        p = AD.src_file(AD.E0_SRC, "trades", h)
        hours.append((h, str(p) if p else None))
    return dict(mode="e0", look=None, schema=E0_SCHEMA, hours=hours, tape=os.path.join(AD.E0_REF_TAPE, "trades"),
                tokens=os.path.join(AD.EXPLORATION_SH, "tokens.parquet"), assembly=None, out=E0_OUT)


def check_materialised(ctx: dict) -> None:
    if ctx.get("assembly"):                               # the runner's own check: schema exp025_look_assembly_v1, look 'look{N}', >= 1 manifest
        import exp025_look as LK
        LK.load_assembly(ctx["look"], os.path.dirname(ctx["assembly"]))
    if not os.path.exists(ctx["tokens"]):
        raise P7Refusal("NOT_READY", "the look's tokens.parquet is not materialised")
    if not any(os.path.exists(os.path.join(ctx["tape"], f"{h}.parquet")) for h, _ in ctx["hours"]):
        raise P7Refusal("NOT_READY", "the look's tape/trades holds no V-covered hour")
    if not any(p for _, p in ctx["hours"]):
        raise P7Refusal("NOT_READY", "no raw trades file in the V-covered hours")


# ------------------------------------------------------------------------------------------------------------------- reads
def v0_map(tokens: str) -> dict:
    import duckdb
    con = duckdb.connect()
    rows = con.execute("SELECT pool, v0_lamports FROM read_parquet(?) WHERE pool IS NOT NULL AND v0_lamports IS NOT NULL", [tokens]).fetchall()
    con.close()
    return {p: int(v) for p, v in rows}


FRAME_FIELDS = ("pool", "side", "slot", "tx_index", "event_index", "zero_sol", "ix_name", "_hour")   # what the frame and draw read


class Row(tuple):
    """A frame row: a tuple read by field name (`row["slot"]`, `row.get("ix_name")`), so a look's frame (millions of prints) fits in memory.
    The pinned frame / draw / exclusion functions read only FRAME_FIELDS; a SAMPLED print is re-read in full (full_rows) before its check."""
    __slots__ = ()
    _IX = {k: i for i, k in enumerate(FRAME_FIELDS)}

    def __getitem__(self, k):
        return tuple.__getitem__(self, self._IX[k] if isinstance(k, str) else k)

    def get(self, k, default=None):
        i = self._IX.get(k)
        return default if i is None else tuple.__getitem__(self, i)


def _con():
    import duckdb
    con = duckdb.connect()
    con.execute("SET memory_limit='1GB'")
    con.execute("SET threads=2")
    return con


def _raw_sql(cols: str) -> str:
    return f"SELECT {cols} FROM read_json(?, format='newline_delimited', columns={RAW_COLS}) WHERE venue = 'pumpswap'"


def frame_rows(ctx: dict, pools) -> list:
    """Raw block rows of the V-covered hours: PumpSwap prints of the pools with a V0, as Rows of FRAME_FIELDS (strings interned)."""
    import pandas as pd
    con = _con()
    con.register("pp", pd.DataFrame({"pool": sorted(pools)}))
    out, intern, keyless = [], sys.intern, 0
    for h, p in ctx["hours"]:
        if not p:
            continue
        cur = con.execute(_raw_sql(", ".join(FRAME_FIELDS[:-1])) + " AND pool IN (SELECT pool FROM pp)", [p])
        while True:
            chunk = cur.fetchmany(100_000)
            if not chunk:
                break
            for pool, side, slot, txi, evi, zs, ix in chunk:
                if slot is None or txi is None or evi is None:
                    keyless += 1
                    continue
                out.append(Row((intern(pool), intern(side) if side else side, slot, txi, evi, zs, intern(ix) if ix else ix, h)))
    con.close()
    ctx["keyless"] = keyless
    if keyless and ctx["mode"] == "look":     # the walker writes slot, tx_index and event_index on every row: a keyless row has no frame place
        raise P7Refusal("NOT_READY", f"{keyless} PumpSwap rows of V0 pools have no (slot, tx_index, event_index): the frame cannot be ordered")
    return out


def full_rows(ctx: dict, sample: list) -> list:
    """The sampled prints re-read in full (RAW_FIELDS + `_hour`) from their hour's raw file, by (slot, tx_index, event_index) and pool."""
    import pandas as pd
    path = dict(ctx["hours"])
    by_hour = {}
    for r in sample:
        by_hour.setdefault(r["_hour"], set()).add((r["slot"], r["tx_index"], r["event_index"], r["pool"]))
    got = {}
    con = _con()
    for h, keys in sorted(by_hour.items()):
        k = sorted(keys)
        con.register("kk", pd.DataFrame({"slot": [a[0] for a in k], "tx_index": [a[1] for a in k], "event_index": [a[2] for a in k],
                                         "pool": [a[3] for a in k]}))
        q = f"SELECT * FROM ({_raw_sql(', '.join(RAW_FIELDS))}) JOIN kk USING (slot, tx_index, event_index, pool)"
        for rec in con.execute(q, [path[h]]).fetchall():
            d = dict(zip(RAW_FIELDS, rec), _hour=h)
            got.setdefault((d["slot"], d["tx_index"], d["event_index"], d["pool"]), d)
        con.unregister("kk")
    con.close()
    return [got[(r["slot"], r["tx_index"], r["event_index"], r["pool"])] for r in sample]


def _int_exact(x):
    """tools/exp025_adapter.py:321 writes token_raw, quote_reserve and base_reserve as DOUBLE (convert.py's writer). An integral double with
    |x| <= 2**53 is that integer exactly. Any other value stays as it is, which makes it field_missing."""
    return int(x) if isinstance(x, float) and x.is_integer() and abs(x) <= 2**53 else x


def adapter_rows(ctx: dict, sample: list) -> dict:
    """{(slot, tx_index, event_index): adapter row} from the look's materialised tape/trades/<hour>.parquet, per sampled print's hour.
    Several rows at one key: the one with the sampled print's pool, else the first."""
    import pandas as pd
    by_hour, want_pool = {}, {}
    for r in sample:
        by_hour.setdefault(r["_hour"], set()).add((r["slot"], r["tx_index"], r["event_index"]))
        want_pool[(r["slot"], r["tx_index"], r["event_index"])] = r["pool"]
    out = {}
    con = _con()
    for h, keys in sorted(by_hour.items()):
        f = os.path.join(ctx["tape"], f"{h}.parquet")
        if not os.path.exists(f):
            continue
        k = sorted(keys)
        con.register("kk", pd.DataFrame({"slot": [a for a, _, _ in k], "tx_index": [b for _, b, _ in k], "event_index": [c for _, _, c in k]}))
        q = (f"SELECT {', '.join('t.' + c for c in ADAPTER_FIELDS)} FROM read_parquet(?) t "
             "JOIN kk USING (slot, tx_index, event_index) ORDER BY t.slot, t.tx_index, t.event_index")
        for rec in con.execute(q, [f]).fetchall():
            d = {k: _int_exact(v) for k, v in zip(ADAPTER_FIELDS, rec)}
            key = (d["slot"], d["tx_index"], d["event_index"])
            if key not in out or (out[key]["pool"] != want_pool[key] and d["pool"] == want_pool[key]):
                out[key] = d
        con.unregister("kk")
    con.close()
    return out


# ------------------------------------------------------------------------------------------------------------------- fetch
def _helius_key(path: str = HELIUS_ENV) -> str:
    with open(path) as fh:
        for line in fh:
            m = re.match(r"\s*(?:export\s+)?HELIUS_API_KEY\s*=\s*['\"]?([^'\"\s#]+)", line)
            if m:
                return m.group(1)
    raise P7Refusal("NOT_READY", "no HELIUS_API_KEY in the walkers' env file")


class Fetcher:
    """getTransaction with at most `attempts` (P7_RAW_TX_ATTEMPTS = 3) calls per transaction and a fixed request rate. `credits` counts every call made."""

    def __init__(self, rps: float = RPS_DEFAULT, post=None, attempts: int = 3):
        self.rps, self.attempts, self.credits, self.failed_calls = float(rps), int(attempts), 0, 0
        self._post, self._last = post, 0.0

    def _real_post(self, body: bytes) -> dict:
        import urllib.request
        from pump_history_backfill import helius_http_url
        req = urllib.request.Request(helius_http_url(_helius_key()), data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())

    def __call__(self, signature: str):
        """The transaction result, or None once every attempt failed or returned null (fetch_failed)."""
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getTransaction", "params": [signature, GET_TX_OPTS]}).encode()
        post = self._post or self._real_post
        for _ in range(self.attempts):
            wait = self._last + 1.0 / self.rps - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.credits += 1
            try:
                d = post(body)
            except Exception:  # noqa: BLE001 - a failed attempt; the message may hold the URL, so it is never printed
                self.failed_calls += 1
                continue
            res = d.get("result") if isinstance(d, dict) else None
            if res is not None and isinstance(res.get("meta"), dict):
                return res
            self.failed_calls += 1
        return None


def decode(signature: str, res: dict) -> dict:
    """{event_index: raw record} by records_from_logs(event_v=True) at the pinned blob (checked by the caller before any fetch)."""
    from observe.trade_decode import records_from_logs
    recs = records_from_logs(res["meta"].get("logMessages") or [], slot=res.get("slot"), signature=signature, t_recv_ms=0,
                             commitment="confirmed", feed="exp025_p7", event_v=True)
    return {r["event_index"]: r for r in recs}


# ------------------------------------------------------------------------------------------------------------------- line 2
def line2_one(row: dict, adapter: dict | None, v0: int, L2=None) -> tuple:
    """(side, hit, cause) of one sampled print of the 1,000 for the fee-tier line. Sells: tools/boostfloor_inputs.py tier_lines' form,
    unchanged. Buys: Amendment 7 B through p7_line2_amend (L2; loaded with its sha256 check when not given).
    side None: neither a sell nor a buy. hit None: a buy in neither denominator, cause one of L2.LINE2_BUY_EXCLUSIONS (excluded) or
    LINE2_SKIPPED (the skip rule)."""
    side = row.get("side")
    if side not in ("sell", "buy"):
        return None, False, None
    if side == "buy":
        L2 = L2 if L2 is not None else load_line2_amend()
        why = L2.line2_buy_exclusion(row)                     # the sampled raw row, before any adapter check
        if why is not None:
            return side, None, why
    if adapter is None:
        return side, False, "no_adapter_row"
    if adapter.get("pool") != row.get("pool") or adapter.get("side") != side:
        return side, False, "identity_mismatch"
    vals = [adapter.get(k) for k in ("quote_reserve", "base_reserve", "token_raw", "sol_lamports")]
    if any(not (isinstance(x, int) and not isinstance(x, bool)) for x in vals):
        return side, False, "field_missing"
    q, b, tok, sol = vals
    Q = q + v0
    if side == "buy":
        if L2.line2_buy_skip(sol, tok, b):
            return side, None, LINE2_SKIPPED
        return side, L2.line2_buy_hit(sol, tok, Q, b, L2.tier_ppm(R.fee(Q, b))), None
    if b <= 0 or b + tok <= 0:
        return side, False, "degenerate"
    f = float(R.fee(Q, b))
    E = Q * tok / (b + tok) * (1 - f)
    return side, bool(E > 0 and abs(sol - E) <= LINE2_BP * E), None


def line2_tally(main: list, adapters: dict, v0_by_pool: dict, L2=None) -> tuple:
    """Line 2 over the main 1,000: every print through line2_one, counted by p7_line2_amend.line2_tally. Returns (tally, per-print outcomes)."""
    L2 = L2 if L2 is not None else load_line2_amend()
    per = [line2_one(r, adapters.get((r["slot"], r["tx_index"], r["event_index"])), v0_by_pool[r["pool"]], L2) for r in main]
    return L2.line2_tally(zip(main, per)), per


# ------------------------------------------------------------------------------------------------------------------- diagnosis
def identity_mismatch_fields(AM, tape_row: dict, raw: dict | None, adapter: dict | None) -> list:
    """Diagnosis only (not deciding; counts): which identity fields differ on a line-1 identity_mismatch, in p7_raw_check's three groups."""
    raw, adapter = raw or {}, adapter or {}
    return ([f"raw_vs_tape:{k}" for k in AM.P7_RAW_IDENTITY_FIELDS if raw.get(k) != tape_row.get(k)]
            + [f"adapter_vs_tape:{k}" for k in AM.P7_RAW_ADAPTER_KEY if adapter.get(k) != tape_row.get(k)]
            + [f"adapter_vs_raw:{k}" for k in AM.P7_RAW_ADAPTER_FIELDS if adapter.get(k) != raw.get(k)])


# ------------------------------------------------------------------------------------------------------------------- the run
def _share(ok, n):
    return (ok / n) if n else None


def run_p7(ctx: dict, fetch, *, frame_fn=None, amend_path: str = AMEND_PATH, decoder_path: str = DECODER_PATH,
           line2_path: str = LINE2_PATH) -> dict:
    """Pins, materialised check, frame and draw, adapter rows, fetch and decode, both lines; writes P7.json and the prints file once."""
    AM = load_amend(amend_path)
    EV = AM.EV
    L2 = load_line2_amend(line2_path)
    check_decoder(decoder_path)
    out_json, out_prints = os.path.join(ctx["out"], "P7.json"), os.path.join(ctx["out"], "p7_prints.jsonl")
    if os.path.exists(out_json) or os.path.exists(out_prints):
        raise P7Refusal("LOCK", f"{out_json} already exists: P7 is drawn once per look")
    check_materialised(ctx)
    v0 = v0_map(ctx["tokens"])
    draw = AM.p7_raw_draw((frame_fn or frame_rows)(ctx, set(v0)), v0)
    full = full_rows(ctx, draw["main"] + draw["topup"])          # the drawn prints, in full, in draw order
    main, topup = full[:len(draw["main"])], full[len(draw["main"]):]
    sample = [("main", r) for r in main] + [("topup", r) for r in topup]
    adapters = adapter_rows(ctx, [r for _, r in sample])
    sigs = []
    for _, r in sample:
        if AM.p7_raw_line(r) is not None and r.get("signature") and r["signature"] not in sigs:
            sigs.append(r["signature"])
    fetched = {}
    for s in sigs:                                             # one fetch per transaction, in sample order
        res = fetch(s)
        fetched[s] = decode(s, res) if res is not None else None
    checked, prints, idm = [], [], {}
    for which, r in sample:
        key = (r["slot"], r["tx_index"], r["event_index"])
        recs = fetched.get(r.get("signature"))
        failed = AM.p7_raw_line(r) is not None and recs is None
        raw = recs.get(r["event_index"]) if recs else None
        res = AM.p7_raw_check(r, raw, adapters.get(key), v0[r["pool"]], fetch_failed=failed)
        checked.append((r, res))
        if res[1] == "unresolved" and res[2] == "identity_mismatch":
            for k in identity_mismatch_fields(AM, r, raw, adapters.get(key)):
                idm[k] = idm.get(k, 0) + 1
        prints.append(dict(draw=which, hour=r["_hour"], slot=r["slot"], tx_index=r["tx_index"], event_index=r["event_index"],
                           signature=r.get("signature"), side=r.get("side"), ix_name=r.get("ix_name"),
                           line1=dict(line=res[0], outcome=res[1], reason=res[2])))
    t1 = AM.p7_raw_tally(checked)
    t2, per2 = line2_tally(main, adapters, v0, L2)
    for p, (side, hit, cause) in zip(prints, per2):       # main prints come first, in order
        p["line2"] = dict(side=side, hit=hit, cause=cause)
    cp = [t1["sell_ok"], t1["sell_n"], t1["buy_ok"], t1["buy_n"]]
    fee = [t2["sell_ok"], t2["sell_n"], t2["buy_ok"], t2["buy_n"]]
    os.makedirs(ctx["out"], exist_ok=True)
    with open(out_prints, "x") as fh:
        for p in prints:
            fh.write(json.dumps(p, sort_keys=True) + "\n")
    counts = dict(frame_n=draw["frame_n"], keyless_dropped=ctx.get("keyless", 0), sample_n=len(main), topup_n=len(topup), transactions=len(sigs),
                  line1=dict(sell_n=t1["sell_n"], sell_ok=t1["sell_ok"], sell_share=_share(t1["sell_ok"], t1["sell_n"]), buy_n=t1["buy_n"],
                             buy_ok=t1["buy_ok"], buy_share=_share(t1["buy_ok"], t1["buy_n"]), excluded=t1["excluded"],
                             excluded_by=t1["excluded_by"], ix_not_listed_by=t1["ix_not_listed_by"],
                             buy_exact_quote_in_by=t1["buy_exact_quote_in_by"], buy_by_ix=t1["buy_by_ix"], unresolved=t1["unresolved"],
                             identity_mismatch_by_field=dict(sorted(idm.items())),
                             pass_=bool(EV.p7_cp_pass(*cp))),
                  line2=dict(sell_n=t2["sell_n"], sell_ok=t2["sell_ok"], sell_share=_share(t2["sell_ok"], t2["sell_n"]), buy_n=t2["buy_n"],
                             buy_ok=t2["buy_ok"], buy_share=_share(t2["buy_ok"], t2["buy_n"]), neither=t2["neither"], buy_skipped=t2["buy_skipped"],
                             miss_by=t2["miss_by"],
                             buy_by_ix_name=t2["buy_by_ix_name"], buy_excluded=t2["buy_excluded"],
                             buy_excluded_by=t2["buy_excluded_by"], buy_excluded_by_name=t2["buy_excluded_by_name"],
                             pass_=bool(EV.p7_pass(*fee))),
                  credits=dict(get_transaction_calls=getattr(fetch, "credits", None), failed_calls=getattr(fetch, "failed_calls", None),
                               rps=getattr(fetch, "rps", None), unit=f"exp025_p7 {ctx['mode']} (getTransaction, Helius key of {HELIUS_ENV})"))
    rec = dict(schema=ctx["schema"], look=ctx["look"], mode=ctx["mode"], cp=cp, fee=fee, p7_all_pass=bool(EV.p7_all_pass(tuple(cp), tuple(fee))),
               counts=counts, prints=out_prints, prints_sha256=_sha256(out_prints),
               pins=dict(p7_buy_amend_sha256=_sha256(amend_path), event_v_map_sha256=AM.EVENT_V_MAP_SHA256, decoder_blob=git_blob(decoder_path),
                         p7_line2_amend_sha256=_sha256(line2_path), driver_blob=git_blob(os.path.abspath(__file__)),
                         look_assembly_sha256=_sha256(ctx["assembly"]) if ctx.get("assembly") else None),
               written_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    with open(out_json, "x") as fh:
        json.dump(rec, fh, indent=1, sort_keys=True)
    return rec


def main(argv=None, *, fetch=None, now: int | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=("run", "e0"))
    ap.add_argument("--look", type=int, choices=(1, 2))
    ap.add_argument("--rps", type=float, default=RPS_DEFAULT)
    a = ap.parse_args(argv)
    try:
        if a.mode == "run":
            if a.look is None:
                raise P7Refusal("USAGE", "run needs --look")
            ctx = look_context(a.look, now)                   # SEAL first: refuses before any October path is opened
        else:
            ctx = e0_context()
        rec = run_p7(ctx, fetch or Fetcher(rps=a.rps))
    except R.Refusal as e:
        print(json.dumps({"refused": e.code, "reason": str(e)[:300]}))
        return 2
    except Exception as e:                                # counts only (section 10, Amendment 1): no message, no traceback
        print(json.dumps({"refused": "CRASH", "type": type(e).__name__}))
        return 3
    print(json.dumps(rec["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
