"""C1 v2 constants (restart after the 10-08 disk incident). Every number here is pinned before any CONFIRMATION read."""
import numpy as np, math, datetime as dt
O = '/data/mal/hunt-1008/c1-cascade-postgrad'
TAPE = '/data/mal/audit-1008/tape'
SH = '/data/mal/hunt-shared'
TMP = '/data/mal/hunt-1008/tmp/c1-cascade-postgrad'
# tools/paper_curve_math.PUMPSWAP_SOL_FEE_TIERS (mcap SOL inclusive lower bound, ppm) == cap_pick_score.fee_ppm
TIERS = ((0, 12_500), (420, 12_000), (1470, 11_500), (2460, 11_000), (3440, 10_500), (4420, 10_000), (9820, 9_500), (14740, 9_000), (19650, 8_500),
         (24560, 8_000), (29470, 7_500), (34380, 7_000), (39300, 6_500), (44210, 6_000), (49120, 5_500), (54030, 5_250), (58940, 5_000), (63860, 4_750),
         (68770, 4_500), (73681, 4_250), (78590, 4_000), (83500, 3_750), (88400, 3_500), (93330, 3_250), (98240, 3_000))
TH = np.array([t for t, _ in TIERS], float); TPP = np.array([p for _, p in TIERS], float)


def fee_frac(Q, B):
    """Q lamports INCLUDING V, B raw base units -> pool fee fraction (cap_pick_score.fee_ppm / 1e6)."""
    mcap = np.asarray(Q, float) / (np.asarray(B, float) * 1000.0) * 1e9
    idx = np.searchsorted(TH, mcap + 1e-9, side='right') - 1
    return TPP[np.clip(idx, 0, len(TPP) - 1)] / 1e6


def ep(s):
    return int(dt.datetime.strptime(s, '%Y-%m-%dT%H').replace(tzinfo=dt.timezone.utc).timestamp())


# tape segments [start, end) epoch s; S1 = DISCOVERY, S2 + S3 = CONFIRMATION
SEGS = [(ep('2026-08-14T12'), ep('2026-08-28T12')), (ep('2026-09-03T12'), ep('2026-09-15T12')), (ep('2026-09-18T23'), ep('2026-09-25T07'))]


def seg_of(t):
    for i, (a, b) in enumerate(SEGS):
        if a <= t < b:
            return i
    return -1


def day_of(t):
    return dt.datetime.fromtimestamp(int(t), dt.timezone.utc).strftime('%Y-%m-%d')


SIZE = 250_000_000; SEND_FEE = 55_000
LAT_P = 1.3; LAT_B = 1.9; EXIT_LAG_S = 0.55
GUARD = 1.15            # buy reverts (one send fee) if landing exec price (size incl. pool fee / tokens out) > 1.15 x decision-time spot
GRID_START = 600; GRID_END = 24 * 3600   # decision grid: whole UTC minutes from grad + 10 min to grad + 24 h
MAX_HOLD = 3600; MARGIN = 300
G_FEE = 0.0125
# exits: (name, kind, tp, sl, trail, cap s). tp/sl/trail on post-trade spot (with our trade applied) vs post-buy mark (cap_pick convention)
EXITS = [('E1', 'tpsl', 0.30, 0.20, None, 600), ('E2', 'tpsl', 0.50, 0.30, None, 1800), ('E3', 'time', None, None, None, 300),
         ('E4', 'tpsl', 1.00, 0.40, None, 3600), ('E5', 'tpsl', 0.20, 0.15, None, 300), ('E6', 'trail', None, None, 0.25, 1800),
         ('E7', 'time', None, None, None, 900), ('E8', 'trail', None, None, 0.15, 900)]
