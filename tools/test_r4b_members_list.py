"""Tests for tools/r4b_members_list.py on SYNTHETIC walker-layout hours (fixtures from test_r4b_layout / test_r4b_count). Nothing here reads /data/mal.

  PYTHONPATH=/data/mal/audit-1008/venv/lib/python3.12/site-packages:. /data/mal/venv/bin/python -m pytest tools/test_r4b_members_list.py
"""

from __future__ import annotations

import hashlib
import json

import pytest

duckdb = pytest.importorskip("duckdb")

from tools import mint_account_fetch as maf  # noqa: E402
from tools import r4b_layout as rl  # noqa: E402
from tools import r4b_members_list as ml  # noqa: E402
from tools.test_r4b_count import hkey, stream  # noqa: E402
from tools.test_r4b_layout import make_walk, pda_of  # noqa: E402


def test_members_equal_the_structure_build_meta(tmp_path):
    walk = make_walk(tmp_path / "walk", stream(5))
    out = tmp_path / "m" / "members.txt"
    summ = ml.run(walk, hkey(0), hkey(5), out, canonical_fn=pda_of)
    ref = tmp_path / "ref"
    rl.build(walk, hkey(0), hkey(5), ref, canonical_fn=pda_of, structure_only=True, trade_end=hkey(5))
    want = sorted({r[0] for r in duckdb.sql(f"select mint from read_parquet('{ref / 'meta.parquet'}')").fetchall()})
    body = out.read_bytes()
    assert body.decode().splitlines() == want and want == [f"M{i}" for i in range(5)]
    assert summ["mints"] == 5 and summ["sha256"] == hashlib.sha256(body).hexdigest() and summ["hours"] == 5
    assert not any(p.name.startswith("r4b-members-") for p in out.parent.iterdir())  # scratch removed
    assert sorted(p.name for p in out.parent.iterdir()) == ["members.txt"]


def test_out_holds_mints_only(tmp_path):
    walk = make_walk(tmp_path / "walk", stream(3))
    out = tmp_path / "members.txt"
    ml.run(walk, hkey(0), hkey(3), out, canonical_fn=pda_of)
    for ln in out.read_text().splitlines():
        assert "\t" not in ln and "," not in ln and " " not in ln


def test_refuses_an_existing_out(tmp_path):
    walk = make_walk(tmp_path / "walk", stream(2))
    out = tmp_path / "members.txt"
    out.write_text("x\n")
    with pytest.raises(ml.Refused, match="write-once"):
        ml.run(walk, hkey(0), hkey(2), out, canonical_fn=pda_of)
    assert out.read_text() == "x\n"


def test_refuses_out_inside_the_walk_dir(tmp_path):
    walk = make_walk(tmp_path / "walk", stream(2))
    with pytest.raises(ml.Refused, match="inside the walk dir"):
        ml.run(walk, hkey(0), hkey(2), walk / "members.txt", canonical_fn=pda_of)


def test_unsealed_hour_refuses_and_writes_nothing(tmp_path):
    walk = make_walk(tmp_path / "walk", stream(3), unsealed=(hkey(2),))
    out = tmp_path / "o" / "members.txt"
    with pytest.raises(rl.LayoutRefused):
        ml.run(walk, hkey(0), hkey(3), out, canonical_fn=pda_of)
    assert not out.exists() and not (out.parent / "members.txt.part").exists()
    assert list(out.parent.iterdir()) == []


def test_cli_refusal_exit_2(tmp_path, capsys):
    walk = make_walk(tmp_path / "walk", stream(2))
    out = tmp_path / "members.txt"
    out.write_text("x\n")
    assert ml.main(["--walk-dir", str(walk), "--from-hour", hkey(0), "--to-hour", hkey(2), "--out", str(out)]) == 2
    assert "REFUSED" in capsys.readouterr().err


def test_summary_is_json_serialisable(tmp_path):
    walk = make_walk(tmp_path / "walk", stream(2))
    summ = ml.run(walk, hkey(0), hkey(2), tmp_path / "members.txt", canonical_fn=pda_of)
    json.dumps(summ)


def test_format_is_what_mint_account_fetch_reads(tmp_path):
    # the same bytes run() writes ("<mint>\n" per member, sorted), with real-looking base58 mints
    mints = sorted(["So11111111111111111111111111111111111111112", "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"])
    p = tmp_path / "members.txt"
    p.write_bytes("".join(f"{m}\n" for m in mints).encode("ascii"))
    assert maf.read_mints(p) == mints
