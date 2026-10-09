"""Per-socket link state for the public logsSubscribe feed: when a socket was up (subscribed), when it was down, and when it was SILENT.

Pure python, no websockets import, so it can be tested anywhere. observe.trade_source calls mark_up() once the subscribe payloads are sent,
mark_down() when a connection ends (before the reconnect backoff) and note_notice() for every notification a socket delivers. Nothing in the
tape recorder reads this; the H5 shadow uses it to tell "every socket was down at a common instant" from "one socket blipped while another was
up".

Connected is not delivering. A half-open socket (a dead TCP path that the websocket only notices after ping_interval + ping_timeout, or after
the 30 s idle timeout) stays "up" for tens of seconds while it delivers nothing. So a socket that has been silent for more than silent_ms is
treated as down FROM ITS LAST NOTIFICATION (the delivery clock starts at the subscribe), and up again on its next notification. On the PumpSwap
firehose (hundreds of notices a second) a silent gap this long on a live socket does not happen; if every socket goes quiet at once that is a
feed outage and is reported as one.
"""

from __future__ import annotations

import bisect
import collections
import time
from typing import Any, Callable

SILENT_MS_DEFAULT = 10_000
# Edges of the inter-notification gap histogram (ms): gaps below 250, 250..500, ..., 5..10 s, 10 s and more. Lets the silence threshold be tuned
# from measured live gaps instead of assumption (the shadow's hb record carries it per socket).
GAP_EDGES_MS = (250, 500, 1_000, 2_000, 3_000, 5_000, 10_000)


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


class LinkState:
    """up / down / silent history of one socket.

    A socket starts DOWN (since construction). The first mark_up() ends that initial interval without recording it (nothing was ever
    expected from it). Later drops are recorded as closed intervals (down_start_ms, up_again_ms); a socket that is down right now is
    reported by snapshot() with its open `down_since_ms`. A socket that has never come up (for example rejected on every handshake) stays
    down since construction. Silence: a gap of more than silent_ms between two notifications of an up socket (the first measured from the
    subscribe) is recorded as a closed silent interval (last_notice_ms, next_notice_ms); an ongoing one is derived from `last_notice_ms`.
    """

    def __init__(self, clock: Callable[[], int] = _now_ms, maxlen: int = 256, silent_ms: int = SILENT_MS_DEFAULT) -> None:
        self._clock = clock
        self.silent_ms = silent_ms
        self.up = False
        self.ever_up = False
        self.down_since_ms: int | None = clock()
        self.down_intervals: collections.deque[tuple[int, int]] = collections.deque(maxlen=maxlen)
        self.last_notice_ms: int | None = None
        self.silent_intervals: collections.deque[tuple[int, int]] = collections.deque(maxlen=maxlen)
        self.gap_hist = [0] * (len(GAP_EDGES_MS) + 1)
        self.max_gap_ms = 0

    def mark_up(self, t_ms: int | None = None) -> None:
        t = self._clock() if t_ms is None else t_ms
        if self.up:
            return
        if self.ever_up and self.down_since_ms is not None:
            self.down_intervals.append((self.down_since_ms, max(t, self.down_since_ms)))
        self.up = True
        self.ever_up = True
        self.down_since_ms = None
        self.last_notice_ms = t  # the delivery clock starts at the subscribe

    def mark_down(self, t_ms: int | None = None) -> None:
        t = self._clock() if t_ms is None else t_ms
        if not self.up:
            return
        if self.last_notice_ms is not None and t - self.last_notice_ms > self.silent_ms:
            # the socket was silent up to this drop (a half-open path closed by the ping timeout or the idle timeout): the silence outlives the drop
            self.silent_intervals.append((self.last_notice_ms, t))
        self.up = False
        self.down_since_ms = t

    def note_notice(self, t_ms: int | None = None) -> None:
        """A notification arrived on this socket: it is delivering. Closes a silent interval if it had been quiet for more than silent_ms."""
        t = self._clock() if t_ms is None else t_ms
        if self.up and self.last_notice_ms is not None:
            gap = t - self.last_notice_ms
            if gap > self.silent_ms:
                self.silent_intervals.append((self.last_notice_ms, t))
            if gap > 0:
                self.gap_hist[bisect.bisect_right(GAP_EDGES_MS, gap)] += 1
                if gap > self.max_gap_ms:
                    self.max_gap_ms = gap
        self.last_notice_ms = t

    def snapshot(self) -> dict[str, Any]:
        return {
            "up": self.up,
            "down_since_ms": self.down_since_ms,
            "intervals": [list(i) for i in self.down_intervals],
            "last_notice_ms": self.last_notice_ms,
            "silent_ms": self.silent_ms,
            "silent_intervals": [list(i) for i in self.silent_intervals],
            "max_gap_ms": self.max_gap_ms,
            "gap_hist": list(self.gap_hist),
        }
