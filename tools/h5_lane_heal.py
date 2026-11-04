"""A-R tip-replay heal, SHADOW side (QP-RULING-1104-oc-feedfix s3.1). Installed only by `tools/h5_lane_shadow.py --lane DEC-035 --lane-heal DEC-035 --tip-dir ...`.

What it is. A subclass of the pinned `tools.h5_shadow.Engine` that, besides everything the stock engine does (every stock record is unchanged), keeps per tracked pool
IN MEMORY ONLY the websocket prints, the BoostBuyAndBurn events, the live slot rate at each print and the tip rows the stock `on_tip_row` is handed, and adds exactly two
fields, both appended last:
  * `lane_gap_seq` on every gap row that flags pools (a monotone int);
  * `lane_heal` on every trigger record (a tip-derived field, sealed like `base_union`): the verdict of ONE evaluation at trigger emission (no waiting, no later update).
The verdict is "healed" only when the pinned rule code, re-run on a PRIVATE pinned Engine over the pool's websocket prints merged with the tip's rows (through the pinned
on_trade / on_boost_event paths only), gives the same trigger, the follower's status.json shows every gap interval covered with no follower gap counted, and the union's settled count is 0.
Q, V, BOOST identity / spent and the window are NOT re-implemented here: only the pinned Engine decides.

Without the flag nothing here is imported by the wrapper's path and every record is byte-identical to the stock lane shadow. A tip row's mint, signature, price, Q, reserve or amount
never reaches an output record: `lane_heal` carries counts, slot distances and tokens only.
"""
from __future__ import annotations

import collections
import json
from pathlib import Path
from typing import Any, Callable

from tools import h5_shadow as hs

LANE_HEAL_REF = "DEC-035"
FOLLOWER_STATUS_PATH = "/var/lib/mal/fast-tip-follower/status.json"  # the ONLY follower file read (SEALS-BRIEF LANE TIP READERS amendment)
ROWS_CAP = 20_000  # per source (websocket / tip) per pool; over it the pool is heal_rows_cap
SAMPLE_EVERY_MS = 1_000  # follower status sampled at most once a second
FOLLOWER_RING = 7_200
SLOT_RING = 4_096
SLOT_RING_BUCKET_MS = 250
TIP_PENDING_ROWS = 5_000
ALLOWED_KINDS = ("slot_jump", "silence", "socket_reconnect", "feed_restart")
HEALTH_KEYS = ("resets", "read_errors", "dir_errors", "open_errors", "stat_errors", "poll_errors", "fragments_dropped", "long_lines_dropped", "parse_errors", "no_dir_polls")
ROW_FIELDS = ("venue", "pool", "side", "slot", "t_recv_ms", "event_ts", "quote_reserve", "base_reserve", "virtual_quote_reserves", "sol_lamports", "token_raw", "lp_fee",
              "pool_quote_amount", "fee_recipient_zero", "protocol_fee", "creator_fee", "trader", "signature", "event_index", "tx_index")
REASONS = ("heal_rows_cap", "heal_union_bad", "heal_union_nonzero", "heal_integrity", "heal_pre_unlinked", "heal_interval_unknown", "heal_no_sample", "heal_follower_pins",
           "heal_follower_gap", "heal_tip_behind", "heal_reader_health", "heal_order_ambiguous", "heal_v_missing", "heal_replay_none", "heal_replay_earlier", "heal_replay_diff",
           "heal_replay_error", "heal_no_store")
LANE_HEAL_KEYS = ("v", "status", "reason", "kinds", "n_gaps_full", "max_interval_slots", "min_slots_gap_end_to_trigger", "tip_only_in_gaps_n", "tip_only_total_n",
                  "ws_only_total_n", "boost_buys_tip_only_n", "replay", "healed_through_seq", "follower")

_PINNED_ENGINE = hs.Engine  # captured at import: the replay always uses the pinned class, never a subclass


def compact(row: dict) -> dict:
    return {k: row.get(k) for k in ROW_FIELDS if k in row}


def _ukey(row: dict) -> tuple:
    return (row.get("signature"), row.get("event_index"))


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def pre_unlinked(unresolved: Any, settled: Any) -> bool:
    """tools.h5_executor.pre_unlinked, verbatim in logic (not imported: the shadow's venv does not need the executor's dependencies)."""
    return _is_int(unresolved) and unresolved >= 1 and _is_int(settled) and settled == 0


