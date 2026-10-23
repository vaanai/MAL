"""Entry and exit prices for the H5 canary twin (QP 10-23 ruling 5 (P1/P6)). PAPER ONLY: no RPC, no key. The frozen scorer row (tools/arena_h5_score) gets NO new fields.

The twin writer's H5 adapter reads legs.flat_1p9.nofail_lamports from the ledger row but the row stores no pool states, so it has no price. This module rebuilds them:
  * the pool states are the SCORER'S OWN: meta.landing_slots["1p9"] (entry) and meta.exit_trigger_slots["1p9"] + meta.exit_lag_slots (exit), read from the book's shadow `strip`
    record of the row's pool with the scorer's own `Strip.state_at` = the PRE-trade PumpSwap reserves INCLUDING V of the next print (not "the decision instant");
  * `Strip`, the cost module `tools/arena_h5_cost.py` and the scorer are loaded from a SEPARATE EXPORT at the book's registration sha (`--frozen-root`), never from this checkout;
    the export's arena_h5_cost.py sha256 is checked against the pin, and the scorer must be loaded from inside the export;
  * the round trip is rebuilt with the export's cost module (rent-charged, the E1 floor, the 505,000 send) and its nofail P&L must equal the row's `nofail_lamports` within
    1 lamport, else the row is price_failed (fail closed);
  * units are the live recorder's: entry_price = stake lamports / raw tokens bought; exit_price = proceeds / raw tokens sold (the haircut-adjusted tokens), lamports per raw token.
The tape is read only as the book's own arena shadow dir (the arena books' own path). Tests use synthetic strips only.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping

TOL_LAMPORTS = 1.0
LEG = "1p9"


class PriceFailed(Exception):
    pass


def load_frozen(root: Path, cost_sha256: str) -> tuple[Any, Any]:
    """(scorer module, cost module) loaded from the export at `root`; sys.modules is restored. The export's cost module must hash to the pin."""
    root = Path(root).resolve()
    f = root / "tools" / "arena_h5_cost.py"
    try:
        got = hashlib.sha256(f.read_bytes()).hexdigest()
    except OSError:
        raise PriceFailed("frozen_root_unreadable") from None
    if got != cost_sha256:
        raise PriceFailed(f"frozen_cost_sha256_mismatch:{got}")
    saved = {k: v for k, v in sys.modules.items() if k == "tools" or k.startswith("tools.")}
    for k in saved:
        del sys.modules[k]
    sys.path.insert(0, str(root))
    try:
        sc = importlib.import_module("tools.arena_h5_score")
        cost = importlib.import_module("tools.arena_h5_cost")
        for m in (sc, cost):
            if not str(Path(m.__file__).resolve()).startswith(str(root)):
                raise PriceFailed("frozen_module_not_from_export")
        return sc, cost
    finally:
        sys.path.remove(str(root))
        for k in [k for k in sys.modules if k == "tools" or k.startswith("tools.")]:
            del sys.modules[k]
        sys.modules.update(saved)


def find_strip(shadow_dir: Path, pool: str) -> dict[str, Any] | None:
    """The first strict-JSON `strip` record of `pool` in the dir's hourly files (the scorer keeps the first too)."""
    for f in sorted(Path(shadow_dir).glob("h5-shadow-????-??-??T??.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if '"strip"' not in line or pool not in line:
                continue
            try:
                r = json.loads(line, parse_constant=lambda n: (_ for _ in ()).throw(ValueError(n)))
            except ValueError:
                continue
            if r.get("type") == "strip" and r.get("pool") == pool:
                return r
    return None


def derive(row: Mapping[str, Any], strip_rec: Mapping[str, Any] | None, sc: Any, cost: Any) -> dict[str, float]:
    """{entry_price, exit_price, pnl_derived} for one ledger row, or PriceFailed(reason)."""
    meta = row.get("meta") or {}
    try:
        land = int((meta["landing_slots"])[LEG])
        xtrig = int((meta["exit_trigger_slots"])[LEG])
        el = int(meta["exit_lag_slots"])
        spot = float(meta["spot"])
        lam = float(row["stake_sol"]) * 1e9
        want = float(row["legs"]["flat_1p9"]["nofail_lamports"])
    except (KeyError, TypeError, ValueError):
        raise PriceFailed("row_missing_meta") from None
    if strip_rec is None:
        raise PriceFailed("no_strip_record")
    try:
        strip = sc.Strip(dict(strip_rec))
    except (KeyError, TypeError):
        raise PriceFailed("strip_unreadable") from None
    if strip.truncated:
        raise PriceFailed("strip_truncated")
    xland = xtrig + el
    qe, be, _ = strip.state_at(land)
    qx, bx, _ = strip.state_at(xland)
    t = cost.deciding_round_trip(lam, qe, be, spot, qx, bx)
    if t["state"] != "filled":
        raise PriceFailed(f"rebuilt_state:{t['state']}")
    if not (math.isfinite(t["pnl"]) and abs(t["pnl"] - want) <= TOL_LAMPORTS):
        raise PriceFailed("rebuilt_pnl_differs_from_row")
    net = lam * (1 - cost.fee_ppm(qe, be) / 1e6)
    tok = be * net / (qe + net)
    tokh = tok / (1 + cost.H_IN)
    qa, ba = qx + net, bx - tok
    gross = tokh * qa / (ba + tokh)
    proceeds = gross * (1 - cost.fee_ppm(qa, ba) / 1e6) * (1 - cost.H_OUT)
    entry, exit_ = lam / tok, proceeds / tokh
    if not (math.isfinite(entry) and math.isfinite(exit_) and entry > 0 and exit_ > 0):
        raise PriceFailed("prices_not_finite")
    return {"entry_price": entry, "exit_price": exit_, "pnl_derived": t["pnl"]}
