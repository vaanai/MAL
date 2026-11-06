"""r4bt_c0_twin: the PAPER TWIN WRITER of the r4-b-T C0 (QP-RULING-1106-r4bt-c0 section 2 and build-list item 3; DEC-031 Amendment 27 items 24 and 29 A6). PAPER ONLY.
No key, no network, no RPC, no ledger of any arena book.

For every order the C0 executor actually SENT it prices the paper twin: the registered pricing engine `tools.arena_paper.ArenaEngine` (sha256 pinned, refused on any
difference) at the order's stake S and its actual per-send fees, no fail model (nofail), the 1.9 s leg. Rent 0 decides; the rent-charged leg is written beside it.

INPUTS (exactly two; nothing else is opened):
  1. A derived-copy tape dir (`tape-<YYYY-MM-DDTHH>.jsonl[.zst]`, the fast-0 arena tip writer's derived copy), read-only, hours at or after floor(first C0 entry
     decision - 3 h). Every file is checked against the receiver's manifest (`--manifest`, sha256 per file name) BEFORE its first row is read; a missing hour in the
     range, a missing manifest entry or a different sha256 refuses. Only CLOSED hours are read (the hour's end at or before the run instant). The job wrapper
     (scripts/mal-fast/r4bt-c0-twin.sh) runs the tool under nice 19 / ionice idle.
  2. The C0 executor's order ledger (`--orders`, JSONL), SENT ORDERS ONLY (a row with `sent` not true is skipped, never parsed further). The reader keeps only the
     fields ORDER_FIELDS names; every other key is dropped by the parse hook. It never reads the r4-b-T declog, the live tip dir, the archive, `.writer/`, the
     research-0 copy or another book's output (a path under any of them refuses).

PRICING. `TwinEngine` subclasses ArenaEngine and changes ONLY where results go: pricing and exclusion results are kept in memory (no ledger, no registry, no bots.json),
and the per-position StackConfig is swapped in around build so the order's own stake and fee are used. `tools/r4b_book.round_trip` is REFUSED (item 24: it starts at
sd(T), and a second pricing is a second outcome computation); the module is never imported and the run refuses if it is loaded. StackConfig is item 24's pin byte for
byte: rent 0, e1_floor_per_005 384022, strand_rule "both", k_rounding "ceil", gap_slots 150, gap_silence_ms 60000, feed_delay_slots d (required, no default); the
order's stake and fee replace `stakes_sol` and `send` only.

LATENCY LEG (item 24 mapping). The 1.9 s leg starts at the decision row's `ms` (the order ledger's entry_ms / exit_ms, copied by the executor from the declog row), mapped
to the tape slot clock exactly as the pricer does: the engine call clock is that `ms`; `ArenaEngine._write_instant` takes max(ms, call clock, newest t_recv_ms);
`ArenaEngine.slot_at(instant, sps)` (tools/arena_paper.py, `slot_at`) maps it to hw_ms_slot + floor((instant - hw_ms) / (sps*1000)); `buy()` / `sell()` set sd / xs =
max(decision_slot, slot_at) with decision_slot = the engine's own highest fed slot (never the logged sd(T)); d is added at the landing and the leg is k = ceil(1.9 /
sps). No second leg is priced; the sd(T)-origin figure is the executor/parity tool's (the order's own `decision_slot`, carried through unchanged).

ORDER. Every tape row with t_recv_ms at or before a decision's ms is fed BEFORE the decision is applied (tools/r4bt_price.py's ordering; a test pins it). A decision
whose ms is not yet below a later tape row (the tape has not reached it) is NOT applied: the order stays `pending_tape`, and a position whose exit tape is not yet
there stays `pending_tape` (no derived-last flush).

OUTPUTS (the C0 state dir, `--out-dir`): `twins.jsonl` (one row per sent order, schema r4bt_c0_twin_row_v1) and `twins.run.json` (the run record, schema
r4bt_c0_twin_run_v1, written AFTER twins.jsonl, atomically). The executor's twin gate checks the run record's `run_ms` (fresh) and `twins_sha256`; the pinned
parity tool reads twins.jsonl. Both are sealed until r4-b-T's confirmation read is written (the SEALS readers line). Interface: INTERFACE below.

    python -m tools.r4bt_c0_twin --tape-dir T --manifest M --orders O --out-dir D --feed-delay-slots d --w-sha W [--now-ms N]

Exit codes: 0 written; 2 refused (nothing written); 3 the inputs cannot be trusted (unparseable tape or order line, manifest mismatch).
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import dataclasses
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools import r4bt_gate as G
from tools import r4bt_price as P
from tools.arena_paper import RENT_LAMPORTS, ArenaEngine, ArenaRefused, StackConfig, price_leg, rules_of

ENGINE_PIN = "9d2fdb04a3da9bb38de9e30c91154f5a9ab2de513e6f4d7936c1eb1a54863514"  # DEC-031 Am.27 item 24 / item 29 A6
ENGINE_FILE = "tools/arena_paper.py"
TWIN_ROW_SCHEMA = "r4bt_c0_twin_row_v1"
RUN_SCHEMA = "r4bt_c0_twin_run_v1"
HOUR_MS = 3_600_000
PRE_MS = 3 * HOUR_MS  # the derived copy is read from (first C0 entry decision - 3 h): the guard spot
LAT_TAG = "1p9"
LEG_S = 1.9
STAKE_FALLBACK_MAX = 1.0
PROD_TAPE_DIR = "/home/claude/mal-data/arena-tipfeed/tape"  # the fast-0 derived copy (the only production tape dir)
TEST_ENV = "R4BT_C0_TWIN_TEST"  # tests only: allow a tape dir other than PROD_TAPE_DIR (a closed-root refusal still applies)
TWIN_CLOSED_MARKS = ("/.writer", "/data2/mal/arena-tip-feed", "/var/lib/mal/sealed", "declog", "tip-tape-archive", "/var/lib/mal-live")
SHA_RE = G.SHA_RE
BOT_ID = "r4bt-c0-twin"  # a label inside the in-memory engine only; no registry line, no ledger exists for it

# The executor's order ledger rows this tool reads (sent orders only). Every other key is dropped by the parse hook. Times are ms; fees are TOTAL lamports of the leg
# (every send, escalated resends included); *_slot are optional diagnostics copied through.
ORDER_FIELDS = ("order_id", "sent", "pool", "mint", "rank", "t_mark", "stake_sol", "entry_ms", "exit_ms", "entry_fee_lamports", "exit_fee_lamports", "restart",
                "gap_hold", "decision_slot", "landed_slot_entry", "landed_slot_exit", "exit_reason")

INTERFACE = {
    "order_row (input, JSONL, sent orders only)": {
        "required": ["order_id (str)", "sent (true)", "pool (str)", "stake_sol (float, the order's actual S)", "entry_ms (int, the entry decision row's ms)",
                     "entry_fee_lamports (int, total of every send of the buy incl. escalated resends)"],
        "optional": ["exit_ms (int|null, the exit decision row's ms; null while open)", "exit_fee_lamports (int|null)", "mint", "rank", "t_mark", "restart (bool)",
                     "gap_hold (bool)", "decision_slot (sd(T), carried through, never used to price)", "landed_slot_entry", "landed_slot_exit", "exit_reason"],
    },
    "twins.jsonl row": "TWIN_ROW_FIELDS",
    "twins.run.json": "RUN_FIELDS",
    "gate": "the executor's twin gate: refuse a buy unless twins.run.json parses, schema == r4bt_c0_twin_run_v1, status == 'ok', now - run_ms <= grace, "
            "engine_sha256 == the pin, twins_sha256 == sha256(twins.jsonl); after the entry leg is observable, a twin row for the order within the grace, else a P7 trip.",
}
TWIN_ROW_FIELDS = ("schema", "order_id", "status", "pool", "mint", "rank", "t_mark", "stake_sol", "send_lamports", "entry_ms", "exit_ms", "entry_leg", "exit_leg",
                   "nofail", "rent_charged", "flags", "gap_or_restart", "v_band_audit", "exclusion_reason", "refused_reason", "decision_slot_sd_t", "landed_slot_entry",
                   "landed_slot_exit", "stack", "leg_s")
RUN_FIELDS = ("schema", "tool", "status", "run_ms", "w_sha", "engine_sha256", "tool_sha256", "stack", "feed_delay_slots", "tape_first_hour", "tape_last_file",
              "tape_last_file_sha256", "tape_files_checked", "manifest_sha256", "orders_sha256", "orders_sent", "orders_skipped_unsent", "counts_by_status",
              "v_band_audit_n", "twins_sha256", "tape_rows", "heartbeat_ms")


class Untrusted(RuntimeError):
    """An input cannot be trusted (unparseable line, manifest mismatch): nothing is written."""


def sha256_file(p: str | Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ------------------------------------------------------------------------------------------------------------------------------ pins and refusals
def check_engine_pin(repo: Path, pin: str = ENGINE_PIN) -> str:
    got = sha256_file(repo / ENGINE_FILE)
    if got != pin:
        raise G.Refused(f"{ENGINE_FILE} sha256 {got} != the pinned {pin} (DEC-031 Am.27 item 24): a changed engine needs its own QP OK and a new pin")
    return got


def refuse_round_trip() -> None:
    """item 24: tools/r4b_book.round_trip is refused as the deciding route and as a report-only figure."""
    if "tools.r4b_book" in sys.modules:
        raise G.Refused("tools.r4b_book is loaded in this process: round_trip is refused for r4-b-T (DEC-031 Am.27 item 24)")


def check_tape_dir(path: str | Path) -> Path:
    p = Path(path).resolve()
    s = str(p)
    G.check_path(p, "the tape dir")
    for mark in TWIN_CLOSED_MARKS:
        if mark in s:
            raise G.Refused(f"the tape dir {s} is inside {mark}: the twin reads only the fast-0 derived copy's tape-* files (QP-RULING-1106-r4bt-c0 section 2)")
    if s != PROD_TAPE_DIR and os.environ.get(TEST_ENV) != "1":
        raise G.Refused(f"the tape dir must be the fast-0 derived copy {PROD_TAPE_DIR}, got {s}")
    return p


def check_other_path(path: str | Path, what: str) -> Path:
    p = G.check_path(path, what)
    s = str(p)
    for mark in TWIN_CLOSED_MARKS:
        if mark in s:
            raise G.Refused(f"{what} {s} is inside {mark}: refused")
    return p


def pinned_config(*, feed_delay_slots: int, stake_sol: float, send_lamports: int, rent: int = 0) -> StackConfig:
    """Item 24's StackConfig pin; stake and send are the order's. d is required (a measured, registered whole number of slots)."""
    base = P.pinned_config(feed_delay_slots=feed_delay_slots, k_rounding="ceil")
    return dataclasses.replace(base, rent=rent, stakes_sol=(float(stake_sol),), send=int(send_lamports))


# ------------------------------------------------------------------------------------------------------------------------------ inputs
def load_manifest(path: Path) -> dict[str, str]:
    """The receiver's per-file sha256 manifest: a JSON object {"files": {name: sha256}} (or a bare {name: sha256}), or JSONL lines with name|file and sha256."""
    text = path.read_text()
    out: dict[str, str] = {}
    try:
        obj = json.loads(text)
        files = obj.get("files", obj) if isinstance(obj, dict) else None
        if isinstance(files, dict):
            out = {str(k): str(v) for k, v in files.items() if isinstance(v, str)}
        elif isinstance(files, list):
            for r in files:
                out[str(r.get("name") or r.get("file"))] = str(r.get("sha256"))
    except ValueError:
        for line in text.splitlines():
            if line.strip():
                r = json.loads(line)
                if isinstance(r, dict) and (r.get("name") or r.get("file")) and r.get("sha256"):
                    out[str(r.get("name") or r.get("file"))] = str(r["sha256"])
    out = {Path(k).name: v for k, v in out.items()}
    if not out or any(not SHA_RE.match(v) for v in out.values()):
        raise Untrusted("the manifest holds no sha256 entries or one is not a lowercase sha256")
    return out


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def load_orders(path: Path) -> tuple[list[dict], int]:
    """Sent orders only. Returns (orders, unsent_lines_skipped). Each kept row is reduced to ORDER_FIELDS by the parse hook (other keys never kept)."""
    keep = set(ORDER_FIELDS)
    orders, skipped, seen = [], 0, set()
    for n, line in enumerate(path.read_bytes().split(b"\n"), 1):
        if not line.strip():
            continue
        try:
            r = json.loads(line, object_pairs_hook=lambda kv: {k: v for k, v in kv if k in keep})
        except ValueError:
            raise Untrusted(f"unparseable order line {n}") from None
        if not isinstance(r, dict):
            raise Untrusted(f"order line {n} is not an object")
        if r.get("sent") is not True:
            skipped += 1
            continue
        oid = r.get("order_id")
        if not isinstance(oid, str) or not oid or not isinstance(r.get("pool"), str) or not r.get("pool"):
            raise Untrusted(f"sent order line {n} lacks a string order_id or pool")
        if oid in seen:
            raise Untrusted(f"duplicate sent order {oid}")
        seen.add(oid)
        s = r.get("stake_sol")
        if not isinstance(s, (int, float)) or isinstance(s, bool) or not 0 < s <= STAKE_FALLBACK_MAX:
            raise Untrusted(f"sent order {oid}: stake_sol must be a number in (0, 1]")
        if not _is_int(r.get("entry_ms")) or not _is_int(r.get("entry_fee_lamports")) or r["entry_fee_lamports"] < 0:
            raise Untrusted(f"sent order {oid}: entry_ms and entry_fee_lamports must be integers")
        if r.get("exit_ms") is not None and (not _is_int(r["exit_ms"]) or r["exit_ms"] < r["entry_ms"]):
            raise Untrusted(f"sent order {oid}: exit_ms must be an integer at or after entry_ms")
        if r.get("exit_fee_lamports") is not None and not _is_int(r["exit_fee_lamports"]):
            raise Untrusted(f"sent order {oid}: exit_fee_lamports must be an integer")
        orders.append(r)
    return orders, skipped