class PoolHeal:
    """Memory-only store of one tracked pool. Freed at pool close."""

    __slots__ = ("ws", "ws_sps", "boost", "tip", "tip_order", "iv", "cap_ws", "cap_tip", "err")

    def __init__(self) -> None:
        self.ws: list[dict] = []  # compact websocket rows in arrival order
        self.ws_sps: list[float | None] = []  # the live slot rate at each of those prints
        self.boost: list[tuple[int, dict]] = []  # (number of ws rows seen before it, the event) in arrival order
        self.tip: dict[tuple, dict] = {}  # ukey -> compact tip row (first sight wins)
        self.tip_order: list[tuple] = []
        self.iv: dict[int, list] = {}  # index into Pool.gaps -> [L, R, seq] (L / R None = not computable)
        self.cap_ws = self.cap_tip = False
        self.err = False  # a store update failed: the heal refuses (heal_no_store)


def blank_heal() -> dict:
    return {"v": 1, "status": "refused", "reason": None, "kinds": [], "n_gaps_full": 0, "max_interval_slots": None, "min_slots_gap_end_to_trigger": None,
            "tip_only_in_gaps_n": None, "tip_only_total_n": None, "ws_only_total_n": None, "boost_buys_tip_only_n": None,
            "replay": {"match": None, "n_rows": None, "n_tip_only": None, "spent_diff_lamports": None}, "healed_through_seq": None,
            "follower": {"done_before": None, "done_after": None, "counters_equal": None}}


