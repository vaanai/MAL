"""r4bt_decode_check: the r4-b-T DECODE CHECK (QP-RULING-1102-r4bt Q2 item 4). PAPER ONLY. Read-only RPC through an injected client; no key is read here.

A PROFILE of `tools/a3_redeploy_check.py`: it imports that module's sample, parity, IDL and frozen-guard functions (no in-place edit, no copy) and runs Amendment 4 item 1.4
(c), (d), (f), (g) and (h) for ONE program-only pump / PumpSwap write, WITHOUT (a)'s BOOST and synthetic terms, WITHOUT (b)'s account terms (an account change is a hard halt of
`tools/r4bt_a3.py`) and WITHOUT (e) (BOOST evidence). `a3_redeploy_check`'s own H5 verdict is NOT an input.

  (c) decoder parity of r4-b-T's decode paths on the pinned sample rule (SAMPLE_MIN_TXS log sample, MIGRATE_K migrate sample, SAMPLE_START_OFFSET_SLOTS after the write):
      the walker decoder (`observe.trade_decode`, through `evaluate_sample` / `migrate_evidence`: 0 undecodable, 0 unknown discriminators, 0 IDL mismatches, the pinned
      trailing-byte set) AND the Helius-window mapping of `tools/r4b_feed_adapter.py` (the `adapter_check` callable: it returns failure strings, [] for none);
  (d) discriminator set and layout lengths equal to the pinned set, and the required events seen;
  (f) the IDL is unchanged (fetch + sha256 against the pin), or fail closed;
  (g) the check finishes within 24 h of the earliest write (`deploy_ms`), else FAIL (timeout);
  (h) the frozen guard: the decoder files' git blobs equal the pinned blobs of W (`r4bt_pins`: {"frozen_sha", "frozen_blobs"}), else ERROR.

FAIL CLOSED. If `adapter_check` is None the path (c) names is not wired: the verdict is ERROR and a PASS is impossible; `r4bt_a3` then halts at the write. That is the state of
this file's PR: the adapter-path parity function is OWED (a later file, same branch rule) and the CLI that builds the RPC client is a host-side wrapper the manager runs.
The output carries `"profile": "r4bt_decode_check_v1"`; `tools/r4bt_a3.py` accepts no other decode-check input.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from typing import Any, Callable, Mapping, Sequence

from tools import a3_redeploy_check as ARC

PROFILE = "r4bt_decode_check_v1"
CHECK_WINDOW_MS = ARC.CHECK_WINDOW_S * 1000


def check_write(write: Mapping[str, Any], pins: Mapping[str, Any], r4bt_pins: Mapping[str, Any], *, client: Any, http_get: Callable, now_ms: Callable[[], int],
                deploy_ms: int, adapter_check: Callable[[Sequence[Sequence[str]]], list[str]] | None = None) -> dict[str, Any]:
    """One write {"name", "slot"} -> {"name", "slot", "verdict": PASS|FAIL|ERROR, "at_ms", "reasons", "detail"}. Never raises for a check failure."""
    out: dict[str, Any] = {"name": write["name"], "slot": write["slot"], "at_ms": None, "reasons": [], "detail": {}}

    def done(verdict: str) -> dict[str, Any]:
        out["verdict"], out["at_ms"] = verdict, int(now_ms())
        return out

    fails: list[str] = out["reasons"]
    try:
        if adapter_check is None:
            raise ARC.CheckError("the r4b_feed_adapter decode path is not wired (adapter_check is None): a PASS is impossible, fail closed")
        ARC.frozen_guard({"frozen_blobs": r4bt_pins.get("frozen_blobs"), "frozen_sha": r4bt_pins.get("frozen_sha")})  # (h)
        idls, idl_fails = ARC.fetch_idls(http_get, pins)  # (f)
        fails += [f"f: {x}" for x in idl_fails]
        ignored, trailing = pins.get("ignored_discs") or {}, ARC.validate_trailing_pins(pins.get("trailing_bytes"))
        idl_discs = set(ARC.idl_event_table(idls))
        start_slot = int(write["slot"]) + ARC.SAMPLE_START_OFFSET_SLOTS
        out["detail"]["sample_start_slot"] = start_slot
        sample, scanned = ARC.fetch_sample(client, start_slot)  # (c), (d): the log sample
        out["detail"]["slots_scanned"] = scanned
        if len(sample) < ARC.SAMPLE_MIN_TXS:
            fails.append(f"c: sample has {len(sample)} txs after {scanned} slots, need {ARC.SAMPLE_MIN_TXS}")
        ev = ARC.evaluate_sample(sample, idls, ignored, trailing)
        ev["_idl_discs"] = idl_discs
        ARC.sample_failures(ev, "log sample", pins, fails, client)
        for name in ARC.REQUIRED_EVENTS:
            if name not in ev["seen_events"]:
                fails.append(f"d: {name} not seen in the log sample")
        mv = ARC.migrate_evidence(client, idls, start_slot, now_ms() / 1000, ignored, trailing)  # the inner-instruction (migrate) sample
        mv["_idl_discs"] = idl_discs
        ARC.sample_failures(mv, "migrate sample", pins, fails, client)
        required = (pins.get("migrate") or {}).get("required_events")
        if not required:
            raise ARC.CheckError("pin missing: migrate.required_events")
        for name in required:
            if name not in mv["seen_events"]:
                fails.append(f"d: {name} not seen in the migrate sample")
        fails += [f"c: adapter path: {x}" for x in adapter_check(sample)]  # (c): r4b_feed_adapter's mapping
    except ARC.CheckError as exc:
        out["reasons"] = [str(exc)]
        return done("ERROR")
    if int(now_ms()) - int(deploy_ms) > CHECK_WINDOW_MS:  # (g)
        fails.append("g: the check did not finish within 24 h of the earliest write")
    return done("FAIL" if fails else "PASS")


def collect(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The file `tools/r4bt_a3.py classify --decode-checks` reads."""
    return {"profile": PROFILE, "redeploys": [dict(r) for r in results]}
