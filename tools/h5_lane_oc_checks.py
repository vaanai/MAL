"""Pinned COUNTS tool for the h5-lane-oc live-start checks (QP on PR #1220: L2, L3, L5; QP-RULING-1103-oc-premise Ruling 2 and Addendum A).

  l2l3 --dry-ledger <jsonl|dir> --shadow-dir <dir> [--shadow-dir <dir2>]
  l5   --shadow-dir <dir> [--shadow-dir <dir2>]

READS (streaming, line by line, read-only; the only write is stdout):
  dry ledger (a *.jsonl file, or every top-level *.jsonl of a dir): rows with kind "oc_recheck" or "oc_l6" and ONLY these fields:
      kind, mint (join key, never printed), reason, oc_boost_remaining_lamports, oc_boost_slot, oc_slot, oc_l6_a, oc_l6_b, oc_l6_b_diff_lamports, which.
  shadow dir (every top-level h5-shadow-*.jsonl):
      trigger rows (variant "pv" only; the pv variant is the one the executor trades): type, variant, suppressed, mint (join key), pool (join key),
          slot, boost_vault_remaining_sol, boost_spent_sol, base_breaks_unresolved_settled; for l5 also the PROJECTION fields below.
      boost_slice rows: type, pool, slot.
  Nothing else is parsed or kept. No price, fill, outcome, P&L, mint or pool is ever printed: output is counts, lamport differences, md5s and verdicts.

L2 (l2l3). Dry `oc_recheck` rows that reached the vault read (have int oc_boost_remaining_lamports), passed or refused. Read slot = oc_boost_slot if an int,
else oc_slot (the pool snapshot's slot; a proxy); the count of each is printed. Joined by mint to the shadow's pv trigger (slot, boost_vault_remaining_sol).
"Last event slot" = the greatest boost_slice slot of that pool that is <= the trigger slot (the trigger carries no event slot), else the trigger slot.
CLEAN = no boost_slice of that pool with slot in (last event slot, read slot]. Also printed, for the executor's stricter L6 rule: clean under no
slice in [trigger slot, read slot]. diff = |oc_boost_remaining_lamports - round(boost_vault_remaining_sol * 1e9)|.
Verdict: FAIL if any clean diff != 0; else PASS if >= 5 clean (all 0); else NOT_YET.
L3 (l2l3). pv triggers with an integer base_breaks_unresolved_settled == 0 and numeric boost_spent_sol, boost_vault_remaining_sol: n, median, max of
|17,585,000,000 - round(boost_spent_sol * 1e9) - round(boost_vault_remaining_sol * 1e9)|. FAIL if max >= 17,585,000 (one floor), else OK (n == 0: NOT_YET).
L5 (l5). The settled = 0 decision md5 of h5-lane-oc against the pinned h5-lane-2 executor, over the real settled = 0 pv trigger rows, through the harness of
tools/test_h5_lane_oc.py (same fake RPC and chain state, same fixture mint/pool, same `decisions()` projection). Each real row is projected onto the
fixture trigger by PROJECTION (a key absent from the real row is absent from the fixture row); the real mint, pool, wall-clock times are NOT used:
t_detect_ms is the fake clock (age 0), slot = fixture s0 + (real slot - real s0). So the md5 is a decision-equivalence check on the rule fields, not a
replay of the real chain state or ages.
Exit 0 on a completed count; 2 on a usage or IO error.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import os
import statistics
import sys
from collections import Counter, defaultdict
from typing import Any, Callable, Iterator

BUDGET_LAMPORTS = 17_585_000_000
FLOOR_LAMPORTS = 17_585_000  # (1 - 0.999) x budget: one floor
L2_MIN_CLEAN = 5
LEDGER_FIELDS = ("kind", "mint", "reason", "oc_boost_remaining_lamports", "oc_boost_slot", "oc_slot", "oc_l6_a", "oc_l6_b", "oc_l6_b_diff_lamports", "which")
# shadow trigger fields the l5 projection takes from the real row (everything the pinned parser and executor read as a rule input)
PROJECTION = ("synthetic", "synthetic_src", "q_trigger_sol", "base_pre", "sell_token_raw", "v_print", "v_missing", "gap", "base_breaks_unresolved",
              "base_breaks", "slot_regress", "s0_reanchored_slots", "s0_minus_announced_slots", "sps", "sps_span_s", "boost_spent_sol", "boost_src",
              "boost_vault_remaining_sol", "base_breaks_unresolved_settled")


class UsageError(Exception):
    pass


def _int(x: Any) -> bool:
    return type(x) is int


def _num(x: Any) -> bool:
    return type(x) in (int, float) and math.isfinite(x)


def _lam(sol: Any) -> int | None:
    return int(round(sol * 1e9)) if _num(sol) else None


def _files(path: str, pattern: str) -> list[str]:
    if os.path.isfile(path):
        return [path]
    if os.path.isdir(path):
        return sorted(glob.glob(os.path.join(path, pattern)))
    raise UsageError(f"not a file or directory: {path}")


def _rows(paths: list[str], tags: tuple[str, ...]) -> Iterator[dict]:
    """Rows of the files whose line contains one of `tags` (a cheap prefilter) and parses to a dict."""
    for p in paths:
        try:
            fh = open(p, encoding="utf-8", errors="replace")
        except OSError as exc:
            raise UsageError(f"cannot read {os.path.basename(p)}: {type(exc).__name__}")
        with fh:
            for line in fh:
                if not any(t in line for t in tags):
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    yield row


def shadow_files(dirs: list[str]) -> list[str]:
    out: list[str] = []
    for d in dirs:
        if not os.path.isdir(d):
            raise UsageError(f"not a directory: {d}")
        out += sorted(glob.glob(os.path.join(d, "h5-shadow-*.jsonl")))
    return out


def pv_triggers(files: list[str]) -> Iterator[dict]:
    for r in _rows(files, ("trigger",)):
        if r.get("type") == "trigger" and r.get("variant") == "pv" and not r.get("suppressed"):
            yield r


def _med(vals: list[int]) -> Any:
    m = statistics.median(vals)
    return int(m) if float(m).is_integer() else m


def _stats(vals: list[int]) -> str:
    if not vals:
        return "n=0"
    return f"n={len(vals)} min={min(vals)} median={_med(vals)} max={max(vals)}"


def _fmt_counter(c: Counter) -> str:
    return "{" + ", ".join(f"{k}: {c[k]}" for k in sorted(c, key=str)) + "}" if c else "{}"


# ---------------------------------------------------------------------------------------------------------------------------------- l2 / l3
def read_ledger(path: str) -> dict[str, Any]:
    rec_reasons: Counter = Counter()
    l6_which: Counter = Counter()
    l6a: Counter = Counter()
    l6b: Counter = Counter()
    diffs: list[int] = []
    reads: list[dict] = []  # oc_recheck rows that reached the vault read
    for r in _rows(_files(path, "*.jsonl"), ("oc_recheck", "oc_l6")):
        kind = r.get("kind")
        if kind == "oc_recheck":
            rec_reasons[str(r.get("reason"))] += 1
            if _int(r.get("oc_boost_remaining_lamports")):
                l6a[str(r.get("oc_l6_a"))] += 1
                l6b[str(r.get("oc_l6_b"))] += 1
                if _int(r.get("oc_l6_b_diff_lamports")):
                    diffs.append(r["oc_l6_b_diff_lamports"])
                reads.append({k: r.get(k) for k in LEDGER_FIELDS if k in r})
        elif kind == "oc_l6":
            l6_which[str(r.get("which"))] += 1
    return {"reasons": rec_reasons, "which": l6_which, "l6a": l6a, "l6b": l6b, "b_diffs": diffs, "reads": reads}


def l2l3(ledger: str, dirs: list[str]) -> dict[str, Any]:
    led = read_ledger(ledger)
    files = shadow_files(dirs)
    need = {r["mint"] for r in led["reads"] if isinstance(r.get("mint"), str)}
    by_mint: dict[str, list[dict]] = defaultdict(list)  # pass 1: triggers (L2 join + L3 identity)
    l3: list[int] = []
    l3_unusable = 0
    for t in pv_triggers(files):
        if _int(t.get("base_breaks_unresolved_settled")) and t["base_breaks_unresolved_settled"] == 0:
            sp, rem = _lam(t.get("boost_spent_sol")), _lam(t.get("boost_vault_remaining_sol"))
            if sp is None or rem is None:
                l3_unusable += 1
            else:
                l3.append(abs(BUDGET_LAMPORTS - sp - rem))
        m = t.get("mint")
        if m in need and isinstance(t.get("pool"), str) and _int(t.get("slot")):
            by_mint[m].append({"pool": t["pool"], "slot": t["slot"], "rem": _lam(t.get("boost_vault_remaining_sol"))})
    pools = {x["pool"] for v in by_mint.values() for x in v}
    slices: dict[str, list[int]] = defaultdict(list)  # pass 2: boost_slice slots of the needed pools
    if pools:
        for s in _rows(files, ("boost_slice",)):
            if s.get("type") == "boost_slice" and s.get("pool") in pools and _int(s.get("slot")):
                slices[s["pool"]].append(s["slot"])
    unjoined = 0
    used = Counter()
    clean_diffs: list[int] = []
    n_cmp = n_clean = n_clean_strict = 0
    for r in led["reads"]:
        trs = by_mint.get(r.get("mint")) or []
        if len({x["pool"] for x in trs}) != 1 or trs[0]["rem"] is None:
            unjoined += 1
            continue
        t = trs[0]
        if _int(r.get("oc_boost_slot")):
            rslot, src = r["oc_boost_slot"], "oc_boost_slot"
        elif _int(r.get("oc_slot")):
            rslot, src = r["oc_slot"], "oc_slot"
        else:
            unjoined += 1
            continue
        n_cmp += 1
        used[src] += 1
        sl = slices.get(t["pool"], [])
        last_ev = max([x for x in sl if x <= t["slot"]], default=t["slot"])
        clean = not any(last_ev < x <= rslot for x in sl)
        if not any(t["slot"] <= x <= rslot for x in sl):
            n_clean_strict += 1
        if clean:
            n_clean += 1
            clean_diffs.append(abs(r["oc_boost_remaining_lamports"] - t["rem"]))
    nz = [d for d in clean_diffs if d != 0]
    if nz:
        verdict = "FAIL"
    elif n_clean >= L2_MIN_CLEAN:
        verdict = "PASS"
    else:
        verdict = "NOT_YET"
    if not l3:
        v3 = "NOT_YET"
    else:
        v3 = "FAIL" if max(l3) >= FLOOR_LAMPORTS else "OK"
    return {"led": led, "unjoined": unjoined, "slot_src": used, "n_cmp": n_cmp, "n_clean": n_clean, "n_clean_strict": n_clean_strict,
            "n_zero": len(clean_diffs) - len(nz), "nz": nz, "l2": verdict, "l3": l3, "l3_unusable": l3_unusable, "l3_verdict": v3}


def print_l2l3(res: dict[str, Any], out=None) -> None:
    out = out or sys.stdout
    led = res["led"]
    w = lambda s: print(s, file=out)  # noqa: E731
    w("== L2: dry oc_recheck BOOST vault read vs shadow boost_vault_remaining (lamports) ==")
    w(f"oc_recheck rows that reached the vault read: {len(led['reads'])}; unjoined (no unique pv trigger, no numeric value, or no slot): {res['unjoined']}")
    w(f"read slot used: {_fmt_counter(res['slot_src'])} (oc_slot is the pool snapshot's slot, a proxy for the vault read's slot)")
    w(f"comparisons: {res['n_cmp']}  clean: {res['n_clean']}  not clean: {res['n_cmp'] - res['n_clean']}  "
      f"(clean under the stricter [trigger slot, read slot] rule: {res['n_clean_strict']})")
    w(f"clean abs-diff: exactly 0: {res['n_zero']}  != 0: {len(res['nz'])}"
      + (f" (min {min(res['nz'])}, median {_med(res['nz'])}, max {max(res['nz'])})" if res["nz"] else ""))
    w(f"L2 VERDICT: {res['l2']}  (PASS: >= {L2_MIN_CLEAN} clean, all 0; FAIL: any clean != 0; else NOT_YET)")
    w(f"oc_recheck reasons (all rows): {_fmt_counter(led['reasons'])}")
    w(f"oc_l6_a (rows that reached the vault read): {_fmt_counter(led['l6a'])}")
    w(f"oc_l6_b (rows that reached the vault read): {_fmt_counter(led['l6b'])}")
    w(f"oc_l6 rows by which: {_fmt_counter(led['which'])}")
    w(f"oc_l6_b_diff_lamports (signed): {_stats(led['b_diffs'])}")
    w("== L3: |17,585,000,000 - boost_spent - boost_vault_remaining| on settled = 0 pv trigger records (lamports) ==")
    l3 = res["l3"]
    w(f"n={len(l3)} (unusable, missing numeric fields: {res['l3_unusable']})"
      + (f" median={_med(l3)} max={max(l3)}" if l3 else "") + f"  threshold (one floor): {FLOOR_LAMPORTS}")
    w(f"L3 VERDICT: {res['l3_verdict']}  (FAIL if max >= {FLOOR_LAMPORTS})")


# ------------------------------------------------------------------------------------------------------------------------------------- l5
def real_settled0(dirs: list[str]) -> Iterator[dict]:
    for t in pv_triggers(shadow_files(dirs)):
        if _int(t.get("base_breaks_unresolved_settled")) and t["base_breaks_unresolved_settled"] == 0:
            yield t


def project(clock: Callable[[], int], real: dict) -> dict:
    """The fixture trigger of the test harness, with the PROJECTION fields of the real row (a missing key stays missing)."""
    from tools.test_h5_executor_shadow import S0, shadow_trigger
    row = shadow_trigger(clock, base_breaks_unresolved_settled=0)
    for k in PROJECTION:
        if k in real:
            row[k] = real[k]
        else:
            row.pop(k, None)
    if _int(real.get("slot")) and _int(real.get("s0")):
        row["slot"] = S0 + (real["slot"] - real["s0"])
    return row


def md5_of_rows(makers: list[Callable[[Any], dict]] | Iterator[Callable[[Any], dict]]) -> dict[str, Any]:
    """Run the harness of tools/test_h5_lane_oc.py (Settled0EquivalenceTests.run_set, same loop and projection) for h5-lane-2 and h5-lane-oc over the
    triggers `makers` build (each maker takes the Env and returns the shadow row). Returns n and both md5s."""
    from tools import test_h5_lane_oc as T
    makers = list(makers) if not isinstance(makers, list) else makers
    case = T.Settled0EquivalenceTests("test_the_parser_is_restored_after_the_tick")
    case.setUp()
    try:
        def run(env_cls, bot: str, tag: str, extra: dict) -> str:
            outs = []
            for i, make in enumerate(makers):
                sub = case.tmp / f"{tag}{i}"
                sub.mkdir()
                d = sub / "lane"
                e = env_cls(sub, lane=case.lane_cfg(d, bot=bot), **{k: (val(d) if callable(val) else val) for k, val in extra.items()})
                e.rpc.balance = 10 * 10**9
                e.rpc.accounts[T.VAULT] = T.boost_acct(10 * 10**9)
                case.feed(e, make(e))
                outs.append(T.decisions(e) + [e.rpc.calls.count("getAccountInfo"), len(e.rpc.calls)])
            return hashlib.md5(json.dumps(outs, sort_keys=True, default=str).encode()).hexdigest()
        lane2 = run(T.Lane2Env, "h5-lane-2", "a", {})
        oc = run(T.OCEnv, T.BOT, "b", {"lane_oc": lambda d: case.oc_cfg(d)})
    finally:
        case.doCleanups()
    return {"n": len(makers), "lane2": lane2, "oc": oc}


def l5(dirs: list[str]) -> dict[str, Any]:
    # the real rows are streamed once into a list of the PROJECTION fields only (rule inputs, no mint/pool/price identity beyond the rule fields)
    keep = [{k: t[k] for k in (*PROJECTION, "slot", "s0") if k in t} for t in real_settled0(dirs)]
    return md5_of_rows([(lambda e, r=r: project(e.clock, r)) for r in keep])


def print_l5(res: dict[str, Any], out=None) -> None:
    out = out or sys.stdout
    w = lambda s: print(s, file=out)  # noqa: E731
    w("== L5: settled = 0 decision md5, h5-lane-oc vs pinned h5-lane-2 (fake RPC harness of tools/test_h5_lane_oc.py, real rule fields) ==")
    w(f"n settled = 0 pv triggers: {res['n']}")
    w(f"md5 h5-lane-oc: {res['oc']}")
    w(f"md5 h5-lane-2:  {res['lane2']}")
    w(f"RESULT: {'EQUAL' if res['oc'] == res['lane2'] else 'DIFF'}")
    w("trigger fields projected from the real row: " + ", ".join(PROJECTION) + ", slot-s0 (as slot offset)")
    w("NOT taken from the real row: mint, pool, wall-clock times (t_detect_ms = fake clock, age 0), chain state (fixture pool).")
    w("md5 covers per trigger: refusal reasons, buys sent (stake, min_out, accounts), decision rows without clock fields and bot_id, getAccountInfo count, total RPC count.")
    w("The stake field is NOT a trigger field: it is config. Both bots run here with the same fixture stake (20,000,000 lamports), so it is in the md5 (inside the sent buys) "
      "but identical by construction. Production h5-lane-2 stake 0.025 SOL vs h5-lane-oc 0.02 SOL is not tested by this md5.")


# ------------------------------------------------------------------------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("l2l3")
    a.add_argument("--dry-ledger", required=True)
    a.add_argument("--shadow-dir", action="append", required=True)
    b = sub.add_parser("l5")
    b.add_argument("--shadow-dir", action="append", required=True)
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        if args.cmd == "l2l3":
            print_l2l3(l2l3(args.dry_ledger, args.shadow_dir))
        else:
            print_l5(l5(args.shadow_dir))
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: {type(exc).__name__}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
