"""EXP-012 entry-model gate for the forward-paper runner (DEC-016 section 3).

Measurement, not a scorer. It computes the frozen model's 18 features online,
from the same prints the runner already holds, with the *same* functions the
offline builder uses (`tools.exploration_entry_model`: `_Feat`, `causal_events`,
`compute_features`, `count_prior_creates`). The runner's fill model, size cap and
fail draw are untouched. A gated migrate book only decides "enter or skip" here.

Where the online path can differ from the offline builder (the replay helper
`tools/forward_exp012_replay.py` measures these; each is also listed in the PR):

1. Create time and first price. Offline: the fast-format create row's chain
   `block_time * 1000` and `quote_reserve / (base_reserve * 1000)`. Online: the
   runner's create comes from observe rows (`t_ws` receive time, and
   `v_sol / v_token_ui`). `time_to_migrate_s`, the 3 s sniper window and the
   24 h creator window therefore shift by the create's receive lag.
2. Trade prints are the runner's `FlowPrint`s, after its dedupe (a repeated
   print is counted once online and twice offline), after `flow_from_tape_row`
   (rows with missing reserves, or `quote_is_wsol == False`, are dropped online but
   recorded offline) and with a derived price when the row has no `price_sol`
   (offline records `None` and keeps the last real price). Trader `"UNK"` is None.
3. Event order on equal `t_recv_ms`: offline is file order, online is the runner's
   (slot, tx_index, event_index) order. Only `price_return_pre` and `mcap_at_t_sol`
   (last price) can see it.
4. 32-minute truncation. The offline builder keeps a mint in a `hot` set and
   moves it to a `watch` set (no more events recorded) only at a flush: every
   300 000 trade lines or at the end of an hour, once `now >= create + WINDOW_MS`
   (32 min). So offline a mint can keep recording for longer than 32 min, by an
   amount that depends on line counts. Online stops recording exactly 32 min after
   the create. It can differ from offline for any mint that migrates after 32 min;
   it is identical for mints that migrate sooner.
5. Memory bound: a mint's accumulator is dropped at the migrate decision, or 60 min
   after the create if it has not migrated. A mint that migrates later than 60 min
   after its create gets `no_features` (skipped) online; offline still scores it.
6. Migration definition. Online: the runner's first `pumpswap` print at or after
   the create. Offline additionally needs a prior bonding print. The gate skips with
   `no_bond_history` if no bonding print was seen, which matches offline (unscored).
7. Creator history (`creator_prior_mints_24h`). Offline sees every create in the
   whole pool. Online sees creates preloaded at boot from the creates dir (the
   previous and current UTC day, plus fast-format hour files) and creates registered
   since. At the 00:00Z daily restart the in-memory accumulators are lost: a mint
   created before the restart is not in the runner's library (it is a "dead mint",
   DEC-015) so it gets no decision at all, and only the creator history is rebuilt
   from disk. Preloaded observe rows carry the receive time, not the chain time.
8. Mints created before boot, or while the runner was down, are never gated.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Sequence

from tools.exploration_entry_model import (
    CREATOR_LOOKBACK_MS,
    _Feat,
    causal_events,
    compute_features,
    count_prior_creates,
)
from tools.latency_curve import WINDOW_MS

SCHEMA_GATE = "forward_paper_exp012_gate_v1"
DROP_AFTER_CREATE_MS = 60 * 60 * 1000
# Must cover a 60-minute-old mint's own 24 h lookback.
HIST_KEEP_MS = CREATOR_LOOKBACK_MS + DROP_AFTER_CREATE_MS + 5 * 60 * 1000
PRUNE_EVERY_MS = 60_000


class GateConfigError(Exception):
    """A gated book's model, md5, threshold or feature list is not what it claims."""


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


