"""r4bt_c0_twin: the PAPER TWIN WRITER of the r4-b-T C0 (QP-RULING-1106-r4bt-c0 section 2 and build-list item 3; DEC-031 Amendment 27 items 24 and 29 A6). PAPER ONLY.
No key, no network, no RPC, no ledger of any arena book.

For every order the C0 executor actually SENT it prices the paper twin: the registered pricing engine `tools.arena_paper.ArenaEngine` (sha256 pinned, refused on any
difference) at the order's stake S and its ACTUAL per-send fees (escalated resends included; never the registered 505,000, which is printed beside as report-only),
no fail model (nofail), the 1.9 s leg. Rent 0 decides; the rent-charged leg is written beside it. The E1 floor is its own named field: the deciding figure is
`pnl_e1_excluded_lamports`, the E1-charged one `pnl_e1_charged_lamports`.

INPUTS (exactly two; nothing else is opened):
  1. The fast-0 derived copy (`/home/claude/mal-data/arena-tipfeed/tape`), read-only: CLOSED `tape-<YYYY-MM-DDTHH>.jsonl.zst` files only (never a `.jsonl`, which is the
     writer's open hour; never `*.part` / `*.tmp`; never `.writer/state.json`), hours at or after floor(first C0 entry decision - 3 h). An hour is read only if it is listed
     in `.push/verified.json` (hour keys only) and has a row in `.writer/counts.jsonl` (fields `hour`, `bytes`, `sha256` only; the sha256 is of the UNCOMPRESSED hour). The
     `.zst` is decompressed once into memory, its length must equal `bytes` and its sha256 `sha256`, and the rows are parsed from those same bytes. Hours after the newest
     contiguous verified hour are pending (the tape has not reached them); a hole below it refuses. RETENTION: the writer deletes a closed hour `--keep-verified-h` (24 h
     by default) after verification; this tool replays from first entry - 3 h on every run, so the writer run covering the C0 MUST keep verified hours >= 400 h
     (`--keep-verified-h 400`, ops, with the #2033 successor), else the run refuses with `hour_deleted_by_retention`. `--check` prints the oldest present hour NAME against
     the needed first hour (names only) and refuses if it is later. The job wrapper runs the tool at nice 19 / ionice idle.
  2. The C0 executor's order ledger (`--orders`, JSONL), SENT ORDERS ONLY (a row with `sent` not true is skipped, never parsed further). The reader keeps only the
     fields ORDER_FIELDS names; every other key is dropped by the parse hook. It never reads the r4-b-T declog, the live tip dir, the archive, `.writer/state.json`, the
     research-0 copy, the arena-tape mirror or another book's output (a path under any of them refuses). The out dir may not sit inside the tape dir.

CODE PINS. At start every loaded `tools.*` module except this one and the tests is blob-hashed and compared with MODULE_BLOBS (the blobs at W =
17e3b05a7696900cda7fcf93e4af4008d8fa0184, the r4bt-a1 decision host); any difference or unlisted module refuses; `--w-sha` must equal W and the pinned blobs must equal what
the install tree's object store has at W. The dict is written to the run record. The install tree is the C0 install sha (a descendant of W); this tool is not at W.

PRICING. `TwinEngine` subclasses ArenaEngine and changes ONLY where results go: pricing and exclusion results are kept in memory (no ledger, no registry, no bots.json),
and the per-position StackConfig is swapped in around build so the order's own stake and fee are used. `tools/r4b_book.round_trip` is REFUSED (item 24: it starts at
sd(T), and a second pricing is a second outcome computation); the module is never imported and the run refuses if it is loaded. StackConfig is item 24's pin byte for
byte: rent 0, e1_floor_per_005 384022, strand_rule "both", k_rounding "ceil", gap_slots 150, gap_silence_ms 60000, feed_delay_slots d (required, no default); the
order's stake and fee replace `stakes_sol` and `send` only.

LATENCY LEG (item 24 mapping). The 1.9 s leg starts at the decision row's `ms` (the order ledger's entry_ms / exit_ms, copied by the executor from the declog row), mapped
to the tape slot clock exactly as the pricer does: the engine call clock is that `ms`; `ArenaEngine._write_instant` takes max(ms, call clock, newest t_recv_ms);
`ArenaEngine.slot_at(instant, sps)` (tools/arena_paper.py, `slot_at`) maps it to hw_ms_slot + floor((instant - hw_ms) / (sps*1000)); `buy()` / `sell()` set sd / xs =
max(decision_slot, slot_at) with decision_slot = the engine's own highest fed slot (never the logged sd(T)); d is added at the landing and the leg is k = ceil(1.9 /
sps_entry) with EACH TRIP'S OWN sps_entry (k_rounding ceil). No second leg is priced.

ENTRY STAGE (for P5 and the gate). The moment an entry is applied the row carries `entry_leg` = {corrected_slot = sd + d, sps_entry, k_1p9, k_3p0, landing_slot =
corrected + k_1p9}; price fields (Qe, Be, spot, ...) join it when the trip is priced. The stage values equal the priced row's.

ORDER. Every tape row with t_recv_ms at or before a decision's ms is fed BEFORE the decision is applied (tools/r4bt_price.py's ordering; a test pins it). A decision
whose ms is not yet below a later tape row (the tape has not reached it) is NOT applied: the order stays `pending_tape`, and a position whose exit tape is not yet
there stays `pending_tape` (no derived-last flush).

OUTPUTS (the C0 state dir, `--out-dir`): `twins.jsonl` (one row per sent order, schema r4bt_c0_twin_row_v1) and `twins.run.json` (the run record, schema
r4bt_c0_twin_run_v1, written AFTER twins.jsonl, atomically). The executor's twin gate checks the run record's `run_ms` (fresh), `w_sha`, `engine_sha256` and
`twins_sha256`; the pinned parity tool reads twins.jsonl. Both are sealed until r4-b-T's confirmation read is written (the SEALS readers line).

    python tools/r4bt_c0_twin.py --tape-dir T --orders O --out-dir D --feed-delay-slots d --w-sha W [--now-ms N]
    python tools/r4bt_c0_twin.py --check --tape-dir T --orders O          (names only: oldest present hour vs the needed first hour)

Exit codes: 0 written; 2 refused (nothing written); 3 the inputs cannot be trusted (unparseable tape or order line, integrity mismatch).
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
import socket
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools import arena_stack as AST
from tools import r4bt_gate as G
from tools import r4bt_price as P
from tools.arena_paper import RENT_LAMPORTS, ArenaEngine, ArenaRefused, StackConfig, price_leg, rules_of
from tools.arena_zst import ZstdReader

ENGINE_PIN = "9d2fdb04a3da9bb38de9e30c91154f5a9ab2de513e6f4d7936c1eb1a54863514"  # DEC-031 Am.27 item 24 / item 29 A6
ENGINE_FILE = "tools/arena_paper.py"
R4BT_W = "17e3b05a7696900cda7fcf93e4af4008d8fa0184"  # the r4bt-a1 decision host's W
# git blob ids at W of every tools.* module the twin loads (QP on #1243 item 3). Any other loaded tools.* module refuses.
MODULE_BLOBS = {
    "tools.arena_declog": "2baa782db6b93fe1dc225009176b889785a20763",
    "tools.arena_paper": "5eef156bb617138a4c04fca6779f9ff4c5215b13",
    "tools.arena_stack": "092bbce0854dc6760706608cb706108f67d0179d",
    "tools.arena_zst": "a847d27ef1280e9f93ded5511a9579bf979aa32f",
    "tools.cap_pick_oracle": "64d5e49767c70379bfa5041dfc03976ead37d208",
    "tools.h5_shadow": "f23cf492211fd79287fa6ac82fd382f1f3e39a3d",
    "tools.paper_curve_math": "42daf5adcdc1532cb29a6f5079997d25b10c86ea",
    "tools.pump_structure_monitor": "1ca0a88cecf0853d94336ea046ba1a910b79f198",
    "tools.r4bt_gate": "2c094ad860c1b838399e4274ca7db3ae837f6d9e",
    "tools.r4bt_price": "7ed62b1f43ed58da862a56a5bd28a6167136cb39",
}
SELF_MODULE = "tools.r4bt_c0_twin"
TWIN_ROW_SCHEMA = "r4bt_c0_twin_row_v1"
RUN_SCHEMA = "r4bt_c0_twin_run_v1"
HOUR_MS = 3_600_000
PRE_MS = 3 * HOUR_MS  # the derived copy is read from (first C0 entry decision - 3 h): the guard spot
LEG_S = 1.9
LEG_LONG_S = 3.0
REGISTERED_SEND_LAMPORTS = 505_000  # report-only: the registered per-send fee; the twin prices the order's ACTUAL fees
STAKE_MAX = 1.0
PROD_TAPE_DIR = "/home/claude/mal-data/arena-tipfeed/tape"  # the fast-0 derived copy (the only production tape dir)
FAST_HOST = "mal-fast-0"
TEST_ENV = "R4BT_C0_TWIN_TEST"  # tests only: allow a tape dir other than PROD_TAPE_DIR (closed-root refusals still apply); refused on mal-fast-0
TWIN_CLOSED_MARKS = ("/.writer", "/.push", "/data2/mal/arena-tip-feed", "/var/lib/mal/sealed", "declog", "tip-tape-archive", "/var/lib/mal-live", "arena-mirror")
COUNTS_REL = ".writer/counts.jsonl"  # the only two files read besides tape-*.jsonl.zst
VERIFIED_REL = ".push/verified.json"
ZST_RE = re.compile(r"^tape-(\d{4}-\d\d-\d\dT\d\d)\.jsonl\.zst$")
HOUR_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d$")
SHA_RE = G.SHA_RE
W_RE = re.compile(r"^[0-9a-f]{40}$")
BOT_ID = "r4bt-c0-twin"  # a label inside the in-memory engine only; no registry line, no ledger exists for it

# The executor's order ledger rows this tool reads (sent orders only). Every other key is dropped by the parse hook. Times are ms; fees are TOTAL lamports of the leg
# (every send, escalated resends included).
ORDER_FIELDS = ("order_id", "sent", "pool", "mint", "rank", "t_mark", "stake_sol", "entry_ms", "exit_ms", "entry_fee_lamports", "exit_fee_lamports", "restart",
                "gap_hold", "decision_slot", "landed_slot_entry", "landed_slot_exit", "exit_reason")

TWIN_ROW_FIELDS = ("schema", "order_id", "status", "pool", "mint", "rank", "t_mark", "stake_sol", "entry_fee_lamports", "exit_fee_lamports", "send_lamports",
                   "registered_send_lamports", "fee_gap_bps", "entry_ms", "exit_ms", "entry_leg", "exit_leg", "nofail", "rent_charged", "flags", "gap_or_restart",
                   "v_band_audit", "exclusion_reason", "refused_reason", "decision_slot_sd_t", "landed_slot_entry", "landed_slot_exit", "stack", "leg_s")
LEG_PNL_FIELDS = ("pnl_e1_excluded_lamports", "pnl_e1_charged_lamports", "e1_floor_extra_lamports", "state", "filled", "strand")
RUN_FIELDS = ("schema", "tool", "status", "run_ms", "heartbeat_ms", "w_sha", "engine_sha256", "tool_sha256", "module_blobs", "stack", "feed_delay_slots", "tape_first_hour",
              "tape_last_file", "tape_last_hour_sha256", "tape_files_checked", "tape_pending_hours", "orders_sha256", "orders_sent", "orders_skipped_unsent",
              "counts_by_status", "v_band_audit_n", "twins_sha256", "tape_rows", "tape_data_loss_rows_by_prog_kind", "engine_counters")


class Untrusted(RuntimeError):
    """An input cannot be trusted (unparseable line, integrity mismatch): nothing is written."""


def sha256_file(p: str | Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def real_host() -> str:
    return socket.gethostname()


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


def check_modules(repo: Path, blobs: Mapping[str, str] = MODULE_BLOBS) -> dict[str, str]:
    """Blob-hash the file of every loaded tools.* module (and every pinned file in the tree) against the blobs at W; an unlisted loaded module refuses."""
    got: dict[str, str] = {}
    for name, mod in sorted(sys.modules.items()):
        if not name.startswith("tools.") or name == SELF_MODULE or name.startswith("tools.test_") or name == "tools.conftest":
            continue
        f = getattr(mod, "__file__", None)
        if name not in blobs:
            raise G.Refused(f"the loaded module {name} is not in the pinned list (blobs at W {R4BT_W[:8]}): the pricing path is pinned whole")
        got[name] = blob_id(Path(f).read_bytes())
    for name in blobs:
        if name not in got:
            got[name] = blob_id((repo / (name.replace(".", "/") + ".py")).read_bytes())
    bad = [n for n in sorted(blobs) if got[n] != blobs[n]]
    if bad:
        raise G.Refused(f"module blob(s) differ from the pinned blobs at W: {bad}")
    return dict(sorted(got.items()))


def check_w(repo: Path, w_sha: str, blobs: Mapping[str, str] = MODULE_BLOBS) -> None:
    """--w-sha must be the registered W, and the pinned blobs must be what the install tree's object store holds at W."""
    if not W_RE.match(str(w_sha)):
        raise G.Refused("--w-sha must be the 40-hex W")
    if w_sha != R4BT_W:
        raise G.Refused(f"--w-sha {w_sha} is not the registered r4bt-a1 W {R4BT_W}")
    for name, want in sorted(blobs.items()):
        rel = name.replace(".", "/") + ".py"
        r = subprocess.run(["git", "-C", str(repo), "rev-parse", f"{w_sha}:{rel}"], capture_output=True, text=True)
        if r.returncode != 0:
            raise G.Refused(f"W {w_sha} is not resolvable in the install tree {repo} ({rel}): the install tree must descend from W")
        if r.stdout.strip() != want:
            raise G.Refused(f"the blob of {rel} at W in the install tree is {r.stdout.strip()}, not the pinned {want}")


