"""A-Q50: the pre-declared POST-READ audit of websocket repeats in the H5 shadow's strip records (QP-RULING-1031-tape-dupes-reads section 4.2, items 1 to 7). PAPER ONLY.

    python -m tools.q50_strip_dup_audit --bot h5v1q50-boostfloor-a1 --ledger-root <arena>/ledger --books-file F [--books-sha256 H] --shadow-dir <arena>/h5-q50-DATE [...]
                                        --read-json <the read's q50-read.json> --read-json-sha256 <its sha256, as the read job logged it> --json OUT

The ruling: /data/mal/hunt-1008/ops/manager18/QP-RULING-1031-tape-dupes-reads.md, sha256 c658e35f3e6eace380b1a1d0a4ceafa8e07cce6f020fe8101638fd3ae02cf40b (written 2026-10-31T00:54Z,
before the Q50 read). It is binding on QP's post-PASS check. No book has cleared the promotion gate; nothing here is a profit claim; a PASS read is a screen (best of N, winner's curse).

WHY. The H5 shadow (tools/h5_shadow.py, the same at Q50's frozen a5f41370, s1's S* and main) keeps no per-pool (signature, event_index) filter, and the registered scorer
(tools/arena_h5_score.py) recomputes ssb / nb from the strip's `buys` = [slot, lamports], which carry no signature, so a websocket repeat that the socket merge let through (cap eviction
at peak load, ruling section 2.3) can raise a counted trade's same-slot buys or 2 s buy lamports, or leave the pool's `post_last` stale. Under the per-cell intercept refit the sign on the
deciding pressure leg is not determined (ruling section 3). The read itself is never changed: this audit runs after it, and only after a PASS.

WHAT IT DOES, item by item (ruling section 4.2):
  1. WHEN. Refuses unless the book's read is RECORDED and PASS: --read-json must be a regular, non-writable file (the read job makes its outputs a-w) whose sha256 equals
     --read-json-sha256 and whose books[<bot>].status is "PASS" with no fail_label. A FAIL, FAIL (n < 100) or NOT_DECIDABLE is final and is never re-opened here: the tool stops
     before it opens a ledger row or a strip. A data defect may block a pass; it never creates one.
  2. INPUT. The read's counted trades only: the trade set of tools/arena_h5_read.py at the read's RECORDED instant (arena_h5_legs.gate, the seal, and arena_h5_read.counts_and_checks,
     imported, never copied), at the deciding stake. The tool checks that this set is the read's: the instant, the counted and excluded counts, the refit intercept and the four
     deciding sub-cells recomputed with the read's own functions must equal the read JSON, and the cost / scorer / gate modules' sha256 must equal the read's provenance (else REFUSED).
     The strip records of those pools only are kept (arena_h5_legs.load_strips: the scorer's reader, first record of a pool wins, dirs in name order); every other record of the
     shadow files is parsed by that reader and dropped at once, never kept or printed. For every counted trade and priced leg, the strip must give the ledger row's ssb, nb and
     state sources (arena_h5_score.Strip, the scorer's own class), or it is not the strip the scorer priced: REFUSED.
  3. REPEAT DEFINITION (outcome-blind). In one strip's `rows` ([slot, q_pv_pre, q_fv_pre, base_pre], arrival order), a row equal on all four columns to an EARLIER row, and arriving after a
     row of a higher slot, is a repeat print. In `buys` ([slot, lamports], arrival order), an entry equal to an earlier entry and arriving after an entry of a higher slot is a repeat buy
     if a repeat print exists at that slot; for buys before `from_slot` (no rows exist there) the late exact repeat alone suffices.
  4. COUNTS FIRST. Per counted trade and leg (1p3, 1p9): repeat buys in the landing slot; repeat buys in [landing - round(2 / sps), landing]; whether the entry or the exit state came from
     `post_last` while the strip's last row is a repeat print; and (QP erratum E2) whether the entry or the exit state came from a `post_last` whose slot is below the strip's
     `from_slot` (the last ARRIVING print is then below the trigger print's slot: always late, and not in `rows`), kept as its own sub-count. Totals. Output: counts and trade ids only
     (the JSON); stdout prints totals and the result word.
  5. All counts 0: "A-Q50 clean"; the read's pressure leg stands (exit 0).
  6. Any count non-zero (exit 3): the deciding cells (stake 0.30, 1.9 s leg, both strand rules) are recomputed, REPORT-ONLY. Pressure: each counted trade's ssb / nb from its strip
     WITHOUT the repeat buys (Strip.pressure_at, the registered function), the intercept refit with the registered rule (arena_h5_cost.press_intercept: mean p over the cell's filled
     sends = 0.289, on every counted trade with its corrected inputs and its recorded state), the leg priced with arena_h5_cost.press_p / press_leg. A trade whose 1.9 s entry or exit
     state came from a repeated last print (item 4, E2 included) is valued at its TOTAL LOSS, the registered strand-class value of a trip that ends with nothing (QP erratum E1:
     stake + one send + the stranded rent, arena_h5_cost._trip / RENT_STRANDED = 302,544,280 lamports at 0.30 SOL), raw (no fail weighting), in the pressure cells AND (QP erratum E3)
     in the deciding flat cells; no other flat input changes (ssb / nb do not enter the flat leg). Each corrected sub-cell is scored with tools.arena_score.cell_stats / cell_verdict
     (the gate's four items). QP's OK, and so every C0 step, then needs the four gate items on BOTH the read's recorded cells AND all four corrected cells (`qp_ok_condition`). The
     recorded read verdict is not changed (one read, final). Errata E1-E3: quant-proof, PR #1144 comment 6537179643 (2026-10-31T01:28:27Z).
  7. This tool. QP OK and a reviewer verdict at the exact head, merged, before it reads any strip.

The 1.3 s leg's counts are item-4 counts (a non-zero one makes the audit not clean); item 6 recomputes only the deciding 1.9 s cells, as the read decides on them.

MODES. Production (no test variable set): the host must be mal-fast-0; the ledger root must be <root>/ledger and every shadow dir a dir of the book under <root> (Q50:
/home/claude/data/arena, h5-q50-YYYY-MM-DD[-succ], as scripts/ops/q50-read.sh accepts; s1: /home/claude/s1home/data/arena, h5-q50s1-YYYY-MM-DD[-succ]); --now-ms and every environment variable whose name starts with MAL_AQ50_T,
and MAL_ARENA_TEST=1, are REFUSED. Test mode (MAL_AQ50_TEST=1 only): synthetic fixtures; every path must lie outside the real roots (/home/claude/data, /home/claude/s1home,
/data/mal/arena, /var/lib/mal), and --now-ms may stand in for the wall clock. Every path, in both modes, passes arena_h5_legs.check_path (no probe, canary, sealed, walk... name).
The --json output is written once (O_EXCL, mode 0444); an existing file is REFUSED before anything is opened.

EXIT: 0 clean; 3 repeats found (the corrected cells are in the JSON, with qp_ok_condition); 2 REFUSED (stderr says why; nothing is written); 1 only on an uncaught error.
Run under a python with numpy (the read's venv on mal-fast-0: /home/claude/venvs/c1nf-ledger/bin/python3).
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python tools/q50_strip_dup_audit.py`
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import hashlib
import json
import os
import re
import socket
import stat
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools import arena_h5_books as B
from tools import arena_h5_cost as cost
from tools import arena_h5_legs as L
from tools import arena_h5_q50_instant as qi
from tools import arena_h5_q50s_instant as s1i
from tools import arena_h5_read as R
from tools import arena_h5_score as h5s
from tools import arena_score as gate_mod
from tools.arena_score import Refused, cell_stats, cell_verdict

TOOL = "A-Q50"
SCHEMA = "a_q50_strip_dup_audit_v1"
RULING = "QP-RULING-1031-tape-dupes-reads section 4.2"
RULING_SHA256 = "c658e35f3e6eace380b1a1d0a4ceafa8e07cce6f020fe8101638fd3ae02cf40b"
Q50_ID, S1_ID = qi.Q50_ID, s1i.S1_ID
BOOKS_AUDITED = (Q50_ID, S1_ID)
DECIDING_STAKE, DECIDING_SFX = R.DECIDING_STAKE, R.DECIDING_SFX  # "0.30", "1p9"
DECIDING_STAKE_LAMPORTS = int(round(float(DECIDING_STAKE) * 1e9))
TOTAL_LOSS_LAMPORTS = DECIDING_STAKE_LAMPORTS + cost.SEND + cost.RENT_STRANDED  # item 6 with QP erratum E1: the strand-class value of a trip that ends with nothing (302,544,280)
SFXS = L.SFXS  # ("1p3", "1p9"); item 4 counts both, item 6 recomputes the deciding 1p9 leg only
RULES = L.RULES  # ("total", "sc")
STATS_KEYS = R.STATS_KEYS
PARITY_TOL = 1e-12
EXIT_CLEAN, EXIT_REFUSED, EXIT_REPEATS = 0, 2, 3  # 1 is left to an uncaught error, so a crash is never read as a result
PROD_HOST = "mal-fast-0"
PROD_ROOTS = {Q50_ID: "/home/claude/data/arena", S1_ID: "/home/claude/s1home/data/arena"}
DIR_RE = {Q50_ID: re.compile(r"h5-q50-\d{4}-\d{2}-\d{2}(-succ)?"), S1_ID: re.compile(r"h5-q50s1-\d{4}-\d{2}-\d{2}(-succ)?")}
REAL_ROOTS = ("/home/claude/data", "/home/claude/s1home", "/data/mal/arena", "/var/lib/mal")
TEST_ENV = "MAL_AQ50_TEST"
TEST_ENV_PREFIX = "MAL_AQ50_T"  # every name with this prefix (TEST_ENV included) is a test override
PROVENANCE_MODULES = {"tools/arena_h5_cost.py": cost.__file__, "tools/arena_h5_score.py": h5s.__file__, "tools/arena_score.py": gate_mod.__file__}  # the modules that make a figure here


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | os.PathLike) -> str:
    return sha256_bytes(Path(path).read_bytes())


def utc(ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


# ------------------------------------------------------------------------------------------------------------------------------ item 3: the repeat definition
def repeat_print_index(rows: Sequence[Sequence[Any]]) -> list[int]:
    """Indices of the repeat prints of one strip's `rows` (arrival order): a row equal on all four columns (slot, q_pv_pre, q_fv_pre, base_pre) to an EARLIER row that arrives after a row
    of a higher slot. A genuine same-slot twin has another pre-state and is not equal; an exact in-order twin is not late."""
    seen: set[tuple] = set()
    hi: int | None = None
    out: list[int] = []
    for j, row in enumerate(rows):
        slot = int(row[0])
        key = (slot, row[1], row[2], row[3])
        if key in seen and hi is not None and hi > slot:
            out.append(j)
        seen.add(key)
        hi = slot if hi is None else max(hi, slot)
    return out


def repeat_buy_index(buys: Sequence[Sequence[Any]], rows: Sequence[Sequence[Any]], from_slot: int) -> list[int]:
    """Indices of the repeat buys of one strip's `buys` ([slot, lamports], arrival order): an entry equal to an earlier entry that arrives after an entry of a higher slot, AND either a
    repeat print exists at that slot or the slot is before `from_slot` (no rows exist there)."""
    rp_slots = {int(rows[j][0]) for j in repeat_print_index(rows)}
    seen: set[tuple] = set()
    hi: int | None = None
    out: list[int] = []
    for j, (s, x) in enumerate(buys):
        slot = int(s)
        key = (slot, x)
        if key in seen and hi is not None and hi > slot and (slot < int(from_slot) or slot in rp_slots):
            out.append(j)
        seen.add(key)
        hi = slot if hi is None else max(hi, slot)
    return out


def pressure_window_slots(sps: float) -> int:
    """round(2 / sps): the scorer's window (arena_h5_score.Strip.pressure_at), restated here only to count repeat buys in it; the corrected ssb / nb come from pressure_at itself."""
    return int(round(h5s.PRESSURE_WINDOW_S / float(sps)))


