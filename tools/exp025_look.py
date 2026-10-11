#!/usr/bin/env python3
"""EXP-025 (C1-NF) P4: the one locked job of a look (section 10, "In each locked job"; section 11.3; section 11.4).

    python3 tools/exp025_look.py ready --look 1 --decoder-blobs J --p7 P7.json --oracle-live F [...]   # phase 0 + R12/R1/R2/R6, no lock
    python3 tools/exp025_look.py run   --look 1 --decoder-blobs J --p7 P7.json --oracle-live F [...]   # the locked read, once

The ledger (LOOK_READS.jsonl), the look's O, the FINAL marker and ledger, the E0 dir, the P2 universe, the R1 cross-check record and the oracle
staleness limit are fixed in code (no CLI override: an override would allow a second read on another ledger / O, quant-proof r3 item 1).
--decoder-blobs, --p7, --oracle-live and --oracle-replay stay until P5 and P7 are pinned; the `lock` event records the sha256 of each of
those files, of the R1 cross-check record, of look_assembly.json and of every manifest (lock_inputs).

Run it with the audit venv (numpy, pandas, duckdb); the two retrains run as subprocesses in the ML venv (numpy, lightgbm).

Order. Nothing below prices a trade before every refusal that can be decided without a price has been decided.
  Gate (not terminal; reads only LOOK_READS.jsonl and the look's READ.lock): Look 2 only, Look 1's terminal LOOK_READS event is
    `not_decidable` or a `read` FAIL (NOT_READY if Look 1 has none; LOOK2 if Look 1 passed: Look 2 never runs, section 3); then no lock yet
    (LOCK: a spent look is never run, or refused, twice).
  R8 (`run` only, terminal, before every readiness check, quant-proof r4): now after the look's deadline (section 3 table) takes the lock and
    writes `not_decidable` R8. Section 3: "a look not run by its deadline is NOT_DECIDABLE"; 11.4 R8: "P2 to P6 are not all met by their
    deadlines". It reads the clock and LOOK_READS only (no tape, manifest or sealed file), so it needs no FINAL marker. A readiness item still
    unmet at the deadline (FINAL marker, R13, assembly, cross-check, --oracle-live, `v_ok`) therefore ends the look instead of leaving it with
    no terminal event, and Look 1's R8 opens Look 2's gate.
  Phase 0, readiness (not terminal, code NOT_READY unless named): section 0 lines, EXP file clean against HEAD, SHA256SUMS pins, the FINAL
    marker and ledger entry and the end of the look's last allowlisted hour (LookGuard), R13 decoder blobs and E0 records, the look assembly
    record of the adapter's look driver (NOT_READY if absent or invalid), --oracle-live given (NOT_READY: a forgotten flag must not turn into R7
    and burn the look), the pinned patch applied (applied sha256 pinned), the adapter's per-print V flag `v_ok` in every V-covered tape hour
    (NOT_READY: R3 and section 6 are per print; no manifest or hour proxy), the R1 cross-check record (NOT_READY if absent). None of these
    takes the lock.
  Phase 1, outcome-blind data refusals before the lock (terminal: a refusal takes the lock and writes `not_decidable`, so the look cannot be
    re-run on the same hours, section 11.5): R12 on the materialised tape directory
    (every O/tape/{trades,creates,migrations}/*.parquet hour allowlisted: pass A opens D T00 .. D+2 T01 as a black box), R12 (manifest block
    vs the allowlist), R1 hour level (bad + unwalked hours + forward-1002ev hours whose raw-JSONL cross-check 1:1 match rate is below 99.5% or
    missing, P6 item 3), R2 (PDA match, the
    adapter's october_vmap record), R6 (slot-time fallback hours of the pinned pass A's clock rule); `ready` stops here. Then R14 (the P7
    record), 10_meta (universe only), R13 `mid` continuity against P2, R7 (pick oracle before pass A, booleans only; a constructor error or a
    stale live source is R7 too), R3 (per-print V coverage from `v_ok` on the PumpSwap prints of the look universe's canonical pools, hours
    [V_COVER_START, hours_end)).
  Lock (O_EXCL READ.lock + LOOK_READS.jsonl `lock`). After it there is no resume.
  Phase 2, inside the lock: pinned pass A per graduation day 10-09 .. end-1, 12_passC, 14_export (labels exist only from here), the rows file,
    the 11.3 precount (exploration-only model; counts only) and R4, the labelled daily retrain, R1 attempt exclusion (> 5% refuses), then
    exp025_read.read_after_lock: the `decisions` event (md5 before any price), R9, pricing, R11, section 7, report-only. Any refusal or crash
    here writes `not_decidable`, a LOCK refusal included.

Adapter interface (#563, tools/exp025_adapter.py). The look driver writes <O>/look_assembly.json:
    {"schema": "exp025_look_assembly_v1", "look": "look1", "manifests": [<convert() manifest.json per block>], "r2": <october_vmap's r2 dict>,
     "append_tokens": <append_tokens' dict>}
  and the hunt layout under <O>/tape (trades carry `block` and `hour`; PumpSwap quote_reserve is event-V mapped where the print had V and the
  pool a V0). A manifest's trade file record carries pumpswap_rows / pumpswap_rows_with_v: that share covers every PumpSwap print of every pool
  and is recorded as `manifest_v_share` only; it decides nothing. R3 and pricing read the per-print column
  `v_ok = venue = 'pumpswap' AND virtual_quote_reserves IS NOT NULL AND v0 IS NOT NULL` (asked of #563). Until the trades carry it, `ready` and
  `run` refuse NOT_READY; neither runs a look on the manifest R3 or the hour-level V proxy.

Audit trail (section 11.5, forking paths). Every exit that does not spend the look (a `ready` preview, passing or refused, a readiness refusal
of `run`, a tool crash before the lock) appends a `ready` event to LOOK_READS.jsonl with its mode, code and counts.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exp025_read as R  # noqa: E402

AUDIT_PY = "/data/mal/audit-1008/venv/bin/python"
ML_PY = "/data/mal/venv/bin/python"
P2_UNIVERSE = "/data/mal/hunt-1008/c1nf-p2/work/universe.parquet"
ASSEMBLY_SCHEMA = "exp025_look_assembly_v1"
MANIFEST_SCHEMA = "exp025_adapter_manifest_v1"
V_COVER_START = "2026-10-09T00"          # P6 item 2 (the adapter's V_COVER_START)
BLOCK_OF_SOURCE = {"forward-1002": "forward-1002", "forward-1002ev": "forward-1002ev", "forward-1016": "walk2"}   # read-tool source -> adapter block
R2_MIN = R3_MIN = 0.95                    # equal to tools/exp025_adapter.R2_MIN / R3_MIN (a test checks it when the adapter is importable)
LANDING_PAD_S = 1.9 + 0.5 + 300 + 60     # R1: hours through landing (round(1.9 / s) * s can be ~half a slot over 1.9 s: pad one slot) + 300 + 60 s
LEDGER_COLS = ("wb5_n", "wb5_new", "ws5_n", "ws5_new")
R1_CROSSCHECK = "/data/mal/exp025/r1_crosscheck/forward-1002ev.json"   # P6 item 3's record (pinned path; written after the FINAL)
CROSSCHECK_SCHEMA = "exp025_r1_crosscheck_v1"
XCHECK_RANGE = ("2026-10-09T00", "2026-10-16T01")                      # forward-1002ev's hours: an hour below XCHECK_MIN, or missing, is bad
XCHECK_MIN = 0.995
TAPE_KINDS = ("trades", "creates", "migrations")   # pass A: 5-minute buyer / seller wallets and the share unknown to the wallet ledger


# ----------------------------------------------------------------------------------------------------------------- adapter outputs
def load_assembly(look: int, o_dir: str):
    p = os.path.join(o_dir, "look_assembly.json")
    if not os.path.exists(p):
        raise R.Refusal("NOT_READY", f"{p} missing: the adapter's look driver has not assembled look {look}")
    A = json.load(open(p))
    if A.get("schema") != ASSEMBLY_SCHEMA or A.get("look") != f"look{look}":
        raise R.Refusal("NOT_READY", f"{p}: schema {A.get('schema')!r} / look {A.get('look')!r}")
    mans = []
    for m in A.get("manifests", []):
        d = json.load(open(m))
        if d.get("schema") != MANIFEST_SCHEMA:
            raise R.Refusal("NOT_READY", f"{m}: not an adapter manifest")
        mans.append(d)
    if not mans:
        raise R.Refusal("NOT_READY", f"{p} lists no manifest")
    return A, mans


def load_crosscheck(path: str) -> dict:
    """P6 item 3 / section 11.4 R1: the raw-JSONL 1:1 cross-check of forward-1002ev against forward-1002, per hour. Schema
    {"schema": "exp025_r1_crosscheck_v1", "hours": {"YYYY-MM-DDTHH": {"match_rate": float} | float}}. NOT_READY if absent or invalid.
    Returns {hour: rate}; a non-numeric rate is kept as None (a bad hour)."""
    if not path or not os.path.exists(path):
        raise R.Refusal("NOT_READY", f"R1 cross-check record {path} is missing (P6 item 3)")
    d = json.load(open(path))
    if d.get("schema") != CROSSCHECK_SCHEMA or not isinstance(d.get("hours"), dict):
        raise R.Refusal("NOT_READY", f"{path}: schema {d.get('schema')!r}, not an R1 cross-check record")
    out = {}
    for h, v in d["hours"].items():
        r = v.get("match_rate") if isinstance(v, dict) else v
        out[h] = float(r) if isinstance(r, (int, float)) and not isinstance(r, bool) else None
    return out


def tape_outside_allowlist(look: int, o_dir: str) -> list:
    """R12 on the materialised tape directory: every O/tape/{trades,creates,migrations}/*.parquet hour must be allowlisted for the look under
    some source (pass A opens D T00 .. D+2 T01 as a black box; at Look 1, D = 10-16 reaches 10-18T01, and 10-17T02 on is walk 2's counted
    hours). Returns the offending files (kind/name); a name that is not an hour counts."""
    srcs = sorted({src for src, _, _ in R.LOOKS[look]["allow"]})
    out = []
    for kind in TAPE_KINDS:
        d = os.path.join(o_dir, "tape", kind)
        for n in (sorted(os.listdir(d)) if os.path.isdir(d) else ()):
            if not n.endswith(".parquet"):
                continue
            h = n[:-8]
            try:
                ok = R.hour_str(R.ep(h)) == h and any(R.hour_allowed(look, src, h) for src in srcs)
            except ValueError:
                ok = False
            if not ok:
                out.append(f"{kind}/{n}")
    return out


def look2_gate(ledger: str) -> str:
    """Section 3: Look 2 runs only after Look 1 ended NOT_DECIDABLE or read FAIL. NOT_READY while Look 1 has no terminal event; LOOK2 (Look 2
    never runs) if Look 1 passed or its terminal events are ambiguous. Phase 0: no lock is taken."""
    term = [e for e in R.ledger_events(ledger, 1) if e["event"] in R.TERMINAL_EVENTS]
    if not term:
        raise R.Refusal("NOT_READY", "Look 1 has no terminal event in LOOK_READS: Look 2 runs only after Look 1 is NOT_DECIDABLE or FAIL")
    e = term[0]
    if len(term) == 1 and e["event"] == "not_decidable":
        return "look1 not_decidable"
    if len(term) == 1 and e["event"] == "read" and e.get("verdict") == "FAIL":
        return "look1 FAIL"
    raise R.Refusal("LOOK2", f"Look 1 ended {[(x['event'], x.get('verdict')) for x in term]}: Look 2 never runs (section 3)")


def _sha_or_none(p):
    return R.sha256(p) if p and os.path.isfile(p) else None


def lock_inputs(a, O: str, A: dict) -> dict:
    """The `lock` event's record of every input file the runner was given: sha256 per path (None when absent)."""
    files = dict(decoder_blobs=[a.decoder_blobs], p7=[a.p7], oracle_live=list(a.oracle_live or ()), oracle_replay=list(a.oracle_replay or ()),
                 r1_crosscheck=[a.r1_crosscheck], look_assembly=[os.path.join(O, "look_assembly.json")], manifests=list(A.get("manifests", [])))
    return {k: {str(p): _sha_or_none(p) for p in v if p} for k, v in files.items()}


def hour_status(look: int, mans, xcheck: dict) -> dict:
    """From the adapter manifests: bad hours (strict-line failures of any kind, plus allowlisted hours with no trades file = not walked, plus
    the allowlisted forward-1002ev hours [XCHECK_RANGE) whose cross-check 1:1 match rate (load_crosscheck) is below XCHECK_MIN or missing),
    V-incomplete hours (from V_COVER_START: event_v false, counts missing, or pumpswap_rows_with_v < pumpswap_rows), and the manifest's V share
    over all PumpSwap prints of every pool (information only: R3 is r3_coverage, on the universe's canonical pools, per print).
    R12: a trades file from a block other than the allowlist's for its hour, or an hour outside the allowlist, refuses."""
    allow = dict((h, src) for src, h in R.allowlisted_hours(look))
    files, bad = {}, set()
    for m in mans:
        for b in m.get("bad", []):
            bad.add(b["hour"])
        for f in m.get("files", []):
            if f.get("kind") != "trades":
                continue
            h = f["hour"]
            if h not in allow:
                raise R.Refusal("R12", f"manifest holds {h}, outside look {look}'s allowlist")
            if m.get("block") != BLOCK_OF_SOURCE[allow[h]]:
                raise R.Refusal("R12", f"{h} came from block {m.get('block')!r}; the allowlist says {allow[h]}")
            files[h] = (m, f)
    unwalked = sorted(h for h in allow if h not in files and h not in bad)
    xbad = set()
    for h in allow:
        if R.ep(XCHECK_RANGE[0]) <= R.ep(h) < R.ep(XCHECK_RANGE[1]):
            r = xcheck.get(h)
            if r is None or not r >= XCHECK_MIN:
                xbad.add(h)
    vmiss, n_ps, n_v = set(), 0, 0
    for h, (m, f) in files.items():
        if R.ep(h) < R.ep(V_COVER_START):
            continue
        ps, wv = f.get("pumpswap_rows"), f.get("pumpswap_rows_with_v")
        if not m.get("event_v") or ps is None or wv is None:
            vmiss.add(h)
            continue
        n_ps += int(ps); n_v += int(wv)
        if int(wv) < int(ps):
            vmiss.add(h)
    return dict(bad=sorted(bad | set(unwalked) | xbad), unwalked=unwalked, xcheck_bad=sorted(xbad), vmiss=sorted(vmiss), n_ps=n_ps, n_v=n_v,
                manifest_v_share=(n_v / n_ps) if n_ps else None)


def v_hours(look: int):
    return [h for _, h in R.allowlisted_hours(look) if R.ep(h) >= R.ep(V_COVER_START)]


def _tape_files(look: int, tape_dir: str, hours, guard):
    allow = dict((h, src) for src, h in R.allowlisted_hours(look))
    fs = []
    for h in hours:
        guard.check_hour(allow.get(h, "outside"), h)
        f = os.path.join(tape_dir, f"{h}.parquet")
        R.assert_not_closed(f)
        if os.path.exists(f):
            fs.append(f)
    return fs


def _lst(fs) -> str:
    return "['" + "','".join(fs) + "']"


def v_flag_missing(look: int, tape_dir: str, guard) -> list:
    """Readiness: the V-covered tape hours whose trades file has no per-print `v_ok` column. R3 and section 6 need it (NOT_READY, not R3)."""
    import duckdb
    fs = _tape_files(look, tape_dir, v_hours(look), guard)
    if not fs:
        return []
    con = duckdb.connect()
    have = {r[0] for r in con.execute(f"SELECT DISTINCT file_name FROM parquet_schema({_lst(fs)}) WHERE name = 'v_ok'").fetchall()}
    con.close()
    return [os.path.basename(f)[:-8] for f in fs if f not in have]


def r3_coverage(look: int, tape_dir: str, pools, guard) -> dict:
    """Section 11.4 R3: per-print V coverage on the canonical-pool PumpSwap prints of the look universe's pools (10_meta's universe after the
    section 5.3 oracle exclusion), from the adapter's `v_ok`, over the V-covered allowlisted hours [V_COVER_START, hours_end). A print whose
    v_ok is false or null counts as V-less. Counts only, in all and per UTC date."""
    import duckdb
    import pandas as pd
    fs = _tape_files(look, tape_dir, v_hours(look), guard)
    pp = sorted({str(x) for x in pools if x is not None})
    by = {}
    if fs and pp:
        con = duckdb.connect()
        con.register("pp", pd.DataFrame({"pool": pp}))
        df = con.execute(f"""SELECT filename f, count(*) n, sum(CASE WHEN v_ok THEN 1 ELSE 0 END) nv
                             FROM read_parquet({_lst(fs)}, filename=true, union_by_name=true)
                             WHERE venue = 'pumpswap' AND pool IN (SELECT pool FROM pp) GROUP BY 1""").df()
        con.close()
        for f, a, b in zip(df.f, df.n, df.nv):
            x = by.setdefault(os.path.basename(f)[:10], [0, 0]); x[0] += int(a); x[1] += int(b)
    n, nv = sum(a for a, _ in by.values()), sum(b for _, b in by.values())
    return dict(share=(nv / n) if n else None, n=n, n_v=nv,
                by_date={d: dict(prints=a, with_v=b, share=(b / a) if a else None) for d, (a, b) in sorted(by.items())},
                basis="tape v_ok: PumpSwap prints of the look universe's canonical pools, hours [V_COVER_START, hours_end)")


def fallback_hours(look: int, tape_dir: str, guard) -> list:
    """R6: hours of [V_COVER_START, hours_end) for which the pinned pass A's clock rule (11_passA.py: an hour has its own seconds-per-slot only if
    its block_time span is >= 1800 s and its slot rises) gives no value, so pass A uses the nearest hour's. Counts only."""
    import duckdb
    hrs = v_hours(look)
    fs = _tape_files(look, tape_dir, hrs, guard)
    if not fs:
        return list(hrs)
    con = duckdb.connect()
    L = "['" + "','".join(fs) + "']"
    CK = con.execute(f"SELECT block_time bt, min(slot) s FROM read_parquet({L}) WHERE block_time IS NOT NULL GROUP BY 1 ORDER BY 1").df()
    con.close()
    bt, sl = CK.bt.values.astype(np.int64), CK.s.values.astype(np.int64)
    ok = set()
    for h in np.unique(bt // 3600):
        m = (bt // 3600) == h
        if bt[m][-1] - bt[m][0] >= 1800 and sl[m][-1] > sl[m][0]:
            ok.add(int(h) * 3600)
    return [h for h in hrs if R.ep(h) not in ok]


def first_print_ms(look: int, tape_dir: str, pools, guard) -> dict:
    """Section 5.3: each universe pool's first PumpSwap print (ms) on the look's V-covered tape (a universe pool has V0, so its s0 is there)."""
    import duckdb
    import pandas as pd
    fs = _tape_files(look, tape_dir, v_hours(look), guard)
    if not fs:
        return {}
    con = duckdb.connect()
    con.register("pp", pd.DataFrame({"pool": sorted(set(pools))}))
    L = "['" + "','".join(fs) + "']"
    df = con.execute(f"""SELECT pool, min(block_time) bt FROM read_parquet({L}) WHERE venue = 'pumpswap' AND pool IN (SELECT pool FROM pp)
                         GROUP BY pool""").df()
    con.close()
    return {p: int(b) * 1000 for p, b in zip(df.pool, df.bt)}


# ----------------------------------------------------------------------------------------------------------------- rows, precount, R1 attempts
def build_rows(october_npz: str, universe_parquet: str, cand_dir: str, days):
    """The read's rows from 14_export's october.npz (t, mid, row order = idx) + the look universe (mint, pool, v, g, gday, cbt) + pass A's
    out/candx/<D>.parquet h_top1 / h_top5 in float64 (14_export's own row source, pass A's h_top1 carried by 12_passC; the export's F is
    float32 and the cap compares h_top1 <= 0.5 on pass A's value).
    Returns (rows, gtime {mid: graduation epoch})."""
    import pandas as pd
    z = np.load(october_npz, allow_pickle=False)
    rows = pd.DataFrame({"idx": np.arange(len(z["t"]), dtype=np.int64), "t": z["t"].astype(np.int64), "mid": z["mid"].astype(np.int64)})
    U = pd.read_parquet(universe_parquet, columns=["mid", "mint", "pool", "v", "g", "gday", "cbt"])
    rows = rows.merge(U, on="mid", how="left", validate="many_to_one")
    if rows.mint.isna().any():
        raise R.Refusal("R13", f"{int(rows.mint.isna().sum())} export rows have a mid outside the look universe")
    parts = []
    for d in days:
        f = os.path.join(cand_dir, f"{d}.parquet")
        if os.path.exists(f):
            c = pd.read_parquet(f)
            parts.append(c[["mid", "t", "h_top1", "h_top5"] + [k for k in LEDGER_COLS if k in c.columns]])
    C = pd.concat(parts, ignore_index=True)
    C = C.rename(columns={k: "f_" + k for k in ("h_top1", "h_top5") + LEDGER_COLS}).astype({"mid": np.int64, "t": np.int64})
    rows = rows.merge(C, on=["mid", "t"], how="left", validate="one_to_one", indicator=True)
    if (rows._merge != "both").any():
        raise R.Refusal("R13", f"{int((rows._merge != 'both').sum())} export rows have no pass-A candidate row")
    rows = rows.drop(columns="_merge").sort_values("idx").reset_index(drop=True)
    return rows, {str(int(m)): int(g) for m, g in zip(U.mid, U.g)}


def attempt_hours(cbt, t) -> list:
    """R1: the hours an attempt needs, from its mint's create hour through the hour holding landing + 300 s + 60 s."""
    h0 = int(cbt) // 3600 * 3600
    return [R.hour_str(h) for h in range(h0, int(t + LANDING_PAD_S) // 3600 * 3600 + 1, 3600)]


def r1_attempt_keep(look: int, rows, kept, bad) -> np.ndarray:
    """R1 attempt level. The refusal (> 5% of the kept attempts need a bad hour) is decided on the kept attempts; the returned mask (False =
    excluded and counted) covers every row, so the report-only books exclude the same attempts."""
    need = [attempt_hours(c, t) for c, t in zip(rows.cbt.values, rows.t.values)]
    R.r1_bad_hours(look, bad, [a for a, k in zip(need, kept) if k])
    b = set(bad)
    return np.array([not (set(a) & b) for a in need], bool)


def ledger_coverage(X):
    """Section 11.3 / section 8: the share of the 5-minute buyers and sellers known to the wallet ledger, from pass A's wb5_ / ws5_ columns
    (`n` wallets, `new` = the share of them not in the ledger; a NaN `new` with n > 0 counts as unknown). Never gates (section 11.2).
    Returns (share or None, wallets, rows with no ledger value)."""
    tot = np.zeros(len(X)); kn = np.zeros(len(X)); have = np.zeros(len(X), bool)
    for s in ("wb5", "ws5"):
        if f"f_{s}_n" not in X:
            continue
        n = X[f"f_{s}_n"].values.astype(float)
        new = X[f"f_{s}_new"].values.astype(float) if f"f_{s}_new" in X else np.full(len(X), np.nan)
        ok = np.isfinite(n)
        have |= ok
        tot += np.where(ok, n, 0.0)
        kn += np.where(ok & (n > 0), n * (1.0 - np.nan_to_num(new, nan=1.0)), 0.0)
    T = float(tot.sum())
    return ((float(kn.sum()) / T) if T > 0 else None), int(round(T)), int((~have).sum())


def precount(look: int, rows, preds, U, excluded_by_gday: dict, hs: dict, fb: list, r2: dict, r3: dict) -> dict:
    """Section 11.3: per UTC date of the window, counts and shares only (PDA match is the adapter's look-level record; V coverage is R3's
    tape count; ledger coverage is pass A's wallet columns). No price, fill, exit, pnl, mean, CI or day sign."""
    import pandas as pd
    cap = R._cap()
    cfg = json.load(open(os.path.join(R.ART, "rule.json")))
    L = R.LOOKS[look]
    P = pd.DataFrame({"t": np.asarray(preds["t"], np.int64), "mid": np.asarray(preds["mid"], np.int64), "pred": np.asarray(preds["pred"], float),
                      "s1": np.asarray(preds["s1"], bool)})
    lcols = [c for c in rows.columns if c[2:] in LEDGER_COLS]
    X = rows[["t", "mid", "gday", "f_h_top1"] + lcols].merge(P, on=["t", "mid"], how="left", validate="one_to_one")
    X["date"] = [R.date_str(x) for x in X.t]
    sel = X.pred.notna().values & (X.pred.values > cfg["threshold"])
    X["sel"] = sel
    X["kept"] = cap.apply_cap_before_book(X.f_h_top1.values, sel)
    dates = [R.date_str(x) for x in range(R.ep(L["start"]), R.ep(L["end"]), 86400)]
    out = {}
    for d in dates:
        m = X.date.values == d
        lc, lw, lno = ledger_coverage(X[m])
        out[d] = dict(graduations=int((U.gday == d).sum() + excluded_by_gday.get(d, 0)), universe_rows=int(m.sum()), stage1=int(X.s1.values[m].sum()),
                      selections=int(X.sel.values[m].sum()), kept=int(X.kept.values[m].sum()), oracle_dropped=int(excluded_by_gday.get(d, 0)),
                      bad_hours=sum(1 for h in hs["bad"] if h.startswith(d)), fallback_hours=sum(1 for h in fb if h.startswith(d)),
                      v_coverage=r3["by_date"].get(d, {}).get("share"), ledger_coverage=lc,
                      ledger_coverage_kept=ledger_coverage(X[m & X.kept.values])[0], ledger_wallets=lw, rows_without_ledger=lno)
    win = np.isin(X.date.values, dates)
    shares = dict(pda_match=r2.get("share"), v_coverage=r3["share"], v_prints=r3["n"], v_basis=r3["basis"],
                  ledger_coverage=ledger_coverage(X[win])[0], ledger_basis="pass A wb5_/ws5_: 5-minute buyers and sellers known to the wallet ledger",
                  manifest_v_share=hs.get("manifest_v_share"))
    return dict(look=look, dates=out, shares=shares)


# ----------------------------------------------------------------------------------------------------------------- steps (injectable for tests)
class Steps:
    """The real side effects. Tests replace these with fixtures; the order and the refusals live in run_look."""

    def __init__(self, a):
        self.a = a

    def preflight(self, look: int):
        a = self.a
        R.section0_lines(open(R.EXP_FILE).read()); R.exp_clean_against_head(); R.check_pins()
        guard = R.LookGuard(look, a.now, a.final_marker, a.final_ledger)
        R.check_decoder_blobs(a.decoder_blobs); R.check_e0_records(a.e0_dir)
        return guard

    def apply_patch(self, look: int, W: str):
        return R.apply_look_patch(look, W)

    def run(self, cmd, log):
        with open(log, "a") as f:
            f.write("$ " + " ".join(cmd) + "\n"); f.flush()
            r = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT)
        if r.returncode != 0:
            raise R.Refusal("RUN", f"{os.path.basename(cmd[1])} exited {r.returncode} (log {log})")

    def retrain(self, look: int, october: str, gtime: str, out: str, exploration_only: bool, log: str):
        cmd = [ML_PY, os.path.join(R.ROOT, "tools", "exp025_read.py"), "retrain", "--look", str(look), "--october", october, "--gtime", gtime,
               "--out", out] + (["--exploration-only"] if exploration_only else [])
        self.run(cmd, log)
        return dict(np.load(out, allow_pickle=False))

    def fallback_hours(self, look, tape_dir, guard):
        return fallback_hours(look, tape_dir, guard)

    def first_print_ms(self, look, tape_dir, pools, guard):
        return first_print_ms(look, tape_dir, pools, guard)

    def v_flag_missing(self, look, tape_dir, guard):
        return v_flag_missing(look, tape_dir, guard)

    def r3_coverage(self, look, tape_dir, pools, guard):
        return r3_coverage(look, tape_dir, pools, guard)

    def oracle(self):
        """The section 5.3 pick oracle. run_look refuses NOT_READY in phase 0 when --oracle-live is absent. Here a constructor error, or a live
        source that is stale or has never beaten, is R7 (section 11.4), the same as a missing or non-boolean answer in oracle_exclude."""
        a = self.a
        try:
            from cap_pick_oracle import PickOracle
            kw = {} if a.oracle_stale_s is None else dict(stale_s=a.oracle_stale_s)
            o = PickOracle(a.oracle_live, a.oracle_replay or (), final_marker=a.final_marker, **kw)
            st = o.staleness_s()
        except Exception as e:  # noqa: BLE001 - every oracle construction error is R7
            raise R.Refusal("R7", f"pick oracle could not be built: {type(e).__name__}: {e}"[:300])
        if st is None or st > o.stale_s:
            raise R.Refusal("R7", f"pick oracle stale at start: staleness {st} s, limit {o.stale_s} s")
        return o

    price_fn = staticmethod(R.price_rows)


# ----------------------------------------------------------------------------------------------------------------- the runner
def _p7(path):
    if not path or not os.path.exists(path):
        raise R.Refusal("R14", "P7 record missing")
    d = json.load(open(path))
    R.r14_p7(tuple(d["cp"]), tuple(d["fee"]))
    return d


def _ready_event(ledger: str, look: int, lock: bool, rec: dict, code, reason: str) -> None:
    """Section 11.5 (forking paths): a `ready` line for every exit that does not spend the look."""
    R.ledger_event(ledger, look, "ready", mode="run" if lock else "ready", ok=code is None, code=code, reason=reason[:500], phase=rec.get("phase"),
                   hours=rec.get("hours"), r2_share=rec.get("r2_share"), applied_sha256=rec.get("applied_sha256"))


def run_look(look: int, a, steps=None, lock: bool = True) -> dict:
    """lock=False is `ready`: phase 0 and R12 / R1 / R2 / R6, no lock, no 10_meta, no oracle, no R3. Returns the record (also printed by main).
    Every exit that does not spend the look appends a `ready` event to the ledger; a spent look's events are `lock` and `read` / `not_decidable`."""
    S = steps or Steps(a)
    rec = dict(look=look, phase="readiness")
    try:
        out = _run_look(look, a, S, lock, rec)
    except Exception as e:  # noqa: BLE001 - recorded, then re-raised unchanged
        _ready_event(a.ledger, look, lock, rec, getattr(e, "code", "ERROR"), f"{type(e).__name__}: {e}")
        raise
    if not lock:
        _ready_event(a.ledger, look, lock, rec, None, "")
    return out


def _run_look(look: int, a, S, lock: bool, rec: dict) -> dict:
    L = R.LOOKS[look]; O = a.o_dir or L["O"]; ledger = a.ledger
    log = os.path.join(O, "run_look.log")
    out_path = os.path.join(O, f"look{look}_result.json")
    # ---- gate (not terminal): LOOK_READS and READ.lock only
    if look == 2:
        rec["look2_gate"] = look2_gate(ledger)                                         # section 3: only after Look 1 NOT_DECIDABLE / FAIL
    if os.path.exists(os.path.join(O, "READ.lock")) or any(e["event"] == "lock" for e in R.ledger_events(ledger, look)):
        raise R.Refusal("LOCK", f"look {look} is already locked (no resume)")
    # ---- R8 (`run` only, terminal): before every readiness check, so a readiness item unmet at the deadline still ends the look (r4)
    if lock:
        now = a.now if a.now is not None else int(time.time())
        if now > R.ep(L["deadline"]):
            rec["phase"] = "deadline"
            return _not_decidable(look, ledger, O, False, rec, "R8", f"now {R.hour_str(now)}Z is after look {look}'s deadline {L['deadline']}Z",
                                  out_path)
    # ---- phase 0: readiness (not terminal)
    guard = S.preflight(look)
    A, mans = load_assembly(look, O)                                                   # NOT_READY if absent or invalid
    xc = load_crosscheck(a.r1_crosscheck)                                              # NOT_READY if absent (P6 item 3)
    if not getattr(a, "oracle_live", None):
        raise R.Refusal("NOT_READY", f"--oracle-live is missing: look {look} holds walk-2 hours, so the section 5.3 pick oracle is required")
    W = os.path.join(O, "work_read")
    rec["applied_sha256"] = S.apply_patch(look, W)
    tape = os.path.join(O, "tape", "trades")
    nov = S.v_flag_missing(look, tape, guard)
    if nov:
        raise R.Refusal("NOT_READY", f"{len(nov)} V-covered tape hours have no per-print v_ok column (first {nov[0]}): R3 and section 6 need it")
    _, cmds = R.pipeline_commands(look)
    took = False
    try:
        # ---- phase 1: outcome-blind data refusals (terminal; R8 ran before phase 0)
        rec["phase"] = "pre-lock refusals"
        outside = tape_outside_allowlist(look, O)                                      # R12 on the materialised tape
        if outside:
            raise R.Refusal("R12", f"{len(outside)} tape files outside look {look}'s allowlist (first {outside[0]}): pass A would open them")
        hs = hour_status(look, mans, xc)                                               # R12 on the manifests
        rec["hours"] = dict(bad=len(hs["bad"]), unwalked=len(hs["unwalked"]), xcheck_bad=len(hs["xcheck_bad"]), v_incomplete=len(hs["vmiss"]),
                            manifest_v_share=hs["manifest_v_share"])
        R.r1_bad_hours(look, hs["bad"], [])                                            # R1, hour level
        fb = S.fallback_hours(look, tape, guard)
        rec["hours"]["fallback"] = len(fb); rec["hours"]["v_hours"] = len(v_hours(look)); rec["r2_share"] = float(A["r2"]["share"])
        R.share_refusals(rec["r2_share"], None, len(fb), len(v_hours(look)))          # R2, R6 (R3 below, on the universe's pools)
        if not lock:
            rec["ready"] = True
            return rec
        p7 = _p7(a.p7)                                                                 # R14
        rec["p7"] = {k: p7[k] for k in ("cp", "fee") if k in p7}
        os.makedirs(O, exist_ok=True)
        S.run(cmds[0], log)                                                            # 10_meta: the universe (no label)
        upath = os.path.join(O, "work", "universe.parquet")
        rec["mid_checked"] = R.check_mid_continuity(upath, a.p2_universe)              # R13
        import pandas as pd
        U = pd.read_parquet(upath)
        fp = S.first_print_ms(look, tape, U.pool.values, guard)
        keep, n_ex = R.oracle_exclude(list(U.mint), [fp.get(p) for p in U.pool], S.oracle())   # R7, booleans only, before pass A
        ex_by_gday = U.gday[~keep].value_counts().to_dict()
        if n_ex:
            os.replace(upath, os.path.join(O, "work", "universe_pre_oracle.parquet"))
            U[keep].to_parquet(upath, index=False)
        rec["oracle_excluded"] = n_ex
        r3 = S.r3_coverage(look, tape, U[keep].pool.values, guard)                    # R3: per print, v_ok, the universe's canonical pools
        rec["r3"] = {k: r3[k] for k in ("share", "n", "n_v")}
        R.r3_v_coverage(r3["share"])
        # ---- the lock
        R.take_lock(look, ledger, O, inputs=lock_inputs(a, O, A))
        took = True
        rec["phase"] = "locked"
        # ---- phase 2: labels exist from here
        for c in cmds[1:]:
            S.run(c, log)                                                              # 11_passA per day, 12_passC, 14_export
        days = [R.date_str(x) for x in range(R.ep("2026-10-09T00"), R.ep(L["end"]), 86400)]
        rows, gt = build_rows(os.path.join(O, "ml", "october.npz"), upath, os.path.join(O, "out", "candx"), days)
        gpath = os.path.join(O, "ml", "gtime.json"); json.dump(gt, open(gpath, "w"))
        pe = S.retrain(look, os.path.join(O, "ml", "october.npz"), gpath, os.path.join(O, "ml", "preds_exploration_only.npz"), True, log)
        pc = precount(look, rows, pe, U[keep], ex_by_gday, hs, fb, A["r2"], r3)
        json.dump(pc, open(os.path.join(O, "precount.json"), "w"), indent=1)
        print(json.dumps(dict(precount=pc)), flush=True)
        R.r4_precount(look, {d: v["kept"] for d, v in pc["dates"].items()})            # R4 (R5: never on a high count)
        pl = S.retrain(look, os.path.join(O, "ml", "october.npz"), gpath, os.path.join(O, "ml", "preds.npz"), False, log)
        cfg = json.load(open(os.path.join(R.ART, "rule.json")))
        P = pd.DataFrame({"t": pl["t"].astype(np.int64), "mid": pl["mid"].astype(np.int64), "pred": pl["pred"].astype(float)})
        X = rows.merge(P, on=["t", "mid"], how="left", validate="one_to_one")
        counted = (X.t >= R.ep(L["start"])).values & (X.t < R.ep(L["end"])).values
        st2 = counted & X.pred.notna().values & (X.pred.values > cfg["threshold"])
        kept = R._cap().apply_cap_before_book(X.f_h_top1.values, st2)
        rows["r1_keep"] = r1_attempt_keep(look, rows, kept, hs["bad"])                # R1, attempt level
        res = R.read_after_lock(look, rows, pl, guard, out_path, o_dir=O, ledger=ledger, vmiss_hours=hs["vmiss"], price_fn=S.price_fn)
        rec.update(phase="read", verdict=res["verdict"], result=out_path)
        return rec
    except R.Refusal as e:
        # not terminal: `ready` (no lock ever), a LOCK refusal, and a readiness gap (NOT_READY) or a tool crash (10_meta: outcome-blind,
        # fixable, no outcome seen) before the lock; after the lock a NOT_READY (e.g. price_rows with no v_ok) or a LOCK (require_lock) is a
        # terminal non-run
        if not lock or (e.code in ("LOCK", "RUN", "NOT_READY") and not took):
            raise
        return _not_decidable(look, ledger, O, took, rec, e.code, str(e), out_path)
    except Exception as e:  # noqa: BLE001 - after the lock a crash is a non-run of the look (section 11.5)
        if not took or not lock:
            raise
        return _not_decidable(look, ledger, O, took, rec, "RUN", f"{type(e).__name__}: {e} | {traceback.format_exc(limit=3)}"[:2000], out_path)


def _not_decidable(look, ledger, O, took, rec, code, reason, out_path) -> dict:
    if not took:
        R.take_lock(look, ledger, O)             # a data refusal before the lock still spends the look: lock it so it cannot be re-run
    R.ledger_event(ledger, look, "not_decidable", code=code, reason=reason[:500], phase=rec.get("phase"))
    rec.update(verdict="NOT_DECIDABLE", refused=code, reason=reason)
    json.dump(rec, open(out_path, "w"), indent=1, default=str)
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    for name in ("ready", "run"):
        c = sp.add_parser(name)
        c.add_argument("--look", type=int, choices=(1, 2), required=True)
        c.add_argument("--decoder-blobs", required=True); c.add_argument("--p7")
        c.add_argument("--oracle-live", nargs="*"); c.add_argument("--oracle-replay", nargs="*")
    a = ap.parse_args(argv)
    # fixed in code (quant-proof r3 item 1): tests inject these through `a`, the CLI cannot
    a.now = None; a.ledger = R.LOOK_LEDGER; a.o_dir = None; a.final_marker = R.FINAL_MARKER; a.final_ledger = R.FINAL_LEDGER
    a.e0_dir = R.E0_DIR; a.p2_universe = P2_UNIVERSE; a.oracle_stale_s = None; a.r1_crosscheck = R1_CROSSCHECK
    try:
        rec = run_look(a.look, a, lock=(a.cmd == "run"))
    except R.Refusal as e:
        print(json.dumps(dict(look=a.look, refused=e.code, reason=str(e), terminal=False)))
        return 2
    print(json.dumps({k: rec.get(k) for k in ("look", "phase", "ready", "verdict", "refused", "result")}))
    return 0 if rec.get("verdict") in (None, "PASS", "FAIL") else 2


if __name__ == "__main__":
    sys.exit(main())
