"""Per-socket link state for the public logsSubscribe feed: when a socket was up (subscribed) and when it was down.

Pure python, no websockets import, so it can be tested anywhere. observe.trade_source calls mark_up() once the subscribe payloads are sent and
mark_down() when a connection ends (before the reconnect backoff). Nothing in the tape recorder reads this; the H5 shadow uses it to tell
"every socket was down at a common instant" from "one socket blipped while another was up".
"""

from __future__ import annotations

import collections
import time
from typing import Any, Callable


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


class LinkState:
    """up / down history of one socket.

    A socket starts DOWN (since construction). The first mark_up() ends that initial interval without recording it (nothing was ever
    expected from it). Later drops are recorded as closed intervals (down_start_ms, up_again_ms); a socket that is down right now is
    reported by snapshot() with its open `down_since_ms`. A socket that has never come up (for example rejected on every handshake) stays
    down since construction.
    """

    def __init__(self, clock: Callable[[], int] = _now_ms, maxlen: int = 256) -> None:
        self._clock = clock
        self.up = False
        self.ever_up = False
        self.down_since_ms: int | None = clock()
        self.down_intervals: collections.deque[tuple[int, int]] = collections.deque(maxlen=maxlen)

    def mark_up(self, t_ms: int | None = None) -> None:
        t = self._clock() if t_ms is None else t_ms
        if self.up:
            return
        if self.ever_up and self.down_since_ms is not None:
            self.down_intervals.append((self.down_since_ms, max(t, self.down_since_ms)))
        self.up = True
        self.ever_up = True
        self.down_since_ms = None

    def mark_down(self, t_ms: int | None = None) -> None:
        t = self._clock() if t_ms is None else t_ms
        if not self.up:
            return
        self.up = False
        self.down_since_ms = t

    def snapshot(self) -> dict[str, Any]:
        return {"up": self.up, "down_since_ms": self.down_since_ms, "intervals": [list(i) for i in self.down_intervals]}
