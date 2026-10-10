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

Two clocks. `last_notice_ms` is the socket's OWN quiet clock: the subscribe time until the first notification, then the last notification. It
measures how long this socket has been quiet. `last_delivery_ms` is only ever set by a notification (never by a subscribe): a peer that merely
resubscribed has not delivered anything, so only this clock may be used to say "some other socket delivered after this one's last notice".
"""

from __future__ import annotations

import bisect
import collections
import time
from typing import Any, Callable

SILENT_MS_DEFAULT = 10_000
# A socket quiet for more than this long that is then closed by the server (mark_down) never caught up, so the quiet stretch is recorded as a
# silent interval even though it is shorter than SILENT_MS_DEFAULT. The shadow's relative-silence rule (tools.h5_shadow.REL_SILENT_MS) uses the
# same value: a quiet this long is "silent" there only while a peer delivered after this socket's last notice.
REL_SILENT_MS_DEFAULT = 3_000
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
    A quiet of more than rel_silent_ms that ends in a drop (the server closed a socket that had stopped delivering) is recorded too, as
    (last_notice_ms, drop time): the stalled notices of that connection are gone, the socket is not merely behind.

    Histograms (edges GAP_EDGES_MS). `gap_hist` / `max_gap_ms`: gaps between consecutive notifications on a connection that was already
    delivering. The first gap after a subscribe is the subscribe-to-first-notice latency (a handshake, not a pause of a live socket) and goes to
    `sub_latency_hist` / `max_sub_latency_ms` instead. `drop_silences` counts quiets of more than rel_silent_ms that ended in a drop (these never
    reach gap_hist: there is no next notification).
    """

    def __init__(self, clock: Callable[[], int] = _now_ms, maxlen: int = 256, silent_ms: int = SILENT_MS_DEFAULT,
                 rel_silent_ms: int = REL_SILENT_MS_DEFAULT) -> None:
        self._clock = clock
        self.silent_ms = silent_ms
        self.rel_silent_ms = rel_silent_ms
        self.up = False
        self.ever_up = False
        self.down_since_ms: int | None = clock()
        self.down_intervals: collections.deque[tuple[int, int]] = collections.deque(maxlen=maxlen)
        self.last_notice_ms: int | None = None  # this socket's own quiet clock: the subscribe time, then each notification
        self.last_delivery_ms: int | None = None  # the last NOTIFICATION (note_notice only); a subscribe never sets it
        self.silent_intervals: collections.deque[tuple[int, int]] = collections.deque(maxlen=maxlen)
        self.gap_hist = [0] * (len(GAP_EDGES_MS) + 1)
        self.max_gap_ms = 0
        self.sub_latency_hist = [0] * (len(GAP_EDGES_MS) + 1)
        self.max_sub_latency_ms = 0
        self.drop_silences = 0
        self._await_first = False  # True from a subscribe until its first notification

    def mark_up(self, t_ms: int | None = None) -> None:
        t = self._clock() if t_ms is None else t_ms
        if self.up:
            return
        if self.ever_up and self.down_since_ms is not None:
            self.down_intervals.append((self.down_since_ms, max(t, self.down_since_ms)))
        self.up = True
        self.ever_up = True
        self.down_since_ms = None
        self.last_notice_ms = t  # the quiet clock starts at the subscribe (last_delivery_ms is NOT touched: nothing was delivered)
        self._await_first = True

    def mark_down(self, t_ms: int | None = None) -> None:
        t = self._clock() if t_ms is None else t_ms
        if not self.up:
            return
        quiet_ms = min(self.rel_silent_ms, self.silent_ms) if self.rel_silent_ms else self.silent_ms
        if self.last_notice_ms is not None and t - self.last_notice_ms > quiet_ms:
            # the socket was quiet up to this drop (a half-open path closed by the ping timeout or the idle timeout, or a stalled path closed by the
            # server): nothing it was owed arrives any more, and the quiet outlives the drop. A 3 s quiet is enough: the intersection with the other
            # sockets still decides whether it is a common outage, and a lone quiet-then-drop socket cannot flag by itself.
            self.silent_intervals.append((self.last_notice_ms, t))
            self.drop_silences += 1
        self.up = False
        self._await_first = False
        self.down_since_ms = t

    def note_notice(self, t_ms: int | None = None) -> None:
        """A notification arrived on this socket: it is delivering. Closes a silent interval if it had been quiet for more than silent_ms."""
        t = self._clock() if t_ms is None else t_ms
        if self.up and self.last_notice_ms is not None:
            gap = t - self.last_notice_ms
            if gap > self.silent_ms:
                self.silent_intervals.append((self.last_notice_ms, t))
            first, self._await_first = self._await_first, False
            if gap > 0:
                if first:  # subscribe-to-first-notice latency, not a pause of a delivering socket
                    self.sub_latency_hist[bisect.bisect_right(GAP_EDGES_MS, gap)] += 1
                    if gap > self.max_sub_latency_ms:
                        self.max_sub_latency_ms = gap
                else:
                    self.gap_hist[bisect.bisect_right(GAP_EDGES_MS, gap)] += 1
                    if gap > self.max_gap_ms:
                        self.max_gap_ms = gap
        self.last_notice_ms = t
        self.last_delivery_ms = t

    def snapshot(self) -> dict[str, Any]:
        return {
            "up": self.up,
            "down_since_ms": self.down_since_ms,
            "intervals": [list(i) for i in self.down_intervals],
            "last_notice_ms": self.last_notice_ms,
            "last_delivery_ms": self.last_delivery_ms,
            "silent_ms": self.silent_ms,
            "silent_intervals": [list(i) for i in self.silent_intervals],
            "max_gap_ms": self.max_gap_ms,
            "gap_hist": list(self.gap_hist),
            "sub_latency_hist": list(self.sub_latency_hist),
            "max_sub_latency_ms": self.max_sub_latency_ms,
            "drop_silences": self.drop_silences,
        }