# ------------------------------------------------------------------------------------------------------------------------------ item 4: per trade and leg
def leg_counts(row: Mapping[str, Any], sfx: str, rec: Mapping[str, Any]) -> dict[str, Any] | None:
    """Item 4 for one counted trade and one leg, after the strip is checked to be the one the scorer priced (raises Refused otherwise). None for a leg the scorer did not price."""
    if row["legs"][f"flat_{sfx}"].get("net_lamports") is None:
        return None
    m = row["meta"]
    sps = float(m["sps"])
    land = int(m["landing_slots"][sfx])
    xland = int(m["exit_trigger_slots"][sfx]) + int(m["exit_lag_slots"])
    strip = h5s.Strip(rec)
    if strip.truncated:
        raise Refused(f"trade {row['trade_id']}: its strip is truncated (the scorer excludes such trades): not the strip the scorer priced")
    pr = row["legs"][f"press_{sfx}"]
    ssb, nb = strip.pressure_at(land, sps)
    _, _, esrc = strip.state_at(land)
    _, _, xsrc = strip.state_at(xland)
    want_src = list((m.get("state_src") or {}).get(sfx) or [])
    if (ssb, nb) != (int(pr["ssb"]), int(pr["nb_lamports"])) or [esrc, xsrc] != want_src:
        raise Refused(f"trade {row['trade_id']} leg {sfx}: the strip gives ssb/nb/state sources that differ from the ledger row: not the strip the scorer priced")
    rows, buys = rec["rows"], rec["buys"]
    rp = repeat_print_index(rows)
    rb = repeat_buy_index(buys, rows, int(rec["from_slot"]))
    w = pressure_window_slots(sps)
    last_is_repeat = bool(rows) and (len(rows) - 1) in set(rp)
    post_below = int(rec["post_last"][0]) < int(rec["from_slot"])
    return {"ssb_repeat_buys": sum(1 for j in rb if int(buys[j][0]) == land),
            "window_repeat_buys": sum(1 for j in rb if land - w <= int(buys[j][0]) <= land),
            "entry_post_last_repeat": esrc == "post" and last_is_repeat,
            "exit_post_last_repeat": xsrc == "post" and last_is_repeat,
            "entry_post_last_below_from_slot": esrc == "post" and post_below,  # QP erratum E2: counted, its own sub-count
            "exit_post_last_below_from_slot": xsrc == "post" and post_below,
            "_rb": rb, "_land": land}


