"""r4-b frozen pins (FROZEN-r4-b.md, 2026-10-08; r4-b walk-2 read tool PLAN, builder task 1).

Constants only, plus three tiny pure helpers (k_slots, exit_lag_slots, md5/sha256 of a file). No data is read at import.
The artifacts live in models/r4b/ because /data/mal/hunt-1008 is .nobackup. This file is the single place where a pin is written down;
tools/r4b_score.py and tools/r4b_book.py import from it. Changing a value here changes the registered read: do not edit without a new freeze.
Numpy-free on purpose (importable from both lab venvs).
"""
from __future__ import annotations

import hashlib
import math
import os

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "r4b")

# --- artifacts (FROZEN section 2 table; manifest.json of the freeze carries the same hashes) -----------------------------------------
SEEDS = (1, 2, 3, 4, 5)
BOOSTER_FILES = tuple(f"r4-b_seed{s}.txt" for s in SEEDS)
ARTIFACT_MD5 = {
    "r4-b_seed1.txt": "2ad4137024aa1d765224c27e5d8b2310",
    "r4-b_seed2.txt": "eb26e2ea1c1b45cecfa4133a735a5002",
    "r4-b_seed3.txt": "8591cc0b4abe97a46f85107bb173fe97",
    "r4-b_seed4.txt": "45d6b1c48c10e0bed550eb338f0f32c9",
    "r4-b_seed5.txt": "4938a1006addb81cc1586320d1a9abbe",
    "features.txt": "18531b0072d79a6127b5a4aea082d2c7",
    "equiv_fixture.npz": "b59137e921895ac4ceef3c415da94b71",
}
ARTIFACT_SHA256 = {
    "r4-b_seed1.txt": "d726c0ab3f294bfe0c059360893a6db959863e8fb031d13cd7163363e7bc0335",
    "r4-b_seed2.txt": "c12831c8a9e8ea1a00ebab6e15f0e51e4b6372913dbb0507c8516b0711d31875",
    "r4-b_seed3.txt": "fd93f682038f248d9cc60ce8972d40ffdd6898a4a806fe9163c9bc23dcb1a2c9",
    "r4-b_seed4.txt": "f2555f6190e34c085431014858a6ac430294127e373614bd6d0fa598bda9fb53",
    "r4-b_seed5.txt": "2451d659cecd43f6a142f4e5efd018ed48e129ff827c0666956625a611f4c469",
    "features.txt": "eb0f980d069b05a38d3e5a76d826274a3faaba312c012c99afd8e3dc825e576a",
    "equiv_fixture.npz": "42704b0c69b9aa8660d1d36af5738a9965efb944f9b3f92d2633a0f362dae285",
}
LIGHTGBM_VERSION = "4.7.0"
# Source pins of the freeze (not shipped in the repo): harness/specs/r4-b.py and 02_build.py after the order fix.
SPEC_MD5 = "6121a64775870521effbf7c34f4d00b8"
SPEC_SHA256 = "e7761004e588911264d149555ccf2cbe04cb2f043ccb0a9f4566f090ba0f7c08"
BUILD_PY_MD5 = "535ac7062ab0eb15c2d08bceb9175710"

# --- features (features.txt order; X columns follow this order; 63 names) --------------------------------------------------------------
FEATURES = (
    'f_age_s', 'f_t_since_create', 'f_r_seed', 'f_ret_60', 'f_ret_300', 'f_ret_900', 'f_ret_3600', 'f_ret_14400', 'f_dd_ath',
    'f_rebound_atl', 'f_t_since_ath', 'f_ath_seed', 'f_rv_300', 'f_rv_900', 'f_rv_3600', 'f_np_300', 'f_np_900', 'f_np_3600',
    'f_np_all', 'f_bv_300', 'f_sv_300', 'f_bv_900', 'f_sv_900', 'f_bv_3600', 'f_sv_3600', 'f_nf_900_liq', 'f_nf_3600_liq', 'f_bv_all',
    'f_sv_all', 'f_lnet_900', 'f_lnet_3600', 'f_nlarge_3600', 'f_ub_900', 'f_us_900', 'f_ub_3600', 'f_us_3600', 'f_uniq_all',
    'f_buyfrac_900', 'f_avgsz_900', 'f_liq_sol', 'f_fee_tier', 'f_t_since_last', 'f_cre_sell_post', 'f_cre_buy_post', 'f_cre_known',
    'f_hour_sin', 'f_hour_cos', 'f_m_grads_1h', 'f_m_grads_10m', 'f_m_vps_10m', 'f_m_nps_10m', 'f_m_vbd_10m', 'f_m_ncr_10m',
    'f_m_ncr_60m', 'f_m_vps_60m', 'f_c_bond_dur', 'f_c_nbond', 'f_c_nbond_tr', 'f_c_bond_buy', 'f_c_bond_sell', 'f_c_cre_bond_sell',
    'f_c_pool_delay', 'f_c_v',
)
N_FEATURES = 63
assert len(FEATURES) == N_FEATURES == len(set(FEATURES))

