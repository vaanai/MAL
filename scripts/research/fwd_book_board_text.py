"""fwd_book_board_text: the verbatim disclosure texts of the forward open-sim books c1nf-fwd-v1, osb-u4, osb-u2 and the U1 pair (osb-u1, osb-u1-lowrep).
Each constant is copied exactly from DEC/DEC-035-live-learning-lane.md (Amendment 9 book-list records of 2026-11-16, the pin records of 2026-11-16 / 2026-11-17 and Erratum 1 of 2026-11-17);
tests/test_fwd_book_board.py checks every one of them as a substring of that file, so a drift fails a test. Placeholders in angle brackets are filled by fwd_book_board.py.
Paper only; no sentence here says any book makes money. No book has cleared the promotion gate."""

C1_SEC4 = (
    "C1-NF's only trade-level figures are IN-SAMPLE: +8.454% flat / +7.479% pressure per trade over 422 trades, 17/"
    '21 days positive, date-cluster CI90 lo +5.681% (JUDGE-4), on September exploration blocks read by about 40 hun'
    'ts. The top-holder cap h_top1 <= 0.5 was chosen AFTER the read from about 4 splits, on top of a best-of-about-'
    '24 cell from a 144-cell scan (DEC-025 Context). Those figures assumed a 1.3 s entry; live lands about 3-4 s af'
    'ter the decision. In October the shadow kept about 1 trade a day (the cap kept 0.9% vs 10.4% in September). EX'
    'P-025 Look 1 and Look 2 both ended NOT_DECIDABLE and were never read: there is no out-of-sample C1-NF result. '
    'The model predates the 2026-10-13 and 2026-11-15 program redeploys. Synthetic-migration pools are kept in and '
    "were absent from September. This book's figures are forward paper from S at 4.085 s entry; treat any good numb"
    'er as shrunk (best of <n_books_watched> rows).'
)

C1_LATE_PRINT = (
    'Market-activity features count only prints of pools inside their engine life (graduation + 25 h; a pool with u'
    'nknown graduation, first print + 6 h), as the live C1-NF shadow does (engine.expire on the stream clock every '
    '600 s). Later prints of older pools are ignored, as live. Whether the training features counted them was not c'
    'hecked; the effect on predictions is unknown in size and direction.'
)

C1_DECODE = (
    'Decode equality: the model and features were built on pre-2026-10-13 and pre-2026-11-15 program semantics; the'
    ' A3 stage 2 of the 2026-11-15 re-pin FAILED (Erratum 1, 2026-11-16); decode equality of post-2026-11-15 rows f'
    "or C1-NF's fields is not shown."
)

C1_WARMUP = (
    'warm-up W = [2026-11-03T12:00Z, S), decisions off, no outcome code path; creator and wallet history start at 2'
    '026-11-03T12:00Z, not at tape start as in training; pools first seen in warm-up: <n>'
)

C1_WARMUP_TAIL = (
    "the wallet ledger's history is at most 14 days, shorter than training, so stage-2 wallet features may score lo"
    'wer than in training (drift, direction unknown).'
)

C1_FEATURES = (
    "Features: by exact name none of r4-b's 63 features is among C1-NF's 107 (`ARTIFACTS/c1nf_model/feature_overlap"
    '_r4b.txt`); semantic overlap was not checked, so "shares features with r4-b" is not ruled out.'
)

C1_OVERLAP = (
    'Overlap with the r4-b open preview, the osb books and floor-lane-1: the same post-graduation PumpSwap pools ma'
    'y appear in several universes.'
)

U4_SEC4 = (
    'U4 has no measured number of any kind before this book: the rule and every constant were fixed a priori in QP-'
    'RULING-1116-u4-u2-p81-forward-specs before any of its data was read. The nearest library priors are dead or ex'
    "plored-no-survivor (A45/E13 overhang vetoes, dead). This book's figures are forward paper from S at 4.085 s en"
    'try with honest costs; they are not a gate read and not out-of-sample evidence of an edge. Treat any good numb'
    'er as shrunk (best of <n_books_watched> rows). Decode equality of post-2026-11-15 rows for the fields used is '
    'not shown.'
)

U4_GAP = (
    'Strict feed-gap exclusion: any data_loss gap between decision and exit removes the trade (engine default; gap-'
    'in-hold mode cannot be combined with the 19-slot latency). Exclusion odds rise with hold length, so early exit'
    's (stops, takes, share-fall) are over-represented among priced trades; excluded <n> of <N> entries (<timed>/<r'
    'equested>).'
)

U4_WARMUP = (
    'warm-up W = [2026-11-03T12:00Z, S), decisions off, no outcome code path; state (balances / prices / registries'
    ') starts at 2026-11-03T12:00Z; pools or mints with history before 2026-11-03T12:00Z are excluded and counted: '
    '<n>'
)

U2_SEC4 = (
    'U2 has no measured number of any kind before this book: the rule and every constant were fixed a priori in QP-'
    'RULING-1116-u4-u2-p81-forward-specs before any of its data was read. The nearest library priors are dead or ex'
    "plored-no-survivor (survivors-1, day-sigma-1, explored-no-survivor). This book's figures are forward paper fro"
    'm S at 4.085 s entry with honest costs; they are not a gate read and not out-of-sample evidence of an edge. Tr'
    'eat any good number as shrunk (best of <n_books_watched> rows). Decode equality of post-2026-11-15 rows for th'
    'e fields used is not shown.'
)