def check_tape_dir(path: str | Path) -> Path:
    p = Path(path).resolve()
    s = str(p)
    G.check_path(p, "the tape dir")
    for mark in TWIN_CLOSED_MARKS:
        if mark in s:
            raise G.Refused(f"the tape dir {s} is inside {mark}: the twin reads only the fast-0 derived copy's tape-*.jsonl.zst files (QP-RULING-1106-r4bt-c0 section 2)")
    if os.environ.get(TEST_ENV) == "1" and real_host() == FAST_HOST:
        raise G.Refused(f"{TEST_ENV} is set on {FAST_HOST}: the test hook is closed in production")
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


def check_out_dir(out_dir: Path, tape_dir: Path) -> Path:
    o = check_other_path(out_dir, "the out dir")
    if o == tape_dir or tape_dir in o.parents or o in tape_dir.parents:
        raise G.Refused(f"the out dir {o} and the tape dir {tape_dir} overlap: the twin never writes into the derived copy")
    return o


def pinned_config(*, feed_delay_slots: int, stake_sol: float, send_lamports: int, rent: int = 0) -> StackConfig:
    """Item 24's StackConfig pin; stake and send are the order's. d is required (a measured, registered whole number of slots)."""
    base = P.pinned_config(feed_delay_slots=feed_delay_slots, k_rounding="ceil")
    return dataclasses.replace(base, rent=rent, stakes_sol=(float(stake_sol),), send=int(send_lamports))


