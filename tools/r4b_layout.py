#!/usr/bin/env python3
"""r4-b walk-2 walker-layout adapter (PLAN r4b-readtool section 1, builder task 4).

Reads a dataset directory in WALKER layout (`D/{trades,creates,migrations}/<kind>-YYYY-MM-DDTHH.jsonl.zst`, `checkpoint.json`, `verify.jsonl`) for the hour
range [H0, H1) and emits exactly the intermediates of l3's `01_extract.py` / `02a_prep.py`, so that the 02_build per-mint logic can be ported unchanged:

    meta.parquet         mid, mint, pool, mslot, mbt, s0, v, day, pda_match        (members only; mid = row_number over (mbt, mint) - 1)
    creators.parquet     mid, ch (DuckDB hash(creator)), cbt, cslot
    mkt_creates.parquet  mnt, n_cr
    ut/<hour>.parquet    mid, slot, txi, evi, bt, isbuy, sol, tok, q, b, th, vq, frn   ordered (mid, slot, txi, evi, frn)    bond/<hour>.parquet  mid, slot, bt, isbuy, sol, th                            (ordered like ut, by the full key)
    clock/<hour>.parquet bt, smin, smax                                           (all trade rows, both venues)
    mkt/<hour>.parquet   mnt, n_ps, v_ps, n_bd, v_bd
    stats.json           the structure numbers for the precount (no outcome, no P&L)

`prep_work()` then does what 02a does (clock_bt.npy, clock_slot.npy, mkt.npz, mintconst.pkl).

Meta (PLAN section 1)
  mslot / mbt   min slot / min block_time of the mint's `type=complete` rows.
  pool          the canonical pool = the PDA (`pumpswap_tx.canonical_pool`), computed from the mint alone.
  pda_match     the earliest `type=migration` event of the mint names pool == PDA and quote_mint == WSOL.
  s0            slot of the first print of the PDA pool in [mslot, mslot + 7300), ordered like r4b_features.forward_order: (slot, tx_index, event_index, frn), a null tx_index
                coalesced to -1 (sorts first), frn = the print's row number in its hour file (offset by the hour index, so it is total across hours).
  V null rules (event mode): a null V0 (V on the s0 print) makes the pool a NON-member (l3 trained on pools with V present and in the band); the per-day
                share of such candidate pools is reported (`v0_missing_share_by_day`, `v0_missing_days_over`) against V0_MISSING_REFUSE_SHARE, which the PRECOUNT
                enforces (the adapter does not refuse on it). A null V on any print of a MEMBER pool at or after s0 REFUSES the build (no dropping a member
                afterwards, no forward-fill).
  V0            `--v-source event`: the event V (`virtual_quote_reserves`) of that s0 print. `--v-source static_map`: the {pool: V} json (pool_v_0909.json),
                EXPLORATION REPLAY ONLY (that tape has no migration event and no event V), and then ut.vq is the map's V for every row of the mint.
  s0 window     7,300 slots is the frozen value. At 200 ms slots that is about 1,460 s, vs about 1,940-3,040 s on the training tape (265-416 ms slots): a slot-era
                difference, disclosed, value kept.
  member        not mayhem (a missing create is kept: its create-derived constants are NaN downstream), s0 exists, V0 in [17.5e9, 17.7e9], and in event mode
                pda_match. A missing V0 is NOT a member here; the count is in stats (`v0_missing`).

Refusals (`LayoutRefused`, all before any intermediate is written): an hour not sealed / not OK in verify.jsonl under `exp012_forward.hour_problems(strict)`;
a bad line (NUL / not JSON / not an object); a parsed row count that differs from verify.jsonl content[sub].rows; a missing migrations file.

ut.frn (BIGINT NOT NULL) is the 0-based line number of the print within its hour file, as l3's 01d_extract_frn.py writes it; 02_build sorts
`mid, slot, txi, evi, coalesce(frn,0)` and `r4b_features.forward_order` needs it. Trade window: besides [H0, H1) the adapter also loads the trades of the
two hours after H1 when they are sealed (as training did), so a token completing near the end keeps its first print; those hours must pass the same checks
(sealed but bad => refusal), unsealed ones are skipped; `trade_extra_hours` in stats lists those used. ut/bond/clock/mkt files are written for them too.

No sealed path is special-cased here: the read tool owns the allowlist. Needs duckdb (/data/mal/audit-1008/venv); the PDA needs solders (/data/mal/venv), or
pass `canonical_fn`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

if __package__ in (None, ""):  # `python tools/r4b_layout.py` from the repo root
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import exp012_forward as ef  # noqa: E402
from tools import tape_lines  # noqa: E402
from tools.latency_curve import _hour_file, _open_text  # noqa: E402

WSOL_MINT = "So11111111111111111111111111111111111111112"
V_LO, V_HI = 17_500_000_000, 17_700_000_000
V0_MISSING_REFUSE_SHARE = 0.02  # the precount refuses if any read day has a larger share of candidate PDA pools with a missing V0 (the EXP pins it)
WINDOW_SLOTS = 7300  # s0 = first PDA-pool print in [mslot, mslot + 7300)
S0_WINDOW_NOTE = ("the s0 window of 7,300 slots is about 1,460 s at 200 ms, vs about 1,940-3,040 s on the training tape "
                  "(265-416 ms slots; slot-era difference; frozen value kept)")
HORIZON_S = 52 * 3600 + 1800  # ut keeps prints with bt <= mbt + 52h + 1800 (l3 01_extract)
V_SOURCES = ("event", "static_map")
SUBS = ("trades", "creates", "migrations")
SCHEMA = "r4b_layout_v1"

TRADE_COLS = {
    "venue": "VARCHAR", "mint": "VARCHAR", "trader": "VARCHAR", "side": "VARCHAR", "sol_lamports": "BIGINT", "token_raw": "BIGINT",
    "quote_reserve": "BIGINT", "base_reserve": "BIGINT", "pool": "VARCHAR", "slot": "BIGINT", "signature": "VARCHAR", "event_index": "INTEGER",
    "tx_index": "INTEGER", "block_time": "BIGINT", "virtual_quote_reserves": "BIGINT",
}
# structure_only (the outcome-blind count job, tools/r4b_count.py): the trade columns that are decision-time / structure fields. NO side, sol_lamports, token_raw,
# quote_reserve, base_reserve, trader. virtual_quote_reserves is masked AT LOAD to the boolean vq_null in the hour table; the candidate table has no V; V0 is a
# targeted re-read of the s0 rows only (s0t.vq). Only s0t, m0 and m hold a V value (stats["v_columns"] reports it).
TRADE_COLS_STRUCT = {k: v for k, v in TRADE_COLS.items() if k in ("venue", "mint", "pool", "slot", "signature", "event_index", "tx_index", "block_time", "virtual_quote_reserves")}
CREATE_COLS = {"type": "VARCHAR", "mint": "VARCHAR", "creator": "VARCHAR", "is_mayhem_mode": "BOOLEAN", "slot": "BIGINT", "block_time": "BIGINT"}
MIG_COLS = {"type": "VARCHAR", "mint": "VARCHAR", "slot": "BIGINT", "block_time": "BIGINT", "tx_index": "INTEGER", "event_index": "INTEGER",
            "pool": "VARCHAR", "quote_mint": "VARCHAR"}


class LayoutRefused(Exception):
    """The adapter refuses to read. `reasons` is the full list; the message joins them."""

    def __init__(self, reasons: Sequence[str] | str):
        self.reasons = [reasons] if isinstance(reasons, str) else list(reasons)
        super().__init__("r4b_layout refused: " + "; ".join(self.reasons))


ProblemsFn = Callable[[Path, Sequence[str]], list]
CanonFn = Callable[[str], str]


def hour_range(h0: str, h1: str) -> list[str]:
    """['YYYY-MM-DDTHH', ...] for [h0, h1)."""
    try:
        a, b = ef.hour_dt(h0), ef.hour_dt(h1)
    except ValueError as exc:
        raise LayoutRefused(f"bad hour: {exc}") from exc
    if not a < b:
        raise LayoutRefused(f"empty hour range [{h0}, {h1})")
    out, cur = [], a
    while cur < b:
        out.append(ef.hour_key(cur))
        cur += timedelta(hours=1)
    return out


def default_problems(walk_dir: Path, hours: Sequence[str]) -> list[str]:
    return ef.hour_problems(walk_dir, hours, strict_bad_lines=True)


def decoder_pin_problems(ref: str | None = None) -> list[str]:
    """Walk 2's decoder blobs (forward_v_join.PINNED_BLOBS) in the working tree, or at a git ref (`pins --ref`). Empty list = all equal."""
    from tools import forward_v_join as fvj

    status = fvj.pin_status_at_ref(ref) if ref else fvj.pin_status()
    return [f"decoder blob {s['file']}: pinned {s['pinned']}, actual {s['actual']}" for s in status if not s["ok"]]