COUNT_KEYS = ("ssb_repeat_buys", "window_repeat_buys", "entry_post_last_repeat", "exit_post_last_repeat", "entry_post_last_below_from_slot", "exit_post_last_below_from_slot")


def stale_state(c: Mapping[str, Any]) -> bool:
    """The leg's entry or exit state came from a repeated last print (item 4, with QP erratum E2): valued at total loss in item 6."""
    return bool(c["entry_post_last_repeat"] or c["exit_post_last_repeat"] or c["entry_post_last_below_from_slot"] or c["exit_post_last_below_from_slot"])


def nonzero(c: Mapping[str, Any]) -> bool:
    return any(bool(c[k]) for k in COUNT_KEYS)


# ------------------------------------------------------------------------------------------------------------------------------ item 6: the corrected deciding cells (errata E1, E3)
def corrected_cells(dec_rows: Sequence[Mapping[str, Any]], strips: Mapping[str, Mapping[str, Any]], per_trade: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Item 6 with QP errata E1 and E3, report-only: the four deciding cells (stake 0.30, 1p9, flat and pressure, both strand rules). Pressure: every counted trade's ssb / nb recomputed
    from its strip without its repeat buys (Strip.pressure_at), the intercept refit with the registered rule on those inputs. A trade whose 1p9 entry or exit state came from a repeated
    last print (E2 included) is valued at its total loss (E1: stake + send + RENT_STRANDED) in the pressure AND the flat cells (E3); every other flat value is the recorded one."""
    items = L.items_from_rows(dec_rows, DECIDING_SFX)
    by_id = {r["trade_id"]: r for r in dec_rows}
    corr, total_loss_ids, changed_ids = [], [], []
    for it in items:
        row = by_id[it["trade_id"]]
        c = per_trade[it["trade_id"]][DECIDING_SFX]
        rec = strips[row["pool"]]
        drop = set(c["_rb"])
        clean = dict(rec, buys=[b for j, b in enumerate(rec["buys"]) if j not in drop])
        ssb, nb = h5s.Strip(clean).pressure_at(c["_land"], float(row["meta"]["sps"]))
        if (ssb, nb) != (it["ssb"], it["nb"]):
            changed_ids.append(it["trade_id"])
        corr.append(dict(it, ssb=ssb, nb=nb))
        if stale_state(c):
            total_loss_ids.append(it["trade_id"])
    ic = L.own_intercept(corr)  # cost.press_intercept on the corrected inputs; the filled set (state != guarded) is the recorded one
    days = [it["day"] for it in corr]
    tl = {it["trade_id"] for it in corr} & set(total_loss_ids)
    cells: dict[str, Any] = {}
    for rule in RULES:
        vals = L.cell_values(corr, rule, ic)  # the flat leg does not read ssb / nb or the intercept: it is the recorded flat value
        for model in R.MODELS:
            v = [-float(TOTAL_LOSS_LAMPORTS) if it["trade_id"] in tl else float(x) for it, x in zip(corr, vals[model])]
            s = cell_stats(v, days)
            gv = cell_verdict(s)
            cells[f"{model}_{DECIDING_SFX}_{rule}"] = dict({k: s[k] for k in STATS_KEYS}, passes=gv["pass"], misses=gv["misses"])
    press_ok = all(c["passes"] for k, c in cells.items() if k.startswith("press_"))
    flat_ok = all(c["passes"] for k, c in cells.items() if k.startswith("flat_"))
    return {"report_only": True, "intercept": ic, "target_mean_p": cost.PRESS_TARGET, "n": len(corr), "trades_with_changed_inputs": sorted(changed_ids),
            "trades_valued_at_total_loss": sorted(total_loss_ids), "total_loss_lamports": TOTAL_LOSS_LAMPORTS, "cells": cells,
            "pressure_passes_both_strand_rules": press_ok, "flat_passes_both_strand_rules": flat_ok, "passes_all_four": press_ok and flat_ok and len(cells) == 4}


# ------------------------------------------------------------------------------------------------------------------------------ modes and paths
def is_test_mode(env: Mapping[str, str]) -> bool:
    return env.get(TEST_ENV) == "1"


def _under(path: str, root: str) -> bool:
    for a in {os.path.abspath(path), os.path.realpath(path)}:
        for b in {os.path.abspath(root), os.path.realpath(root)}:
            if a == b or a.startswith(b.rstrip("/") + "/"):
                return True
    return False


def check_mode(a: argparse.Namespace, env: Mapping[str, str], host: str) -> bool:
    """Returns True in test mode. Refuses a production run with any test override, a test run on a real path, and a path with a closed name. Opens no file."""
    paths = [("ledger root", a.ledger_root), ("books file", a.books_file), ("read json", a.read_json), ("json output", a.json)] + [("shadow dir", d) for d in a.shadow_dir]
    for what, p in paths:
        L.check_path(p, what)
    if is_test_mode(env):
        for what, p in paths:
            for root in REAL_ROOTS:
                if _under(p, root):
                    raise Refused(f"test mode ({TEST_ENV}=1) must not touch a real path: {what} {p!r} is under {root}")
        return True
    overrides = sorted(k for k in env if k.startswith(TEST_ENV_PREFIX))
    if overrides:
        raise Refused(f"a test override is set in production: {overrides} ({TEST_ENV}=1 is the only test mode)")
    if env.get("MAL_ARENA_TEST") == "1":
        raise Refused("MAL_ARENA_TEST=1 is never set in a production audit")
    if a.now_ms is not None:
        raise Refused("--now-ms is a test override: refused in production (the wall clock is used)")
    if host != PROD_HOST:
        raise Refused(f"production runs only on {PROD_HOST} (the book's ledger and shadow dirs are there); host is {host!r}")
    root = PROD_ROOTS[a.bot]
    if os.path.realpath(a.ledger_root) != os.path.realpath(os.path.join(root, "ledger")):
        raise Refused(f"--ledger-root must be {root}/ledger for {a.bot}")
    for d in a.shadow_dir:
        if os.path.realpath(os.path.dirname(os.path.abspath(d))) != os.path.realpath(root) or not DIR_RE[a.bot].fullmatch(os.path.basename(os.path.abspath(d))):
            raise Refused(f"--shadow-dir {d!r} is not a shadow dir of {a.bot} under {root}")
    return False


# ------------------------------------------------------------------------------------------------------------------------------ item 1: the recorded PASS
def recorded_pass(path: str, want_sha256: str, bot: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The read record: (the whole report, the book's entry). Refuses unless it is a regular non-writable file with the given sha256 and the book's status is PASS. Nothing else is opened."""
    p = Path(path)
    try:
        st = os.lstat(p)
        if not stat.S_ISREG(st.st_mode):
            raise Refused(f"--read-json {path!r} is not a regular file")
        if st.st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
            raise Refused(f"--read-json {path!r} is writable: a recorded read output is made a-w by the read job")
        data = p.read_bytes()
    except OSError as exc:
        raise Refused(f"--read-json {path!r}: no recorded read output ({type(exc).__name__}); nothing else is opened") from None
    got = sha256_bytes(data)
    if not re.fullmatch(r"[0-9a-f]{64}", want_sha256 or "") or got != want_sha256:
        raise Refused(f"--read-json sha256 {got} is not --read-json-sha256 {want_sha256!r} (the sha256 the read job logged)")
    try:
        rep = json.loads(data)
        book = rep["books"][bot]
    except (ValueError, KeyError, TypeError):
        raise Refused(f"--read-json holds no recorded read of {bot}: no PASS read record, nothing else is opened") from None
    if book.get("status") != "PASS" or book.get("fail_label") or book.get("bot_id") != bot:
        raise Refused(f"the recorded read of {bot} is not PASS: a FAIL or NOT_DECIDABLE is final and A-Q50 never re-opens it (item 1); nothing else is opened")
    if not isinstance(book.get("read_instant_ms"), int) or "refit" not in book or "subcells" not in book:
        raise Refused(f"the recorded read of {bot} lacks read_instant_ms / refit / subcells")
    return rep, book


def check_provenance(book: Mapping[str, Any]) -> dict[str, str]:
    """The cost, scorer and gate modules this tool imports must be byte-identical to the read's (its provenance block), so item 6 uses the registered cost functions."""
    prov = book.get("provenance") or {}
    out = {}
    for name, f in PROVENANCE_MODULES.items():
        got = sha256_file(f)
        if prov.get(name) != got:
            raise Refused(f"{name} sha256 {got} is not the read's {prov.get(name)!r}: item 6 must use the registered cost functions")
        out[name] = got
    return out


# ------------------------------------------------------------------------------------------------------------------------------ the audit
def audit(a: argparse.Namespace, *, env: Mapping[str, str] | None = None, host: str | None = None) -> tuple[int, dict[str, Any]]:
    env = dict(os.environ) if env is None else dict(env)
    host = socket.gethostname() if host is None else host
    is_test = check_mode(a, env, host)
    if os.path.lexists(a.json):
        raise Refused(f"--json {a.json!r} exists: the audit output is write-once")
    now = int(a.now_ms) if (is_test and a.now_ms is not None) else int(time.time() * 1000)
    rep, book = recorded_pass(a.read_json, a.read_json_sha256, a.bot)  # item 1, before any ledger row or strip
    prov = check_provenance(book)
    books = B.load_books(L.check_path(a.books_file, "books file"), a.books_sha256)
    if a.bot not in books:
        raise Refused(f"{a.bot} is not in the books file {sorted(books)}")
    if (rep.get("books_file") or {}).get("sha256") not in (None, books.sha256):
        raise Refused(f"the books file sha256 {books.sha256} is not the read's {rep['books_file']['sha256']}")
    ri = int(book["read_instant_ms"])
    doc = L.gate(a.bot, a.ledger_root, ri, True, now, books)  # the seal: the recorded instant, nothing open before it
    if int(doc["read_instant_ms"]) != ri:
        raise Refused(f"the recorded instant {doc['read_instant_ms']} is not the read's {ri}")
    c = R.counts_and_checks(a.bot, a.ledger_root, doc)  # item 2: the read's own trade set, with the read's reconciliations
    rc = book.get("counts") or {}
    for k in ("n_attempts", "n_counted", "n_excluded", "n_refused"):
        if rc.get(k) != c[k]:
            raise Refused(f"{k}: the ledger at the recorded instant gives {c[k]}, the read recorded {rc.get(k)}: not the read's trade set")
    refit = h5s.refit_cells(c["rows"])
    key = f"{DECIDING_STAKE}|{DECIDING_SFX}"
    ic = refit[key]["intercept"]
    if ic != book["refit"].get("intercept"):
        raise Refused("the refit intercept recomputed from the ledger is not the read's: not the read's trade set")
    items = L.items_from_rows(c["dec_rows"], DECIDING_SFX)
    if L.own_intercept(items) != ic:
        raise Refused("the deciding cell's own intercept differs from arena_h5_score.refit_cells: the two refits drifted apart")
    days = [it["day"] for it in items]
    for rule in RULES:
        v = L.cell_values(items, rule, ic)
        for model in R.MODELS:
            k = f"{model}_{DECIDING_SFX}_{rule}"
            s, rec_cell = cell_stats(v[model], days), book["subcells"].get(k) or {}
            if any(abs((s[f] or 0.0) - (rec_cell.get(f) or 0.0)) > PARITY_TOL for f in STATS_KEYS) or rec_cell.get("passes") is not True:
                raise Refused(f"{k}: the sub-cell recomputed with the read's functions is not the read's recorded passing cell")
    pools = {r["pool"] for r in c["dec_rows"]}
    strips = L.load_strips([L.check_path(d, "shadow dir") for d in a.shadow_dir], pools)
    missing = sorted(pools - set(strips))
    if missing:
        raise Refused(f"{len(missing)} counted trade(s) have no strip record in the given shadow dirs: not the read's files")
    per_trade: dict[str, dict[str, Any]] = {}
    for row in c["dec_rows"]:
        rec = strips[row["pool"]]
        if not isinstance(rec.get("buys"), list) or not isinstance(rec.get("rows"), list) or not rec["rows"]:
            raise Refused(f"trade {row['trade_id']}: its strip has no rows / buys list")
        try:
            per_trade[row["trade_id"]] = {sfx: leg_counts(row, sfx, rec) for sfx in SFXS}
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise Refused(f"trade {row['trade_id']}: its strip or ledger row is malformed ({type(exc).__name__}): not the strip the scorer priced") from None
    totals: dict[str, dict[str, int]] = {}
    flagged: dict[str, list[str]] = {}
    for sfx in SFXS:
        legs = [(tid, v[sfx]) for tid, v in per_trade.items() if v[sfx] is not None]
        totals[sfx] = {"legs_priced": len(legs), **{k: sum(int(x[k]) for _, x in legs) for k in COUNT_KEYS}, "trades_flagged": sum(1 for _, x in legs if nonzero(x)),
                       "trades_stale_state": sum(1 for _, x in legs if stale_state(x))}
        flagged[sfx] = sorted(tid for tid, x in legs if nonzero(x))
    rp_n = [len(repeat_print_index(rec["rows"])) for rec in strips.values()]
    rb_n = [len(repeat_buy_index(rec["buys"], rec["rows"], int(rec["from_slot"]))) for rec in strips.values()]
    strip_totals = {"strips": len(strips), "strips_with_repeat_prints": sum(1 for x in rp_n if x), "repeat_prints": sum(rp_n), "strips_with_repeat_buys": sum(1 for x in rb_n if x),
                    "repeat_buys": sum(rb_n)}
    clean = not any(flagged[sfx] for sfx in SFXS)
    out: dict[str, Any] = {
        "schema": SCHEMA, "tool": TOOL, "ruling": RULING, "ruling_sha256": RULING_SHA256, "bot_id": a.bot, "test_mode": is_test,
        "read_json": {"path": os.fspath(a.read_json), "sha256": a.read_json_sha256, "status": "PASS", "read_instant_ms": ri, "read_instant_utc": utc(ri)},
        "books_file_sha256": books.sha256, "provenance": dict(prov, **{"tools/q50_strip_dup_audit.py": sha256_file(__file__)}),
        "shadow_dirs": [os.fspath(d) for d in a.shadow_dir], "counted_trades": len(c["dec_rows"]), "strip_totals": strip_totals,
        "counts_by_leg": totals, "trade_ids_flagged_by_leg": flagged,
        "per_trade_flagged": {tid: {sfx: {k: v[sfx][k] for k in COUNT_KEYS}
                                     for sfx in SFXS if v[sfx] is not None and nonzero(v[sfx])} for tid, v in per_trade.items() if any(v[s] is not None and nonzero(v[s]) for s in SFXS)},
        "errata": "QP errata E1-E3 to items 4 and 6 (quant-proof, PR #1144 comment 6537179643, 2026-10-31T01:28:27Z)",
        "note": ("items 4 and 5 apply to every count above, on both legs: a non-zero 1p3 count makes the audit not clean, and item 6 then recomputes the deciding 1p9 cells "
                 "(the 1p3 leg never decides)"),
    }
    if clean:
        out["result"] = "A-Q50 clean"
        out["read_pressure_leg"] = "stands (item 5)"
        out["qp_ok_condition"] = {"met": True, "why": "every item-4 count is 0: the read's recorded cells stand"}
        return EXIT_CLEAN, out
    corr = corrected_cells(c["dec_rows"], strips, per_trade)
    out["result"] = "A-Q50 repeats found"
    out["corrected_deciding_cells"] = corr
    out["recorded_cells_pass"] = {k: bool(v.get("passes")) for k, v in book["subcells"].items()}
    met = all(out["recorded_cells_pass"].values()) and len(out["recorded_cells_pass"]) == 4 and corr["passes_all_four"]
    out["qp_ok_condition"] = {"met": met, "why": "item 6 with QP erratum E3: the four gate items must hold on the read's recorded cells AND on all four corrected deciding cells "
                                                 "(pressure and flat, both strand rules); the recorded read verdict is not changed"}
    return EXIT_REPEATS, out


def write_once(path: str, doc: Mapping[str, Any]) -> str:
    data = (json.dumps(doc, sort_keys=True, allow_nan=False, indent=1) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    return sha256_bytes(data)


def render(rc: int, out: Mapping[str, Any]) -> list[str]:
    """stdout: totals and the result word only (no figure, no trade id, no pool)."""
    lines = [f"A-Q50 bot={out['bot_id']} read_json_sha256={out['read_json']['sha256']} read_status=PASS read_instant={out['read_json']['read_instant_utc']} test_mode={out['test_mode']}"]
    st = out["strip_totals"]
    lines.append(f"A-Q50 STRIPS repeat_prints={st['repeat_prints']} repeat_buys={st['repeat_buys']} strips_with_repeat_prints={st['strips_with_repeat_prints']}")
    for sfx in SFXS:
        t = out["counts_by_leg"][sfx]
        lines.append(f"A-Q50 COUNTS leg={sfx} ssb_repeat_buys={t['ssb_repeat_buys']} window_repeat_buys={t['window_repeat_buys']} entry_post_last_repeat={t['entry_post_last_repeat']} "
                     f"exit_post_last_repeat={t['exit_post_last_repeat']} entry_post_last_below_from_slot={t['entry_post_last_below_from_slot']} "
                     f"exit_post_last_below_from_slot={t['exit_post_last_below_from_slot']} trades_flagged={t['trades_flagged']}")
    if rc == EXIT_CLEAN:
        lines.append("A-Q50 RESULT clean: the read's pressure leg stands (item 5)")
    else:
        cp = out["corrected_deciding_cells"]
        lines.append(f"A-Q50 RESULT repeats found: corrected deciding cells (report-only) pressure_pass={cp['pressure_passes_both_strand_rules']} "
                     f"flat_pass={cp['flat_passes_both_strand_rules']} "
                     f"trades_valued_at_total_loss={len(cp['trades_valued_at_total_loss'])} qp_ok_condition_met={out['qp_ok_condition']['met']} (item 6; the read verdict is unchanged)")
    return lines


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bot", default=Q50_ID, choices=BOOKS_AUDITED)
    ap.add_argument("--ledger-root", required=True)
    ap.add_argument("--books-file", required=True, help="the books file the read used")
    ap.add_argument("--books-sha256", default=None, help="its sha256 (default: the registered v2 / v3 texts, as tools/arena_h5_books.load_books)")
    ap.add_argument("--shadow-dir", action="append", required=True, help="the shadow dir(s) the book's scorer counted")
    ap.add_argument("--read-json", required=True, help="the recorded read output (arena_h5_read --json), made a-w by the read job")
    ap.add_argument("--read-json-sha256", required=True, help="its sha256, as the read job logged it")
    ap.add_argument("--json", required=True, help="the audit output (write-once, 0444)")
    ap.add_argument("--now-ms", type=int, default=None, help=f"TEST MODE ONLY ({TEST_ENV}=1): stands in for the wall clock; refused in production")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    a = parser().parse_args(argv)
    try:
        rc, out = audit(a)
        sha = write_once(a.json, out)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except FileExistsError:
        print(f"REFUSED: --json {a.json!r} exists: the audit output is write-once", file=sys.stderr)
        return EXIT_REFUSED
    for line in render(rc, out):
        print(line)
    print(f"output {a.json} sha256 {sha}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
