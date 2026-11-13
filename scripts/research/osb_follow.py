#!/usr/bin/env python3
"""osb_follow: follow-mode open-simulation driver for one FLOOR "open simulation book" (OSB). PAPER only; in-sample research, not a gate read.
Design: /data/mal/hunt-1008/ops/reports/2026-11-13/open-sim-board-design.md sections 0-2 and 7 (PR-1). Nothing here says any book makes money.

One long-lived process per book. It seeds the pool table once, then replays the FLOOR host code (tools/floor_lane_host.make_lane_host(live=False), or the plain HostV2 path
of scripts/research/floor_nov_paper.py) over closed tape hours as they appear in the manifest's tape_dir, one hour per host.run(prev_end, hour_end) call. Engine, pool table and
open positions stay in memory across hours. The tape_dir is only READ (a separate keeper hard-links verified hours there); this script never touches the tape source.

Manifest (JSON, required): book_id, title, host ("lane" | "hostv2"), rule_overrides {NAME: number} (only Q_STAR AGE_LO AGE_HI VOL_MIN_LAMPORTS R6_MAX TP HOLD_S), stakes_sol [..],
send_lamports, start_hour "YYYY-MM-DDTHH", seed_from_hour (>= SEED_MIN_H = 201 h before start_hour), tape_dir, out_root; optional: lane {gap_lookback_s, status_span_no_loss,
cheap_restart} (host "lane" only; replay never runs lane_start, so cheap_restart only labels the checkpoint), gap_exclusion "on"|"off" (default "on").
The sha256 of the canonical manifest is written to out_root/manifest.sha256 on first start; a later start with a different manifest is refused (a tweak is a new book).

Layout under out_root (same as floor_nov_paper.py, inside a per-run directory so floor_nov_pnl.py can read `book/ledger`): runs/NNNN/{ledger, bots.json, variant.json, decision log};
`book` is a symlink to the active run; md5-by-hour.jsonl; status.json; manifest.sha256.
Restart: no checkpoint semantics. A new start replays from start_hour into a NEW run directory and compares the decision md5 of every hour the stored md5-by-hour.jsonl covers.
Any mismatch = exit 4 with the stored list and the active `book` link untouched. The `book` link moves to the new run only once it has verified every stored hour.
Exit codes: 0 ok, 2 refused (manifest, path, override, pins), 4 md5 mismatch against the stored segment.
Usage: osb_follow.py MANIFEST.json [--once] [--max-hours N]
"""
import argparse, dataclasses, hashlib, json, os, re, sys, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools import arena_rules as R  # noqa: E402
from tools import arena_rule_l1 as L  # noqa: E402
from tools import floor_lane_host as H  # noqa: E402
from tools.arena_declog import DecisionLog  # noqa: E402
from tools.arena_paper import canon  # noqa: E402
from tools.arena_rules_v2 import HostV2, load_rules_v2  # noqa: E402
from tools.arena_stack_v2 import stack_config_for  # noqa: E402
from tools.arena_state import WARM_MIN_DEFAULT, ArenaState  # noqa: E402
from floor_replay_proof_reg import scratch_register  # noqa: E402
import floor_tape_paths as TP  # noqa: E402
from floor_gap_range_engine import GapRangeEngine  # noqa: E402

OSB_ROOT = "/data2/mal/scratch/floor-open-sim"  # the only root a tape_dir or out_root may sit in (the CLI does not expose an override)
HOUR_MS = 3_600_000
POLL_S = 60
EXIT_OK, EXIT_REFUSED, EXIT_MISMATCH = 0, 2, 4
MANIFEST_KEYS = {"book_id", "title", "host", "rule_overrides", "stakes_sol", "send_lamports", "start_hour", "seed_from_hour", "tape_dir", "out_root", "lane", "gap_exclusion"}
REQUIRED_KEYS = MANIFEST_KEYS - {"lane", "gap_exclusion"}
LANE_KEYS = {"gap_lookback_s", "status_span_no_loss", "cheap_restart"}
OVERRIDE_TYPES = {"Q_STAR": float, "AGE_LO": int, "AGE_HI": int, "VOL_MIN_LAMPORTS": float, "R6_MAX": float, "TP": float, "HOLD_S": float}


class Refused(Exception):
    """exit 2"""


class Mismatch(Exception):
    """exit 4"""


