"""floor_killcheck: step B of the FLOOR kill-line check (DEC-035 Am.7 Post-install check "Kill line", Erratum 1). Runs on mal-research-0.

Question: after a CHEAP restart (a `lane_restart_shadow` row with shadow_complete true), did the LIVE lane enter any pool the unguarded, uninterrupted reference rule holds open at that
instant? Inputs: (1) the extract file written by step A on mal-fast-0 (scripts/ops/floor_killcheck_extract.py; entry rows and restart rows of the declared window only), copied to
/data2/mal/scratch/floor-killcheck/in/ and checked against the sha256 given on the command line; (2) the tape source, with the proof's rules (scripts/ops/floor_stage1_proof.py,
imported, not copied): the research-0 hv7 copy or a frozen hard-link copy /data2/mal/scratch/floor-stage1-proof/tapecopy-<MMDD> with its list (TAPE-CHECK). Nothing else is read.

The reference is the P2 reference (floor_stage1_proof.reference_rows): HostV2 + the frozen rule straight through, no guard, no restart. It starts one BLOCK_MS (and the restart's
downtime) before the earliest restart so that every reference position open at the restart exists in it, and ends at the end of the checked entries (hour-aligned, closed hours only).
`verify_pins()` runs before the reference (QP note 4); outside test mode HEAD's tools/floor_lane_host.py must be the stage-1 merge's blob.

Per cheap restart r (start instant = the row's `ms`; `--restart-ms` overrides and REQUIRES `--smoke`), the live entries with r <= t < r + BLOCK_MS (and inside the reference window) are checked:
  n_live_entries_after_restart                  the live entries in that span
  n_live_entry_on_ref_held_pool_MUST0           entries on a pool where a DIFFERENT reference entry (another trigger) is open at t (ref entry ms < t <= its exit ms, or no exit)
  n_live_entry_not_in_reference_INFO            entries with no reference entry on the same pool at the same trigger (`trig_ms`; if absent: within 2 s). INFORMATION, not the kill
                                                line: DEC-035 Am.7 kill line 1 is "any entry on a pool the reference holds". With `cheap_restart` the host is meant to be a subset of the
                                                reference, so every live entry should also be a reference entry; a miss is a SUBSET break the manager reports to QP (the lane clock runs up
                                                to ~1.1 s later than the reference; a pool the reference does not hold is not a kill), so it is printed, never scored (QP on #1337 at 496d6e5a).
  n_live_entries_beyond_reference_window_INFO   entries after the reference window end (a partial run is allowed; complete it later)
Order ids (or `seq:<n>` when the row carried none) of the MUST0 offenders are printed; never a pool id, price or P&L. Exit 0 `KILLCHECK PASS` only if MUST0 == 0, at least one cheap
restart was checked and at least one live entry followed it; exit 3 (vacuous, says so) if there is no cheap restart or no live entry after one; exit 1 `KILLCHECK FAIL`; exit 2 a refusal.
`--smoke` labels everything `SMOKE` (final line `KILLCHECK_SMOKE PASS`, json `killcheck-SMOKE.json`): a smoke run with a hand-set restart instant is NOT a kill-line check.
Writes only under /data2/mal/scratch/floor-killcheck/<run>/ (a NEW dir). The reference's decision log (pool ids) is deleted after its rows are read. Paper only. NOT a promotion read."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
import floor_stage1_proof as S  # noqa: E402  (the proof's guards, tape rules and P2 reference: imported, not copied)

TP = S.TP
OUT_ROOT = "/data2/mal/scratch/floor-killcheck"
IN_ROOT = OUT_ROOT + "/in"
SCHEMA = "floor-killcheck-extract-v1"
ALLOWED_ENTRY_KEYS = {"t_ms", "pool", "order_id", "seq", "trig_ms"}
TOL_MS = 2_000  # same-entry tolerance when a live row carries no trigger instant
HOUR_MS = S.HOUR_MS
EXIT_FAIL, EXIT_VACUOUS = S.EXIT_FAIL, S.EXIT_VACUOUS
refuse = S.refuse


def bind_roots() -> None:
    """The proof's out-dir and symlink guards read S.OUT_ROOT at call time; point them at this job's root."""
    S.OUT_ROOT = OUT_ROOT


def check_extract(path: str, sha: str) -> bytes:
    """The extract must be a regular file directly under IN_ROOT (abspath == realpath, no `..`), and its sha256 must be the one given. -> its bytes."""
    if ".." in path.split("/") or not path.startswith("/"):
        refuse("--extract must be an absolute path without `..`")
    ap = os.path.abspath(path)
    if os.path.realpath(ap) != ap:
        refuse("--extract: a symlink in the path is refused")
    inroot = os.path.realpath(IN_ROOT).rstrip("/")
    if os.path.dirname(ap) != inroot:
        refuse("--extract must be a file directly in %s" % IN_ROOT)
    why = TP.refusal(ap, roots=(inroot,))
    if why:
        refuse(why)
    if not os.path.isfile(ap):
        refuse("--extract is not a regular file")
    try:
        fd = os.open(ap, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        refuse("cannot open --extract")
    with os.fdopen(fd, "rb") as f:
        data = f.read()
    got = hashlib.sha256(data).hexdigest()
    if got != sha.lower():
        refuse("extract sha256 mismatch (got %s, expected %s)" % (got, sha))
    return data


def parse_extract(data: bytes) -> tuple:
    """-> (header, entries). Entry lines may carry only ALLOWED_ENTRY_KEYS; anything else is refused (the extract is not trusted to be small)."""
    lines = [ln for ln in data.decode().split("\n") if ln.strip()]
    if not lines:
        refuse("empty extract")
    h = json.loads(lines[0])
    if h.get("kind") != "header" or h.get("schema") != SCHEMA:
        refuse("the extract header is not %s" % SCHEMA)
    entries = []
    for ln in lines[1:]:
        e = json.loads(ln)
        if not isinstance(e, dict) or set(e) - ALLOWED_ENTRY_KEYS or not isinstance(e.get("t_ms"), int) or not isinstance(e.get("pool"), str) or not ("order_id" in e or "seq" in e):
            refuse("an extract line has a key or a type outside the allowed set")
        entries.append(e)
    if len(entries) != h.get("n_entries"):
        refuse("the extract header count does not match its lines")
    return h, entries


def eid(e: dict) -> str:
    return e["order_id"] if "order_id" in e else "seq:%s" % e["seq"]


def ref_positions(ref_rows: list) -> dict:
    """pool -> list of (entry_ms, exit_ms or None, trig, order_id) of the reference."""
    last_exit: dict = {}
    for r in ref_rows:
        if r.get("kind") == "exit":
            last_exit[r.get("order_id")] = max(last_exit.get(r.get("order_id"), -1), int(r["ms"]))
    out: dict = {}
    for r in ref_rows:
        if r.get("kind") == "entry":
            out.setdefault(r["pool"], []).append((int(r["ms"]), last_exit.get(r.get("order_id")), (r.get("meta") or {}).get("signal_t_recv_ms"), r.get("order_id")))
    return out


def same_entry(p: tuple, e: dict) -> bool:
    if e.get("trig_ms") is not None and p[2] is not None:
        return int(p[2]) == int(e["trig_ms"])
    return abs(p[0] - int(e["t_ms"])) <= TOL_MS


def compare(ref_rows: list, entries: list, restarts: list, block_ms: int, ref_to_ms: int) -> dict:
    pos = ref_positions(ref_rows)
    per, bad = [], set()
    for i, r in enumerate(restarts, 1):
        r0 = int(r["t_ms"])
        win = [e for e in entries if r0 <= e["t_ms"] < r0 + block_ms]
        beyond = [e for e in win if e["t_ms"] >= ref_to_ms]
        chk = [e for e in win if e["t_ms"] < ref_to_ms]
        held, notref = [], 0
        for e in chk:
            t = int(e["t_ms"])
            ps = pos.get(e["pool"], [])
            if not any(same_entry(p, e) for p in ps):
                notref += 1
            if any((not same_entry(p, e)) and p[0] < t and (p[1] is None or t <= p[1]) for p in ps):
                held.append(eid(e))
        bad.update(held)
        per.append({"idx": i, "restart_ms": r0, "n_live_entries_after_restart": len(chk), "n_live_entry_on_ref_held_pool_MUST0": len(held), "n_live_entry_not_in_reference_INFO": notref,
                    "n_live_entries_beyond_reference_window_INFO": len(beyond), "offender_ids": held})
    return {"restarts": per, "n_cheap_restarts_checked": len(per), "n_live_entries_after_restart": sum(p["n_live_entries_after_restart"] for p in per),
            "n_live_entry_on_ref_held_pool_MUST0": len(bad), "n_live_entry_not_in_reference_INFO": sum(p["n_live_entry_not_in_reference_INFO"] for p in per),
            "ref_entries": sum(1 for r in ref_rows if r.get("kind") == "entry")}


def floor_h(ms: int) -> int:
    return ms - ms % HOUR_MS


def ceil_h(ms: int) -> int:
    return floor_h(ms + HOUR_MS - 1)


def run_reference(out: str, srcs, seed_ms, frm: int, to: int, step: int, test_mode: bool) -> list:
    sys.path.insert(0, str(REPO))
    from tools import arena_rules as R
    from tools import floor_lane_host as H
    try:
        pins = H.verify_pins()
    except R.HostError as e:
        refuse("verify_pins: %s" % e)
    S.emit("KILLCHECK-PINS", {"verify_pins_ok": True, "n_pins": len(pins)})
    tape = S.make_tapelink(out, srcs, seed_ms if seed_ms is not None else frm, to) if not test_mode else srcs[0]
    work = os.path.join(out, "ref-work")
    os.makedirs(work)
    reg = os.path.join(work, "bots.json")
    S.register_scratch(reg, frm, test_mode)
    try:
        return S.reference_rows(work, reg, tape, frm, to, step, "first_print" if test_mode else None, bool(seed_ms is not None or test_mode))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="a NEW directory under %s" % OUT_ROOT)
    ap.add_argument("--extract", required=True, help="file directly in %s" % IN_ROOT)
    ap.add_argument("--extract-sha256", required=True)
    ap.add_argument("--restart-ms", type=int, action="append", default=None, help="override the extract's cheap restarts; REQUIRES --smoke")
    ap.add_argument("--smoke", action="store_true", help="label the run SMOKE; it is never a kill-line check")
    ap.add_argument("--ref-from", default=None, help="reference start hour YYYY-MM-DDTHH (default: the hour of earliest restart/downtime start minus BLOCK_MS)")
    ap.add_argument("--to", default=None, help="reference end hour (exclusive; default: the hour after the last checked entry window)")
    ap.add_argument("--seed-from-hour", default="auto")
    ap.add_argument("--tape-list", default=None)
    ap.add_argument("--tape-src", action="append", default=None)
    ap.add_argument("--step-ms", type=int, default=1000)
    ap.add_argument("--count-only", action="store_true", help="check the extract, the tape source and the window; run no reference")
    ap.add_argument("--test-mode", action="store_true", help="synthetic tapes only (needs MAL_ARENA_TEST=1)")
    ap.add_argument("--now-ms", type=int, default=None)
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    bind_roots()
    label = "SMOKE" if a.smoke else "KILLCHECK"
    if a.restart_ms and not a.smoke:
        refuse("--restart-ms is for the smoke run only: pass --smoke")
    if a.test_mode and os.environ.get("MAL_ARENA_TEST") != "1":
        refuse("--test-mode needs MAL_ARENA_TEST=1")
    data = check_extract(a.extract, a.extract_sha256)
    header, entries = parse_extract(data)
    sys.path.insert(0, str(REPO))
    from tools import floor_lane_host as H
    block = int(H.BLOCK_MS)
    if a.restart_ms:
        restarts = [{"t_ms": int(x), "down_from_ms": None, "source": "override"} for x in a.restart_ms]
    else:
        restarts = [dict(r, source="extract") for r in header.get("lane_starts", []) if r.get("shadow_complete") is True and isinstance(r.get("t_ms"), int)]
    restarts.sort(key=lambda r: r["t_ms"])
    S.emit("%s-INPUT" % label, {"extract_sha256": a.extract_sha256.lower(), "n_entries_in_extract": len(entries), "n_restart_rows_in_extract": len(header.get("lane_starts", [])),
                                "n_cheap_restarts": len(restarts), "restart_source": "override" if a.restart_ms else "extract", "block_ms": block, "smoke": bool(a.smoke), "test_mode": bool(a.test_mode)})
    if not restarts:
        print("%s VACUOUS: the window has no cheap restart (no lane_restart_shadow row with shadow_complete true); nothing was checked" % label, file=sys.stderr)
        return EXIT_VACUOUS
    first = min(min(r["t_ms"], r.get("down_from_ms") or r["t_ms"]) for r in restarts)
    last_end = min(int(header["to_ms"]), max(r["t_ms"] for r in restarts) + block)
    frm = S.parse_hour(a.ref_from) if a.ref_from else floor_h(first - block)
    to = S.parse_hour(a.to) if a.to else ceil_h(last_end)
    srcs = S.check_srcs(a.tape_src or list(S.TAPE_SRC), a.test_mode)
    if a.seed_from_hour == "none":
        seed_ms = None
        if not a.test_mode:
            refuse("--seed-from-hour none is for synthetic tests only")
    elif a.seed_from_hour == "auto":
        seed_ms = S.discover_seed(srcs)
    else:
        seed_ms = S.parse_hour(a.seed_from_hour)
    info = {"seed_hour": S.hour_name(seed_ms) if seed_ms is not None else "none", "ref_from": S.hour_name(frm), "ref_to": S.hour_name(to)}
    if seed_ms is not None:
        info.update(S.verify_frozen(srcs, seed_ms, to, a.tape_list))
        S.check_window(frm, to, seed_ms, now_ms=a.now_ms)
        info.update(S.coverage(srcs, seed_ms, to))
    S.emit("%s-TAPE" % label, info)
    if seed_ms is not None and info["hours_missing"]:
        refuse("tape hours missing: %d" % info["hours_missing"])
    if a.count_only:
        return 0
    out = S.check_out(a.out)
    os.makedirs(out)
    if not a.test_mode:
        for what, rev in (("HEAD", "HEAD"), ("the merge", S.MERGE_SHA)):
            if S.git("rev-parse", rev + ":tools/floor_lane_host.py") != S.MERGE_HOST_BLOB:
                refuse("%s tools/floor_lane_host.py is not the stage-1 merge's blob" % what)
    ref_rows = run_reference(out, srcs, seed_ms, frm, to, a.step_ms, a.test_mode)
    res = compare(ref_rows, entries, restarts, block, to)
    del ref_rows
    res.update(label=label, extract_sha256=a.extract_sha256.lower(), tape_info=info, restart_source="override" if a.restart_ms else "extract")
    for p in res["restarts"]:
        S.emit("%s-RESTART" % label, {"idx": p["idx"], "restart_hour": S.hour_name(p["restart_ms"]), "n_live_entries_after_restart": p["n_live_entries_after_restart"],
                                      "n_live_entry_on_ref_held_pool_MUST0": p["n_live_entry_on_ref_held_pool_MUST0"], "n_live_entry_not_in_reference_INFO": p["n_live_entry_not_in_reference_INFO"],
                                      "n_live_entries_beyond_reference_window_INFO": p["n_live_entries_beyond_reference_window_INFO"], "offender_order_ids": ",".join(p["offender_ids"]) or "none"})
    S.emit(label, {k: res[k] for k in ("n_cheap_restarts_checked", "n_live_entries_after_restart", "n_live_entry_on_ref_held_pool_MUST0", "n_live_entry_not_in_reference_INFO", "ref_entries")})
    Path(out, "killcheck-%s.json" % ("SMOKE" if a.smoke else "KILLCHECK")).write_text(json.dumps(res, sort_keys=True))
    if res["n_cheap_restarts_checked"] < 1 or res["n_live_entries_after_restart"] < 1:
        print("%s VACUOUS: no live entry followed a cheap restart inside the reference window; nothing was shown" % label, file=sys.stderr)
        return EXIT_VACUOUS
    ok = res["n_live_entry_on_ref_held_pool_MUST0"] == 0
    print(("KILLCHECK_SMOKE %s (a hand-set restart instant: NOT a kill-line check)" if a.smoke else "KILLCHECK %s") % ("PASS" if ok else "FAIL"))
    return 0 if ok else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