def build_engine_class(status_path: str | Path = FOLLOWER_STATUS_PATH, base: type | None = None) -> type:
    base = base or _PINNED_ENGINE

    class HealEngine(base):  # type: ignore[valid-type,misc]
        _lane_heal = True
        _lh_status_path = str(status_path)

        def __init__(self, *a: Any, **kw: Any) -> None:
            super().__init__(*a, **kw)
            self._lh_seq = 0
            self._lh_cur_seq: int | None = None
            self._lh_pools: dict[str, PoolHeal] = {}
            self._lh_tip_pending: dict[str, dict[tuple, dict]] = {}
            self._lh_pending_over: set[str] = set()  # announced pools whose pending tip rows overflowed (never silent: heal_rows_cap at track)
            self._lh_ring: collections.deque = collections.deque(maxlen=SLOT_RING)  # (recv_ms, hw_slot)
            self._lh_last_bucket: int | None = None
            self._lh_fol: collections.deque = collections.deque(maxlen=FOLLOWER_RING)
            self._lh_fol_ms: int | None = None
            self._lh_pending: list[tuple[str, int]] = []  # (pool, gap index) whose R is the next event's slot
            self._lh_errors = 0  # NOT in self.counters: no stock record may change
            inner = self._emit
            self._emit = lambda rec: inner(self._lh_decorate(rec))

        # ---- record decoration (the only two additions) -------------------------------------------------------
        def _lh_decorate(self, rec: dict) -> dict:
            t = rec.get("type")
            if t == "gap" and self._lh_cur_seq is not None and rec.get("flags_pools") is True:
                return {**rec, "lane_gap_seq": self._lh_cur_seq}
            if t == "trigger" and self.tip is not None:
                try:
                    heal = self._lh_heal(rec)
                except Exception:  # noqa: BLE001 - the heal may never take a trigger down
                    self._lh_errors += 1
                    heal = blank_heal()
                    heal["reason"] = "heal_replay_error"
                return {**rec, "lane_heal": heal}
            return rec

        # ---- stores ---------------------------------------------------------------------------------------------
        def _maybe_track(self, row: dict, slot: int, recv_ms: int):  # noqa: ANN202
            p = super()._maybe_track(row, slot, recv_ms)
            if p is not None and p.pool not in self._lh_pools:
                st = self._lh_pools[p.pool] = PoolHeal()
                for key, r in self._lh_tip_pending.pop(p.pool, {}).items():
                    st.tip[key] = r
                    st.tip_order.append(key)
                if p.pool in self._lh_pending_over:
                    st.cap_tip = True  # the pending buffer dropped rows of this pool before it was tracked
                    self._lh_pending_over.discard(p.pool)
            return p

        def _print_row(self, p, row: dict, slot: int, recv_ms: int, ts):  # noqa: ANN001
            st = self._lh_pools.get(p.pool)
            if st is not None:
                try:
                    if len(st.ws) >= ROWS_CAP:
                        st.cap_ws = True
                    else:
                        c = compact(row)
                        sp = self.sps_fn(p) if self.sps_fn else self.clock.sps()  # not _sps(): that sets Pool.sps_last, a record field
                        st.ws.append(c)
                        st.ws_sps.append(sp)
                except Exception:  # noqa: BLE001
                    self._lh_errors += 1
                    st.err = True
            super()._print_row(p, row, slot, recv_ms, ts)

        def on_boost_event(self, ev: dict, slot: int, recv_ms: int) -> None:
            st = self._lh_pools.get(ev.get("pool", "")) if isinstance(ev, dict) else None
            if st is not None and len(st.boost) >= ROWS_CAP:
                st.cap_ws = True
            elif st is not None:
                st.boost.append((len(st.ws), {k: ev.get(k) for k in ("pool", "authority", "boost_vault_remaining") if k in ev}))
            super().on_boost_event(ev, slot, recv_ms)

        def on_tip_row(self, row: dict) -> None:
            super().on_tip_row(row)
            try:
                if row.get("venue") != "pumpswap":
                    return
                pool, key = row.get("pool"), _ukey(row)
                if key[0] is None or key[1] is None:
                    return
                st = self._lh_pools.get(pool)
                if st is not None:
                    if key in st.tip:
                        return
                    if len(st.tip) >= ROWS_CAP:
                        st.cap_tip = True
                        return
                    st.tip[key] = compact(row)
                    st.tip_order.append(key)
                elif pool in self.announced and pool not in self.seen:
                    buf = self._lh_tip_pending.setdefault(pool, {})
                    if len(buf) < TIP_PENDING_ROWS:
                        buf.setdefault(key, compact(row))
                    else:
                        self._lh_pending_over.add(pool)
            except Exception:  # noqa: BLE001
                self._lh_errors += 1

        def tick(self, now: int) -> None:
            super().tick(now)
            self._lh_sample(now)
            for k in [k for k in self._lh_tip_pending if k not in self.announced or k in self.seen]:
                del self._lh_tip_pending[k]
            self._lh_pending_over &= set(self.announced)

        def _close(self, p, reason: str) -> None:  # noqa: ANN001
            try:
                super()._close(p, reason)
            finally:
                self._lh_pools.pop(p.pool, None)

        # ---- clock, gaps, follower ------------------------------------------------------------------------------
        def _advance_clock(self, slot: int, recv_ms: int, ts) -> None:  # noqa: ANN001
            try:
                self._lh_resolve(slot)
            except Exception:  # noqa: BLE001
                self._lh_errors += 1
            super()._advance_clock(slot, recv_ms, ts)
            try:
                if self.hw_slot is not None:
                    b = recv_ms // SLOT_RING_BUCKET_MS
                    if b != self._lh_last_bucket:
                        self._lh_last_bucket = b
                        self._lh_ring.append((recv_ms, self.hw_slot))
                self._lh_sample(recv_ms)
            except Exception:  # noqa: BLE001
                self._lh_errors += 1

        def _lh_resolve(self, slot: int) -> None:
            if not self._lh_pending:
                return
            pend, self._lh_pending = self._lh_pending, []
            for pool, idx in pend:
                st = self._lh_pools.get(pool)
                iv = None if st is None else st.iv.get(idx)
                if iv is not None and iv[0] is not None and iv[1] is None:
                    iv[1] = max(int(slot), iv[0])

        def _lh_ring_lookup(self, ms: int | None) -> int | None:
            if ms is None:
                return None
            best = None
            for rms, hw in self._lh_ring:
                if rms <= ms:
                    best = hw
                else:
                    break
            return best

        def _gap(self, kind: str, *, flag_pools: bool = True, at_ms: int | None = None, **kw: Any) -> None:
            if not flag_pools:
                super()._gap(kind, flag_pools=flag_pools, at_ms=at_ms, **kw)
                return
            self._lh_seq += 1
            seq = self._lh_seq
            lo: int | None = None
            hi: int | None = None
            pend = False
            if kind == "slot_jump":
                lo, hi = kw.get("from_slot"), kw.get("to_slot")
            elif kind == "silence":
                lo, pend = self.hw_slot, True
            elif kind == "feed_restart":
                lo, pend = self.hw_slot, True
            elif kind == "socket_reconnect":
                start = kw.get("common_down_start_ms")
                lo, pend = (self._lh_ring_lookup(start) if _is_int(start) else None), True
            self._lh_cur_seq = seq
            try:
                super()._gap(kind, flag_pools=flag_pools, at_ms=at_ms, **kw)
            finally:
                self._lh_cur_seq = None
            for pool, p in self.pools.items():
                st = self._lh_pools.get(pool)
                if st is None or not p.gaps:
                    continue
                idx = len(p.gaps) - 1
                st.iv[idx] = [lo if _is_int(lo) else None, hi if (_is_int(hi) and not pend) else None, seq]
                if pend and _is_int(lo):
                    self._lh_pending.append((pool, idx))

        def _lh_sample(self, now_ms: int, force: bool = False) -> None:
            if not force and self._lh_fol_ms is not None and abs(now_ms - self._lh_fol_ms) < SAMPLE_EVERY_MS:
                return
            self._lh_fol_ms = now_ms
            try:
                with open(self._lh_status_path, "r", encoding="utf-8") as f:
                    s = json.load(f)
                done = s["last_done_slot"]
                if not _is_int(done):
                    return
                cnt = tuple(s.get(k) for k in ("gaps", "unresolved_dropped", "lookup_failures", "backlog_jumps"))
                health = self._lh_health()
                self._lh_fol.append((now_ms, done, cnt, s.get("decoder_blobs_pinned") is True and s.get("trade_event_v") is True, health))
            except Exception:  # noqa: BLE001 - unreadable / partial status file: no sample
                self._lh_errors += 1

        def _lh_health(self) -> tuple:
            c = getattr(self.tip, "c", None) or {}
            return tuple(c.get(k, 0) for k in HEALTH_KEYS)

        # ---- the heal -------------------------------------------------------------------------------------------
        def _lh_merge(self, st: PoolHeal, trig_slot: int, mint: str | None) -> dict:
            ws, tip = st.ws, st.tip
            ws_keys = {_ukey(r) for r in ws}
            tip_only = [(k, tip[k]) for k in st.tip_order if k not in ws_keys and _is_int(tip[k].get("slot")) and tip[k]["slot"] <= trig_slot]
            tip_hw = getattr(self.tip, "hw_slot", None)
            ws_only_idx = {i for i, r in enumerate(ws) if _ukey(r) not in tip and (tip_hw is None or (_is_int(r.get("slot")) and r["slot"] <= tip_hw))}
            elems: list[tuple[int, int, Any, Any]] = []  # (slot, arrival order, kind, payload)
            for i, r in enumerate(ws):
                elems.append((int(r["slot"]), i, "ws", i))
            for j, (k, r) in enumerate(tip_only):
                elems.append((int(r["slot"]), len(ws) + j, "tip", k))
            by_slot: dict[int, list] = collections.defaultdict(list)
            for e in elems:
                by_slot[e[0]].append(e)
            ambiguous = False
            seq: list[tuple[str, Any]] = []
            for slot in sorted(by_slot):
                grp = by_slot[slot]
                has_tip_only = any(e[2] == "tip" for e in grp)
                has_ws_only = any(e[2] == "ws" and e[3] in ws_only_idx for e in grp)
                if has_tip_only and has_ws_only:
                    ambiguous = True

                def tkey(e):  # noqa: ANN001, ANN202
                    r = tip[e[3]] if e[2] == "tip" else tip.get(_ukey(ws[e[3]]))
                    if r is None:
                        return None
                    return (r.get("tx_index") if _is_int(r.get("tx_index")) else 1 << 60, r.get("event_index") if _is_int(r.get("event_index")) else 1 << 60)

                if all(tkey(e) is not None for e in grp):
                    grp = sorted(grp, key=lambda e: (tkey(e), e[1]))
                else:
                    grp = sorted(grp, key=lambda e: e[1])  # arrival order (tip-only rows cannot be in this group unless ambiguous)
                seq.extend((e[2], e[3]) for e in grp)
            return {"seq": seq, "tip_only": tip_only, "ws_only_n": len(ws_only_idx), "ambiguous": ambiguous,
                    "v_missing": any(r.get("virtual_quote_reserves") is None for _, r in tip_only)}

        def _lh_replay(self, p, st: PoolHeal, m: dict, rec: dict) -> tuple[str | None, dict]:
            """Feed the merged sequence to a private pinned Engine; returns (reason or None, replay info)."""
            info = {"match": None, "n_rows": len(m["seq"]), "n_tip_only": len(m["tip_only"]), "spent_diff_lamports": None}
            cur: list[float | None] = [next((s for s in st.ws_sps if s is not None), None) if st.ws_sps else None]
            out: list[dict] = []
            eng = _PINNED_ENGINE(out.append, boost_mode=self.boost_mode, sps_fn=lambda _p: cur[0], require_announce=False, pda_fn=self.pda_fn, seal_start_ms=None,
                                 h5_look2_start_ms=None, classifier=hs.StaticClassifier(False), q_star_sol=self.q_star_sol, wall=lambda: 0)
            boost_after: dict[int, list[dict]] = collections.defaultdict(list)
            for n_before, ev in st.boost:
                boost_after[n_before - 1].append(ev)  # after the (n_before-1)-th ws row; -1 = before every row
            tip = st.tip
            last_recv = int(st.ws[0]["t_recv_ms"]) if st.ws else 0  # a leading tip-only row borrows the first websocket print's receive time
            pos_of: dict[Any, int] = {}
            try:
                for ev in boost_after.get(-1, ()):
                    eng.on_boost_event(ev, 0, 0)
                for n, (kind, ref) in enumerate(m["seq"]):
                    if kind == "ws":
                        r = dict(st.ws[ref])
                        cur[0] = st.ws_sps[ref]
                        last_recv = int(r["t_recv_ms"])
                    else:
                        r = dict(tip[ref])
                        r["t_recv_ms"] = last_recv  # monotone: the previous row's receive time
                    r["mint"] = p.mint
                    pos_of[_ukey(r)] = n
                    eng.on_trade(r)
                    if kind == "ws":
                        for ev in boost_after.get(ref, ()):
                            eng.on_boost_event(ev, int(r["slot"]), last_recv)
            except Exception:  # noqa: BLE001
                return "heal_replay_error", info
            first: dict[str, dict] = {}
            for r in out:
                if r.get("type") == "trigger" and r.get("variant") not in first:
                    first[r["variant"]] = r
            reason = None
            cur_var = rec.get("variant")
            for var in hs.VARIANTS:
                live = p.trig.get(var)
                if not isinstance(live, dict) or live.get("type") != "trigger":
                    continue
                rp = first.get(var)
                if rp is None:
                    r_ = "heal_replay_none"
                elif all(rp.get(k) == live.get(k) for k in ("signature", "event_index", "slot", "boost_src", "s0", "v0")):
                    r_ = None
                    if var == cur_var:
                        info["spent_diff_lamports"] = round(rp["boost_spent_sol"] * 1e9) - round(live["boost_spent_sol"] * 1e9)
                elif rp.get("s0") != live.get("s0") or rp.get("v0") != live.get("v0"):
                    r_ = "heal_replay_diff"
                elif _is_int(rp.get("slot")) and rp["slot"] < live["slot"]:
                    r_ = "heal_replay_earlier"
                else:
                    r_ = "heal_replay_diff"
                reason = reason or r_
            info["match"] = reason is None
            return reason, info

        def _lh_cover(self, lo: int, hi: int) -> tuple[str | None, tuple | None]:
            samples = list(self._lh_fol)
            ia = None
            for i, s in enumerate(samples):
                if s[1] < lo:
                    ia = i
                else:
                    break
            if ia is None:
                return "heal_no_sample", None
            ib = next((i for i in range(ia + 1, len(samples)) if samples[i][1] >= hi + hs.TIPAUTH_MARGIN_SLOTS), None)
            if ib is None:
                return "heal_no_sample", None
            a, b = samples[ia], samples[ib]
            if not (a[3] and b[3]):
                return "heal_follower_pins", (a, b)
            if a[2] != b[2] or any(x is None for x in a[2]):
                return "heal_follower_gap", (a, b)
            return None, (a, b)

        def _lh_heal(self, rec: dict) -> dict:
            out = blank_heal()
            p = self.pools.get(rec.get("pool"))
            st = self._lh_pools.get(rec.get("pool"))
            if p is None or st is None or st.err:
                out["reason"] = "heal_no_store"
                return out
            gaps = list(p.gaps)
            trig_slot = int(rec["slot"])
            out["kinds"] = sorted({g.get("kind") if isinstance(g, dict) and isinstance(g.get("kind"), str) else "malformed" for g in gaps})
            out["n_gaps_full"] = len(gaps)
            ws_settled = rec.get("base_breaks_unresolved_settled")
            control = not gaps and ws_settled == 0
            m = self._lh_merge(st, trig_slot, p.mint)
            ivs = [st.iv.get(i) for i in range(len(gaps))]
            known = [iv for iv in ivs if iv is not None and iv[0] is not None and iv[1] is not None]
            tip_only_rows = [r for _, r in m["tip_only"]]
            out["tip_only_total_n"], out["ws_only_total_n"] = len(tip_only_rows), m["ws_only_n"]
            if known:
                out["max_interval_slots"] = max(iv[1] - iv[0] for iv in known)
                out["min_slots_gap_end_to_trigger"] = min(trig_slot - iv[1] for iv in known)
                out["tip_only_in_gaps_n"] = sum(1 for r in tip_only_rows if any(iv[0] <= r["slot"] <= iv[1] for iv in known))
            else:
                out["tip_only_in_gaps_n"] = 0
            ident = rec.get("boost_id")
            boosts = {x for x in (ident, p.pda, p.boost_auth) if x}
            out["boost_buys_tip_only_n"] = sum(1 for r in tip_only_rows if r.get("side") == "buy" and r.get("trader") in boosts)

            def finish(reason: str | None, replay: dict | None = None) -> dict:
                if replay is not None:
                    out["replay"] = replay
                if control:
                    out["status"], out["reason"] = "not_needed", None
                    return out
                out["reason"] = reason
                out["status"] = "healed" if reason is None else "refused"
                if reason is None:
                    out["healed_through_seq"] = self._lh_seq
                return out

            if control:
                if st.cap_ws or st.cap_tip or m["v_missing"]:
                    return finish(None)
                _r, info = self._lh_replay(p, st, m, rec)
                return finish(None, info)
            if st.cap_ws or st.cap_tip:
                return finish("heal_rows_cap")
            u = p.union
            if u is None or u.broken or (rec.get("base_union") or {}).get("status") != "ok":
                return finish("heal_union_bad")
            if rec.get(hs.UNION_FIELD) != 0:
                return finish("heal_union_nonzero")
            if any(not isinstance(g, dict) or g.get("kind") not in ALLOWED_KINDS for g in gaps):
                return finish("heal_integrity")
            if pre_unlinked(rec.get("base_breaks_unresolved"), ws_settled):
                return finish("heal_pre_unlinked")
            if len(known) != len(gaps):
                return finish("heal_interval_unknown")
            first_cover: tuple | None = None
            if known:
                oldest_a = None
                done_before = done_after = None
                for iv in known:
                    reason, ab = self._lh_cover(iv[0], iv[1])
                    if reason is not None:
                        return finish(reason)
                    a, b = ab  # type: ignore[misc]
                    oldest_a = a if oldest_a is None or a[0] < oldest_a[0] else oldest_a
                    done_before = a[1] if done_before is None else min(done_before, a[1])
                    done_after = b[1] if done_after is None else max(done_after, b[1])
                out["follower"] = {"done_before": done_before, "done_after": done_after, "counters_equal": True}
                first_cover = oldest_a
                hi = max(iv[1] for iv in known)
                tip_hw = getattr(self.tip, "hw_slot", None)
                pend = getattr(self.tip, "pending", None)
                if not _is_int(tip_hw) or tip_hw < hi + hs.TIPAUTH_MARGIN_SLOTS or (callable(pend) and pend()):  # unread tip bytes may hold rows below hw
                    return finish("heal_tip_behind")
                if getattr(self.tip, "err", None) is not None or self.tip.status(self.hw_slot) != "ok" or (first_cover is not None and self._lh_health() != first_cover[4]):
                    return finish("heal_reader_health")
            else:  # a break with no gap interval: the tip must still be fully read and healthy
                pend = getattr(self.tip, "pending", None)
                if callable(pend) and pend():
                    return finish("heal_tip_behind")
                if getattr(self.tip, "err", None) is not None or self.tip.status(self.hw_slot) != "ok":
                    return finish("heal_reader_health")
            if m["ambiguous"]:
                return finish("heal_order_ambiguous")
            if m["v_missing"]:
                return finish("heal_v_missing")
            reason, info = self._lh_replay(p, st, m, rec)
            return finish(reason, info)

    HealEngine.__name__ = HealEngine.__qualname__ = "HealEngine"
    return HealEngine


def install(status_path: str | Path = FOLLOWER_STATUS_PATH) -> type:
    """Replace the module global `Engine` of tools.h5_shadow (run_live looks it up at call time), exactly as the wrapper replaces the oracle."""
    cls = build_engine_class(status_path)
    hs.Engine = cls  # type: ignore[misc,assignment]
    return cls
