"""HV6 phantom-row filter (QP ruling 2026-10-26, section 4 HV6; P48). Pure, no I/O, no shared module touched.

Three kinds of tape row are dropped before the state and the engine see them (Runner's row hook):
  sig_ones      the signature is 64 '1' characters (64 zero bytes: a simulated transaction)
  signer        a trade row whose trader is one of PHANTOM_SIGNERS (this also drops some rows of theirs that did land: P48 found 10 of 20 child sells on chain)
  repeat_slot   a signature already seen at a DIFFERENT slot (the first sighting cannot be retracted in a stream and passes)
A row is counted once, under the first kind that matches, in the order sig_ones, signer, repeat_slot.

The signature history is bounded: three generations of GEN_MS of tape time each (drop the oldest when a new one starts), so a signature is remembered for at
least 2 * GEN_MS = 2 h and at most 3 * GEN_MS. A key is an 8-byte blake2b digest of the signature (a collision needs two different signatures that also differ
in slot; about 1e-7 for two million live keys). The history is in memory only. The runner replays 365 min of tape before its first decision (manager17, 2026-10-26), so after a start or restart the history is rebuilt from that replay and covers the 2 h window before the first decision.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

PHANTOM_SIGNERS = frozenset({"BUpWiWy8Tqed4jy86dr7ZpoaE747DpQsrGnwmtpMwfBr", "oi1ewFJP7HikgdbjhnUboCzuWXHanAe4Wtvy8UFEnd6"})
ALL_ONES_SIG = "1" * 64
GEN_MS = 3_600_000
KINDS = ("sig_ones", "signer", "repeat_slot")


class PhantomFilter:
    def __init__(self, gen_ms: int = GEN_MS) -> None:
        if gen_ms < 1:
            raise ValueError("gen_ms must be >= 1")
        self.gen_ms = gen_ms
        self.gens: list[dict[bytes, int]] = [{}]  # newest last, at most 3
        self.gen_start: int | None = None
        self.last_t = 0
        self.counts = {k: 0 for k in KINDS}

    def size(self) -> int:
        return sum(len(g) for g in self.gens)

    def total(self) -> int:
        return sum(self.counts.values())

    def _rotate(self, t: int) -> None:
        if self.gen_start is None:
            self.gen_start = t - t % self.gen_ms
        elif t >= self.gen_start + self.gen_ms:
            self.gen_start = t - t % self.gen_ms
            self.gens.append({})
            del self.gens[:-3]

    def check(self, row: Mapping[str, Any]) -> str | None:
        """None = keep the row (and remember its signature); else the kind it is dropped under (already counted)."""
        sig = row.get("signature")
        if sig == ALL_ONES_SIG:
            return self._drop("sig_ones")
        if row.get("type") == "trade" and row.get("trader") in PHANTOM_SIGNERS:
            return self._drop("signer")
        t = row.get("t_recv_ms")
        if isinstance(t, int) and not isinstance(t, bool):
            self.last_t = max(self.last_t, t)
        self._rotate(self.last_t)
        slot = row.get("slot")
        if not isinstance(sig, str) or not sig or not isinstance(slot, int) or isinstance(slot, bool):
            return None
        key = hashlib.blake2b(sig.encode("utf-8", "replace"), digest_size=8).digest()
        for g in self.gens:
            seen = g.get(key)
            if seen is not None:
                return self._drop("repeat_slot") if seen != slot else None
        self.gens[-1][key] = slot
        return None

    def _drop(self, kind: str) -> str:
        self.counts[kind] += 1
        return kind