class GateSpec:
    """A verified model: md5 matched, feature list equals the model's, in order."""

    def __init__(self, booster: Any, names: list[str], threshold: float, model_path: Path) -> None:
        self.booster = booster
        self.names = names
        self.threshold = threshold
        self.model_path = model_path

    def score(self, feats: dict[str, float]) -> float:
        import numpy as np

        x = np.asarray([[float(feats[n]) for n in self.names]], dtype=np.float64)
        return float(self.booster.predict(x, num_threads=1)[0])


def load_gate(model_path: str, model_md5: str, threshold: float, features_path: str) -> GateSpec:
    """Raises GateConfigError on any mismatch. Called at config load and engine build.
    No hot reload: callers must not call it again for a running book."""
    mp, fp = Path(model_path), Path(features_path)
    if not mp.is_file():
        raise GateConfigError(f"entry_model {mp} is not a file")
    if not fp.is_file():
        raise GateConfigError(f"entry_features {fp} is not a file")
    got = _md5(mp)
    if got != str(model_md5).strip().lower():
        raise GateConfigError(f"entry_model md5 mismatch: file {got}, config {model_md5}")
    try:
        doc = json.loads(fp.read_text(encoding="utf-8"))
        want = list(doc["frozen_feature_names"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise GateConfigError(f"entry_features unreadable: {exc!r}") from exc
    import lightgbm as lgb

    booster = lgb.Booster(model_file=str(mp))
    have = list(booster.feature_name())
    if have != want:
        raise GateConfigError(f"entry_features differ from the model's feature names (order matters): {want} vs {have}")
    return GateSpec(booster, want, float(threshold), mp)


class _Acc:
    __slots__ = ("feat", "had_bond", "migrated", "truncated")

    def __init__(self, feat: _Feat) -> None:
        self.feat = feat
        self.had_bond = False
        self.migrated = False
        self.truncated = False


class Exp012Online:
    """Per-mint accumulators plus a trailing creator history. One per engine."""

    def __init__(self) -> None:
        self.acc: dict[str, _Acc] = {}
        self.hist: dict[str, list[int]] = {}
        self.hist_mints: dict[str, tuple[str, int]] = {}
        self._pruned_at: int | None = None

    # --- creator history -------------------------------------------------
    def _add_hist(self, mint: str, creator: str | None, create_ms: int) -> None:
        if not creator or mint in self.hist_mints:
            return
        self.hist_mints[mint] = (creator, create_ms)
        bisect.insort(self.hist.setdefault(creator, []), create_ms)

    def preload(self, creates_dir: Path, boot_ms: int) -> int:
        """Creator history from disk, rows strictly before `boot_ms`. Never raises.
        Reads observe-{day}.jsonl[.zst] for the 25 h before boot and fast-format
        creates-{hour}.jsonl[.zst] (type == create). Returns rows added."""
        from tools.paper_price_path import create_from_observe_row, open_text

        added = 0
        lo = boot_ms - HIST_KEEP_MS
        days = {time.strftime("%Y-%m-%d", time.gmtime(t / 1000.0)) for t in range(lo, boot_ms + 1, 3_600_000)}
        days.add(time.strftime("%Y-%m-%d", time.gmtime(boot_ms / 1000.0)))
        paths: list[Path] = []
        for day in sorted(days):
            for sfx in (".jsonl", ".jsonl.zst"):
                paths.append(creates_dir / f"observe-{day}{sfx}")
        hours = {time.strftime("%Y-%m-%dT%H", time.gmtime(t / 1000.0)) for t in range(lo, boot_ms + 1, 1_800_000)}
        for hour in sorted(hours):
            for sfx in (".jsonl", ".jsonl.zst"):
                paths.append(creates_dir / f"creates-{hour}{sfx}")
        for path in paths:
            try:
                if not path.is_file():
                    continue
                with open_text(path) as fh:
                    for line in fh:
                        try:
                            row = json.loads(line)
                        except ValueError:
                            continue
                        if not isinstance(row, dict):
                            continue
                        mint = creator = None
                        t_ms = None
                        if row.get("type") == "create" and isinstance(row.get("block_time"), int):
                            mint, creator, t_ms = row.get("mint"), row.get("creator"), row["block_time"] * 1000
                        else:
                            c = create_from_observe_row(row)
                            if c is not None:
                                mint, creator, t_ms = c.mint, c.creator, c.t_signal_ms
                        if isinstance(mint, str) and isinstance(creator, str) and t_ms is not None and lo <= t_ms < boot_ms:
                            before = len(self.hist_mints)
                            self._add_hist(mint, creator, t_ms)
                            added += len(self.hist_mints) - before
            except Exception:  # noqa: BLE001 - a bad file must not stop boot; history just undercounts
                continue
        return added

    # --- feed ------------------------------------------------------------
    def note_create(self, mint: str, creator: str | None, create_ms: int, first_price: float | None) -> None:
        if mint in self.acc:
            return
        self.acc[mint] = _Acc(_Feat(creator or "", create_ms, first_price if first_price and first_price > 0 else None))
        self._add_hist(mint, creator, create_ms)

    def note_print(self, mint: str, *, venue: str, t_ms: int, side: str, trader: str | None, sol_lamports: int, token_raw: int, price_sol: float | None) -> None:
        a = self.acc.get(mint)
        if a is None or a.migrated or venue != "pump_bonding":
            return
        a.had_bond = True
        if a.truncated:
            return
        if t_ms >= a.feat.create_ms + WINDOW_MS:
            a.truncated = True  # offline's hot -> watch move, at exactly 32 min (see module note 4)
            return
        a.feat.record(
            {"side": side, "trader": trader, "sol_lamports": sol_lamports, "token_raw": token_raw, "price_sol": price_sol},
            t_ms,
        )

    def mark_migrated(self, mint: str) -> None:
        a = self.acc.get(mint)
        if a is not None:
            a.migrated = True

    # --- decision ---------------------------------------------------------
    def features_at(self, mint: str, mig_ms: int) -> tuple[dict[str, float] | None, str | None]:
        """(features, None) or (None, reason). Cutoff is strictly `t < mig_ms`."""
        a = self.acc.get(mint)
        if a is None:
            return None, "no_features"
        if not a.had_bond:
            return None, "no_bond_history"
        f = a.feat
        prior = count_prior_creates(self.hist, f.creator, f.create_ms)
        feats = compute_features(
            causal_events(f.events, mig_ms),
            create_ms=f.create_ms,
            first_price=f.first_price,
            mig_ms=mig_ms,
            creator_prior_mints_24h=prior,
        )
        return feats, None

    def drop(self, mint: str) -> None:
        self.acc.pop(mint, None)

    def prune(self, now_ms: int) -> None:
        last = self._pruned_at
        if last is not None and now_ms - last < PRUNE_EVERY_MS:
            return
        self._pruned_at = now_ms
        for mint, a in list(self.acc.items()):
            if not a.migrated and now_ms - a.feat.create_ms > DROP_AFTER_CREATE_MS:
                del self.acc[mint]
        cut = now_ms - HIST_KEEP_MS
        for mint, (creator, t) in list(self.hist_mints.items()):
            if t < cut:
                del self.hist_mints[mint]
        for creator, times in list(self.hist.items()):
            k = bisect.bisect_left(times, cut)
            if k:
                del times[:k]
            if not times:
                del self.hist[creator]


def gate_row(book: str, mint: str, mig_ms: int, gate: GateSpec, feats: dict[str, float] | None, score: float | None, passed: bool, reason: str | None) -> dict[str, Any]:
    return {
        "schema": SCHEMA_GATE,
        "book": book,
        "mint": mint,
        "mig_ms": mig_ms,
        "score": score,
        "threshold": gate.threshold,
        "entered": passed,
        "reason": reason,
        "features": None if feats is None else {n: feats[n] for n in gate.names},
    }