# --- book -------------------------------------------------------------------------------------------------------------------------------
K = 10                          # hold the top-K by score at every UTC hour mark; no hysteresis, no minimum score
REBALANCE_S = 3600
STAKE_SOL = 0.25
STAKE_LAMPORTS = 250_000_000    # 0.25 SOL per position, 2.5 SOL at risk

# --- timing -----------------------------------------------------------------------------------------------------------------------------
LATENCIES_S = (1.3, 1.9)        # both gate
EXIT_LAG_S = 0.55
SLOT_TOL = 1e-9
MIN_EXIT_LAG_SLOTS = 2          # FROZEN section 4: "at least 2 slots"; ceil(0.55 / 0.2 - 1e-9) = 3 at 200 ms
# Entry k = lat / slot_s -> whole slots. FROZEN and the harness use "round" (numpy/Python round, half to even: 6.5 -> 6).
# At 200 ms slots 1.3 s is 6.5 slots, where "round" gives 6 and "ceil" gives 7; EXP-022 used ceil. QP ruled 2026-10-16: np.round (6.5 -> 6 at 200 ms), so "round".
# The tests cover both values. Do not switch on results.
K_ROUNDING = "round"

# --- costs (deciding stack, FROZEN section 4; the VERIFY re-score formulas) --------------------------------------------------------------
GUARD = 1.15                    # buy reverts (pays one send fee) if exec price > 1.15 x decision spot
SEND_FEE_DECIDING = 505_000     # lamports per send; a guarded buy pays one
SEND_FEE_REPORT = 55_000        # report-only leg; also the harness stack of the 10-08 development numbers
RENT_DECIDING = 2_039_280       # per filled trip; harness stack refunds it (0)
HAIRCUT_ENTRY = 0.002608        # DEC-021 measured live entry gap: tokens received / (1 + h)
HAIRCUT_SELL = 0.0016           # measured sell shortfall: proceeds x (1 - h)
FAIL_FLAT = 0.15
PRESS_SLOPE_SLOT = 0.8          # slope scale 1: 0.8 * ln(1 + ssb)
PRESS_SLOPE_SOL = 0.35          #                + 0.35 * ln(1 + nearby_lamports / 1e9)
PRESS_P_TARGET = 0.289          # intercept refit per cell so that the mean p over the cell's fills is this
NEARBY_WINDOW_S = 2.0           # nearby = buy SOL over slots [x - round(2 / slot_s), x]
BOOT_DRAWS = 1000
BOOT_SEED = 1
BOOT_PCT = 5

# PumpSwap fee tiers (market cap SOL threshold, ppm); identical to tools.paper_curve_math.PUMPSWAP_SOL_FEE_TIERS and harness hlib.TIERS
FEE_TIERS = (
    (0, 12_500), (420, 12_000), (1470, 11_500), (2460, 11_000), (3440, 10_500), (4420, 10_000), (9820, 9_500), (14740, 9_000),
    (19650, 8_500), (24560, 8_000), (29470, 7_500), (34380, 7_000), (39300, 6_500), (44210, 6_000), (49120, 5_500), (54030, 5_250),
    (58940, 5_000), (63860, 4_750), (68770, 4_500), (73681, 4_250), (78590, 4_000), (83500, 3_750), (88400, 3_500), (93330, 3_250),
    (98240, 3_000),
)
FINAL_FEE = 0.0125              # fee used to derive the state after the last print (G's constant)


def k_slots(lat_s: float, slot_s: float, rounding: str | None = None) -> int:
    """Entry latency in whole slots. "round" = Python round (half to even); "ceil" = ceil(x - 1e-9)."""
    r = K_ROUNDING if rounding is None else rounding
    x = lat_s / slot_s
    if r == "round":
        return int(round(x))
    if r == "ceil":
        return max(0, math.ceil(x - SLOT_TOL))
    raise ValueError(f"rounding must be 'round' or 'ceil', got {r!r}")


def exit_lag_slots(slot_s: float) -> int:
    """ceil(0.55 s / slot_s - 1e-9), asserted >= 2 (it is 3 at 200 ms, 2 at 400 ms)."""
    lag = math.ceil(EXIT_LAG_S / slot_s - SLOT_TOL)
    assert lag >= MIN_EXIT_LAG_SLOTS, f"exit lag {lag} slots < {MIN_EXIT_LAG_SLOTS} at {slot_s} s/slot"
    return lag


def file_md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()