def send_of(o: Mapping) -> int:
    """The engine's `send` is per send and enters a trip twice (entry + exit): the order's two total fees are carried as their mean, so the trip total is exact."""
    e = int(o["entry_fee_lamports"])
    x = o.get("exit_fee_lamports")
    return int(round((e + int(x)) / 2.0)) if x is not None else e


def select_files(tape_dir: Path, lo_ms: int, now_ms: int, manifest: Mapping[str, str]) -> tuple[list[Path], list[str]]:
    """Closed hours from floor(lo) on, oldest first. A hole inside the range, a file with no manifest line or a different sha256 refuses. Returns (files, hours)."""
    have: dict[str, Path] = {}
    for p in sorted(tape_dir.iterdir()):
        m = P.TAPE_RE.match(p.name)
        if m and (m.group(1) not in have or not p.name.endswith(".zst")):
            have[m.group(1)] = p
    h = lo_ms // HOUR_MS * HOUR_MS
    files, hours = [], []
    while h + HOUR_MS <= now_ms:
        name = P.hour_name(h)
        p = have.get(name)
        if p is None:
            if files or any(k > name for k in have):
                raise G.Refused(f"the derived copy lacks the closed hour {name} inside the read range: the twin never skips an hour")
            raise G.Refused(f"the derived copy lacks the first hour {name} (first C0 entry - 3 h)")
        want = manifest.get(p.name)
        if want is None:
            raise Untrusted(f"{p.name} has no line in the receiver's manifest")
        got = sha256_file(p)
        if got != want:
            raise Untrusted(f"{p.name} sha256 {got} != the receiver's manifest {want}")
        files.append(p)
        hours.append(name)
        h += HOUR_MS
    return files, hours


