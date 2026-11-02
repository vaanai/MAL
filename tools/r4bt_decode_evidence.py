"""r4bt_decode_evidence: the DECODE-ONLY migrate evidence, and the decode check run on EVERY unreviewed write over its own live interval (QP on #1197: D1 and Addendum 1 item A;
QP-RULING-1102-r4bt Q2 items 3 and 4). PAPER ONLY. Read-only RPC through an injected client. New file (r4bt_*): it edits no existing file.

D1 - NO BOOST TERMS. `a3_redeploy_check.migrate_evidence` bundles the migrate sample's decode evidence with H5's BOOST evidence (it raises when the graduations or BOOST pools are too
few and calls `mon.annotate_mayhem` / `mon.profile_boost`). Q2 items 4 and 6: the r4-b-T check runs WITHOUT BOOST evidence and BOOST never halts r4-b-T. `migrate_decode_evidence`
keeps only the decode evidence of the same sample (`ARC.migrate_sample`, `mon.tx_event_blobs`, `ARC.evaluate_blobs`, imported not copied) and keeps the MIGRATE_K_MIN usable-tx floor
as an ERROR for an open-ended interval.

ADDENDUM 1 A - EVERY WRITE, ON ITS OWN LIVE INTERVAL. Am.4 item 1.9: the fail instant is the earliest upgrade write after the pin that has no PASS. Between write w_i and the next
write of the same program the chain ran w_i's version, and rows decoded then reach r4-b-T's decisions and prices; a sample taken only after the newest write tests none of them. So
`check_all` takes the enumerated writes (`tools/r4bt_writes.py`) and checks each pump / pumpswap write on [slot_i + SAMPLE_START_OFFSET_SLOTS, slot_{i+1}) (the newest: open-ended).
  * An interval no longer than SAMPLE_MAX_SLOTS is scanned WHOLE (the population): the 500-tx floor, the migrate floor and the "required events were seen" terms are waived (absence
    in a small population is not evidence of a layout change); every event that IS there must still decode and map with exact parity. Mode "population".
  * An interval with ZERO program transactions and ZERO settled migrate transactions decoded nothing under that version: PASS, mode "vacuous", recorded with its slot range. An empty
    interval (the next write within the offset) is vacuous without any sample RPC.
  * A longer interval is sampled from its start by the pinned rule (floors apply). Mode "sample". The newest write (open-ended) is also mode "sample".
  * The first SAMPLE_START_OFFSET_SLOTS after each write stay unchecked (disclosed at every read).
  * Every result names its write (name, slot, mode, slot range). A FAIL, ERROR or timeout on any write is a hard halt at THAT write's instant (`tools/r4bt_a3_writes.py`);
    every write must finish within 24 h of the earliest unreviewed write. fees writes are not checked here (a fees-program write is a hard halt in `r4bt_a3`).
`check_write` is `r4bt_decode_check.check_write` with those substitutions (same terms (c), (d), (f), (g), (h), same profile, so `tools/r4bt_a3.py` accepts its `collect()` file).
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from typing import Any, Callable, Mapping, Sequence

from tools import a3_redeploy_check as ARC
from tools import pump_structure_monitor as mon
from tools import r4bt_decode_check as DC
from tools import r4bt_decode_parity as PAR

PROFILE = DC.PROFILE
CHECK_WINDOW_MS = DC.CHECK_WINDOW_MS
collect = DC.collect
CHECKED_PROGRAMS = ("pump", "pumpswap")

# the decode fields (everything `evaluate_blobs` and the sample add); the BOOST fields of `migrate_evidence` are not in this list
DECODE_FIELDS = ("n_events", "disc_len", "undecodable", "unknown", "idl_mismatch", "seen_events", "n_init_boost", "capped", "n_swap_events", "layout_mismatch", "nonzero_trailing",
                 "trailing_seen", "n_migrate_txs", "migrate_skipped", "slot_min", "slot_max")


def migrate_decode_evidence(client: Any, idls: Mapping[str, Any], min_slot: int, now_s: float, ignored: Mapping[str, str] | None,
                            trailing: Mapping[str, Sequence[int]] | None, end_slot: int | None = None) -> dict[str, Any]:
    """The decode half of `ARC.migrate_evidence`. end_slot None (open-ended): fewer than MIGRATE_K_MIN usable migrate txs raises `ARC.CheckError` (ERROR). end_slot given: only migrate
    txs with slot < end_slot, and no floor (the bounded interval's population: `ARC.migrate_sample` returns the oldest settled txs first, so every one below end_slot is in it)."""
    txs = ARC.migrate_sample(client, min_slot, now_s)
    skipped = dict(getattr(ARC.migrate_sample, "skipped", {}))
    if end_slot is not None:
        txs = [(tx, g) for tx, g in txs if g["slot"] < end_slot]
    elif len(txs) < ARC.MIGRATE_K_MIN:
        raise ARC.CheckError(f"migrate sample: {len(txs)} settled post-deploy migrate txs, need {ARC.MIGRATE_K_MIN}")
    ev = ARC.evaluate_blobs((b for tx, _g in txs for b in mon.tx_event_blobs(tx)), idls, ignored, trailing)
    ev["n_migrate_txs"], ev["migrate_skipped"] = len(txs), skipped
    ev["slot_min"], ev["slot_max"] = (min(g["slot"] for _tx, g in txs), max(g["slot"] for _tx, g in txs)) if txs else (None, None)
    return ev


def fetch_interval_sample(client: Any, start_slot: int, end_slot: int | None) -> tuple[list[list[str]], int, bool]:
    """(log lists, slots scanned, whole_interval). Same call and qualifying rule as `ARC.fetch_sample`, but never past end_slot. whole_interval = the scan covered the whole
    interval [start, end) (end - start <= SAMPLE_MAX_SLOTS), so the list is the population. An open-ended interval is `ARC.fetch_sample`."""
    if end_slot is None:
        sample, scanned = ARC.fetch_sample(client, start_slot)
        return sample, scanned, False
    last = min(end_slot, start_slot + ARC.SAMPLE_MAX_SLOTS)
    out: list[list[str]] = []
    scanned = 0
    for slot in range(start_slot, last):
        scanned += 1
        blk = client.call("getBlock", [slot, {"encoding": "json", "transactionDetails": "full", "rewards": False,
                                              "maxSupportedTransactionVersion": mon.MAX_TX_VERSION, "commitment": "finalized"}])
        for tx in (blk or {}).get("transactions") or []:
            if ARC.tx_is_sample(tx):
                out.append(list(tx["meta"]["logMessages"]))
                if len(out) >= ARC.SAMPLE_MIN_TXS:
                    return out, scanned, False
    return out, scanned, end_slot - start_slot <= ARC.SAMPLE_MAX_SLOTS


def write_intervals(writes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """[{name, slot, at_ms}] (the `r4bt_writes` output) -> one entry per pump / pumpswap write, oldest first: {name, slot, end_slot (the next write of the SAME program, else None),
    deploy_ms (the earliest pump / pumpswap write in the file: every write's check must finish within 24 h of it)}. fees writes are skipped. A malformed entry is a ValueError."""
    rows = []
    for w in writes:
        if not (isinstance(w.get("name"), str) and isinstance(w.get("slot"), int) and not isinstance(w.get("slot"), bool) and isinstance(w.get("at_ms"), int)):
            raise ValueError("a write needs name (str), slot (int) and at_ms (int)")
        if w["name"] in CHECKED_PROGRAMS:
            rows.append(w)
    rows.sort(key=lambda w: (w["slot"], w["name"]))
    first = min((w["at_ms"] for w in rows), default=None)
    out = []
    for w in rows:
        nxt = [x["slot"] for x in rows if x["name"] == w["name"] and x["slot"] > w["slot"]]
        out.append({"name": w["name"], "slot": w["slot"], "end_slot": min(nxt) if nxt else None, "deploy_ms": first, "at_ms": w["at_ms"]})
    return out


def check_write(write: Mapping[str, Any], pins: Mapping[str, Any], r4bt_pins: Mapping[str, Any], *, client: Any, http_get: Callable, now_ms: Callable[[], int],
                deploy_ms: int, adapter_check: Callable[..., list[str]] | None = None, end_slot: int | None = None) -> dict[str, Any]:
    """One write {"name", "slot"} on [slot + offset, end_slot) -> {"name", "slot", "verdict": PASS|FAIL|ERROR, "mode", "slot_range", "at_ms", "reasons", "detail"}. Never raises for a
    check failure. `adapter_check(sample, population=bool)` is the parity function (`r4bt_decode_parity.parity_failures` with the floors waived for a population)."""
    start_slot = int(write["slot"]) + ARC.SAMPLE_START_OFFSET_SLOTS
    out: dict[str, Any] = {"name": write["name"], "slot": write["slot"], "at_ms": None, "reasons": [], "detail": {"sample_start_slot": start_slot}, "mode": "sample",
                           "slot_range": [start_slot, end_slot]}

    def done(verdict: str) -> dict[str, Any]:
        out["verdict"], out["at_ms"] = verdict, int(now_ms())
        return out

    fails: list[str] = out["reasons"]
    try:
        if adapter_check is None:
            raise ARC.CheckError("the r4b_feed_adapter decode path is not wired (adapter_check is None): a PASS is impossible, fail closed")
        ARC.frozen_guard({"frozen_blobs": r4bt_pins.get("frozen_blobs"), "frozen_sha": r4bt_pins.get("frozen_sha")})  # (h)
        if end_slot is not None and end_slot <= start_slot:  # the next write came within the offset: this version decoded nothing we check
            out["mode"] = "vacuous"
            return done("PASS")
        idls, idl_fails = ARC.fetch_idls(http_get, pins)  # (f)
        fails += [f"f: {x}" for x in idl_fails]
        ignored, trailing = pins.get("ignored_discs") or {}, ARC.validate_trailing_pins(pins.get("trailing_bytes"))
        idl_discs = set(ARC.idl_event_table(idls))
        sample, scanned, whole = fetch_interval_sample(client, start_slot, end_slot)  # (c), (d): the log sample
        out["detail"]["slots_scanned"] = scanned
        mv = migrate_decode_evidence(client, idls, start_slot, now_ms() / 1000, ignored, trailing, end_slot)  # decode evidence only
        population = end_slot is not None and whole
        out["detail"].update(n_txs=len(sample), n_migrate_txs=mv.get("n_migrate_txs"))
        if population and not sample and not mv.get("n_migrate_txs"):
            out["mode"] = "vacuous"  # zero program transactions decoded under this version: a vacuous PASS, recorded with its slot range
            return done("PASS")
        if population:
            out["mode"] = "population"
        elif len(sample) < ARC.SAMPLE_MIN_TXS:
            fails.append(f"c: sample has {len(sample)} txs after {scanned} slots, need {ARC.SAMPLE_MIN_TXS}")
        if sample:
            ev = ARC.evaluate_sample(sample, idls, ignored, trailing)
            ev["_idl_discs"] = idl_discs
            ARC.sample_failures(ev, "log sample", pins, fails, client)
            if not population:
                fails += [f"d: {n} not seen in the log sample" for n in ARC.REQUIRED_EVENTS if n not in ev["seen_events"]]
            fails += [f"c: adapter path: {x}" for x in (adapter_check(sample, population=True) if population else adapter_check(sample))]  # (c): r4b_feed_adapter's mapping
        mv["_idl_discs"] = idl_discs
        ARC.sample_failures(mv, "migrate sample", pins, fails, client)
        required = (pins.get("migrate") or {}).get("required_events")
        if not required:
            raise ARC.CheckError("pin missing: migrate.required_events")
        if not population and not (end_slot is not None and mv["n_migrate_txs"] < ARC.MIGRATE_K_MIN):  # a bounded interval with fewer migrates than the floor is its own population
            fails += [f"d: {n} not seen in the migrate sample" for n in required if n not in mv["seen_events"]]
    except ARC.CheckError as exc:
        out["reasons"] = [str(exc)]
        return done("ERROR")
    if int(now_ms()) - int(deploy_ms) > CHECK_WINDOW_MS:  # (g)
        fails.append("g: the check did not finish within 24 h of the earliest write")
    return done("FAIL" if fails else "PASS")


def check_all(writes: Sequence[Mapping[str, Any]], pins: Mapping[str, Any], r4bt_pins: Mapping[str, Any], *, client: Any, http_get: Callable, now_ms: Callable[[], int],
              adapter_check: Callable[..., list[str]] | None) -> list[dict[str, Any]]:
    """Every pump / pumpswap write of `writes`, each on its own live interval; one result per write, oldest first. Never stops at a failure: every write gets its own result."""
    return [check_write({"name": w["name"], "slot": w["slot"]}, pins, r4bt_pins, client=client, http_get=http_get, now_ms=now_ms, deploy_ms=w["deploy_ms"],
                        adapter_check=adapter_check, end_slot=w["end_slot"]) for w in write_intervals(writes)]
