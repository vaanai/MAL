#!/usr/bin/env python3
"""p35_md5_proof: the md5 decision-equivalence replay proofs of P35 item (b) on the QP-pinned arena slice (QP OK on #945, comment 6424863148;
QP-RULING-1026 Q4 / C5). PAPER only. No key, no network.

SLICE (production): arena tape hours [2026-10-20T00:00Z, 2026-10-25T00:00Z) ONLY.
  * The tool links those hour files into <scratch>/tape/ as symlinks, chosen by NAME (a glob). No other hour is linked, opened or read.
  * Nothing at or after 2026-10-25T00Z, nothing before 2026-10-20T00Z.
  * These are walk-2, in-sample hours for FLOOR, never out-of-sample.
  * Every reader replays from the slice start, so the 365-min warm-up and 26 h of pool-table prints lie inside the slice.
  * t0 (book clock) 2026-10-21T04:00Z; HO_TEST 2026-10-22T10:00Z and 2026-10-23T10:00Z; end 2026-10-24T23:00Z (24 h holds exit).
BOOK: the frozen FLOOR rule (tools/arena_rule_l1.py, bot l1-floor-v2), hosted by the registered host tools/arena_rules_v2.HostV2.
  * Stack v2 (160,000 per send), feed delay 7.
  * Registered in a SCRATCH registry only: MAL_ARENA_TEST=1 is required for the past clock; the real bots.json is never opened.
  * frozen_sha = this checkout's HEAD, which must be clean.

MODES
  ckpt   (#957) R0 = HostV2 + ArenaEngine + ArenaState, continuous [t0, end).
         R1 = the same with EngineCkpt + StateCkpt, writing an engine and an ArenaState checkpoint at each HO_TEST.
         PASS iff ALL hold:
           * R1's decision md5 and ledger md5 equal R0's (writing checkpoints changes no decision);
           * a FRESH process restoring each checkpoint reproduces its state digest (`python -m tools.arena_paper_ckpt digest`,
             `python -m tools.arena_state_ckpt digest`).
         The downstream decision equivalence after a restore needs the host's hand-off (HostV3): that is mode `host` of #962.
OUTPUT: one JSON summary (stdout and <scratch>/summary.json). It holds md5s, counts, open-position counts at the HOs and digests: never a pool, mint,
price, fill or P&L. After the md5s, the job DELETES every ledger, decision log, pool table and checkpoint under <scratch>, and prints the deletion line.
SCRATCH: must be a new directory under /data2/mal/p35/bp1/; it is created with mode 0700.
TEST MODE (--test): any tape dir except the production one, any instants and rule; used by tools/test_p35_md5_proof.py on synthetic tape.
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
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

PROD_TAPE = "/data/mal/arena/tape"
SCRATCH_ROOT = "/data2/mal/p35/bp1"
HOUR_MS = 3_600_000
DAY_MS = 86_400_000


def iso_ms(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)


PROD = {"slice_from": iso_ms("2026-10-20T00:00:00Z"), "slice_to": iso_ms("2026-10-25T00:00:00Z"), "t0": iso_ms("2026-10-21T04:00:00Z"),
        "ho": [iso_ms("2026-10-22T10:00:00Z"), iso_ms("2026-10-23T10:00:00Z")], "end": iso_ms("2026-10-24T23:00:00Z"), "rule": "l1-floor-v2",
        "stack_version": 2, "feed_delay_slots": 7}
VOLATILE_LEDGER = ("engine_id", "pid", "written_ms", "code_head", "code_dirty")
CHAIN = ("seq", "prev", "sha")
HANDOFF_KINDS = frozenset({"segment_end", "segment_resume", "handoff_down_exit", "handoff_down_missed", "handoff_stale_switch"})


class Refused(Exception):
    pass


# ---- the slice ----------------------------------------------------------------------------------------------------------
def hour_of(name: str) -> int | None:
    if not name.startswith("tape-"):
        return None
    try:
        return int(datetime.strptime(name[5:18], "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except ValueError:
        return None


def link_slice(src: Path, dst: Path, lo: int, hi: int) -> dict[str, Any]:
    """Symlink one file per hour in [lo, hi) from src into dst (.jsonl if it exists, else .jsonl.zst: what ArenaState would read). Names only."""
    dst.mkdir(parents=True, exist_ok=False)
    by: dict[int, dict[str, Path]] = {}
    for p in src.glob("tape-*.jsonl*"):
        h = hour_of(p.name)
        kind = "jsonl" if p.name.endswith(".jsonl") else "zst" if p.name.endswith(".jsonl.zst") else None
        if h is None or kind is None or not (lo <= h < hi):
            continue
        by.setdefault(h, {})[kind] = p
    for h, d in sorted(by.items()):
        p = d.get("jsonl") or d["zst"]
        assert lo <= h < hi
        (dst / p.name).symlink_to(p)
    want = (hi - lo) // HOUR_MS
    return {"hours_linked": len(by), "hours_in_slice": want, "hours_missing": want - len(by),
            "first": min(by) if by else None, "last": max(by) if by else None}


# ---- md5s ---------------------------------------------------------------------------------------------------------------
def decision_rows(root: Path, bot: str) -> list[dict]:
    from tools.arena_declog import DecisionLog

    return [{k: v for k, v in r.items() if k not in CHAIN} for r in DecisionLog(root).rows(bot) if r.get("kind") not in HANDOFF_KINDS]


def ledger_rows(ledger_root: Path, bot: str) -> list[dict]:
    from tools.arena_paper import Ledger

    return [{k: v for k, v in r.items() if k not in VOLATILE_LEDGER} for r in Ledger(ledger_root).read_bot(bot) if r.get("kind") != "start"]


def md5_rows(rows: Sequence[dict]) -> str:
    return hashlib.md5("\n".join(json.dumps(r, sort_keys=True, separators=(",", ":")) for r in rows).encode()).hexdigest()


def pid_hash(pid: str) -> str:
    return hashlib.sha256(pid.encode()).hexdigest()[:16]


def counts(rows: Sequence[dict], key: str = "kind") -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[str(r.get(key))] = out.get(str(r.get(key)), 0) + 1
    return dict(sorted(out.items()))


# ---- one run ------------------------------------------------------------------------------------------------------------
def build(run_dir: Path, tape: Path, cfgd: dict[str, Any], *, ckpt: bool, host_cls: Any = None, host_kw: dict | None = None):
    """A host over the linked slice with its own scratch registry, ledger and decision root."""
    from tools import arena_rules as R
    from tools import arena_rules_v2 as V2
    from tools.arena_stack_v2 import stack_config_for

    rules = load_rules(cfgd)
    reg = run_dir / "bots.json"
    head = cfgd["frozen_sha"]
    if not reg.exists():
        R.register_rules(rules, str(reg), head, cfgd["t0"], now_ms=cfgd["t0"] - 1, verify_sha=False)
    stack = stack_config_for(cfgd["stack_version"], cfgd["feed_delay_slots"])
    code = cfgd.get("code_state")
    eng_kw = {"cfg": stack}
    if code is not None:
        eng_kw["code_state_fn"] = lambda: tuple(code)
    if ckpt:
        from tools.arena_paper_ckpt import EngineCkpt
        from tools.arena_state_ckpt import StateCkpt

        state = StateCkpt(tape, start_ms=cfgd["slice_from"])
        engine = EngineCkpt(run_dir / "ledger", reg, **eng_kw)
    else:
        from tools.arena_paper import ArenaEngine
        from tools.arena_state import ArenaState

        state = ArenaState(tape, start_ms=cfgd["slice_from"])
        engine = ArenaEngine(run_dir / "ledger", reg, **eng_kw)
    if cfgd.get("sps_fixed"):
        engine.sps_fn = lambda: float(cfgd["sps_fixed"])
    hcfg = R.HostConfig(root=str(run_dir / "rules"), registry=str(reg), frozen_sha=head, clock_start_ms=cfgd["t0"], live=False)
    cls = host_cls or V2.HostV2
    return cls(hcfg, rules, state, engine, **(host_kw or {}))


def load_rules(cfgd: dict[str, Any]) -> list:
    from tools import arena_rules_v2 as V2

    if cfgd.get("test_rule"):  # test mode only: a rule module path:factory
        import importlib

        mod, fn = cfgd["test_rule"].split(":")
        r = getattr(importlib.import_module(mod), fn)()
        if getattr(r, "table_mode", None) is None:
            r.table_mode = "first_print"
        return [r]
    return V2.load_rules_v2([cfgd["rule"]])


def hooked(host: Any, on_step: Callable[[Any, int], None]) -> Any:
    """Wrap host.step so that on_step(host, T) runs after every tick (report-only hooks: counts and checkpoints; no decision)."""
    orig = host.step

    def step(T: int) -> None:
        orig(T)
        on_step(host, T)

    host.step = step
    return host


def open_count(host: Any, bot: str) -> int:
    return sum(1 for p in getattr(host.engine, "positions", {}).values() if getattr(p, "bot_id", None) == bot)


def run_continuous(run_dir: Path, tape: Path, cfgd: dict[str, Any], *, ckpt: bool, ckpt_at: Sequence[int] = ()) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=False)
    h = build(run_dir, tape, cfgd, ckpt=ckpt)
    bot = h.rules[0].bot_id
    opens: dict[str, int] = {}
    cks: list[dict[str, Any]] = []

    def on_step(host: Any, T: int) -> None:
        if T % HOUR_MS == 0:
            opens[str(T)] = open_count(host, bot)
        if T in ckpt_at:
            e = host.engine.checkpoint(run_dir / f"engine-{T}.ckpt", at_ms=T)
            s = host.state.checkpoint(run_dir / f"state-{T}.ckpt", at_ms=T)
            cks.append({"at_ms": T, "engine": {k: e[k] for k in ("sha256", "state_digest", "bytes", "positions", "pools")},
                        "state": {k: s[k] for k in ("sha256", "state_digest", "bytes", "keys")}})

    hooked(h, on_step)
    t_wall = time.monotonic()
    h.resume()
    h.run(cfgd["t0"], cfgd["end"])
    dec, led = decision_rows(run_dir / "rules", bot), ledger_rows(run_dir / "ledger", bot)
    return {"decision_md5": md5_rows(dec), "ledger_md5": md5_rows(led), "decisions": counts(dec), "ledger": counts(led),
            "excluded_by_reason": counts([r for r in led if r.get("kind") == "excluded"], "exclusion_reason"), "open_at_hour": opens,
            "checkpoints": cks, "wall_s": round(time.monotonic() - t_wall, 1), "hook_errors": len(getattr(h, "hook_errors", []))}


def fresh_digest(kind: str, path: Path, tape: Path) -> dict[str, Any]:
    cmd = [sys.executable, "-m", "tools.arena_paper_ckpt", "digest", str(path)] if kind == "engine" else \
          [sys.executable, "-m", "tools.arena_state_ckpt", "digest", str(path), "--tape-dir", str(tape)]
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent))
    try:
        return {"rc": p.returncode, **json.loads(p.stdout.strip().splitlines()[-1])}
    except (ValueError, IndexError):
        return {"rc": p.returncode, "error": (p.stderr or "")[-200:]}


# ---- scratch, cleanup -------------------------------------------------------------------------------------------------------
def make_scratch(path: str, test: bool) -> Path:
    p = Path(path)
    r = str(p.resolve())
    if not test and not r.startswith(SCRATCH_ROOT + "/"):
        raise Refused(f"scratch must be under {SCRATCH_ROOT}/ (QP-RULING-1026 Q4)")
    if r.startswith("/data/mal/") or "meta-capture" in r:
        raise Refused("scratch may not be under /data/mal")
    if p.exists() and any(p.iterdir()):
        raise Refused(f"scratch {p} is not empty: a proof runs on a new directory")
    p.mkdir(parents=True, exist_ok=True)
    os.chmod(p, 0o700)
    return p


def cleanup(scratch: Path) -> dict[str, int]:
    """Delete every ledger, decision log, pool table, checkpoint and registry under scratch (the in-sample P&L never outlives the job). Keeps summary.json."""
    n = b = 0
    for p in sorted(scratch.rglob("*"), reverse=True):
        if p.is_symlink() or p.is_file():
            if p.name == "summary.json" and p.parent == scratch:
                continue
            if not p.is_symlink():
                b += p.stat().st_size
            p.unlink()
            n += 1
        elif p.is_dir():
            p.rmdir()
    return {"files_deleted": n, "bytes_deleted": b}


def preflight(test: bool, tape_src: str) -> dict[str, Any]:
    if os.environ.get("MAL_ARENA_TEST") != "1":
        raise Refused("set MAL_ARENA_TEST=1: the book is registered in a scratch registry with a past clock (test-only override; the real bots.json is never opened)")
    if test and str(Path(tape_src).resolve()) == PROD_TAPE:
        raise Refused("test mode refuses the production tape")
    if not test and tape_src != PROD_TAPE:
        raise Refused(f"production mode reads only {PROD_TAPE} (the slice is linked by name)")
    from tools.arena_paper import process_code_state

    head, dirty = process_code_state()
    if not test and (head is None or dirty is not False):
        raise Refused(f"the checkout must be clean at a known HEAD (head={head}, dirty={dirty})")
    return {"head": head, "dirty": dirty}


def run_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Execute one run described by a spec (in THIS process). Kinds: continuous (#957); seg1 / seg2 (#962, HostV3)."""
    kind = spec["kind"]
    if kind == "continuous":
        return run_continuous(Path(spec["run_dir"]), Path(spec["tape"]), spec["cfgd"], ckpt=spec["ckpt"], ckpt_at=spec.get("ckpt_at", ()))
    ext = SPEC_KINDS.get(kind)
    if ext is None:
        raise Refused(f"unknown run kind {kind!r}")
    return ext(spec)