# ------------------------------------------------------------------------------------------------------------------------------ engine
class _NullLedger:
    """No arena ledger is written or read: the twin keeps its results in memory and writes only its own twins.jsonl."""

    refused: list[dict]

    def __init__(self) -> None:
        self.refused = []
        self.write_failures = 0

    def append(self, row: Mapping[str, Any]) -> bool:
        if row.get("kind") == "refused":
            self.refused.append(dict(row))
        return True

    def incident(self, *a: Any, **k: Any) -> bool:
        return True

    def flush_incidents(self) -> int:
        return 0

    def read_bot(self, bot_id: str) -> list:
        return []


class TwinEngine(ArenaEngine):
    """ArenaEngine whose results stay in memory and whose StackConfig is per order (stake S, the order's fee). Nothing else is overridden: slot mapping, latency, ordering
    of fills, gaps, V handling, the E1 floor and the strand rules are the pinned engine's."""

    def __init__(self, base_cfg: StackConfig, **kw: Any) -> None:
        super().__init__(ledger_root="/nonexistent-r4bt-c0-twin", registry_path="/nonexistent-r4bt-c0-twin", cfg=base_cfg, **kw)
        self.ledger = _NullLedger()  # type: ignore[assignment]
        self.order_cfg: dict[str, StackConfig] = {}  # position id -> the order's cfg (rent 0)
        self.results: dict[str, dict[str, Any]] = {}
        self.pos_order: dict[str, str] = {}

    def _bot(self, bot_id: str) -> dict[str, Any]:
        return {"frozen_sha": "twin", "clock_start_ms": 0, "stopped_at_ms": None}

    def start_record(self, bot_id: str) -> bool:
        return False

    def _write_excluded(self, pos: Any, reason: str, src: str | None) -> None:
        self.results[pos.position_id] = {"excluded": reason}
        self._count(f"excluded_{reason}")

    def _price_and_write(self, pos: Any, h: Any, src: str) -> None:
        cfg0 = self.order_cfg[pos.position_id]
        cfgr = dataclasses.replace(cfg0, rent=RENT_LAMPORTS)
        stake = float(round(cfg0.stakes_sol[0] * 1e9))
        saved = self.cfg
        self.cfg = cfg0
        try:
            st = self.leg_inputs(pos, h)
            vs = self._v_sources(pos, h)
            base = self._base_row(pos, "trade", cfg0.stakes_sol[0], src)
        finally:
            self.cfg = saved
        legs: dict[str, Any] = {}
        for name, cfg in (("nofail", cfg0), ("rent_charged", cfgr)):
            legs[name] = {}
            for rule in rules_of(cfg):
                r = price_leg(pos.prog, stake, st, LEG_S, cfg, rule)
                legs[name][rule] = {"pnl_lamports": r["pnl"], "state": r["state"], "filled": r["filled"], "strand": r["strand"], "e1_floor_extra_lamports": r["e1_extra"]}
        self.results[pos.position_id] = {
            "base": base, "legs": legs, "v_src": vs,
            "entry_leg": {"landing_slot": int(st["land"][LEG_S]), "Qe": st["Qe"][LEG_S], "Be": st["Be"][LEG_S], "spot": st["ref"], "ssb": st["ssb"][LEG_S],
                          "nearby_lamports": st["nearby"][LEG_S], "effective_slot": pos.sd, "corrected_slot": base["corrected_slot"]["entry"]},
            "exit_leg": {"Qx": st["Qx"], "Bx": st["Bx"], "effective_slot": pos.xs, "corrected_slot": base["corrected_slot"]["exit"], "state_src": src},
        }


