"""Old-vs-new golden for DEC-032 Amendment 5 item 6b (P7 gap share at C0) in tools/arena_live_parity.py. SYNTHETIC FIXTURES ONLY: no live, shadow, canary or
sealed file is read. usage: python scripts/ops/p7-c0-golden.py --old OLD_arena_live_parity.py [--n 600] [--seed 1]

For every fixture and every rung state (none, "C1", "C0"): md5 of the result (json, sorted keys) and the exit code the CLI would return, old against new.
PASS only if: (1) without the rung "C0", every fixture is md5-equal; (2) at "C0", every fixture WITHOUT a gap-share breach is md5-equal; (3) at "C0", on breach
fixtures the differing keys are a subset of {halts, soft_halts, stop_required, not_shown_because, not_decidable (False -> True only)} and the rc differs only from 10 to 0 when the gap share
was the only halt."""
from __future__ import annotations

import argparse
import hashlib
import importlib.machinery
import importlib.util
import json
import sys

import numpy as np

from tools import arena_live_parity as NEW

NOW = 1_800_000_000_000
STAKE = 50_000_000
ALLOWED = {"halts", "soft_halts", "stop_required", "not_shown_because", "not_decidable"}  # not_decidable may only go False -> True (checked)


def load_old(path: str):
    loader = importlib.machinery.SourceFileLoader("old_arena_live_parity", path)  # the exported file has no .py suffix
    spec = importlib.util.spec_from_loader("old_arena_live_parity", loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def fixture(rng: np.random.Generator):
    n = int(rng.integers(1, 121))
    p_gap = float(rng.choice([0.0, 0.0, 0.02, 0.05, 0.10, 0.30]))
    p_notwin = float(rng.choice([0.0, 0.0, 0.0, 0.03, 0.08]))
    d_mu = float(rng.choice([0.0, -50.0, -350.0]))
    trips, twins = [], []
    for i in range(n):
        t0 = NOW - 10_000_000 + i * 1000
        d = float(rng.normal(d_mu, 80.0))
        landed = bool(rng.random() > 0.03)
        trips.append({"trip_id": f"t{i}", "stake_lamports": STAKE, "decision_ms": t0, "buy_landed_ms": t0 + int(rng.uniform(0.5, 3.5) * 1000), "closed_ms": t0 + 500,
                      "gap": bool(rng.random() < p_gap), "exit_landed": landed, "exit_reason": "stop_loss" if rng.random() < 0.2 else "timer",
                      "exit_landed_ms": t0 + 400 + (200 if rng.random() < 0.03 else 0), "exit_bound_ms": t0 + 400, "gap_through": bool(rng.random() < 0.1),
                      "live_net_lamports": 1_000_000 + d / 1e4 * STAKE, "live_buy_price": 1.0 + float(rng.normal(30, 120)) / 1e4,
                      "live_sell_price": 2.0 * (1 + float(rng.normal(-30, 150)) / 1e4)})
        if rng.random() >= p_notwin:
            twins.append({"trip_id": f"t{i}", "pnl_lamports": 1_000_000, "pnl_e1_lamports": 615_978, "pnl_live_basis_lamports": 1_000_000,
                          "pnl_e1_live_basis_lamports": 615_978, "entry_price": 1.0, "exit_price": 2.0})
    att = []
    for i in range(int(rng.integers(0, 61))):
        f = bool(rng.random() < 0.12)
        t0 = NOW - 20_000_000 + i * 1000
        att.append({"attempt_id": f"a{i}", "trip_id": None if f else "t0", "decision_ms": t0, "landed": not f, "landed_ms": None if f else t0 + int(rng.uniform(0.5, 3.5) * 1000),
                    "failed_or_expired": f, "p_fail_press": float(rng.uniform(0.05, 0.4))})
    st = {"at_ms": {k: NOW - 1000 for k in NEW.STATE_NAMES}, "last_landed_ms": NOW - 5000, "unresolved_hard_halts": []}
    if rng.random() < 0.05:
        st["at_ms"]["loss"] = NOW - 70_000  # a stale-state P7 now and then
    return trips, twins, att, st


def rc(res) -> int:
    return 11 if res["hard_halts"] else 10 if res["soft_halts"] else 0


def md5(res) -> str:
    return hashlib.md5(json.dumps(res, sort_keys=True, allow_nan=False).encode()).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True)
    ap.add_argument("--n", type=int, default=600)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(argv)
    OLD = load_old(a.old)
    rng = np.random.default_rng(a.seed)
    fails, counts = [], {"fixtures": 0, "equal_no_rung": 0, "equal_c1": 0, "c0_no_breach": 0, "c0_no_breach_equal": 0, "c0_breach": 0, "c0_breach_rc_10_to_0": 0,
                         "c0_breach_rc_same": 0, "skipped_bad_input": 0}
    diff_keys: set = set()
    for k in range(a.n):
        trips, twins, att, st = fixture(rng)
        try:
            old = OLD.evaluate(trips, twins, att, st, NOW)
        except OLD.BadInput:
            counts["skipped_bad_input"] += 1
            continue
        counts["fixtures"] += 1
        for label, rung in (("no_rung", None), ("c1", "C1")):
            s2 = dict(st) if rung is None else {**st, "rung": rung}
            new = NEW.evaluate(trips, twins, att, s2, NOW)
            if md5(new) == md5(old) and rc(new) == rc(old):
                counts["equal_" + label] += 1
            else:
                fails.append(f"fixture {k} {label}: md5 or rc differ")
        new = NEW.evaluate(trips, twins, att, {**st, "rung": "C0"}, NOW)
        breach = any(NEW.share_breach(sum(1 for t in sorted(trips, key=lambda t: t["closed_ms"])[:j] if t["gap"]), j) for j in range(1, len(trips) + 1))
        if not breach:
            counts["c0_no_breach"] += 1
            if md5(new) == md5(old) and rc(new) == rc(old):
                counts["c0_no_breach_equal"] += 1
            else:
                fails.append(f"fixture {k} c0 without a gap breach: md5 or rc differ")
            continue
        counts["c0_breach"] += 1
        dk = {key for key in set(old) | set(new) if old.get(key) != new.get(key)}
        diff_keys |= dk
        if not dk <= ALLOWED:
            fails.append(f"fixture {k} c0 breach: unexpected keys differ {sorted(dk - ALLOWED)}")
        if rc(old) != rc(new):
            if (rc(old), rc(new)) == (10, 0) and set(old["soft_halts"]) == {"P7"} and old["halts"]["P7"]["detail"].startswith("gap "):
                counts["c0_breach_rc_10_to_0"] += 1
            else:
                fails.append(f"fixture {k} c0 breach: rc {rc(old)} -> {rc(new)} not a gap-only P7")
        else:
            counts["c0_breach_rc_same"] += 1
        if old["not_decidable"] and not new["not_decidable"]:
            fails.append(f"fixture {k} c0 breach: not_decidable went True -> False")
        if not (new["not_decidable"] and new["match"] is False):
            fails.append(f"fixture {k} c0 breach: not_decidable/match wrong")
    print("p7-c0-golden counts:", json.dumps(counts, sort_keys=True))
    print("p7-c0-golden keys that differ on C0 breach fixtures:", sorted(diff_keys))
    for f in fails[:20]:
        print("FAIL", f)
    ok = not fails and counts["fixtures"] > 0 and counts["c0_breach"] > 0 and counts["c0_no_breach"] > 0
    print("p7-c0-golden VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(main())
