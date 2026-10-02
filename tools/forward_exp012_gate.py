"""EXP-012 entry-model gate for the forward-paper runner (DEC-016 section 3).

Measurement, not a scorer. It computes the frozen model's 18 features online,
from the same prints the runner already holds, with the *same* functions the
offline builder uses (`tools.exploration_entry_model`: `_Feat`, `causal_events`,
`compute_features`, `count_prior_creates`). The runner's fill model, size cap and
fail draw are untouched. A gated migrate book only decides "enter or skip" here.

Where the online path can differ from the offline builder (the replay helper
`tools/forward_exp012_replay.py` measures these; each is also listed in the PR):

1. Time basis. Feature inputs use chain time: `event_ts * 1000` (whole seconds; in
   the offline getBlock walk `t_recv_ms` is None and times are `block_time * 1000`).
   Prints and the migration use the row's `event_ts`. Live observe create rows carry no
   chain time (only `t_ws`, receive), so the create's chain second comes from the tape:
   `create_ms = event_ts * 1000` of the first bonding print whose `signature` equals the
   create row's signature (the creator's initial buy is usually in the create tx).
   Features are computed at decision time, so a matching print that arrives late still
   fixes it. If none arrived by the decision, `create_ms = floor(t_ws to the second)`.
   Counted in the gate row: `time_fallbacks.create_sig_match` / `create_fallback`
   (and `create_row` if the runner was given a chain time on the create row itself);
   `prints` / `migration` count fallbacks to `t_recv_ms`. A fallback create is later than
   the true create by the receive lag, which also shifts the 32 min truncation edge.
   The runner's own decision clock and fills stay on `t_recv_ms`.
   First price is `v_sol / v_token_ui` online, reserves-derived offline (same value
   up to float rounding).
2. Trade prints are the runner's `FlowPrint`s, after its dedupe (a repeated
   print is counted once online and twice offline), after `flow_from_tape_row`
   (rows with missing reserves, or `quote_is_wsol == False`, are dropped online but
   recorded offline) and with a derived price when the row has no `price_sol`
   (offline records `None` and keeps the last real price). Trader `"UNK"` is None.
3. Event order on equal time: offline is file order, online is the runner's
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
7. Creator history (`creator_prior_mints_24h`). Preloaded observe creates are resolved to
   chain seconds by signature match against the tape files for the same window when
   `tape_dir` is given and its hours exist (a regex signature scan, bounded by a time
   budget, default 120 s); creates not matched, or if there is no tape, use
   `floor(t_ws)`. Live creates are fixed by the same signature match as note 1, and the
   creator's history entry is moved to the corrected time. Offline sees every create in the
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
    dummy = compute_features([], create_ms=0, first_price=None, mig_ms=0, creator_prior_mints_24h=0)
    unknown = [n for n in want if n not in dummy]
    if unknown:
        raise GateConfigError(f"entry_features names not produced by compute_features: {unknown}")
    import lightgbm as lgb

    booster = lgb.Booster(model_file=str(mp))
    have = list(booster.feature_name())
    if have != want:
        raise GateConfigError(f"entry_features differ from the model's feature names (order matters): {want} vs {have}")
    return GateSpec(booster, want, float(threshold), mp)


class _Acc:
    __slots__ = ("feat", "had_bond", "migrated", "truncated", "mig_ms", "fb_prints", "create_src", "create_sig", "fb_mig", "mint", "creator")

    def __init__(self, feat: _Feat, create_src: str = "row", create_sig: str | None = None, mint: str = "") -> None:
        self.feat = feat
        self.create_src = create_src  # "row" (chain time given), "sig" (matched a print), "fallback" (floor t_ws)
        self.create_sig = create_sig
        self.mint = mint
        self.had_bond = False
        self.migrated = False
        self.truncated = False
        self.mig_ms: int | None = None
        self.fb_prints = 0
        self.fb_mig = False


def _chain_ms(event_ts: int | None, t_recv_ms: int) -> tuple[int, bool]:
    """(time_ms, used_fallback). event_ts is whole seconds of chain time."""
    if isinstance(event_ts, int) and not isinstance(event_ts, bool) and event_ts >= 1_000_000_000:
        return event_ts * 1000, False
    return t_recv_ms, True


def create_chain_ms(row: dict[str, Any]) -> int | None:
    """Chain time of a create row, ms, if the row carries one."""
    for key in ("event_ts", "block_time", "blockTime"):
        v = row.get(key)
        if isinstance(v, int) and not isinstance(v, bool) and v >= 1_000_000_000:
            return v * 1000
    return None


class Exp012Online:
    """Per-mint accumulators plus a trailing creator history. One per engine."""

    def __init__(self) -> None:
        self.acc: dict[str, _Acc] = {}
        self.hist: dict[str, list[int]] = {}
        self.hist_mints: dict[str, tuple[str, int]] = {}
        self._pruned_at: int | None = None
        self.preload_stats: dict[str, int] = {}
        self.errors: dict[str, str] = {}  # mint -> exception class name from a feed hook

    # --- creator history -------------------------------------------------
    def _add_hist(self, mint: str, creator: str | None, create_ms: int) -> None:
        if not creator or mint in self.hist_mints:
            return
        self.hist_mints[mint] = (creator, create_ms)
        bisect.insort(self.hist.setdefault(creator, []), create_ms)

    def _resolve_sigs(self, tape_dir: Path, wanted: dict[str, int], lo_ms: int, boot_ms: int, budget_s: float) -> dict[str, int]:
        """signature -> chain ms for `wanted` signatures, from the first bonding-looking
        row of each in the tape files for [lo, boot]. Regex scan, no json parse. Never raises."""
        import re
        import time as _time

        from tools.paper_price_path import open_text

        pat = re.compile(r'"signature":\s*"([1-9A-HJ-NP-Za-km-z]{40,100})"')
        ets = re.compile(r'"event_ts":\s*(\d{10})')
        found: dict[str, int] = {}
        deadline = _time.monotonic() + budget_s
        hours = sorted({_time.strftime("%Y-%m-%dT%H", _time.gmtime(t / 1000.0)) for t in range(lo_ms, boot_ms + 1, 1_800_000)})
        for hour in hours:
            for sfx in (".jsonl", ".jsonl.zst"):
                path = tape_dir / f"trades-{hour}{sfx}"
                try:
                    if not path.is_file():
                        continue
                    with open_text(path) as fh:
                        for line in fh:
                            if '"pump_bonding"' not in line:
                                continue
                            m = pat.search(line)
                            if m is None or m.group(1) not in wanted or m.group(1) in found:
                                continue
                            e = ets.search(line)
                            if e is not None:
                                found[m.group(1)] = int(e.group(1)) * 1000
                            if _time.monotonic() > deadline:
                                return found
                except Exception:  # noqa: BLE001
                    continue
        return found

    def preload(self, creates_dir: Path, boot_ms: int, tape_dir: Path | None = None, budget_s: float = 120.0) -> int:
        """Creator history from disk, rows strictly before `boot_ms`. Never raises.
        Reads observe-{day}.jsonl[.zst] for the 25 h before boot and fast-format
        creates-{hour}.jsonl[.zst] (type == create). Returns rows added."""
        from tools.paper_price_path import create_from_observe_row, open_text

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
        rows: list[tuple[str, str, int, str | None]] = []  # mint, creator, t_ms, signature (observe rows only)
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
                        if row.get("type") == "create" and isinstance(row.get("block_time"), int):
                            mint, creator, t_ms, sig = row.get("mint"), row.get("creator"), row["block_time"] * 1000, None
                        else:
                            c = create_from_observe_row(row)
                            if c is None:
                                continue
                            mint, creator, t_ms, sig = c.mint, c.creator, (c.t_signal_ms // 1000) * 1000, c.signature
                        if isinstance(mint, str) and isinstance(creator, str) and lo <= t_ms < boot_ms:
                            rows.append((mint, creator, t_ms, sig))
            except Exception:  # noqa: BLE001 - a bad file must not stop boot; history just undercounts
                continue
        sig_matched = 0
        resolved: dict[str, int] = {}
        if tape_dir is not None:
            wanted = {sig: t for _m, _c, t, sig in rows if sig}
            if wanted:
                resolved = self._resolve_sigs(Path(tape_dir), wanted, lo, boot_ms, budget_s)
        added = 0
        for mint, creator, t_ms, sig in rows:
            if sig and sig in resolved:
                t_ms = resolved[sig]
                sig_matched += 1
            before = len(self.hist_mints)
            self._add_hist(mint, creator, t_ms)
            added += len(self.hist_mints) - before
        self.preload_stats = {"rows": added, "sig_matched": sig_matched, "fallback_floor_t_ws": sum(1 for r in rows if r[3]) - sig_matched}
        return added

    # --- feed ------------------------------------------------------------
    def note_create(self, mint: str, creator: str | None, create_ms: int, first_price: float | None, chain: bool = True, signature: str | None = None) -> None:
        """`create_ms` is chain time if `chain`; else the floor-second of the observe
        receive time, to be replaced by the first bonding print with `signature`."""
        try:
            if mint in self.acc:
                return
            src = "row" if chain else "fallback"
            if not chain:
                create_ms = (create_ms // 1000) * 1000
            self.acc[mint] = _Acc(
                _Feat(creator or "", create_ms, first_price if first_price and first_price > 0 else None),
                create_src=src,
                create_sig=signature if isinstance(signature, str) and signature and not chain else None,
                mint=mint,
            )
            self._add_hist(mint, creator, create_ms)
        except Exception as exc:  # noqa: BLE001 - never break the runner's hot path
            self.errors[mint] = type(exc).__name__

    def _fix_create(self, a: _Acc, new_ms: int) -> None:
        old = a.feat.create_ms
        a.feat.create_ms = new_ms
        a.create_src = "sig"
        entry = self.hist_mints.get(a.mint)
        if entry is not None:
            creator, _t = entry
            times = self.hist.get(creator)
            if times is not None:
                k = bisect.bisect_left(times, old)
                if k < len(times) and times[k] == old:
                    del times[k]
                bisect.insort(times, new_ms)
            self.hist_mints[a.mint] = (creator, new_ms)

    def note_print(self, mint: str, *, venue: str, t_recv_ms: int, event_ts: int | None, side: str, trader: str | None, sol_lamports: int, token_raw: int, price_sol: float | None, signature: str | None = None) -> None:
        a = self.acc.get(mint)
        if a is None or venue != "pump_bonding":
            return
        try:
            if a.create_src == "fallback" and a.create_sig is not None and signature == a.create_sig:
                ts, fb = _chain_ms(event_ts, t_recv_ms)
                if not fb:
                    self._fix_create(a, ts)
            a.had_bond = True
            if a.truncated:
                return
            t_ms, fb = _chain_ms(event_ts, t_recv_ms)
            if t_ms >= a.feat.create_ms + WINDOW_MS:
                a.truncated = True  # offline's hot -> watch move, at exactly 32 min (see module note 4)
                return
            if fb:
                a.fb_prints += 1
            # Recording continues until the decision: a bonding print that arrives after the
            # first pumpswap print but has t < mig_ms is counted. causal_events filters at decision.
            a.feat.record(
                {"side": side, "trader": trader, "sol_lamports": sol_lamports, "token_raw": token_raw, "price_sol": price_sol},
                t_ms,
            )
        except Exception as exc:  # noqa: BLE001
            self.errors[mint] = type(exc).__name__

    def mark_migrated(self, mint: str, event_ts: int | None, t_recv_ms: int) -> None:
        """The first pumpswap print: fixes the migration time (chain seconds) and keeps the
        accumulator past the 60 min prune until the decision."""
        a = self.acc.get(mint)
        if a is not None:
            a.migrated = True
            a.mig_ms, a.fb_mig = _chain_ms(event_ts, t_recv_ms)

    # --- decision ---------------------------------------------------------
    def features_at(self, mint: str, fallback_mig_ms: int) -> tuple[dict[str, float] | None, str | None]:
        """(features, None) or (None, reason). Cutoff is strictly `t < mig_ms` (chain ms)."""
        if mint in self.errors:
            return None, "gate_error"
        a = self.acc.get(mint)
        if a is None:
            return None, "no_features"
        if not a.had_bond:
            return None, "no_bond_history"
        f = a.feat
        mig_ms = a.mig_ms if a.mig_ms is not None else fallback_mig_ms
        prior = count_prior_creates(self.hist, f.creator, f.create_ms)
        feats = compute_features(
            causal_events(f.events, mig_ms),
            create_ms=f.create_ms,
            first_price=f.first_price,
            mig_ms=mig_ms,
            creator_prior_mints_24h=prior,
        )
        return feats, None

    def mig_ms_of(self, mint: str, fallback_mig_ms: int) -> int:
        a = self.acc.get(mint)
        return a.mig_ms if a is not None and a.mig_ms is not None else fallback_mig_ms

    def time_fallbacks(self, mint: str) -> dict[str, Any] | None:
        a = self.acc.get(mint)
        if a is None:
            return None
        return {
            "prints": a.fb_prints,
            "create_sig_match": a.create_src == "sig",
            "create_fallback": a.create_src == "fallback",
            "create_row": a.create_src == "row",
            "migration": a.fb_mig,
        }

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


def gate_row(
    book: str,
    mint: str,
    mig_ms: int,
    gate: GateSpec,
    feats: dict[str, float] | None,
    score: float | None,
    passed: bool,
    reason: str | None,
    *,
    time_fallbacks: dict[str, Any] | None = None,
    error: str | None = None,
    decision_t_ms: int | None = None,
) -> dict[str, Any]:
    """`mig_ms` is chain time (the scorer's key). `decision_t_ms` is the runner's receive clock."""
    return {
        "schema": SCHEMA_GATE,
        "book": book,
        "mint": mint,
        "mig_ms": mig_ms,
        "decision_t_ms": decision_t_ms,
        "score": score,
        "threshold": gate.threshold,
        "entered": passed,
        "reason": reason,
        "error": error,
        "time_fallbacks": time_fallbacks,
        "features": None if feats is None else {n: feats[n] for n in gate.names},
    }