def v_audit_ids(results: Mapping[str, Mapping], prints: Mapping[str, list]) -> set[str]:
    """Item 24's V audit through the pricer's own function: position ids whose entry or exit landing print has V outside [17.5e9, 17.7e9] or a non-WSOL quote."""
    trades = []
    for pid, r in results.items():
        if "base" not in r:
            continue
        b = r["base"]
        trades.append({"position_id": pid, "pool": b["pool"], "stack": {"sps_exit": b["stack"]["sps_exit"]}, "calc": {P.LAT: {"landing_slot": r["entry_leg"]["landing_slot"]}},
                       "corrected_slot": b["corrected_slot"]})
    return set(P.v_audit(trades, prints))


# ------------------------------------------------------------------------------------------------------------------------------ the run
def build_rows(orders: Sequence[Mapping], by_order: Mapping[str, str], eng: TwinEngine, applied: Mapping[str, set], audit: set[str], refused: Mapping[str, str],
               tape_loss: Counter) -> list[dict]:
    rows = []
    for o in sorted(orders, key=lambda x: (x["entry_ms"], x["order_id"])):
        oid = o["order_id"]
        pid = by_order.get(oid)
        flags = []
        if o.get("restart"):
            flags.append("order_restart")
        if o.get("gap_hold"):
            flags.append("order_gap_hold")
        row: dict[str, Any] = {"schema": TWIN_ROW_SCHEMA, "order_id": oid, "pool": o["pool"], "mint": o.get("mint"), "rank": o.get("rank"), "t_mark": o.get("t_mark"),
                               "stake_sol": o["stake_sol"], "send_lamports": send_of(o), "entry_ms": o["entry_ms"], "exit_ms": o.get("exit_ms"), "entry_leg": None,
                               "exit_leg": None, "nofail": None, "rent_charged": None, "exclusion_reason": None, "refused_reason": refused.get(oid),
                               "decision_slot_sd_t": o.get("decision_slot"), "landed_slot_entry": o.get("landed_slot_entry"), "landed_slot_exit": o.get("landed_slot_exit"),
                               "leg_s": LEG_S, "v_band_audit": False}
        res = eng.results.get(pid) if pid else None
        if oid not in applied["entry"]:
            status = "pending_tape"
        elif refused.get(oid):
            status = "refused"
        elif res is None:
            status = "open" if o.get("exit_ms") is None else ("pending_tape")
        elif "excluded" in res:
            status = "excluded"
            row["exclusion_reason"] = res["excluded"]
            flags.append("excluded:" + res["excluded"])
        else:
            status = "priced"
            row.update(entry_leg=res["entry_leg"], exit_leg=res["exit_leg"], nofail=res["legs"]["nofail"], rent_charged=res["legs"]["rent_charged"],
                       v_band_audit=pid in audit, stack=res["base"]["stack"])
            if pid in audit:
                flags.append("v_band")
        row.setdefault("stack", None)
        row["status"] = status
        row["flags"] = sorted(flags)
        row["gap_or_restart"] = bool(o.get("restart") or o.get("gap_hold") or row["exclusion_reason"] in ("engine_restart", "feed_gap"))
        rows.append({k: row.get(k) for k in TWIN_ROW_FIELDS})
    return rows


