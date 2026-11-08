"""forge common: constants shared by events.py and evaluate.py. PAPER research only. No key, no network.

Pricing conventions are the arena's (tools/arena_stack.py, tools/arena_pool_table.py): PumpSwap reserves in the row are PRE-trade and
V-corrected (Q = quote_reserve + virtual_quote_reserves, V in [F.V_LO, V_BAND_MAX], WSOL pools only); bonding-curve reserves in the row are POST-trade
(virtual, 30 SOL start). fee_ppm tiers and V_LO are imported from arena_stack, not retyped.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from tools import arena_stack as F

WSOL = "So11111111111111111111111111111111111111112"
V_LO = F.V_LO
V_BAND_MAX = 30_000_000_000  # tools/arena_paper.V_BAND_MAX
CURVE_FEE = F.FINAL_FEE  # 0.0125, the arena's curve/derived fee
CURVE_V0 = (30_000_000_000.0, 1_073_000_000_000_000.0)  # initial virtual (quote, base) of a bonding curve
CURVE_REAL_BASE0 = 793_100_000_000_000.0
SEND_LAMPORTS = 160_000
FILL_DELAY_MS = 2000
FILL_MAX_WAIT_MS = 600_000  # no print within 10 min after the delay -> no fill
THIN_MS = 30_000
GAP_EXCL_MS = 60_000
T0_MS = 1_790_000_000_000
KEY_SHIFT = 38  # composite sort key = rank << 38 | (t_ms - T0_MS)
SCHEMA = 2  # DONE-marker version; a stage whose marker has another version is rebuilt (overwritten) by `build`
MIN_REAL_QUOTE = 2_000_000_000  # hard floor: no entry where the pool's real quote reserve at the fill is below 2 SOL (live cannot fill there)
DEAD_REAL_QUOTE = 10_000_000  # PumpSwap pool with real quote below 0.01 SOL after a print is marked dead (price 0)

# forward-path grid, seconds after the fill: 5 s to 30 m, 1 m to 6 h, 5 m to 24 h
GRID_S = np.concatenate([np.arange(5, 1801, 5), np.arange(1860, 21601, 60), np.arange(21900, 86401, 300)]).astype(np.int64)
NG = len(GRID_S)  # 906
C_DEAD, C_MIGRATED, C_GAP, C_CENS = 0, 65533, 65534, 65535
LN_OFF, LN_SCALE = 12.0, 2500.0


def encode_ratio(r: np.ndarray) -> np.ndarray:
    """ratio (spot / entry fill spot) -> uint16 code; 0 = dead (price 0)."""
    r = np.asarray(r, float)
    with np.errstate(all="ignore"):
        c = np.clip(np.rint((np.log(np.where(r > 0, r, 1.0)) + LN_OFF) * LN_SCALE), 1, 65000)
    return np.where(r > 0, c, 0).astype(np.uint16)


def _lut() -> np.ndarray:
    k = np.arange(65536, dtype=np.float64)
    out = np.exp(k / LN_SCALE - LN_OFF)
    out[C_DEAD] = 0.0
    out[C_MIGRATED:] = np.nan
    return out.astype(np.float32)


DECODE = _lut()
RES_OFF, RES_SCALE = 8.0, 2500.0


def encode_res(x) -> np.ndarray:
    """reserve in lamports -> uint16 code (0 = none/zero); resolution 0.04%, range e^8 .. e^34 lamports"""
    x = np.asarray(x, float)
    with np.errstate(all="ignore"):
        c = np.clip(np.rint((np.log(np.where(x > 1, x, 1.0)) - RES_OFF) * RES_SCALE), 1, 65000)
    return np.where(x > 1, c, 0).astype(np.uint16)


def _lut_res() -> np.ndarray:
    out = np.exp(np.arange(65536, dtype=np.float64) / RES_SCALE + RES_OFF)
    out[0] = 0.0
    return out


DECODE_RES = _lut_res()

FEATURES = (
    "venue", "age_s", "mig_age_s", "liq_sol", "liq_real_sol", "mcap_sol",
    "ret_30s", "ret_1m", "ret_5m", "ret_15m", "ret_1h", "ret_6h",
    "dd_high", "rise_low",
    *[f"{k}_{w}" for w in ("1m", "5m", "1h") for k in ("n", "uniq", "buy_share", "buy_sol", "sell_sol", "max_sell", "max_buy", "net_flow")],
    "ev_is_buy", "ev_sol", "since_last_s", "curve_progress", "utc_hour",
)
FIDX = {f: i for i, f in enumerate(FEATURES)}
NF = len(FEATURES)

SPLITS = {"train": ("2026-10-19T08", "2026-11-02T23"), "val": ("2026-11-03T00", "2026-11-07T23")}
DATA_END = "2026-11-08T00"  # hours from here on are live-period data and never read


def hour_ms(h: str) -> int:
    return int(datetime.strptime(h, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)


def hour_str(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def hour_range(a: str, b: str) -> list[str]:
    """inclusive hour strings a..b"""
    out, t, e = [], hour_ms(a), hour_ms(b)
    while t <= e:
        out.append(hour_str(t))
        t += 3_600_000
    return out


def split_of_hour(h: str) -> str | None:
    for s, (a, b) in SPLITS.items():
        if a <= h <= b:
            return s
    return None


def split_censor_ms(h: str) -> int:
    """Forward paths of an event are censored at the end of its split, so TRAIN fitness never sees VAL prices and VAL never sees 11-08+."""
    s = split_of_hour(h)
    if s is None:  # outside the named splits (smoke / tests): censor at DATA_END
        return hour_ms(DATA_END)
    return hour_ms(SPLITS[s][1]) + 3_600_000


def fee_frac(q, b, venue):
    """fee fraction at state (Q incl V, B): PumpSwap tier, curve 1.25%."""
    q = np.asarray(q, float)
    b = np.asarray(b, float)
    with np.errstate(all="ignore"):
        ps = F.fee_ppm(q, np.where(b > 0, b, 1.0)) / 1e6
    return np.where(np.asarray(venue) == 1, ps, CURVE_FEE)