# ------------------------------------------------------------------------------------------------------------------------------ inputs
def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def load_counts(tape_dir: Path) -> dict[str, tuple[int, str]]:
    """`.writer/counts.jsonl`: hour -> (bytes, sha256 of the uncompressed hour). Only the keys hour, bytes and sha256 are kept (parse hook); a repeated hour must agree."""
    keep = {"hour", "bytes", "sha256"}
    out: dict[str, tuple[int, str]] = {}
    for n, line in enumerate((tape_dir / COUNTS_REL).read_bytes().split(b"\n"), 1):
        if not line.strip():
            continue
        try:
            r = json.loads(line, object_pairs_hook=lambda kv: {k: v for k, v in kv if k in keep})
        except ValueError:
            raise Untrusted(f"unparseable counts line {n}") from None
        h, b, sh = r.get("hour"), r.get("bytes"), r.get("sha256")
        if not isinstance(h, str) or not HOUR_RE.match(h) or not _is_int(b) or not isinstance(sh, str) or not SHA_RE.match(sh):
            raise Untrusted(f"counts line {n} lacks a valid hour, bytes or sha256")
        if h in out and out[h] != (b, sh):
            raise Untrusted(f"counts.jsonl holds two different rows for hour {h}")
        out[h] = (b, sh)
    return out