def run(*, tape_dir: str | Path, manifest: str | Path, orders_path: str | Path, out_dir: str | Path, feed_delay_slots: int, w_sha: str, now_ms: int | None = None,
        repo: str | Path | None = None, engine_pin: str = ENGINE_PIN) -> dict:
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    repo = Path(repo) if repo else Path(__file__).resolve().parent.parent
    refuse_round_trip()
    engine_sha = check_engine_pin(repo, engine_pin)
    if not re.match(r"^[0-9a-f]{40}$", str(w_sha)):
        raise G.Refused("--w-sha must be the 40-hex install sha W")
    troot = check_tape_dir(tape_dir)
    mpath = check_other_path(manifest, "the manifest")
    opath = check_other_path(orders_path, "the order ledger")
    odir = check_other_path(out_dir, "the out dir")
    pinned_config(feed_delay_slots=feed_delay_slots, stake_sol=0.05, send_lamports=0)  # validates d, k_rounding ceil
    orders, unsent = load_orders(opath)
    out_dir_p = Path(odir)
    out_dir_p.mkdir(parents=True, exist_ok=True)
    man = load_manifest(mpath)
    heartbeat_only = not orders
    first_entry = min((o["entry_ms"] for o in orders), default=None)
    counts: Counter = Counter()
    refused: dict[str, str] = {}
    by_order: dict[str, str] = {}
    applied: dict[str, set] = {"entry": set(), "exit": set()}
    rows_out: list[dict] = []
    files: list[Path] = []
    hours: list[str] = []
    eng_counters: dict = {}
    tape_rows = 0
    audit: set[str] = set()
    if not heartbeat_only:
        lo_ms = first_entry - PRE_MS
        files, hours = select_files(troot, lo_ms, now, man)
        clock = [lo_ms]
        base_cfg = pinned_config(feed_delay_slots=feed_delay_slots, stake_sol=0.05, send_lamports=505_000)
        eng = TwinEngine(base_cfg, sps_fn=None, now_fn=lambda: clock[0], code_state_fn=lambda: (None, None))
        events = sorted([{"_ms": o["entry_ms"], "_k": 0, "k": "entry", "o": o} for o in orders]
                        + [{"_ms": o["exit_ms"], "_k": 1, "k": "exit", "o": o} for o in orders if o.get("exit_ms") is not None],
                        key=lambda x: (x["_ms"], x["_k"], x["o"]["order_id"]))
        pools = {o["pool"] for o in orders}
        prints: dict[str, list] = {p: [] for p in pools}
        tape_loss: Counter = Counter()

        def apply(ev: Mapping[str, Any]) -> None:
            o = ev["o"]
            clock[0] = ev["_ms"]  # the call clock IS the decision instant
            oid = o["order_id"]
            if ev["k"] == "entry":
                applied["entry"].add(oid)
                cfg = pinned_config(feed_delay_slots=feed_delay_slots, stake_sol=o["stake_sol"], send_lamports=send_of(o))
                try:
                    pid = eng.buy(BOT_ID, o["pool"], o["stake_sol"], o["entry_ms"], eng.hw_slot, {"order_id": oid})
                except ArenaRefused as exc:
                    refused[oid] = str(exc).split(":")[0]
                    return
                eng.order_cfg[pid] = cfg
                by_order[oid] = pid
                return
            applied["exit"].add(oid)
            pid = by_order.get(oid)
            if pid is None or pid not in eng.positions:
                return
            if o.get("restart") or o.get("exit_reason") == "restart":
                eng.positions[pid].exit_ms = o["exit_ms"]
                eng._write_excluded(eng.positions[pid], "engine_restart", None)
                del eng.positions[pid]
                return
            try:
                eng.sell(BOT_ID, pid, 1.0, o["exit_ms"], eng.hw_slot, str(o.get("exit_reason") or ""))
            except ArenaRefused as exc:
                refused[oid] = "sell:" + str(exc).split(":")[0][:40]

        i = 0
        for f in files:
            for row in P.iter_rows(f):
                t = row.get("t_recv_ms")
                while i < len(events) and P._is_int(t) and events[i]["_ms"] < t:  # rows at or before the decision's ms are fed first
                    apply(events[i])
                    i += 1
                if P._is_int(t):
                    clock[0] = max(clock[0], t)
                if row.get("type") == "gap" and (row.get("data_loss") or row.get("kind") in ("process_restart", "feed_restart", "feed_start")):
                    tape_loss[f"{row.get('prog')}:{row.get('kind')}"] += 1
                if row.get("type", "trade") == "trade" and row.get("prog") == "pumpswap" and row.get("pool") in prints and P._is_int(row.get("slot")):
                    prints[row["pool"]].append((int(row["slot"]), row.get("virtual_quote_reserves"), row.get("quote_mint")))
                eng.on_row(row)
                tape_rows += 1
            eng.trim_history()
            busy = {p.pool for p in eng.positions.values()}
            for k in [k for k, h in eng.pools.items() if h.venue == "pump_bonding" and k not in busy]:
                del eng.pools[k]
        # events past the last fed tape row are NOT applied (pending_tape); no flush: a sell without its exit tape stays pending
        for p in prints.values():
            p.sort(key=lambda x: x[0])
        audit = v_audit_ids(eng.results, prints)
        rows_out = build_rows(orders, by_order, eng, applied, audit, refused, tape_loss)
        eng_counters = dict(sorted(eng.counters.items()))
        counts.update(r["status"] for r in rows_out)
    # --- write: twins.jsonl first, then the run record (atomic), so the record's twins_sha256 always matches a complete file
    body = "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows_out)
    tw = out_dir_p / "twins.jsonl"
    tmp = out_dir_p / f".twins.jsonl.{os.getpid()}.tmp"
    tmp.write_text(body)
    os.replace(tmp, tw)
    last = files[-1] if files else None
    record = {
        "schema": RUN_SCHEMA, "tool": "r4bt_c0_twin", "status": "ok", "run_ms": now, "heartbeat_ms": now, "w_sha": w_sha, "engine_sha256": engine_sha,
        "tool_sha256": sha256_file(Path(__file__)), "stack": pinned_config(feed_delay_slots=feed_delay_slots, stake_sol=0.05, send_lamports=505_000).as_dict(),
        "feed_delay_slots": feed_delay_slots, "tape_first_hour": hours[0] if hours else None, "tape_last_file": last.name if last else None,
        "tape_last_file_sha256": sha256_file(last) if last else None, "tape_files_checked": len(files), "manifest_sha256": sha256_file(mpath),
        "orders_sha256": sha256_file(opath), "orders_sent": len(orders), "orders_skipped_unsent": unsent, "counts_by_status": dict(sorted(counts.items())),
        "v_band_audit_n": len(audit), "twins_sha256": hashlib.sha256(body.encode()).hexdigest(), "tape_rows": tape_rows, "engine_counters": eng_counters,
    }
    rr = out_dir_p / "twins.run.json"
    tmp2 = out_dir_p / f".twins.run.json.{os.getpid()}.tmp"
    tmp2.write_text(json.dumps(record, sort_keys=True) + "\n")
    os.replace(tmp2, rr)
    return record


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="r4bt_c0_twin", description=__doc__.split("\n")[0])
    ap.add_argument("--tape-dir", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--orders", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--feed-delay-slots", required=True, type=int)
    ap.add_argument("--w-sha", required=True)
    ap.add_argument("--now-ms", type=int, default=None)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        rec = run(tape_dir=a.tape_dir, manifest=a.manifest, orders_path=a.orders, out_dir=a.out_dir, feed_delay_slots=a.feed_delay_slots, w_sha=a.w_sha, now_ms=a.now_ms)
    except G.Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    except (Untrusted, P.Untrusted) as exc:
        print(f"UNTRUSTED: {exc}", file=sys.stderr)
        return 3
    print(json.dumps({k: rec[k] for k in ("schema", "status", "run_ms", "orders_sent", "counts_by_status", "v_band_audit_n", "tape_files_checked", "twins_sha256")},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