# ------------------------------------------------------------------------------------------------------------------------------ manifest
def load_manifest(path):
    try:
        m = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise Refused("manifest unreadable: %s" % e)
    if not isinstance(m, dict):
        raise Refused("manifest must be a JSON object")
    extra, missing = sorted(set(m) - MANIFEST_KEYS), sorted(REQUIRED_KEYS - set(m))
    if extra or missing:
        raise Refused("manifest keys: unknown %s, missing %s" % (extra, missing))
    if not isinstance(m["book_id"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", m["book_id"]):
        raise Refused("book_id must match [A-Za-z0-9_.-]{1,64}")
    if not isinstance(m["title"], str):
        raise Refused("title must be a string")
    if m["host"] not in ("lane", "hostv2"):
        raise Refused("host must be 'lane' or 'hostv2'")
    st = m["stakes_sol"]
    if not (isinstance(st, list) and st and all(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0 for x in st)):
        raise Refused("stakes_sol must be a non-empty list of positive numbers")
    if not (isinstance(m["send_lamports"], int) and not isinstance(m["send_lamports"], bool) and m["send_lamports"] >= 0):
        raise Refused("send_lamports must be a non-negative integer")
    for k in ("tape_dir", "out_root"):
        if not isinstance(m[k], str) or not m[k]:
            raise Refused("%s must be a path string" % k)
    for k in ("start_hour", "seed_from_hour"):
        try:
            H.parse_hour(m[k])
        except (R.HostError, TypeError) as e:
            raise Refused("%s: %s" % (k, e))
    if H.parse_hour(m["seed_from_hour"]) > H.parse_hour(m["start_hour"]) - H.SEED_MIN_H * HOUR_MS:  # the lane host's own check (floor_lane_host.main), applied to both hosts
        raise Refused("seed_from_hour must be at least %d h before start_hour" % H.SEED_MIN_H)
    if not isinstance(m["rule_overrides"], dict):
        raise Refused("rule_overrides must be an object")
    check_overrides(m["rule_overrides"])
    if m["host"] == "lane":
        lane = m.get("lane", {})
        if not isinstance(lane, dict) or set(lane) - LANE_KEYS:
            raise Refused("lane: allowed keys %s" % sorted(LANE_KEYS))
        for k in ("status_span_no_loss", "cheap_restart"):
            if k in lane and not isinstance(lane[k], bool):
                raise Refused("lane.%s must be true or false" % k)
        lb = lane.get("gap_lookback_s", H.LOOKBACK_MIN_S)
        if not (isinstance(lb, int) and not isinstance(lb, bool) and H.LOOKBACK_MIN_S <= lb <= H.LOOKBACK_MAX_S):
            raise Refused("lane.gap_lookback_s must be an integer in [%d, %d]" % (H.LOOKBACK_MIN_S, H.LOOKBACK_MAX_S))
    elif "lane" in m:
        raise Refused("'lane' belongs to host 'lane' only")
    if m.get("gap_exclusion", "on") not in ("on", "off"):
        raise Refused("gap_exclusion must be 'on' or 'off'")
    return m


def manifest_sha256(m):
    return hashlib.sha256(json.dumps(m, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def check_overrides(ov):
    for k, v in ov.items():
        if k not in OVERRIDE_TYPES:  # explicit refusal (an assert vanishes under python -O)
            raise Refused("refusing override of %r (allowed: %s)" % (k, sorted(OVERRIDE_TYPES)))
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
            raise Refused("override %s must be a positive number" % k)


def apply_overrides(ov):
    """Set the allowed names on tools.arena_rule_l1 (read at call time by the rule). HOLD_S also moves HOLD_MS exactly as arena_rule_l1 derives it at import
    (int(HOLD_S * 1000) + LEG_MS) and the lane host's BLOCK_MS exactly as floor_lane_host derives it (L_HOLD_MS + COOLDOWN_MS + 1_000 + DECISION_MARGIN_MS). Returns a restore function."""
    check_overrides(ov)
    names = [("L", L, n) for n in ("Q_STAR", "AGE_LO", "AGE_HI", "VOL_MIN_LAMPORTS", "R6_MAX", "TP", "HOLD_S", "HOLD_MS")] + [("H", H, n) for n in ("L_HOLD_MS", "BLOCK_MS")]
    saved = [(mod, n, getattr(mod, n)) for _, mod, n in names]

    def restore():
        for mod, n, v in saved:
            setattr(mod, n, v)
    for k, v in ov.items():
        setattr(L, k, OVERRIDE_TYPES[k](v))
    if "HOLD_S" in ov:
        L.HOLD_MS = int(L.HOLD_S * 1000) + L.LEG_MS
        H.L_HOLD_MS = L.HOLD_MS
        H.BLOCK_MS = L.HOLD_MS + L.COOLDOWN_MS + 1_000 + H.DECISION_MARGIN_MS
    return restore


# ------------------------------------------------------------------------------------------------------------------------------ paths
def guard_paths(m, roots):
    """tape_dir and out_root must sit under the allowed roots (floor_tape_paths.refusal: sealed / live / probe / q50 ... refused first). Returns nothing."""
    for k in ("tape_dir", "out_root"):
        why = TP.refusal(m[k], roots)
        if why:
            raise Refused("%s: %s" % (k, why))
    if R._inside(m["out_root"], R.ARENA_OUTPUT_ROOT):
        raise Refused("refusing to write inside the arena output root")


def hour_key(ms):
    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def hour_file(tape_dir, ms):
    k = hour_key(ms)
    for suf in (".jsonl", ".jsonl.zst"):
        p = Path(tape_dir) / ("tape-%s%s" % (k, suf))
        if p.exists():
            return p
    return None


# ------------------------------------------------------------------------------------------------------------------------------ book
def build_book(m, run_dir, *, table_mode=None, engine_kw=None):
    """Seed + construct (nothing run yet). -> (host, engine, cfg). table_mode, engine_kw: tests only (synthetic tapes have no create/complete rows and too few rows for the engine's slot clock)."""
    start_ms = H.parse_hour(m["start_hour"])
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    ledger, registry = str(run_dir / "ledger"), str(run_dir / "bots.json")
    ov = m["rule_overrides"]
    (run_dir / "variant.json").write_text(json.dumps({"variant": {k: getattr(L, k) for k in ov}, "frozen_rule": not ov, "registered_frozen_sha_is_not_the_rule": bool(ov), "send": m["send_lamports"],
                                                       "book_id": m["book_id"], "host": m["host"]}, indent=1, sort_keys=True) + "\n")
    scratch_register(registry, start_ms)
    cfg = dataclasses.replace(stack_config_for(2, R.DEFAULT_FEED_DELAY_SLOTS), send=m["send_lamports"], stakes_sol=tuple(float(x) for x in m["stakes_sol"]))
    engine = GapRangeEngine(ledger, registry, cfg=cfg, gap_exclusion=(m.get("gap_exclusion", "on") == "on"), **(engine_kw or {}))
    warm_ms = start_ms - WARM_MIN_DEFAULT * R.MIN_MS
    if m["host"] == "lane":
        lane = m.get("lane", {})
        host = H.make_lane_host(root=str(run_dir), ledger_root=ledger, registry=registry, tape_dir=m["tape_dir"], clock_start_ms=start_ms, live=False, binding=None, start_ms=warm_ms,
                                lookback_s=int(lane.get("gap_lookback_s", H.LOOKBACK_MIN_S)), table_mode=table_mode, engine=engine,
                                status_span_no_loss=bool(lane.get("status_span_no_loss", False)), cheap_restart=bool(lane.get("cheap_restart", False)))
    else:
        rules = load_rules_v2([H.BOT_ID])
        if table_mode is not None:
            for r in rules:
                r.table_mode = table_mode
        hc = R.HostConfig(root=str(run_dir), registry=registry, frozen_sha=H.FROZEN_SHA, clock_start_ms=start_ms, live=False, verbose=False)
        host = HostV2(hc, rules, ArenaState(m["tape_dir"], start_ms=warm_ms, warm_min=WARM_MIN_DEFAULT), engine)
    n = H.rebuild_table_since(host.table, m["tape_dir"], warm_ms, H.parse_hour(m["seed_from_hour"]), str(run_dir / "seed-tmp"))
    print(canon({"book": m["book_id"], "table_seeded_rows": n, "seed_from_hour": m["seed_from_hour"], "run": run_dir.name}), flush=True)
    host.resume()
    return host, engine, cfg


def decision_md5(run_dir):
    return H.decision_digest(run_dir)


def count_rows(run_dir):
    rows = DecisionLog(str(run_dir)).rows(H.BOT_ID)
    return {"n_decision_rows": len(rows), "n_entries": sum(1 for r in rows if r.get("kind") == "entry"), "n_exits": sum(1 for r in rows if r.get("kind") == "exit")}


PROCESS_KEYS = ("engine_id", "trade_id")  # per-process identity the engine writes into every ledger row (random engine id and the id derived from it); not part of a decision or a price


def ledger_rows(d):
    """[(relative file, row)] of every ledger row under d, sorted by file, `start` rows (pid, wall clock) dropped and PROCESS_KEYS removed."""
    d = Path(d)
    out = []
    for f in sorted(p for p in d.rglob("*") if p.is_file()):
        for ln in f.read_bytes().splitlines():
            if ln.strip():
                r = json.loads(ln)
                if r.get("kind") != "start":
                    out.append((f.parent.name + "/" + f.name, {k: v for k, v in r.items() if k not in PROCESS_KEYS}))
    return out


def tree_md5(d):
    """md5 of the ledger projection (ledger_rows, canonical JSON): what 'equal ledger md5' means in the equivalence proof."""
    return hashlib.md5(canon(ledger_rows(d)).encode()).hexdigest()


def read_stored(out_root):
    p = Path(out_root) / "md5-by-hour.jsonl"
    out = {}
    if p.exists():
        for ln in p.read_text().splitlines():
            if ln.strip():
                d = json.loads(ln)
                out[d["hour"]] = d
    return out


def _atomic_json(path, obj):
    H._atomic_write(Path(path), obj)


def _next_run_dir(out_root):
    runs = Path(out_root) / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    n = 1 + max([int(p.name) for p in runs.iterdir() if p.name.isdigit()] or [0])
    return runs / ("%04d" % n)


def _flip(out_root, run_dir):
    link, tmp = Path(out_root) / "book", Path(out_root) / ".book.new"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(os.path.relpath(run_dir, out_root), tmp)
    os.replace(tmp, link)


# ------------------------------------------------------------------------------------------------------------------------------ follow
def follow(manifest_path, once=False, max_hours=None, *, sleep_fn=time.sleep, _roots=None, _table_mode=None, _engine_kw=None):
    """-> exit code. _roots / _table_mode / _engine_kw are test-only (the CLI does not expose them)."""
    try:
        return _follow(manifest_path, once, max_hours, sleep_fn, _roots or (OSB_ROOT,), _table_mode, _engine_kw)
    except Refused as e:
        print("osb_follow: refused: %s" % e, file=sys.stderr)
        return EXIT_REFUSED
    except Mismatch as e:
        print("osb_follow: MD5 MISMATCH: %s" % e, file=sys.stderr)
        return EXIT_MISMATCH
    except (R.HostError, R.HostFatal, SystemExit) as e:
        print("osb_follow: %s: %s" % (type(e).__name__, str(e)[:300]), file=sys.stderr)
        return EXIT_REFUSED


def _follow(manifest_path, once, max_hours, sleep_fn, roots, table_mode, engine_kw=None):
    m = load_manifest(manifest_path)
    guard_paths(m, roots)
    TP.require_ok(m["tape_dir"], roots)
    out_root = Path(m["out_root"])
    out_root.mkdir(parents=True, exist_ok=True)
    sha = manifest_sha256(m)
    sf = out_root / "manifest.sha256"
    if sf.exists():
        if sf.read_text().strip() != sha:
            raise Refused("manifest differs from the one this book started with (%s): a tweak is a new book" % sf.read_text().strip()[:12])
    else:
        sf.write_text(sha + "\n")
    H.verify_pins()
    restore = apply_overrides(m["rule_overrides"])
    try:
        stored = read_stored(out_root)
        run_dir = _next_run_dir(out_root)
        host, engine, cfg = build_book(m, run_dir, table_mode=table_mode, engine_kw=engine_kw)
        verified = not stored  # nothing stored: this run is the book from its first hour
        if verified:
            _flip(out_root, run_dir)
        last_stored = max(stored) if stored else None
        cursor, done = H.parse_hour(m["start_hour"]), 0
        while True:
            f = hour_file(m["tape_dir"], cursor)
            if f is None:
                if once or (max_hours is not None and done >= max_hours):
                    break
                sleep_fn(POLL_S)
                continue
            why = TP.refusal(str(f), roots)
            if why:
                raise Refused(why)
            end = cursor + HOUR_MS
            host.run(cursor, end)
            key, md5 = hour_key(cursor), decision_md5(run_dir)
            if key in stored and stored[key]["decision_md5"] != md5:
                raise Mismatch("hour %s: stored %s, replay %s (run %s kept for inspection; stored list and `book` untouched)" % (key, stored[key]["decision_md5"], md5, run_dir.name))
            if not verified and key == last_stored:
                verified = True
                _flip(out_root, run_dir)
            if verified and key not in stored:
                with open(out_root / "md5-by-hour.jsonl", "a") as fh:
                    fh.write(canon({"hour": key, "end_ms": end, "decision_md5": md5}) + "\n")
            _atomic_json(out_root / "status.json", {"book_id": m["book_id"], "manifest_sha256": sha, "last_hour": key, "last_hour_end_ms": end, "wall_time_utc": datetime.now(timezone.utc).isoformat(),
                                                    "run": run_dir.name, "active": verified, "catching_up_to_stored_hours": not verified, "decision_md5": md5, "stakes_sol": list(cfg.stakes_sol), **count_rows(run_dir)})
            cursor, done = end, done + 1
            if max_hours is not None and done >= max_hours:
                break
        if stored and not verified:
            print("osb_follow: tape does not yet cover every stored hour (%s); `book` still points at the previous run" % last_stored, file=sys.stderr)
        return EXIT_OK
    finally:
        restore()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("manifest")
    ap.add_argument("--once", action="store_true", help="process the closed hours present now, then exit")
    ap.add_argument("--max-hours", type=int, default=None, help="stop after this many processed hours (tests, catch-up)")
    a = ap.parse_args(argv)
    return follow(a.manifest, once=a.once, max_hours=a.max_hours)


if __name__ == "__main__":
    raise SystemExit(main())