U2_GAP = (
    'Strict feed-gap exclusion: any data_loss gap between decision and exit removes the trade (engine default; gap-'
    'in-hold mode cannot be combined with the 19-slot latency). Exclusion odds rise with hold length, so early exit'
    's (stops, takes, share-fall) are over-represented among priced trades; excluded <n> of <N> entries (<timed>/<r'
    'equested>).'
)

U2_WARMUP = (
    'warm-up W = [2026-11-03T12:00Z, S), decisions off, no outcome code path; state (balances / prices / registries'
    ') starts at 2026-11-03T12:00Z; pools or mints with history before 2026-11-03T12:00Z are excluded and counted: '
    '<n>'
)

U4_NS = (
    "NS is a tape proxy: transfers are invisible, so 'never sold' includes tokens moved to other wallets."
)

BOUNDED = (
    'U4/U2 bounded state (PR #1432): decisions, counts and mark records are md5-equal to the previous code path (bo'
    'unded=False) on synthetic fixtures in block-time order. On the real input this rests on the arena tip writer/r'
    'eader contract at 916b3275 (rows released in slot order; out-of-order refetches become gaps). It was not measu'
    "red on the tape. If a pool's prints were ever out of block-time order by tens of minutes, U2's price-at-or-bef"
    'ore lookups could differ from the previous code (QP synthetic probes: 0 of 4,200 lookups with 5% of prints up '
    'to 30 min late; 39 of 4,200 with 5% up to 2 h late). U4 matched in all 408 hours with rows up to 2 h late. A c'
    'reate row repeated more than 8 days after the first, with different fields, would keep that mint up to 8 days '
    'longer than the previous code (a true repeat behaves as before).'
)

E1_CODE_SHA = (
    "the runner's `code_sha256()` hashes a fixed file list that does not include `u4_u2_deciders.py`, so this value"
    ' equals the C1-NF book\'s at 6faf1d4b and the runner\'s "simulator version changed" refusal does not cover the d'
    'ecider file.'
)


# --- U1 (osb-u1, osb-u1-lowrep): DEC-035 Amendment 9 book-list record of 2026-11-17, disclosures (1), (11), (12). The other U1 disclosures are imported from the runner modules by fwd_book_board.py, never retyped.

U1_SEC9 = (
    'U1 has no measured number of any kind before this book: the rule and every constant were fixed a '
    'priori in QP-RULING-1116-u1-forward-spec before any of its data was read. The nearest library priors'
    ' are dead (lib-a-A22 launch count; dev-events-7 counts only). Clusters are first-funder links, one '
    "hop, with hub funders removed by a fixed rule; exchanges and shared wallets below the rule's "
    "thresholds can still merge unrelated creators. This book's figures are forward paper from S at 4.085"
    ' s entry with honest costs; they are not a gate read and not out-of-sample evidence of an edge. '
    'Treat any good number as shrunk (best of <n_books_watched> rows). Decode equality of post-2026-11-15'
    ' rows for the fields used is not shown. Helius credits used to date: <n>.'
)

U1_PILOT_D1 = (
    'The U1 funder pilot ran twice. The first run (#2947, 9d117de7) failed criterion (c): 3 of 10 '
    'launchers were fetch_error from RPC code -32015, because the fetcher asked for transaction version 0'
    ' and those transactions were version 1. QP-RULING-1117-u1-pilot changed only that request parameter,'
    ' from 0 to 1. The funder definition did not change. The re-run on the same 10 launchers is the pilot'
    ' of record, and its result was known in advance from counts-only diagnostics (#2948-#2950, 83 '
    'credits).'
)

U1_PILOT_D2 = (
    'Funder links are System Program transfers, transferWithSeed and createAccount only. Nonce '
    'withdrawals, token-account closes and program transfers do not count, so launchers funded that way '
    'have own history only. In the 10-launcher pilot, 3 of 10 were no_sol_inflow.'
)

U1_DISC13 = (
    'U1R ADD1 (3) placebo-lag disclosure is replaced by (5) above for this book (QP-RULING-1117-u1-start Q7).'
)


# QP-RULING-1118-u2-v2 section 4, verbatim (a QP ruling, not DEC text: the DEC-verbatim test skips these two)
QP1118_U2_V1_DEFECT = (
    'osb-u2 (U2 v1) has a simulator defect found from code on 2026-11-18 (QP-RULING-1118-u2-v2): its pricing engine was not fed warm-up hours and dropped pools quiet for more than 20,000 slots (about 2.2 h), while U2 picks pools whose last print can be up to 6 h old. Picks the engine had not seen were refused (no_prints), and at the first mark the slot clock was not ready (clock_not_ready). As of <instant>: <r> of <N> forward entries refused (no_prints <a>, clock_not_ready <b>); placebo <r_p> of <N_p>. Its priced trades are a subset biased towards recently active pools. osb-u2 cannot license U2 by itself; its look is reported beside osb-v2-u2 and never pooled with it.'
)

QP1118_V2_U2 = (
    "osb-v2-u2 (U2 v2) uses the same decider and constants as osb-u2 (decision md5 equal on fixtures) and differs only in pricing coverage: its engine is fed the 7 h before S2 and never prunes pools (trim_history only). It is the second book of the same rule, started after the first book's early board was visible; treat any good number as shrunk (best of <n_books_watched> rows, osb-u2's 9 included)."
)