def load_verified(tape_dir: Path) -> set[str]:
    """`.push/verified.json` {"hours": {hour: ms}}: only the hour keys are used."""
    try:
        obj = json.loads((tape_dir / VERIFIED_REL).read_bytes())
        hours = obj["hours"]
    except (ValueError, KeyError, TypeError):
        raise Untrusted("verified.json is unreadable or has no hours") from None
    if not isinstance(hours, dict):
        raise Untrusted("verified.json hours is not an object")
    return {h for h in hours if isinstance(h, str) and HOUR_RE.match(h)}


def zst_names(tape_dir: Path) -> dict[str, Path]:
    """The closed-hour files present. A `.jsonl` (the open hour), `.part` or `.tmp` is never listed or opened."""
    out = {}
    for p in sorted(tape_dir.iterdir()):
        m = ZST_RE.match(p.name)
        if m:
            out[m.group(1)] = p
    return out


def first_hour_ms(orders: Sequence[Mapping]) -> int:
    return (min(o["entry_ms"] for o in orders) - PRE_MS) // HOUR_MS * HOUR_MS


def select_hours(tape_dir: Path, lo_ms: int, now_ms: int, counts: Mapping[str, tuple[int, str]], verified: set[str]) -> tuple[list[tuple[str, Path, int, str]], int]:
    """Closed hours from floor(lo): the contiguous verified prefix (hour, path, bytes, sha256), and the number of trailing pending hours. A hole below the newest verified hour
    refuses; a verified hour whose file was deleted refuses as hour_deleted_by_retention."""
    present = zst_names(tape_dir)
    out: list[tuple[str, Path, int, str]] = []
    pending = 0
    h = lo_ms // HOUR_MS * HOUR_MS
    while h + HOUR_MS <= now_ms:
        name = P.hour_name(h)
        known = name in verified and name in counts
        if known and name not in present:
            raise G.Refused(f"hour_deleted_by_retention: {name} is verified and counted but its file is gone (the writer's --keep-verified-h must be >= 400 for the C0 run)")
        if known:
            if pending:
                raise G.Refused(f"the derived copy has a hole: {name} is verified after a pending hour (the twin never skips an hour)")
            out.append((name, present[name], counts[name][0], counts[name][1]))
        else:
            pending += 1  # present-but-unverified or not yet arrived: the tape has not reached it
        h += HOUR_MS
    if not out:
        raise G.Refused(f"no verified closed hour from {P.hour_name(lo_ms // HOUR_MS * HOUR_MS)}: the tape has not reached the first C0 entry - 3 h")
    return out, pending