def check_hours(walk_dir: Path, hours: Sequence[str], problems_fn: ProblemsFn | None = None, verify: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Refuse unless every hour is sealed, strict-OK in verify.jsonl, sha256-matched, has a migrations file, holds no bad line, and every file's line count
    equals verify content[sub].rows. `problems_fn` / `verify` can be injected (tests); the default is the real strict `hour_problems` and `verified_hours`.
    Returns {hour: {sub: path}}."""
    probs = list((problems_fn or default_problems)(walk_dir, hours))
    table = verify if verify is not None else ef.verified_hours(walk_dir, strict_bad_lines=True)
    files: dict[str, dict[str, Path]] = {}
    for h in hours:
        files[h] = {}
        rec = table.get(h)
        if rec is None:
            probs.append(f"hour {h}: no OK verify line")
            continue
        content = rec.get("content") if isinstance(rec.get("content"), dict) else {}
        for sub in SUBS:
            f = _hour_file(walk_dir / sub, sub, h)
            if f is None:
                probs.append(f"hour {h}: no {sub} file under {walk_dir / sub}")
                continue
            files[h][sub] = f
            want = (content.get(sub) or {}).get("rows") if isinstance(content.get(sub), dict) else None
            if not isinstance(want, int) or isinstance(want, bool):
                probs.append(f"hour {h}: verify.jsonl has no content[{sub}].rows")
                continue
            try:
                counts = tape_lines.scan_file(f)
            except Exception as exc:  # truncated zstd etc.
                probs.append(f"hour {h}: {sub} unreadable ({type(exc).__name__}: {exc})")
                continue
            if counts.bad:
                probs.append(f"hour {h}: {sub} has {counts.bad} bad line(s) {counts.as_dict()}")
            if counts.lines != want:
                probs.append(f"hour {h}: {sub} has {counts.lines} lines but verify.jsonl says rows={want}")
    if probs:
        raise LayoutRefused(probs)
    return files


def _read_json_sql(path: Path, cols: Mapping[str, str]) -> str:
    spec = ", ".join(f"'{k}': '{v}'" for k, v in cols.items())
    return f"read_json('{path}', format='newline_delimited', columns={{{spec}}}, maximum_object_size=67108864)"


def _load(con: Any, table: str, path: Path, cols: Mapping[str, str], want_rows: int, label: str, create: bool, hix: int | None = None) -> None:
    src = _read_json_sql(path, cols)
    if hix is not None:  # add the file row number (hour index * 1e10 + line ordinal)
        src = f"(select {hix} * 10000000000 + (row_number() over () - 1) as frn, * from {src})"
    try:
        if create:
            con.sql(f"create or replace temp table {table} as select * from {src}")
        else:
            con.sql(f"insert into {table} select * from {src}")
    except Exception as exc:
        raise LayoutRefused(f"{label}: DuckDB could not parse {path.name} ({type(exc).__name__}: {str(exc)[:200]})") from exc


def _load_trades(con: Any, path: Path, hix: int, label: str, cols: Mapping[str, str] | None = None, mask_v: bool = False) -> None:
    """Load an hour's trades into temp table t with `frn` = hour_index * 1e10 + row number in the file (insertion order is preserved by DuckDB).
    mask_v (structure mode): virtual_quote_reserves is replaced AT LOAD by the boolean `vq_null`; t never holds a V value."""
    src = _read_json_sql(path, cols or TRADE_COLS)
    star = "* exclude (virtual_quote_reserves), (virtual_quote_reserves is null) as vq_null" if mask_v else "*"
    try:
        con.sql(f"create or replace temp table t as select {hix} * 10000000000 + (row_number() over () - 1) as frn, {star} from {src}")
    except Exception as exc:
        raise LayoutRefused(f"{label}: DuckDB could not parse {path.name} ({type(exc).__name__}: {str(exc)[:200]})") from exc
    _check_frn(con, path, hix, label)


FRN_SAMPLE_EVERY = 100_000


_RE_SLOT = re.compile(r'[,{]\s*"slot"\s*:\s*(-?\d+)')
_RE_SIG = re.compile(r'[,{]\s*"signature"\s*:\s*"([^"\\]*)"')
_RE_EVI = re.compile(r'[,{]\s*"event_index"\s*:\s*(-?\d+)')


def _slot_sig_evi(line: str) -> tuple:
    """(slot, signature, event_index) of a trade line by regex on the three fields only (no other field is parsed); a missing / null field is None."""
    a, b, c = _RE_SLOT.search(line), _RE_SIG.search(line), _RE_EVI.search(line)
    return (int(a.group(1)) if a else None, b.group(1) if b else None, int(c.group(1)) if c else None)


def _check_frn(con: Any, path: Path, hix: int, label: str) -> None:
    """Run-time guard: the rows at ordinals 0, last and every 100,000th, re-read from the file in Python, must equal (slot, signature, event_index) of the row
    DuckDB gave that frn. Any mismatch refuses ("frn_order"): the within-slot order would not be the file order."""
    base = hix * 10_000_000_000
    n = con.sql("select count(*) from t").fetchone()[0]
    if n == 0:
        return
    want_ord = {0, n - 1} | set(range(0, n, FRN_SAMPLE_EVERY))
    seen: dict[int, tuple] = {}
    i = 0
    for line in _open_text(path):
        if not line.strip():
            continue
        if i in want_ord:
            seen[i] = _slot_sig_evi(line)
        i += 1
    if i != n:
        raise LayoutRefused(f"{label}: frn_order: file has {i} rows but DuckDB loaded {n}")
    ords = sorted(want_ord)
    got = {int(f) - base: (sl, sg, ev) for f, sl, sg, ev in con.sql(
        f"select frn, slot, signature, event_index from t where frn in ({','.join(str(base + o) for o in ords)})").fetchall()}
    bad = [o for o in ords if got.get(o) != seen.get(o)]
    if bad:
        raise LayoutRefused(f"{label}: frn_order: row ordinal(s) {bad[:5]} of {path.name} differ between the file and DuckDB's frn assignment")


def _hour_rows(files: Mapping[str, Mapping[str, Path]], verify: Mapping[str, Mapping[str, Any]], h: str, sub: str) -> int:
    return int(verify[h]["content"][sub]["rows"])


def _utc_day(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d")


def load_static_map(path: str | Path) -> dict[str, int | None]:
    """pool_v_0909.json is {"fetched_utc": ..., "v": {pool: V}}; a bare {pool: V} json is accepted too."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    v = doc["v"] if isinstance(doc.get("v"), dict) else doc
    return {k: (None if x is None else int(x)) for k, x in v.items()}


def default_canonical() -> CanonFn:
    from tools.exp016_screen import canonical_pool_str

    return canonical_pool_str


def build(walk_dir: Path, h0: str, h1: str, out_dir: Path, *, v_source: str = "event", static_map: Mapping[str, int | None] | None = None,
          canonical_fn: CanonFn | None = None, problems_fn: ProblemsFn | None = None, verify: Mapping[str, Mapping[str, Any]] | None = None,
          memory_limit: str = "6GB", threads: int = 4, tmp_dir: Path | None = None, structure_only: bool = False, trade_end: str | None = None) -> dict[str, Any]:
    """Run the adapter. Returns the stats dict (also written to out_dir/stats.json).

    trade_end (exclusive hour key, default none): the lookahead trade hours past H1 are only loaded below it (the count job passes the end of its allowlist).

    structure_only=True (the count job): trade rows are parsed with TRADE_COLS_STRUCT only; ut gets (mid, slot, txi, evi, bt) and bond / mkt / mkt_creates are not
    written; the clock is. The V null refusal on member prints selects `virtual_quote_reserves IS NULL` only. meta, creators, m0.parquet (every completed mint
    with pda_match / mayhem / s0 / v0) and stats.json are the same as in full mode."""
    import duckdb

    if v_source not in V_SOURCES:
        raise LayoutRefused(f"v_source must be one of {V_SOURCES}, got {v_source!r}")
    if v_source == "static_map" and static_map is None:
        raise LayoutRefused("v_source=static_map needs the {pool: V} map")
    if v_source == "event" and static_map is not None:
        raise LayoutRefused("a static map was given with v_source=event; refusing to mix the V sources")
    walk_dir, out_dir = Path(walk_dir), Path(out_dir)
    tcols = TRADE_COLS_STRUCT if structure_only else TRADE_COLS
    try:
        out_dir.resolve().relative_to(walk_dir.resolve())
    except ValueError:
        pass
    else:
        raise LayoutRefused("out_dir is inside the dataset dir")
    hours = hour_range(h0, h1)
    files = check_hours(walk_dir, hours, problems_fn, verify)
    table = verify if verify is not None else ef.verified_hours(walk_dir, strict_bad_lines=True)
    sealed = ef._sealed_hours(walk_dir)
    extras: list[str] = []
    for k in (1, 2):  # training loaded 2 trade hours past the last graduation hour
        eh = ef.hour_key(ef.hour_dt(h1) + timedelta(hours=k - 1))
        if trade_end is not None and eh >= trade_end:
            break
        if eh in sealed or (verify is not None and eh in verify):
            extras.append(eh)
        else:
            break
    if extras:
        files.update(check_hours(walk_dir, extras, problems_fn, verify))
    trade_hours = hours + extras
    canon = canonical_fn or default_canonical()
    for sub in (("ut", "clock") if structure_only else ("ut", "bond", "clock", "mkt")):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()
    con.sql(f"SET memory_limit='{memory_limit}'; SET threads={int(threads)}; SET enable_progress_bar=false; SET preserve_insertion_order=true")
    if tmp_dir is not None:
        Path(tmp_dir).mkdir(parents=True, exist_ok=True)
        con.sql(f"SET temp_directory='{tmp_dir}'")

    # ---- pass 1a: migrations and creates of all hours
    for i, h in enumerate(hours):
        _load(con, "mig", files[h]["migrations"], MIG_COLS, 0, h, create=(i == 0), hix=i)
        _load(con, "cre_raw", files[h]["creates"], CREATE_COLS, 0, h, create=(i == 0))
    # per-file parsed counts (DuckDB must agree with the line count, i.e. no line was silently dropped)
    for h in hours:
        for sub in ("migrations", "creates"):
            cols = MIG_COLS if sub == "migrations" else CREATE_COLS
            got = con.sql(f"select count(*) from {_read_json_sql(files[h][sub], cols)}").fetchone()[0]
            want = _hour_rows(files, table, h, sub)
            if got != want:
                raise LayoutRefused(f"hour {h}: {sub} parsed {got} rows but verify.jsonl says rows={want}")
    con.sql("""create table comp as select mint, min(slot) mslot, min(block_time) mbt from mig where type='complete' and mint is not null group by 1""")
    con.sql("""create table mev as select mint, arg_min(pool, (slot, coalesce(tx_index,-1), coalesce(event_index,-1), frn)) pool,
               arg_min(quote_mint, (slot, coalesce(tx_index,-1), coalesce(event_index,-1), frn)) quote_mint from mig where type='migration' and mint is not null group by 1""")
    con.sql("""create table cre as select mint, arg_min(is_mayhem_mode, slot) mayhem, any_value(hash(creator)) ch, min(block_time) cbt, min(slot) cslot
               from cre_raw where coalesce(type,'create')='create' and mint is not null group by 1""")

    # PDA of every completed mint: outcome-blind, from the mint alone
    pda: dict[str, str | None] = {}
    n_pda_err = 0
    for (mint,) in con.sql("select mint from comp order by mint").fetchall():
        try:
            pda[mint] = str(canon(mint))
        except Exception:
            pda[mint] = None
            n_pda_err += 1
    con.sql("create table pda(mint varchar, pda varchar)")
    con.executemany("insert into pda values (?, ?)", list(pda.items()))
    con.sql("create table m_comp as select c.*, p.pda from comp c left join pda p using(mint)")

    # ---- pass 1b: per hour, row-count check, clock + mkt output, s0 candidates
    if structure_only:  # no V in the candidate table; V0 is re-read from the s0 rows only (below)
        con.sql("create table cand(mint varchar, slot bigint, txi integer, evi integer, frn bigint, bt bigint, pool varchar)")
    else:
        con.sql("create table cand(mint varchar, slot bigint, txi integer, evi integer, frn bigint, vq bigint, bt bigint, pool varchar)")
    n_trade_rows = 0
    for hix, h in enumerate(trade_hours):
        _load_trades(con, files[h]["trades"], hix, h, tcols, structure_only)
        got = con.sql("select count(*) from t").fetchone()[0]
        want = _hour_rows(files, table, h, "trades")
        if got != want:
            raise LayoutRefused(f"hour {h}: trades parsed {got} rows but verify.jsonl says rows={want}")
        n_trade_rows += got
        con.sql(f"""copy (select block_time bt, min(slot) smin, max(slot) smax from t where block_time is not null group by 1 order by 1)
                    to '{out_dir}/clock/{h}.parquet' (format parquet)""")
        if not structure_only:
            con.sql(f"""copy (select (block_time//60)*60 AS mnt, sum((venue='pumpswap')::int)::double n_ps,
                    sum(case when venue='pumpswap' then sol_lamports else 0 end)::double v_ps, sum((venue='pump_bonding')::int)::double n_bd,
                    sum(case when venue='pump_bonding' then sol_lamports else 0 end)::double v_bd
                    from t where block_time is not null group by 1 order by 1) to '{out_dir}/mkt/{h}.parquet' (format parquet)""")
        vq_sel = "" if structure_only else "t.virtual_quote_reserves,"
        con.sql(f"""insert into cand select t.mint, t.slot, coalesce(t.tx_index,-1), coalesce(t.event_index,-1), t.frn, {vq_sel}
                    t.block_time, t.pool from t join m_comp c on t.mint=c.mint and t.pool=c.pda
                    where t.venue='pumpswap' and t.slot >= c.mslot and t.slot < c.mslot + {WINDOW_SLOTS}""")
    if structure_only:
        con.sql("""create table s0t as select mint, slot s0, frn, null::bigint vq, false as matched from (select *, row_number() over (partition by mint order by slot, txi, evi, frn) rn
                   from cand) where rn=1""")
        for hix, h in enumerate(trade_hours):  # targeted re-read over ALL loaded trade hours: ONLY the s0 rows' V (matched on frn, same assignment as _load_trades)
            src = _read_json_sql(files[h]["trades"], {"slot": "BIGINT", "virtual_quote_reserves": "BIGINT"})
            con.sql(f"""update s0t set vq = x.v, matched = true from (select frn, sl, v from (select {hix} * 10000000000 + (row_number() over () - 1) as frn, slot sl,
                        virtual_quote_reserves v from {src}) where frn in (select frn from s0t)) x where s0t.frn = x.frn and s0t.s0 = x.sl""")
        n_unmatched = con.sql("select count(*) from s0t where not matched").fetchone()[0]
        if n_unmatched:
            raise LayoutRefused(f"frn_order: {n_unmatched} s0 row(s) found no match (frn, slot) in the V0 re-read")
        con.sql("alter table s0t drop column matched")
        con.sql("alter table cand drop column frn")
    else:
        con.sql("""create table s0t as select mint, slot s0, vq from (select *, row_number() over (partition by mint order by slot, txi, evi, frn) rn from cand) where rn=1""")
    if not structure_only:
        con.sql(f"""copy (select (block_time//60)*60 AS mnt, count(*) n_cr from cre_raw where coalesce(type,'create')='create' and block_time is not null
                group by 1 order by 1) to '{out_dir}/mkt_creates.parquet' (format parquet)""")

    # ---- V0 and membership
    if v_source == "static_map":
        con.sql("create table smap(pool varchar, v bigint)")
        con.executemany("insert into smap values (?, ?)", [(p, None if v is None else int(v)) for p, v in static_map.items()])  # type: ignore[union-attr]
        v0_expr = "smap.v"
        join_v = "left join smap on smap.pool = mc.pda"
        match_req = "true"
    else:
        v0_expr = "s0t.vq"
        join_v = ""
        match_req = "pda_match"
    con.sql(f"""create table m0 as select mc.mint, mc.pda, mc.mslot, mc.mbt, s0t.s0, {v0_expr} v0, cre.mayhem,
                (cre.mint is not null) has_create, (mev.mint is not null) has_mig,
                coalesce(mev.pool = mc.pda and mev.quote_mint = '{WSOL_MINT}', false) pda_match, (mev.quote_mint is not null and mev.quote_mint <> '{WSOL_MINT}') nonwsol
                from m_comp mc left join mev using(mint) left join cre using(mint) left join s0t using(mint) {join_v}""")
    con.sql(f"""create table m as select row_number() over (order by mbt, mint) - 1 as mid, mint, pda as pool, mslot, mbt, s0, v0 as v,
                strftime(make_timestamp(mbt * 1000000), '%Y-%m-%d') as "day", pda_match from m0
                where coalesce(mayhem,false) = false and mbt is not null and pda is not null and s0 is not null and v0 between {V_LO} and {V_HI} and {match_req}""")
    con.sql(f"""copy (select * from m order by mid) to '{out_dir}/meta.parquet' (format parquet)""")
    con.sql(f"""copy (select mint, mbt, mslot, s0, v0, coalesce(mayhem,false) mayhem, pda_match, has_create from m0 order by mbt, mint) to '{out_dir}/m0.parquet' (format parquet)""")
    con.sql(f"""copy (select m.mid, cre.ch, cre.cbt, cre.cslot from m left join cre using(mint) order by m.mid) to '{out_dir}/creators.parquet' (format parquet)""")

    # ---- pass 2: ut and bond per hour
    vq_col = "t.virtual_quote_reserves" if v_source == "event" else "m.v"
    try:
        for hix, h in enumerate(trade_hours):
            _load_trades(con, files[h]["trades"], hix, h, tcols, structure_only)
            o = f"{out_dir}/ut/{h}.parquet"
            if structure_only:
                con.sql(f"""copy (select m.mid, t.slot, coalesce(t.tx_index,-1)::integer txi, coalesce(t.event_index,-1)::integer evi, t.block_time bt
                            from t join m on t.pool=m.pool and t.mint=m.mint
                            where t.venue='pumpswap' and t.slot>=m.s0 and (t.block_time is null or t.block_time <= m.mbt + {HORIZON_S})
                            order by m.mid, t.slot, txi, evi, t.frn) to '{o}' (format parquet)""")
                n_null = con.sql(f"""select count(*) from t join m on t.pool=m.pool and t.mint=m.mint where t.venue='pumpswap' and t.slot>=m.s0
                                     and (t.block_time is null or t.block_time <= m.mbt + {HORIZON_S}) and t.vq_null""").fetchone()[0]
                if v_source == "event" and n_null:
                    raise LayoutRefused(f"hour {h}: {n_null} print(s) of MEMBER pool(s) at/after s0 have a null/missing virtual_quote_reserves "
                                        "(no dropping members afterwards, no forward-fill)")
                continue
            con.sql(f"""copy (select m.mid, t.slot, coalesce(t.tx_index,-1)::integer txi, coalesce(t.event_index,-1)::integer evi, t.block_time bt, (t.side='buy') isbuy,
                        t.sol_lamports sol, t.token_raw::double tok, t.quote_reserve::double q, t.base_reserve::double b, hash(t.trader) th, {vq_col}::bigint vq, (t.frn - {hix} * 10000000000)::bigint frn
                        from t join m on t.pool=m.pool and t.mint=m.mint
                        where t.venue='pumpswap' and t.slot>=m.s0 and (t.block_time is null or t.block_time <= m.mbt + {HORIZON_S})
                        order by m.mid, t.slot, txi, evi, t.frn) to '{o}' (format parquet)""")
            con.sql(f"""copy (select m.mid, t.slot, t.block_time bt, (t.side='buy') isbuy, t.sol_lamports sol, hash(t.trader) th
                        from t join m on t.mint=m.mint where t.venue='pump_bonding' and t.slot < m.mslot + 1
                        order by m.mid, t.slot, coalesce(t.tx_index,-1), coalesce(t.event_index,-1), t.frn) to '{out_dir}/bond/{h}.parquet' (format parquet)""")
            if v_source == "event":
                n_null = con.sql(f"select count(*) from read_parquet('{o}') where vq is null").fetchone()[0]
                if n_null:
                    raise LayoutRefused(f"hour {h}: {n_null} print(s) of MEMBER pool(s) at/after s0 have a null/missing virtual_quote_reserves "
                                        "(no dropping members afterwards, no forward-fill)")
    except LayoutRefused:
        for f in ("meta.parquet", "creators.parquet"):  # an incomplete build must not look complete
            (out_dir / f).unlink(missing_ok=True)
        raise

    tie = _txi_stats(con, out_dir)
    stats = _stats(con, v_source, hours, n_trade_rows, n_pda_err, duckdb.__version__)
    stats["trade_extra_hours"] = extras
    stats.update(tie)
    if structure_only:  # every non-boolean V-like column of every table / view of this run (the test pins: only s0t, m0, m)
        stats["v_columns"] = sorted(f"{t}.{c}" for t, c in con.sql(
            """select table_name, column_name from information_schema.columns where data_type <> 'BOOLEAN'
               and (column_name in ('v', 'v0', 'vq') or column_name like '%virtual%' or column_name like 'vq%')""").fetchall())
    (out_dir / "stats.json").write_text(json.dumps(stats, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    con.close()
    return stats


def _txi_stats(con: Any, out_dir: Path) -> dict[str, int]:
    """Null tx_index (txi < 0 in ut) diagnostics over the member prints, same definitions as r4b_features.null_txi_tie_groups / mixed_null_txi_slots:
    groups of >= 2 null-tx rows sharing (mid, slot, evi), and (mid, slot) cells mixing null and non-null tx_index."""
    src = f"read_parquet('{out_dir}/ut/*.parquet')"
    tie = con.sql(f"select count(*) from (select 1 from {src} where txi < 0 group by mid, slot, evi having count(*) > 1)").fetchone()[0]
    mixed = con.sql(f"select count(*) from (select 1 from {src} group by mid, slot having bool_or(txi < 0) and bool_or(txi >= 0))").fetchone()[0]
    return {"null_txi_tie_groups": int(tie), "mixed_null_txi_slots": int(mixed)}


def _stats(con: Any, v_source: str, hours: Sequence[str], n_trade_rows: int, n_pda_err: int, duckdb_version: str) -> dict[str, Any]:
    """Structure numbers only (counts and shares). No outcome, no price."""
    q = lambda s: con.sql(s).fetchone()[0]  # noqa: E731
    n_comp = q("select count(*) from m0")
    n_may = q("select count(*) from m0 where coalesce(mayhem,false)")
    n_non = n_comp - n_may
    n_match = q("select count(*) from m0 where not coalesce(mayhem,false) and pda_match")
    n_nomig = q("select count(*) from m0 where not coalesce(mayhem,false) and not has_mig")
    n_nonwsol = q("select count(*) from m0 where not coalesce(mayhem,false) and nonwsol")
    elig = "not coalesce(mayhem,false) and s0 is not null" + (" and pda_match" if v_source == "event" else "")
    n_s0 = q(f"select count(*) from m0 where {elig}")
    n_v0_missing = q(f"select count(*) from m0 where {elig} and v0 is null")
    n_band = q(f"select count(*) from m0 where {elig} and v0 between {V_LO} and {V_HI}")
    per_day = dict(con.sql("select day, count(*) from m group by 1 order by 1").fetchall())
    miss = {d: {"candidates": int(n), "v0_missing": int(k), "share": (k / n) if n else None} for d, n, k in con.sql(
        f"""select strftime(make_timestamp(mbt * 1000000), '%Y-%m-%d') d, count(*), sum((v0 is null)::int) from m0 where {elig} and mbt is not null
            group by 1 order by 1""").fetchall()}
    return {
        "v0_missing_share_by_day": miss, "v0_missing_refuse_share": V0_MISSING_REFUSE_SHARE,
        "v0_missing_days_over": sorted(d for d, r in miss.items() if r["share"] is not None and r["share"] > V0_MISSING_REFUSE_SHARE),
        "s0_window_note": S0_WINDOW_NOTE, "schema": SCHEMA, "v_source": v_source, "hours": [hours[0], hours[-1]], "n_hours": len(hours), "n_trade_rows": n_trade_rows, "duckdb": duckdb_version,
        "completes": n_comp, "mayhem": n_may, "non_mayhem": n_non, "no_create": q("select count(*) from m0 where not has_create"),
        "pda_errors": n_pda_err, "no_migration_event": n_nomig, "non_wsol_quote": n_nonwsol,
        "pda_match": n_match, "pda_match_share": (n_match / n_non) if n_non else None,
        "with_s0": n_s0, "v0_missing": n_v0_missing, "v0_in_band": n_band, "v0_band_share": (n_band / (n_s0 - n_v0_missing)) if n_s0 - n_v0_missing else None,
        "members": q("select count(*) from m"), "members_per_day": per_day,
    }


def prep_work(out_dir: Path, work_dir: Path | None = None, threads: int = 4, memory_limit: str = "6GB") -> dict[str, int]:
    """What l3's 02a_prep.py does, on this adapter's output: clock_bt.npy / clock_slot.npy, mkt.npz, mintconst.pkl in `work_dir` (default out_dir/work)."""
    import duckdb
    import numpy as np

    out_dir = Path(out_dir)
    work = Path(work_dir) if work_dir else out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    c = duckdb.connect()
    c.sql(f"SET memory_limit='{memory_limit}'; SET threads={int(threads)}; SET enable_progress_bar=false; SET preserve_insertion_order=true")
    ck = c.sql(f"select bt, min(smin) smin from '{out_dir}/clock/*.parquet' group by 1 order by 1").fetchnumpy()
    np.save(work / "clock_bt.npy", ck["bt"].astype(np.int64))
    np.save(work / "clock_slot.npy", ck["smin"].astype(np.int64))
    mk = c.sql(f"""with a as (select mnt, sum(n_ps) n_ps, sum(v_ps) v_ps, sum(n_bd) n_bd, sum(v_bd) v_bd from '{out_dir}/mkt/*.parquet' group by 1),
       b as (select mnt, n_cr from '{out_dir}/mkt_creates.parquet')
       select a.*, coalesce(b.n_cr,0) n_cr from a left join b using(mnt) order by mnt""").fetchnumpy()
    np.savez(work / "mkt.npz", **{k: np.asarray(v, dtype=np.float64) for k, v in mk.items()})
    c.sql(f"create table cr as select * from '{out_dir}/creators.parquet'")
    mc = c.sql(f"""select m.mid, m.mbt, m.mslot, m.s0, m.v, m.day, cr.ch, cr.cbt,
       bs.nb, bs.ntr, bs.bbuy, bs.bsell, bs.bfirst, bs.cre_sell
       from '{out_dir}/meta.parquet' m left join cr using(mid)
       left join (select b.mid, count(*) nb, count(distinct b.th) ntr, sum(case when isbuy then sol else 0 end) bbuy, sum(case when not isbuy then sol else 0 end) bsell,
            min(bt) bfirst, sum(case when (not isbuy) and b.th=cr.ch then sol else 0 end) cre_sell
            from '{out_dir}/bond/*.parquet' b join cr using(mid) group by 1) bs using(mid) order by m.mid""").df()
    mc.to_pickle(work / "mintconst.pkl")
    return {"clock": len(ck["bt"]), "mkt": len(mk["mnt"]), "mints": len(mc)}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--walk-dir", required=True)
    ap.add_argument("--from-hour", required=True, help="H0, YYYY-MM-DDTHH (inclusive)")
    ap.add_argument("--to-hour", required=True, help="H1, YYYY-MM-DDTHH (exclusive)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--v-source", choices=V_SOURCES, default="event")
    ap.add_argument("--static-map", default=None, help="pool_v_0909.json (exploration replay only; needs --v-source static_map)")
    ap.add_argument("--memory-limit", default="6GB")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--tmp-dir", default=None)
    ap.add_argument("--prep", action="store_true", help="also run the 02a prep step")
    a = ap.parse_args(argv)
    smap = load_static_map(a.static_map) if a.static_map else None
    try:
        stats = build(Path(a.walk_dir), a.from_hour, a.to_hour, Path(a.out_dir), v_source=a.v_source, static_map=smap, memory_limit=a.memory_limit,
                      threads=a.threads, tmp_dir=Path(a.tmp_dir) if a.tmp_dir else None)
        if a.prep:
            prep_work(Path(a.out_dir), threads=a.threads, memory_limit=a.memory_limit)
    except LayoutRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(stats, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