SPEC_KINDS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def run_fresh(scratch: Path, name: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Run a spec in a FRESH python process (memory is returned between runs; a segment 2 shares no object with segment 1)."""
    sp = scratch / f"spec-{name}.json"
    out = scratch / f"result-{name}.json"
    sp.write_text(json.dumps(spec))
    p = subprocess.run([sys.executable, "-m", "tools.p35_md5_proof", "_run", "--spec", str(sp), "--out", str(out)], cwd=str(Path(__file__).resolve().parent.parent),
                       capture_output=True, text=True)
    if p.returncode != 0 or not out.exists():
        raise RuntimeError(f"run {name} failed rc={p.returncode}: {(p.stderr or '')[-400:]}")
    return json.loads(out.read_text())


def ckpt_mode(scratch: Path, tape: Path, cfgd: dict[str, Any]) -> dict[str, Any]:
    base = {"kind": "continuous", "tape": str(tape), "cfgd": cfgd}
    r0 = run_fresh(scratch, "R0", {**base, "run_dir": str(scratch / "R0"), "ckpt": False})
    r1 = run_fresh(scratch, "R1", {**base, "run_dir": str(scratch / "R1"), "ckpt": True, "ckpt_at": list(cfgd["ho"])})
    restores = []
    for c in r1["checkpoints"]:
        e = fresh_digest("engine", scratch / "R1" / f"engine-{c['at_ms']}.ckpt", tape)
        s = fresh_digest("state", scratch / "R1" / f"state-{c['at_ms']}.ckpt", tape)
        restores.append({"at_ms": c["at_ms"], "engine_ok": e.get("state_digest") == c["engine"]["state_digest"],
                         "state_ok": s.get("state_digest") == c["state"]["state_digest"], "engine_rc": e.get("rc"), "state_rc": s.get("rc"),
                         "open_positions": c["engine"]["positions"]})
    ok = (r0["decision_md5"] == r1["decision_md5"] and r0["ledger_md5"] == r1["ledger_md5"] and len(restores) == len(cfgd["ho"])
          and all(x["engine_ok"] and x["state_ok"] for x in restores) and r0["hook_errors"] == 0 and r1["hook_errors"] == 0)
    return {"R0": r0, "R1": r1, "restores": restores, "verdict": "PASS" if ok else "FAIL",
            "checks": {"R1_equals_R0_decisions": r0["decision_md5"] == r1["decision_md5"], "R1_equals_R0_ledger": r0["ledger_md5"] == r1["ledger_md5"],
                       "fresh_restores_reproduce_digests": all(x["engine_ok"] and x["state_ok"] for x in restores)}}


MODES: dict[str, Callable[[Path, Path, dict[str, Any]], dict[str, Any]]] = {"ckpt": ckpt_mode}


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "_run":  # internal: one run in a fresh process
        ip = argparse.ArgumentParser()
        ip.add_argument("_run")
        ip.add_argument("--spec", required=True)
        ip.add_argument("--out", required=True)
        ia = ip.parse_args(argv)
        res = run_spec(json.loads(Path(ia.spec).read_text()))
        Path(ia.out).write_text(json.dumps(res))
        return 0
    ap = argparse.ArgumentParser(description="P35 md5 replay proofs on the QP-pinned arena slice (counts and md5s only; scratch deleted in the job)")
    ap.add_argument("mode", choices=sorted(MODES))
    ap.add_argument("--scratch", required=True, help=f"a new directory under {SCRATCH_ROOT}/")
    ap.add_argument("--tape-src", default=PROD_TAPE)
    ap.add_argument("--test", action="store_true", help="test mode: synthetic tape and instants (refuses the production tape)")
    ap.add_argument("--test-config", default=None, help="test mode: JSON with slice_from, slice_to, t0, ho, end, test_rule, stack_version, ...")
    a = ap.parse_args(argv)
    t_start = time.time()
    try:
        pre = preflight(a.test, a.tape_src)
        cfgd = dict(PROD)
        if a.test:
            cfgd.update(json.loads(a.test_config or "{}"))
        elif a.test_config:
            raise Refused("--test-config is test mode only")
        cfgd["frozen_sha"] = cfgd.get("frozen_sha") or pre["head"]
        scratch = make_scratch(a.scratch, a.test)
    except Refused as exc:
        print(json.dumps({"refused": str(exc)}), file=sys.stderr)
        return 2
    summary: dict[str, Any] = {"schema": "p35_md5_proof_v1", "mode": a.mode, "head": pre["head"], "dirty": pre["dirty"], "test": a.test,
                               "instants": {k: cfgd[k] for k in ("slice_from", "slice_to", "t0", "ho", "end")}, "rule": cfgd.get("test_rule") or cfgd["rule"],
                               "disclosure": "walk-2 in-sample hours for FLOOR, never OOS; counts and md5s only; scratch ledger deleted in this job"}
    try:
        summary["slice"] = link_slice(Path(a.tape_src), scratch / "tape", cfgd["slice_from"], cfgd["slice_to"])
        summary["result"] = MODES[a.mode](scratch, scratch / "tape", cfgd)
        summary["verdict"] = summary["result"]["verdict"]
    except Exception as exc:  # noqa: BLE001 - the scratch is deleted whatever happened
        summary["verdict"] = "ERROR"
        summary["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    finally:
        summary["wall_s"] = round(time.time() - t_start, 1)
        (scratch / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True))
        summary["deleted"] = cleanup(scratch)
        (scratch / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True))
    print(json.dumps(summary, sort_keys=True))
    print(f"DELETED scratch ledgers, decision logs, tables and checkpoints: files={summary['deleted']['files_deleted']} bytes={summary['deleted']['bytes_deleted']}")
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