def read_verified_hour(path: Path, nbytes: int, sha: str) -> bytes:
    """Decompress once into memory; the length and the sha256 must equal the writer's counts row before any row is parsed."""
    rd = ZstdReader(path)
    chunks = []
    try:
        while True:
            c = rd.read()
            if not c:
                break
            chunks.append(c)
    finally:
        rd.close()
    data = b"".join(chunks)
    if len(data) != nbytes or hashlib.sha256(data).hexdigest() != sha:
        raise Untrusted(f"{path.name}: decompressed bytes do not match the writer's counts row (bytes/sha256)")
    return data


def rows_of_bytes(data: bytes, name: str):
    for n, line in enumerate(data.split(b"\n"), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            raise Untrusted(f"unparseable tape line {n} in {name}") from None
        if not isinstance(row, dict):
            raise Untrusted(f"tape line {n} in {name} is not an object")
        yield row


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
        if not isinstance(s, (int, float)) or isinstance(s, bool) or not 0 < s <= STAKE_MAX:
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
    """The engine's `send` is per send and enters a trip twice (entry + exit): the order's two ACTUAL total fees are carried as their mean, so the trip total is exact."""
    e = int(o["entry_fee_lamports"])
    x = o.get("exit_fee_lamports")
    return int(round((e + int(x)) / 2.0)) if x is not None else e


def fee_gap_bps(o: Mapping) -> float | None:
    """Report-only: (2 x registered send - actual trip fees) / stake x 10,000. None while the exit fee is unknown."""
    if o.get("exit_fee_lamports") is None:
        return None
    actual = int(o["entry_fee_lamports"]) + int(o["exit_fee_lamports"])
    return (2 * REGISTERED_SEND_LAMPORTS - actual) / (float(o["stake_sol"]) * 1e9) * 10_000.0


def check_tape(tape_dir: Path, orders: Sequence[Mapping]) -> dict:
    """Names only: the oldest present closed-hour name against floor(first entry - 3 h)."""
    present = sorted(zst_names(tape_dir))
    need = P.hour_name(first_hour_ms(orders)) if orders else None
    if need and (not present or present[0] > need):
        raise G.Refused(f"hour_deleted_by_retention: the oldest present hour is {present[0] if present else None}, the first needed hour is {need} (writer --keep-verified-h >= 400)")
    return {"oldest_present_hour": present[0] if present else None, "first_needed_hour": need, "present_hours": len(present)}


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


def entry_stage(pos: Any, d: int) -> dict[str, Any]:
    """The entry leg's landing fields, known the moment the entry is applied: each trip's own sps_entry, k = ceil(L / sps_entry)."""
    corrected = pos.sd + d
    k19 = int(AST.k_slots(LEG_S, pos.sps_entry, "ceil"))
    return {"effective_slot": pos.sd, "corrected_slot": corrected, "sps_entry": pos.sps_entry, "k_1p9": k19, "k_3p0": int(AST.k_slots(LEG_LONG_S, pos.sps_entry, "ceil")),
            "landing_slot": corrected + k19}


class TwinEngine(ArenaEngine):
    """ArenaEngine whose results stay in memory and whose StackConfig is per order (stake S, the order's actual fee). Nothing else is overridden: slot mapping, latency,
    ordering of fills, gaps, V handling, the E1 floor and the strand rules are the pinned engine's."""

    def __init__(self, base_cfg: StackConfig, **kw: Any) -> None:
        super().__init__(ledger_root="/nonexistent-r4bt-c0-twin", registry_path="/nonexistent-r4bt-c0-twin", cfg=base_cfg, **kw)
        self.ledger = _NullLedger()  # type: ignore[assignment]
        self.order_cfg: dict[str, StackConfig] = {}  # position id -> the order's cfg (rent 0)
        self.results: dict[str, dict[str, Any]] = {}
        self.stage: dict[str, dict[str, Any]] = {}

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
                # price_leg's pnl is AFTER the E1 floor: the E1-excluded (deciding) figure adds it back
                legs[name][rule] = {"pnl_e1_excluded_lamports": r["pnl"] + r["e1_extra"], "pnl_e1_charged_lamports": r["pnl"], "e1_floor_extra_lamports": r["e1_extra"],
                                    "state": r["state"], "filled": r["filled"], "strand": r["strand"]}
        self.results[pos.position_id] = {
            "base": base, "legs": legs, "v_src": vs,
            "entry_leg": {**self.stage[pos.position_id], "Qe": st["Qe"][LEG_S], "Be": st["Be"][LEG_S], "spot": st["ref"], "ssb": st["ssb"][LEG_S],
                          "nearby_lamports": st["nearby"][LEG_S]},
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
def build_rows(orders: Sequence[Mapping], by_order: Mapping[str, str], eng: TwinEngine, applied: Mapping[str, set], audit: set[str], refused: Mapping[str, str]) -> list[dict]:
    rows = []
    for o in sorted(orders, key=lambda x: (x["entry_ms"], x["order_id"])):
        oid = o["order_id"]
        pid = by_order.get(oid)
        flags = []
        if o.get("restart"):
            flags.append("order_restart")
        if o.get("gap_hold"):
            flags.append("order_gap_hold")
        if o.get("exit_ms") is not None and o.get("exit_fee_lamports") is None:
            flags.append("exit_fee_missing")  # the entry fee stands in for the exit leg: parity must not treat it as an actual fee
        row: dict[str, Any] = {"schema": TWIN_ROW_SCHEMA, "order_id": oid, "pool": o["pool"], "mint": o.get("mint"), "rank": o.get("rank"), "t_mark": o.get("t_mark"),
                               "stake_sol": o["stake_sol"], "entry_fee_lamports": o["entry_fee_lamports"], "exit_fee_lamports": o.get("exit_fee_lamports"),
                               "send_lamports": send_of(o), "registered_send_lamports": REGISTERED_SEND_LAMPORTS, "fee_gap_bps": fee_gap_bps(o), "entry_ms": o["entry_ms"],
                               "exit_ms": o.get("exit_ms"), "entry_leg": None, "exit_leg": None, "nofail": None, "rent_charged": None, "exclusion_reason": None,
                               "refused_reason": refused.get(oid), "decision_slot_sd_t": o.get("decision_slot"), "landed_slot_entry": o.get("landed_slot_entry"),
                               "landed_slot_exit": o.get("landed_slot_exit"), "leg_s": LEG_S, "v_band_audit": False}
        res = eng.results.get(pid) if pid else None
        if pid and pid in eng.stage:
            row["entry_leg"] = dict(eng.stage[pid])  # P5 needs the landing fields as soon as the entry is applied
        if oid not in applied["entry"]:
            status = "pending_tape"
        elif refused.get(oid):
            status = "refused"
        elif res is None:
            status = "open" if o.get("exit_ms") is None else "pending_tape"
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


def run(*, tape_dir: str | Path, orders_path: str | Path, out_dir: str | Path, feed_delay_slots: int, w_sha: str, now_ms: int | None = None,
        repo: str | Path | None = None, engine_pin: str = ENGINE_PIN, module_blobs: Mapping[str, str] | None = None) -> dict:
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    repo = Path(repo) if repo else Path(__file__).resolve().parent.parent
    blobs = MODULE_BLOBS if module_blobs is None else module_blobs
    refuse_round_trip()
    engine_sha = check_engine_pin(repo, engine_pin)
    mod_blobs = check_modules(repo, blobs)
    check_w(repo, w_sha, blobs)
    troot = check_tape_dir(tape_dir)
    opath = check_other_path(orders_path, "the order ledger")
    odir = check_out_dir(Path(out_dir), troot)
    pinned_config(feed_delay_slots=feed_delay_slots, stake_sol=0.05, send_lamports=0)  # validates d, k_rounding ceil
    orders, unsent = load_orders(opath)
    counts: Counter = Counter()
    refused: dict[str, str] = {}
    by_order: dict[str, str] = {}
    applied: dict[str, set] = {"entry": set(), "exit": set()}
    rows_out: list[dict] = []
    used: list[tuple[str, Path, int, str]] = []
    pending_hours = 0
    eng_counters: dict = {}
    tape_rows = 0
    audit: set[str] = set()
    tape_loss: Counter = Counter()
    if orders:
        lo_ms = first_hour_ms(orders)
        used, pending_hours = select_hours(troot, lo_ms, now, load_counts(troot), load_verified(troot))
        clock = [lo_ms]
        d = feed_delay_slots
        eng = TwinEngine(pinned_config(feed_delay_slots=d, stake_sol=0.05, send_lamports=REGISTERED_SEND_LAMPORTS), sps_fn=None, now_fn=lambda: clock[0],
                         code_state_fn=lambda: (None, None))
        events = sorted([{"_ms": o["entry_ms"], "_k": 0, "k": "entry", "o": o} for o in orders]
                        + [{"_ms": o["exit_ms"], "_k": 1, "k": "exit", "o": o} for o in orders if o.get("exit_ms") is not None],
                        key=lambda x: (x["_ms"], x["_k"], x["o"]["order_id"]))
        pools = {o["pool"] for o in orders}
        prints: dict[str, list] = {p: [] for p in pools}

        def apply(ev: Mapping[str, Any]) -> None:
            o = ev["o"]
            clock[0] = ev["_ms"]  # the call clock IS the decision instant
            oid = o["order_id"]
            if ev["k"] == "entry":
                applied["entry"].add(oid)
                cfg = pinned_config(feed_delay_slots=d, stake_sol=o["stake_sol"], send_lamports=send_of(o))
                try:
                    pid = eng.buy(BOT_ID, o["pool"], o["stake_sol"], o["entry_ms"], eng.hw_slot, {"order_id": oid})
                except ArenaRefused as exc:
                    refused[oid] = str(exc).split(":")[0]
                    return
                eng.order_cfg[pid] = cfg
                eng.stage[pid] = entry_stage(eng.positions[pid], d)
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
        for name, path, nbytes, sha in used:
            data = read_verified_hour(path, nbytes, sha)  # integrity first: no row of an unverified hour is parsed
            for row in rows_of_bytes(data, path.name):
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
            del data
            eng.trim_history()
            busy = {p.pool for p in eng.positions.values()}
            for k in [k for k, h in eng.pools.items() if h.venue == "pump_bonding" and k not in busy]:
                del eng.pools[k]
        # events past the last fed tape row are NOT applied (pending_tape); no flush: a sell without its exit tape stays pending
        for p in prints.values():
            p.sort(key=lambda x: x[0])
        audit = v_audit_ids(eng.results, prints)
        rows_out = build_rows(orders, by_order, eng, applied, audit, refused)
        eng_counters = dict(sorted(eng.counters.items()))
        counts.update(r["status"] for r in rows_out)
    # --- write: twins.jsonl first, then the run record (atomic), so the record's twins_sha256 always matches a complete file
    out_dir_p = Path(odir)
    out_dir_p.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows_out)
    tw = out_dir_p / "twins.jsonl"
    tmp = out_dir_p / f".twins.jsonl.{os.getpid()}.tmp"
    tmp.write_text(body)
    os.replace(tmp, tw)
    last = used[-1] if used else None
    record = {
        "schema": RUN_SCHEMA, "tool": "r4bt_c0_twin", "status": "ok", "run_ms": now, "heartbeat_ms": now, "w_sha": w_sha, "engine_sha256": engine_sha,
        "tool_sha256": sha256_file(Path(__file__)), "module_blobs": mod_blobs,
        "stack": pinned_config(feed_delay_slots=feed_delay_slots, stake_sol=0.05, send_lamports=REGISTERED_SEND_LAMPORTS).as_dict(),
        "feed_delay_slots": feed_delay_slots, "tape_first_hour": used[0][0] if used else None, "tape_last_file": last[1].name if last else None,
        "tape_last_hour_sha256": last[3] if last else None, "tape_files_checked": len(used), "tape_pending_hours": pending_hours, "orders_sha256": sha256_file(opath),
        "orders_sent": len(orders), "orders_skipped_unsent": unsent, "counts_by_status": dict(sorted(counts.items())), "v_band_audit_n": len(audit),
        "twins_sha256": hashlib.sha256(body.encode()).hexdigest(), "tape_rows": tape_rows, "tape_data_loss_rows_by_prog_kind": dict(sorted(tape_loss.items())),
        "engine_counters": eng_counters,
    }
    rr = out_dir_p / "twins.run.json"
    tmp2 = out_dir_p / f".twins.run.json.{os.getpid()}.tmp"
    tmp2.write_text(json.dumps(record, sort_keys=True) + "\n")
    os.replace(tmp2, rr)
    return record


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="r4bt_c0_twin", description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="names only: oldest present hour vs the first needed hour; no other output")
    ap.add_argument("--tape-dir", required=True)
    ap.add_argument("--orders", required=True)
    ap.add_argument("--out-dir")
    ap.add_argument("--feed-delay-slots", type=int)
    ap.add_argument("--w-sha")
    ap.add_argument("--now-ms", type=int, default=None)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        if a.check:
            troot = check_tape_dir(a.tape_dir)
            orders, _ = load_orders(check_other_path(a.orders, "the order ledger"))
            print(json.dumps(check_tape(troot, orders), sort_keys=True))
            return 0
        if a.out_dir is None or a.feed_delay_slots is None or a.w_sha is None:
            print("REFUSED: --out-dir, --feed-delay-slots and --w-sha are required", file=sys.stderr)
            return 2
        rec = run(tape_dir=a.tape_dir, orders_path=a.orders, out_dir=a.out_dir, feed_delay_slots=a.feed_delay_slots, w_sha=a.w_sha, now_ms=a.now_ms)
    except G.Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    except (Untrusted, P.Untrusted) as exc:
        print(f"UNTRUSTED: {exc}", file=sys.stderr)
        return 3
    print(json.dumps({k: rec[k] for k in ("schema", "status", "run_ms", "orders_sent", "counts_by_status", "v_band_audit_n", "tape_files_checked", "tape_pending_hours",
                                          "twins_sha256")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
