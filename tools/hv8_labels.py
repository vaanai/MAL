#!/usr/bin/env python3
"""HV8 C3: label tool (outcomes). Design: docs/research/hv8/DESIGN.md section 4 "Labels" and "Pin order". Prints COUNTS only. 0 API.

THE PIN GATE (before ANYTHING else is opened, the tape included): tools.hv8_calib.read_pin() must return a sha256, the ratings dir's manifest.json must hash to it
and to --manifest-sha, the dump manifest it names must hash to the ratings manifest's dump_manifest_sha256, and the ratings dir (and every file in it) must be
read-only. So no rating can be made after any outcome exists, and the order is auditable. Without RATINGS-PIN.md the tool raises and prices nothing.

PRICING: the SAME replay as the dump (tools.hv8_dump.make_runner: 7-day warm-up, event-based created_known, HV7 screen, FakeChain) with a real ArenaEngine, arm U only
(every screened card is bought at t_e = t_d + 45 s, 0.25 SOL, HV6 X exit: stop -8%, target +20%, 60 min), the 1.9 s leg, tape end 2026-08-28T12:00:00Z EXCLUSIVE.
Replay constants are HV7's drill constants (feed delay 2 slots, 0.4 s/slot), not measured for August. The replayed sets must equal the dump's cards.jsonl exactly.
Return = flat-15% leg net lamports / stake lamports (`ret`); the pressure-fail leg is `ret_press`, report-only. A card with no priced trade row, an excluded or refused
row, or an exit that would need a row at or after the tape end is CENSORED (ret null) and counted by reason, never extended.
y = 1 if ret is above the median of the set's known returns, 0 if below; ties and censored cards get y null (counted). Relative label: the hour's market move cancels.
Outputs (one run into an empty dir): labels.jsonl {card_sha, t_d, ret, ret_press, y, censored}, labels-manifest.json (counts, sha256s). Nothing here says HV8 makes money.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import numpy as np

os.environ.setdefault("MAL_ARENA_TEST", "1")

from tools import hv6_agents as A  # noqa: E402
from tools import hv7_runner as R  # noqa: E402
from tools import hv8_calib as CAL  # noqa: E402
from tools import hv8_dump as D  # noqa: E402
from tools import hv8_rate as H8  # noqa: E402
from tools.arena_paper import ArenaEngine, register_bot, rules_of  # noqa: E402
from tools.drill_hv7_real_layout import FEED_DELAY_SLOTS, script  # noqa: E402

Refused = D.Refused
STAKE_SOL = R.STAKE_SOL
LEG = "1p9"
EXIT_LEG_MS = 1900
SPS = 0.4


# ---------------------------------------------------------------- the pin gate
def read_only(path: Path) -> bool:
    return not (os.stat(path).st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def check_pin(ratings_dir: Path, dump_dir: Path, manifest_sha: str, pin_path: Path | None = None) -> str:
    """Raises (CalibError or Refused) unless the pin, the ratings manifest, the argument and the dump all agree and the ratings dir is read-only."""
    pinned = CAL.read_pin(pin_path)                       # raises CalibError when RATINGS-PIN.md is missing or malformed
    if manifest_sha != pinned:
        raise Refused("manifest_sha_argument_differs_from_the_pin")
    mpath = Path(ratings_dir) / "manifest.json"
    if not mpath.is_file() or hashlib.sha256(mpath.read_bytes()).hexdigest() != pinned:
        raise Refused("ratings_manifest_sha_differs_from_the_pin")
    rd = Path(ratings_dir)
    if not read_only(rd) or any(not read_only(p) for p in rd.iterdir()):
        raise Refused("ratings_dir_not_read_only")
    man = json.loads(mpath.read_text())
    if hashlib.sha256((Path(dump_dir) / "manifest.json").read_bytes()).hexdigest() != man.get("dump_manifest_sha256"):
        raise Refused("dump_manifest_differs_from_the_ratings_manifest")
    return pinned


# ---------------------------------------------------------------- pure label arithmetic
def classify(rows: list[dict], end_ms: int, rule: str | None = None) -> dict[str, Any]:
    """The ledger rows of ONE card (stake 0.25 only) -> {ret, ret_press, censored}. Censored: no row, excluded, refused, or an exit past the tape end."""
    rows = [r for r in rows if abs(float(r.get("stake_sol", 0)) - STAKE_SOL) < 1e-9]
    if not rows:
        return {"ret": None, "ret_press": None, "censored": "no_row"}
    r = rows[0]
    if r.get("kind") != "trade":
        return {"ret": None, "ret_press": None, "censored": str(r.get("kind")) + (":" + str(r.get("exclusion_reason") or r.get("reason") or "")).rstrip(":")}
    x = (r.get("decision_ms") or {}).get("exit")
    if not isinstance(x, int) or x + EXIT_LEG_MS >= end_ms:
        return {"ret": None, "ret_press": None, "censored": "exit_at_or_after_tape_end"}
    legs = r["legs"]
    cell = legs[rule] if rule else legs[sorted(legs)[-1] if "total" not in legs else "total"]
    stake = STAKE_SOL * 1e9
    return {"ret": cell[f"flat_{LEG}"]["net_lamports"] / stake, "ret_press": cell[f"press_{LEG}"]["net_lamports"] / stake, "censored": None}


def median_labels(items: list[tuple[str, str, float | None]]) -> dict[str, dict[str, Any]]:
    """items: (t_d, card_sha, ret or None). Per set: y = 1 above the median of the known returns, 0 below; a tie with the median or a censored card gets y None."""
    by: dict[str, list[tuple[str, float | None]]] = {}
    for t, sha, ret in items:
        by.setdefault(t, []).append((sha, ret))
    out = {}
    for t, group in by.items():
        known = [v for _s, v in group if v is not None]
        med = float(np.median(known)) if known else None
        for sha, v in group:
            if v is None:
                out[sha] = {"y": None, "status": "censored"}
            elif v == med:
                out[sha] = {"y": None, "status": "tied"}
            else:
                out[sha] = {"y": 1 if v > med else 0, "status": "kept"}
    return out


# ---------------------------------------------------------------- pricing
def read_ledger(root: Path, bot: str) -> list[dict]:
    rows = []
    for p in sorted((Path(root) / bot).glob("trades-*.jsonl")):
        rows += [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    return rows


def price(tape: Path, scratch: Path, cards: list[dict], win: D.Window, rule: str | None = None, t_ds: list[int] | None = None) -> dict[str, Any]:
    scratch = D.check_path(scratch)
    D.allow_roots(scratch)
    guard = R.Hv7Guard(scratch / "v7")
    model = A.StubModel(script, guard=guard, latency_ms=50)          # the L arm's stub: no API call, no key
    reg = scratch / "reg.json"
    eng = ArenaEngine(scratch / "engine-ledger", reg, cfg=R.stack_config(FEED_DELAY_SLOTS), sps_fn=lambda: SPS)
    bot = R.bot_id("U")
    register_bot(bot, "abcdef1234567", win.block_start_ms, str(reg), deciding_stake=STAKE_SOL, model_ids=[], now_ms=win.block_start_ms - 60_000, verify_sha=False)
    run, info = D.make_runner(tape, scratch, win, t_ds=t_ds, engine=eng, model=model, guard=guard)
    grid = info["t_ds"]
    gset = set(grid)
    seen: set[tuple[str, str, str]] = set()
    orig_screen = run.screen

    def screen(T, *a, **k):
        orig_screen(T, *a, **k)
        if run.pending is not None:
            for c in run.pending["cards"].values():
                seen.add((D.iso_s(T), c["id"], H8.view_sha256(c)))

    run.screen = screen                      # type: ignore[method-assign]
    T = grid[0]
    while T < win.end_ms:
        run.sim_now = max(run.sim_now, T)
        run.poll_to(T)
        run.exits(T)                         # exits before entries, always
        m = T % R.HOUR_MS
        if m == R.T_D_OFFSET_MS and T in gset:
            run.screen(T)
        elif m == R.T_E_OFFSET_MS:
            run.enter(T)
        T += R.TICK_MS
    want = {(c["t_d"], c["pool"], c["card_sha"]) for c in cards}
    if seen != want:
        raise Refused("replayed_sets_differ_from_the_dump")
    ledger = read_ledger(scratch / "engine-ledger", bot)
    by_key: dict[tuple[str, int], list[dict]] = {}
    for r in ledger:
        by_key.setdefault((r.get("pool"), (r.get("stamp_ms") or {}).get("entry")), []).append(r)
    rule = rule or ("total" if "total" in rules_of(eng.cfg) else rules_of(eng.cfg)[0])
    out = {}
    for c in cards:
        t_e = D.iso_ms(c["t_d"]) + 45_000
        out[c["card_sha"]] = classify(by_key.get((c["pool"], t_e), []), win.end_ms, rule)
    return {"returns": out, "counters": dict(sorted(run.counters.items())), "hook_errors": len(run.hook_errors), "engine_counters": dict(sorted(eng.counters.items())),
            "rule": rule}


def run_labels(dump_dir: Path, ratings_dir: Path, manifest_sha: str, tape: Path, scratch: Path, out_dir: Path, win: D.Window = D.DEFAULT,
               pin_path: Path | None = None) -> dict[str, Any]:
    check_pin(ratings_dir, dump_dir, manifest_sha, pin_path)          # first: nothing else is opened before this passes
    out_dir = Path(out_dir)
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        raise Refused("out_not_empty")
    cards = [json.loads(x) for x in (Path(dump_dir) / "cards.jsonl").read_text().splitlines() if x.strip()]
    res = price(Path(tape), Path(scratch), cards, win)
    rets = res["returns"]
    lab = median_labels([(c["t_d"], c["card_sha"], rets[c["card_sha"]]["ret"]) for c in cards])
    out_dir.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps({"card_sha": c["card_sha"], "t_d": c["t_d"], "ret": rets[c["card_sha"]]["ret"], "ret_press": rets[c["card_sha"]]["ret_press"],
                               "y": lab[c["card_sha"]]["y"], "censored": rets[c["card_sha"]]["censored"]}, sort_keys=True, separators=(",", ":")) + "\n"
                   for c in cards).encode()
    (out_dir / "labels.jsonl").write_bytes(body)
    reasons: dict[str, int] = {}
    for v in rets.values():
        if v["censored"]:
            reasons[v["censored"]] = reasons.get(v["censored"], 0) + 1
    st = {"kept": 0, "tied": 0, "censored": 0}
    for v in lab.values():
        st[v["status"]] += 1
    man = {"hv8_labels": "manifest", "ratings_manifest_sha256": manifest_sha, "cards": len(cards), **st, "censored_reasons": dict(sorted(reasons.items())),
           "tape_end_excl": D.iso_s(win.end_ms), "rule": res["rule"], "leg": LEG, "stake_sol": STAKE_SOL, "labels_sha256": hashlib.sha256(body).hexdigest(),
           "runner_counters": res["counters"], "engine_counters": res["engine_counters"], "hook_errors": res["hook_errors"], "api_calls": 0,
           "replay_constants": {"feed_delay_slots": FEED_DELAY_SLOTS, "sps": SPS}}
    (out_dir / "labels-manifest.json").write_text(json.dumps(man, sort_keys=True, indent=1) + "\n")
    return {k: man[k] for k in ("hv8_labels", "cards", "kept", "tied", "censored", "censored_reasons", "labels_sha256", "api_calls")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HV8 labels (after RATINGS-PIN.md). Counts only; 0 API.")
    ap.add_argument("--dump", required=True)
    ap.add_argument("--ratings", required=True)
    ap.add_argument("--manifest-sha", required=True, help="the ratings manifest sha256; must equal the one in RATINGS-PIN.md at the job's git head")
    ap.add_argument("--tape", required=True)
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        res = run_labels(Path(a.dump), Path(a.ratings), a.manifest_sha, Path(a.tape), Path(a.scratch), Path(a.out))
    except (Refused, CAL.CalibError) as e:
        print(json.dumps({"hv8_labels": "refused", "why": str(e)}), file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - the class name only
        print(json.dumps({"hv8_labels": "error", "class": type(e).__name__}), file=sys.stderr)
        return 3
    print(json.dumps(res, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
