#!/usr/bin/env python3
"""osb_follow: follow-mode open-simulation driver for one FLOOR "open simulation book" (OSB). PAPER only; in-sample research, not a gate read.
Design: /data/mal/hunt-1008/ops/reports/2026-11-13/open-sim-board-design.md sections 0-2 and 7 (PR-1). Nothing here says any book makes money.

One long-lived process per book. It seeds the pool table once, then replays the FLOOR host code (tools/floor_lane_host.make_lane_host(live=False), or the plain HostV2 path
of scripts/research/floor_nov_paper.py) over closed tape hours as they appear in the manifest's tape_dir, one hour per host.run(prev_end, hour_end) call. Engine, pool table and
open positions stay in memory across hours. The tape_dir is only READ (a separate keeper hard-links verified hours there); this script never touches the tape source.

Manifest (JSON, required): book_id, title, host ("lane" | "hostv2"), rule_overrides {NAME: number} (only Q_STAR AGE_LO AGE_HI VOL_MIN_LAMPORTS R6_MAX TP HOLD_S), stakes_sol [..],
send_lamports, start_hour "YYYY-MM-DDTHH", seed_from_hour (>= SEED_MIN_H = 201 h before start_hour), tape_dir, out_root; optional: lane {gap_lookback_s, status_span_no_loss,
cheap_restart} (host "lane" only; replay never runs lane_start, so cheap_restart only labels the checkpoint), gap_exclusion "on"|"off"|"hold" (default "on"; "hold" = a gap-in-hold twin: the feed_gap exclusion only for a gap in the entry or exit window,
trades with a gap elsewhere in the hold priced and flagged gap_in_hold / blind_s / gap_count, see scripts/research/floor_gap_range_engine.py), twin_of "<book_id>" (only with gap_exclusion "hold" or with
pricing_lat_entry_s), pricing_lat_entry_s (a LATENCY twin; QP ruling 2026-11-14 osb latency twin, sections 2 and 3: the TOTAL chain time in seconds from the decision to the entry landing, exactly one of
LATENCY_VALUES_S = 3.010, 4.085, 5.590, only together with twin_of, gap_exclusion "on" or absent; the engine converts it to slots with its own measured seconds per slot; the exit and every decision are the strict
book's, so decisions.md5 equals the strict book's). A latency twin differs from its strict book in no other key than book_id, title, out_root, twin_of and pricing_lat_entry_s (twin_manifest_diff): the board checks
it for every pair, and the driver checks it at start when given --strict-manifest PATH (refused, exit 2, on a difference).
The sha256 of the canonical manifest is written to out_root/manifest.sha256 on first start; a later start with a different manifest is refused (a tweak is a new book).

Layout under out_root (same as floor_nov_paper.py, inside a per-run directory so floor_nov_pnl.py can read `book/ledger`): runs/NNNN/{ledger, bots.json, variant.json, decision log};
`book` is a symlink to the active run; md5-by-hour.jsonl; status.json; manifest.sha256.
Restart: no checkpoint semantics. A new start replays from start_hour into a NEW run directory and compares the decision md5 of every hour the stored md5-by-hour.jsonl covers.
Any mismatch = exit 4 with the stored list and the active `book` link untouched. The `book` link moves to the new run only once it has verified every stored hour.
Version pin: code.sha256 (written at first start) is the sha256 of {path: sha256} of this script, floor_gap_range_engine.py, floor_lane_host.py, floor_replay_proof_reg.py and every file
floor_lane_host.verify_pins() lists; a later start whose value differs is refused (a changed simulator is a new book). md5-by-hour.jsonl stores decision_md5 AND ledger_md5 per hour; both are compared on restart.
One process per book: out_root/.osb.lock is flock'ed for the process lifetime. While an hour file is missing status.json carries waiting_for_hour / waiting_since_utc and a fresh wall_time_utc each poll.
A torn last line of md5-by-hour.jsonl (a partial write) is dropped, said on stderr, and the file is rewritten without it; that hour is verified or appended again.
decisions.md5 (out_root, rewritten after every hour of an active book): md5 of the ordered (position_id, entry decision_ms, exit decision_ms) rows of the active run's ledger, for every book,
so a twin can be compared with its strict book (a pricing-only twin has the same value).
Exit codes: 0 ok (with --once and stored hours not yet covered by the tape: 0, `book` unmoved, a message on stderr); 1 uncaught error (OSError, bad JSON, disk full: a traceback);
2 refused (manifest, path, override, pins, code version, lock held by another process); 3 host fatal during run (R.HostFatal: a log or ledger write failed); 4 md5 mismatch against the stored segment.
Usage: osb_follow.py MANIFEST.json [--once] [--max-hours N] [--strict-manifest PATH]
"""
import argparse, dataclasses, fcntl, hashlib, json, math, os, re, sys, time
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
EXIT_OK, EXIT_REFUSED, EXIT_FATAL, EXIT_MISMATCH = 0, 2, 3, 4
MANIFEST_KEYS = {"book_id", "title", "host", "rule_overrides", "stakes_sol", "send_lamports", "start_hour", "seed_from_hour", "tape_dir", "out_root", "lane", "gap_exclusion", "twin_of", "pricing_lat_entry_s"}
REQUIRED_KEYS = MANIFEST_KEYS - {"lane", "gap_exclusion", "twin_of", "pricing_lat_entry_s"}
LATENCY_VALUES_S = (3.010, 4.085, 5.590)  # QP ruling 2026-11-14 osb latency twin, section 3: declared before any output; nothing else is accepted (a new value needs a new erratum)
TWIN_FREE_KEYS = {"book_id", "title", "out_root", "twin_of", "pricing_lat_entry_s"}  # the only manifest keys a latency twin may differ from its strict book in (section 2)
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
    if any(x > 2.0 for x in st) or any(b <= a for a, b in zip(st, st[1:])):
        raise Refused("stakes_sol: each value in (0, 2.0], strictly ascending")
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
    if not isinstance(m["rule_overrides"], dict):
        raise Refused("rule_overrides must be an object")
    check_overrides(m["rule_overrides"])
    age_hi, age_lo = m["rule_overrides"].get("AGE_HI", L.AGE_HI), m["rule_overrides"].get("AGE_LO", L.AGE_LO)
    if age_lo >= age_hi:
        raise Refused("AGE_LO must be below AGE_HI")
    need_h = max(H.SEED_MIN_H, math.ceil(age_hi / 3600) + 1)  # the lane host's own 201 h check (floor_lane_host.main) for the default AGE_HI of 200 h; longer if AGE_HI is overridden up
    if H.parse_hour(m["seed_from_hour"]) > H.parse_hour(m["start_hour"]) - need_h * HOUR_MS:
        raise Refused("seed_from_hour must be at least %d h before start_hour (SEED_MIN_H %d, AGE_HI %s s)" % (need_h, H.SEED_MIN_H, age_hi))
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
    if m.get("gap_exclusion", "on") not in ("on", "off", "hold"):
        raise Refused("gap_exclusion must be 'on', 'off' or 'hold'")
    if "pricing_lat_entry_s" in m:  # explicit refusals throughout (an assert vanishes under python -O)
        v = m["pricing_lat_entry_s"]
        if "twin_of" not in m:
            raise Refused("pricing_lat_entry_s needs twin_of (a latency twin of a strict book)")
        if m.get("gap_exclusion", "on") != "on":
            raise Refused("pricing_lat_entry_s needs gap_exclusion 'on' or absent (one dimension per twin; a latency twin cannot also be a gap-in-hold twin)")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v not in LATENCY_VALUES_S:
            raise Refused("pricing_lat_entry_s must be one of the declared values %s" % (list(LATENCY_VALUES_S),))
    if "twin_of" in m:
        if "pricing_lat_entry_s" not in m and m.get("gap_exclusion") != "hold":
            raise Refused("twin_of needs gap_exclusion 'hold' or pricing_lat_entry_s")
        if not isinstance(m["twin_of"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", m["twin_of"]) or m["twin_of"] == m["book_id"]:
            raise Refused("twin_of must be another book_id matching [A-Za-z0-9_.-]{1,64}")
    return m


def twin_manifest_diff(twin, strict):
    """-> sorted list of the manifest keys (and problems) that make `twin` (a latency twin's manifest) something other than a pricing-only twin of `strict`: [] = fine. QP ruling 2026-11-14 osb latency
    twin, section 2. Every key other than TWIN_FREE_KEYS must be equal (gap_exclusion compared with its default "on"); the strict manifest must itself be a strict book (no twin_of, no pricing_lat_entry_s,
    gap_exclusion "on"), and the twin's twin_of must name it."""
    bad = []
    if strict.get("book_id") != twin.get("twin_of"):
        bad.append("twin_of is not the strict manifest's book_id")
    for k in ("twin_of", "pricing_lat_entry_s"):
        if k in strict:
            bad.append("strict manifest carries %s" % k)
    if strict.get("gap_exclusion", "on") != "on":
        bad.append("strict manifest gap_exclusion is not 'on'")
    for k in sorted((set(twin) | set(strict)) - TWIN_FREE_KEYS - {"gap_exclusion"}):
        if twin.get(k, None) != strict.get(k, None) or (k in twin) != (k in strict):
            bad.append(k)
    if twin.get("gap_exclusion", "on") != strict.get("gap_exclusion", "on"):
        bad.append("gap_exclusion")
    return bad


def check_strict_manifest(m, strict_manifest_path):
    """driver-side start check (--strict-manifest PATH): the twin's manifest against its strict book's. Refused (exit 2) on any difference; a strict book needs no such file"""
    if strict_manifest_path is None:
        return
    if "pricing_lat_entry_s" not in m:
        raise Refused("--strict-manifest is for a latency twin (pricing_lat_entry_s)")
    s = load_manifest(strict_manifest_path)
    bad = twin_manifest_diff(m, s)
    if bad:
        raise Refused("latency twin %s differs from its strict book %s in %s: not a pricing-only twin" % (m["book_id"], m["twin_of"], bad))


def manifest_sha256(m):
    return hashlib.sha256(json.dumps(m, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def check_overrides(ov):
    for k, v in ov.items():
        if k not in OVERRIDE_TYPES:  # explicit refusal (an assert vanishes under python -O)
            raise Refused("refusing override of %r (allowed: %s)" % (k, sorted(OVERRIDE_TYPES)))
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
            raise Refused("override %s must be a positive number" % k)
        if OVERRIDE_TYPES[k] is int and isinstance(v, float) and not v.is_integer():
            raise Refused("override %s must be a whole number" % k)


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
    t, o = os.path.realpath(m["tape_dir"]), os.path.realpath(m["out_root"])
    if TP._under(o, t) or TP._under(t, o):
        raise Refused("tape_dir and out_root must be disjoint")


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
                                                       "book_id": m["book_id"], "host": m["host"], "reason_labels_fixed": True}, indent=1, sort_keys=True) + "\n")
    scratch_register(registry, start_ms)
    cfg = dataclasses.replace(stack_config_for(2, R.DEFAULT_FEED_DELAY_SLOTS), send=m["send_lamports"], stakes_sol=tuple(float(x) for x in m["stakes_sol"]))
    engine = GapRangeEngine(ledger, registry, cfg=cfg, gap_exclusion={"on": True, "off": False, "hold": "hold"}[m.get("gap_exclusion", "on")],
                            **({"pricing_lat_entry_s": float(m["pricing_lat_entry_s"])} if "pricing_lat_entry_s" in m else {}), **(engine_kw or {}))
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


def _atomic_text(path, text):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def decision_rows(ledger_dir):
    """every complete, parseable ledger row under ledger_dir (a half-written last line or a bad line is skipped); `start` rows are not needed by the decisions digest"""
    for f in sorted(p for p in Path(ledger_dir).rglob("*") if p.is_file()):
        data = f.read_bytes()
        for ln in data[: data.rfind(b"\n") + 1].splitlines():
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if isinstance(r, dict):
                yield r


def decisions_md5_from_rows(rows):
    """md5 of the ordered (position_id, entry decision_ms, exit decision_ms) rows (open, trade and excluded rows; one row per position: the per-stake rows collapse, the exit comes from the
    trade / excluded row). Strict and twin books on the same decisions give the same value (QP ruling 2026-11-14 Q1, the Am.9 erratum text). THE one definition: the board and the recon import it."""
    seen = {}
    for r in rows:
        if r.get("kind") in ("open", "trade", "excluded") and isinstance(r.get("position_id"), str):
            dm = r.get("decision_ms") or {}
            old = seen.get(r["position_id"])
            seen[r["position_id"]] = (r["position_id"], dm.get("entry"), dm.get("exit") if dm.get("exit") is not None else (old[2] if old else None))
    out = sorted(seen.values(), key=lambda t: (t[1] if t[1] is not None else -1, t[0]))
    return hashlib.md5(canon(out).encode()).hexdigest()


def decisions_md5(run_dir):
    return decisions_md5_from_rows(decision_rows(Path(run_dir) / "ledger"))


def book_decisions_md5(out_root):
    """a book's decisions md5 for a reader (board, recon): the out_root/decisions.md5 file when present and well formed, else computed from the rows of the active run (out_root/book/ledger).
    The file is only a cache; this never writes. None when neither exists (no book link, no ledger row), i.e. the value cannot be read."""
    out_root = Path(out_root)
    try:
        v = (out_root / "decisions.md5").read_text().strip()
        if re.fullmatch(r"[0-9a-f]{32}", v):
            return v
    except OSError:
        pass
    led = out_root / "book" / "ledger"
    if not led.is_dir():
        return None
    rows = list(decision_rows(led))
    return decisions_md5_from_rows(rows) if any(r.get("kind") == "open" for r in rows) else None


def count_rows(run_dir):
    rows = DecisionLog(str(run_dir)).rows(H.BOT_ID)
    return {"n_decision_rows": len(rows), "n_entries": sum(1 for r in rows if r.get("kind") == "entry"), "n_exits": sum(1 for r in rows if r.get("kind") == "exit")}


PROCESS_KEYS = ("engine_id",)  # the engine's random per-process id; trade_id is deterministic outside the (dropped) start rows and stays in the projection


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
        lines = [ln for ln in p.read_text().splitlines() if ln.strip()]
        for i, ln in enumerate(lines):
            try:
                d = json.loads(ln)
                out[d["hour"]] = d
            except (ValueError, KeyError, TypeError):
                if i != len(lines) - 1:
                    raise Refused("md5-by-hour.jsonl line %d is unreadable (not the last line): repair by hand" % (i + 1))
                print("osb_follow: torn last line of md5-by-hour.jsonl dropped (that hour is verified or appended again)", file=sys.stderr)
                p.write_text("".join(x + "\n" for x in lines[:-1]))
    return out


def code_sha256():
    """sha256 of canonical {relpath: sha256}: this driver, its research helpers, floor_lane_host.py and every pinned file verify_pins() returns."""
    files = {}
    for rel in ("scripts/research/osb_follow.py", "scripts/research/floor_gap_range_engine.py", "scripts/research/floor_replay_proof_reg.py", "tools/floor_lane_host.py"):
        files[rel] = hashlib.sha256((H.REPO / rel).read_bytes()).hexdigest()
    files.update(H.verify_pins())
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _atomic_json(path, obj):
    H._atomic_write(Path(path), obj)


def _next_run_dir(out_root):
    runs = Path(out_root) / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    n = 1 + max([int(p.name) for p in runs.iterdir() if p.name.isdigit()] or [0])
    d = runs / ("%04d" % n)
    d.mkdir()  # no exist_ok: a second writer on the same run dir fails here
    return d


def _flip(out_root, run_dir):
    link, tmp = Path(out_root) / "book", Path(out_root) / ".book.new"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(os.path.relpath(run_dir, out_root), tmp)
    os.replace(tmp, link)


# ------------------------------------------------------------------------------------------------------------------------------ follow
def follow(manifest_path, once=False, max_hours=None, *, sleep_fn=time.sleep, _roots=None, _table_mode=None, _engine_kw=None, strict_manifest=None):
    """-> exit code. _roots / _table_mode / _engine_kw are test-only (the CLI does not expose them). strict_manifest: a latency twin's strict book's manifest path (start check)."""
    try:
        return _follow(manifest_path, once, max_hours, sleep_fn, _roots or (OSB_ROOT,), _table_mode, _engine_kw, strict_manifest)
    except Refused as e:
        print("osb_follow: refused: %s" % e, file=sys.stderr)
        return EXIT_REFUSED
    except Mismatch as e:
        print("osb_follow: MD5 MISMATCH: %s" % e, file=sys.stderr)
        return EXIT_MISMATCH
    except R.HostFatal as e:
        print("osb_follow: HostFatal: %s" % str(e)[:300], file=sys.stderr)
        return EXIT_FATAL
    except (R.HostError, SystemExit) as e:
        print("osb_follow: %s: %s" % (type(e).__name__, str(e)[:300]), file=sys.stderr)
        return EXIT_REFUSED


def _follow(manifest_path, once, max_hours, sleep_fn, roots, table_mode, engine_kw=None, strict_manifest=None):
    m = load_manifest(manifest_path)
    check_strict_manifest(m, strict_manifest)
    guard_paths(m, roots)
    TP.require_files_ok(m["tape_dir"], roots)  # the folder and the realpath of every tape-* entry (seed hours and hours ahead of the cursor included)
    out_root = Path(m["out_root"])
    out_root.mkdir(parents=True, exist_ok=True)
    lock_fh = open(out_root / ".osb.lock", "a")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_fh.close()
        raise Refused("another osb_follow holds %s" % (out_root / ".osb.lock"))
    try:
        return _locked(m, out_root, once, max_hours, sleep_fn, roots, table_mode, engine_kw)
    finally:
        lock_fh.close()


def _locked(m, out_root, once, max_hours, sleep_fn, roots, table_mode, engine_kw):
    sha = manifest_sha256(m)
    sf = out_root / "manifest.sha256"
    if sf.exists():
        if sf.read_text().strip() != sha:
            raise Refused("manifest differs from the one this book started with (%s): a tweak is a new book" % sf.read_text().strip()[:12])
    else:
        sf.write_text(sha + "\n")
    H.verify_pins()
    csha, cf = code_sha256(), out_root / "code.sha256"
    if cf.exists():
        if cf.read_text().strip() != csha:
            raise Refused("simulator version changed (code.sha256 %s != %s): a new book" % (cf.read_text().strip()[:12], csha[:12]))
    else:
        cf.write_text(csha + "\n")
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
        base = {"book_id": m["book_id"], "manifest_sha256": sha, "code_sha256": csha, "seed_from_hour": m["seed_from_hour"], "run": run_dir.name, "stakes_sol": list(cfg.stakes_sol)}
        status, waiting_since = dict(base), None
        while True:
            f = hour_file(m["tape_dir"], cursor)
            if f is None:
                if once or (max_hours is not None and done >= max_hours):
                    break
                waiting_since = waiting_since or datetime.now(timezone.utc).isoformat()
                status.update(waiting_for_hour=hour_key(cursor), waiting_since_utc=waiting_since, wall_time_utc=datetime.now(timezone.utc).isoformat())
                _atomic_json(out_root / "status.json", status)
                sleep_fn(POLL_S)
                continue
            waiting_since = None
            status.pop("waiting_for_hour", None)
            status.pop("waiting_since_utc", None)
            TP.require_files_ok(m["tape_dir"], roots)
            end = cursor + HOUR_MS
            host.run(cursor, end)
            key, md5, lmd5 = hour_key(cursor), decision_md5(run_dir), tree_md5(run_dir / "ledger")
            if key in stored:
                for fld, got in (("decision_md5", md5), ("ledger_md5", lmd5)):
                    if stored[key].get(fld) != got:
                        raise Mismatch("hour %s %s: stored %s, replay %s (run %s kept for inspection; stored list and `book` untouched)" % (key, fld, stored[key].get(fld), got, run_dir.name))
            if not verified and key == last_stored:
                verified = True
                _flip(out_root, run_dir)
            if verified and key not in stored:
                with open(out_root / "md5-by-hour.jsonl", "a") as fh:
                    fh.write(canon({"hour": key, "end_ms": end, "decision_md5": md5, "ledger_md5": lmd5}) + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
            dmd5 = decisions_md5(run_dir)
            if verified:
                _atomic_text(out_root / "decisions.md5", dmd5 + "\n")
            status.update(decisions_md5=dmd5, last_hour=key, last_hour_end_ms=end, wall_time_utc=datetime.now(timezone.utc).isoformat(), active=verified, catching_up_to_stored_hours=not verified,
                          decision_md5=md5, ledger_md5=lmd5, **count_rows(run_dir))
            _atomic_json(out_root / "status.json", status)
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
    ap.add_argument("--strict-manifest", default=None, help="a latency twin's strict book's manifest: refuse at start if the twin differs in any key other than book_id, title, out_root, twin_of, pricing_lat_entry_s")
    a = ap.parse_args(argv)
    return follow(a.manifest, once=a.once, max_hours=a.max_hours, strict_manifest=a.strict_manifest)


if __name__ == "__main__":
    raise SystemExit(main())
