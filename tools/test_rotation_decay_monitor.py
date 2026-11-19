"""rotation_decay_monitor tests (DEC-036 section 9 item 9): synthetic ledgers only (no real ledger, no host, no key).

  /data/mal/venv/bin/python -m pytest tools/test_rotation_decay_monitor.py -q
"""

from __future__ import annotations

import json
import random

import pytest

from tools import rotation_decay_monitor as M

BOT, OTHER = "rot-test-1", "rot-other-1"
S05, S10, S125, S25 = 50_000_000, 100_000_000, 125_000_000, 250_000_000
SHA = "ab" * 32


def cal_dict(stake=S10, **kw):
    d = {"schema": M.SCHEMA, "bot_id": BOT, "stake_lamports": stake, "winsor_lo": -0.5, "winsor_hi": 0.5, "mu0": 0.02, "mu1": -0.02, "h_w": 0.05, "h_a": 0.2,
         "n0": 25, "sim_source_sha256": SHA}
    d.update(kw)
    return d


class Fx:
    """Closed trades with an exact x: intent, ok fill (cash out stake + 1000), exit (cash in stake + 1000 + r), r = round(x * stake)."""

    def __init__(self, tmp):
        self.p = tmp / "lane-ledger.jsonl"
        self.c = tmp / "cal.json"
        self.rows, self.t, self.i = [], 1_000_000, 0

    def row(self, ev, mint, bot=BOT, **kw):
        self.t += 1000
        self.rows.append({"schema": "x", "bot_id": bot, "row_id": f"r{len(self.rows)}", "ev": ev, "ts_ms": self.t, "mint": mint, "sol_in_lamports": 0, "sol_out_lamports": 0, **kw})

    def start(self, stake, bot=BOT):
        self.row("start", "", bot=bot, stake_lamports=stake)
        return self

    def trade(self, x, stake=S10, bot=BOT):
        self.i += 1
        m = f"M{self.i}"
        r = round(x * stake)
        self.row("send_intent", m, bot=bot, stake_lamports=stake)
        self.row("fill", m, bot=bot, ok=True, sol_out_lamports=stake + 1000)
        self.row("exit", m, bot=bot, sol_in_lamports=stake + 1000 + r)
        return self

    def many(self, xs, stake=S10, bot=BOT):
        for x in xs:
            self.trade(x, stake, bot)
        return self

    def write(self, tail=b"", cal=None):
        self.p.write_bytes(b"".join(json.dumps(r).encode() + b"\n" for r in self.rows) + tail)
        self.c.write_text(json.dumps(cal if cal is not None else cal_dict()))
        return self

    def args(self, *extra):
        return ["--bot", BOT, "--ledger-file", str(self.p), "--calibration", str(self.c), *extra]


def run_json(fx, capsys, *extra):
    code = M.main(fx.args("--json", *extra))
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.strip() else None)


def test_rotation_decay_cusum_matches_card_calibration(tmp_path, capsys):
    """S from the ledger equals S computed by hand from the card's mu0/mu1 and winsor bounds (an outlier is clipped); flags fire exactly at the card's h_w / h_a."""
    xs = [-0.10, 0.30, -0.20, 5.0, -3.0, 0.01]  # 5.0 -> clip 0.5, -3.0 -> clip -0.5
    fx = Fx(tmp_path).start(S10).many(xs).write()
    mu0, mu1 = 0.02, -0.02
    kappa = (mu0 - mu1) / 2
    s = 0.0
    for x in xs:
        w = min(max(x, -0.5), 0.5)
        s = max(0.0, s + (mu0 - kappa) - w)
    code, res = run_json(fx, capsys)
    assert res["S"] == pytest.approx(s)
    assert s > 0  # sanity: the hand value is positive
    assert res["warning"] == (s >= 0.05) and res["alarm"] == (s >= 0.2)
    assert res["calibration"]["sim_source_sha256"] == SHA
    # the crossing is exactly at h: a single loss of x = -h_w (w = -0.05, S = 0.05) is a warning; just under is not
    for name, x, want in (("a", -0.05, True), ("b", -0.0499, False)):
        (tmp_path / name).mkdir()
        assert run_json(Fx(tmp_path / name).start(S10).trade(x).write(), capsys)[1]["warning"] is want


def test_rotation_decay_false_alarm_rates_near_targets():
    """Sanity of the CUSUM against the section 6 item 4 procedure (reimplemented here, synthetic sim, seeded): h set by 10,000 bootstrap paths of 100
    (re-centred on mu0) gives warning / alarm rates near 20% / 5% on independent paths run through the monitor's own cusum_path. QP sets the card's h; this checks the form."""
    rng = random.Random(1)
    mu0, mu1 = 0.02, -0.02
    sim = sorted([rng.gauss(-0.03, 0.12) for _ in range(300)] + [rng.uniform(0.5, 1.5) for _ in range(8)])
    lo, hi = sim[int(0.025 * len(sim))], sim[int(0.975 * len(sim))]
    w_sim = [min(max(x, lo), hi) for x in sim]
    mean_w = sum(w_sim) / len(w_sim)
    centred = [w - mean_w + mu0 for w in w_sim]

    def peak(seed, n):
        r = random.Random(seed)
        return sorted(max(M.cusum_path([r.choice(centred) for _ in range(100)], mu0, mu1)) for _ in range(n))

    cal = peak(1, 10_000)
    h_w, h_a = cal[int(0.80 * len(cal))], cal[int(0.95 * len(cal))]
    fresh = peak(2, 4_000)
    rate_w = sum(1 for p in fresh if p >= h_w) / len(fresh)
    rate_a = sum(1 for p in fresh if p >= h_a) / len(fresh)
    assert abs(rate_w - 0.20) < 0.03, rate_w
    assert abs(rate_a - 0.05) < 0.02, rate_a
    assert h_w < h_a


def test_rotation_decay_requires_mu1_below_mu0(tmp_path, capsys):
    fx = Fx(tmp_path).start(S10).trade(0.01)
    for bad in (dict(mu1=0.02), dict(mu1=0.03), dict(mu1=0.01, mu0=0.005)):
        fx.write(cal=cal_dict(**bad))
        assert M.main(fx.args()) == 2
        assert "mu1" in capsys.readouterr().err
    fx.write(cal=cal_dict(mu0=-0.01, mu1=-0.03))  # mu1 < mu0 <= 0 loads (the monitor does not decide go-live; section 4 does)
    assert M.main(fx.args()) in (0, 10, 20)


def test_rotation_decay_calibration_refusals(tmp_path, capsys):
    fx = Fx(tmp_path).start(S10).trade(0.01)
    cases = [dict(bot_id=OTHER), dict(stake_lamports=S25), dict(stake_lamports=77), dict(sim_source_sha256="xyz"), dict(h_w=0.3, h_a=0.2), dict(winsor_lo=0.5, winsor_hi=-0.5),
             dict(schema="nope"), dict(mu0="0.02")]
    for c in cases:
        fx.write(cal=cal_dict(**c))
        assert M.main(fx.args()) == 2, c
        capsys.readouterr()
    d = cal_dict()
    del d["mu0"]
    fx.write(cal=d)
    assert M.main(fx.args()) == 2
    fx.c.write_text("{not json")
    assert M.main(fx.args()) == 2


def _ladder_ledger(tmp_path, stake, n_losses):
    return Fx(tmp_path).start(stake).many([-0.10] * n_losses, stake).write(cal=cal_dict(stake=stake))


@pytest.mark.parametrize("stake,warn,alarm", [(S25, S125, S05), (S125, S05, S05), (S10, S05, S05), (S05, None, None)])
def test_rotation_decay_ladder_table(tmp_path, capsys, stake, warn, alarm):
    """Every row of DEC-036 section 6 item 5, end to end: one loss of 0.10 s = S 0.10 (warning only: h_w 0.05 <= S < h_a 0.2); two = S 0.20 (alarm)."""
    d = tmp_path / "w"
    d.mkdir()
    code, res = run_json(_ladder_ledger(d, stake, 1), capsys)
    assert res["warning"] and not res["alarm"]
    assert res["ladder"]["to_stake"] == warn
    assert res["ladder"]["action"] == ("stop_retire" if warn is None else "step_down")
    assert code == (20 if warn is None else 10)
    d = tmp_path / "a"
    d.mkdir()
    code, res = run_json(_ladder_ledger(d, stake, 2), capsys)
    assert res["alarm"]
    assert res["ladder"]["to_stake"] == alarm
    assert res["ladder"]["action"] == ("stop_retire" if alarm is None else "step_down")
    assert code == (20 if alarm is None else 10)
    d = tmp_path / "ok"
    d.mkdir()
    code, res = run_json(_ladder_ledger(d, stake, 0), capsys)
    assert (code, res["ladder"]["action"]) == (0, "stay")


def test_rotation_decay_ladder_function_rows():
    for stake, w, a in ((S25, S125, S05), (S125, S05, S05), (S10, S05, S05)):
        assert M.ladder_action(stake, True, False)["to_stake"] == w
        assert M.ladder_action(stake, False, True)["to_stake"] == a
        assert M.ladder_action(stake, True, True)["to_stake"] == a  # alarm outranks warning
    for flags in ((True, False), (False, True), (True, True)):
        assert M.ladder_action(S05, *flags)["action"] == "stop_retire"
    assert M.ladder_action(S25, False, False)["action"] == "stay"
    # 0.125 -> 0.10 and any up move are never produced
    assert all(t in (S125, S05, None) for s in M.LADDER for t in M.LADDER[s])
    # section 6 item 3: mu0 <= 0 at the target rung makes the step a STOP
    assert M.ladder_action(S25, True, False, {S125: -0.001})["action"] == "stop_retire"
    assert M.ladder_action(S25, True, False, {S125: 0.01})["to_stake"] == S125
    with pytest.raises(M.MonitorError):
        M.ladder_action(77, True, False)


def test_rotation_retire_check_raw_basis(tmp_path, capsys):
    """m = (n mean(x) + n0 mu_p) / (n + n0) on RAW x from trade 50. At 0.10, c = 0.03825: m >= 0 needs mean(x) >= 0.019125 at n = 50, n0 = 25."""
    big = dict(h_w=100.0, h_a=200.0)  # keep the CUSUM quiet
    # raw mean(x) = 0.02 -> m > 0: ok
    fx = Fx(tmp_path).start(S10).many([0.02] * 50).write(cal=cal_dict(**big))
    code, res = run_json(fx, capsys)
    assert res["retire"]["armed"] and not res["retire"]["fired"] and code == 0
    assert res["retire"]["m"] == pytest.approx((50 * 0.02 - 25 * 0.03825) / 75)
    # raw mean(x) = 0.019 -> m < 0: retire, exit 20
    d = tmp_path / "b"
    d.mkdir()
    fx = Fx(d).start(S10).many([0.019] * 50).write(cal=cal_dict(**big))
    code, res = run_json(fx, capsys)
    assert res["retire"]["fired"] and code == 20 and "retire" in res["flags"]
    # not armed at 49 closed trades even when m < 0
    d = tmp_path / "c"
    d.mkdir()
    code, res = run_json(Fx(d).start(S10).many([-0.05] * 49).write(cal=cal_dict(**big)), capsys)
    assert not res["retire"]["armed"] and not res["retire"]["fired"] and code == 0 and res["retire"]["m"] < 0
    # RAW basis: 49 x 0.01 losses (-0.01) and one +1.5 win: raw mean 0.0202 -> m >= 0, ok. Winsorized at hi 0.5 the mean would be ~0.0002 and m < 0.
    d = tmp_path / "e"
    d.mkdir()
    fx = Fx(d).start(S10).many([-0.01] * 49 + [1.5]).write(cal=cal_dict(**big))
    code, res = run_json(fx, capsys)
    assert res["retire"]["mean_x"] == pytest.approx(0.0202)
    assert not res["retire"]["fired"] and code == 0
    wmean = (-0.49 + 0.5) / 50
    assert (50 * wmean - 25 * 0.03825) / 75 < 0  # the winsorized basis would have retired it
    # all stakes count in the retire check, each at its own cost fraction
    d = tmp_path / "f"
    d.mkdir()
    fx = Fx(d).start(S25).many([0.0] * 25, S25).start(S10).many([0.0] * 25, S10).write(cal=cal_dict(**big))
    res = run_json(fx, capsys)[1]
    assert res["retire"]["n"] == 50 and res["retire"]["mu_p"] == pytest.approx(-(25 * 0.05268 + 25 * 0.03825) / 50)
    assert res["n_closed_other_stake_or_before_start"] == 25 and res["n_population"] == 25


def test_rotation_loss_cap_retires(tmp_path, capsys):
    # realized -0.40 SOL with no 0.25 start row: the first-book cap; 4 trades of -0.10 SOL = -1.0 s each at stake 0.10 SOL -> -0.40 SOL total
    fx = Fx(tmp_path).start(S10).many([-1.0] * 4).write(cal=cal_dict(h_w=100.0, h_a=200.0, winsor_lo=-1.0, winsor_hi=1.0))
    code, res = run_json(fx, capsys)
    assert res["loss_cap_lamports"] == 400_000_000 and res["loss_cap_hit"] and code == 20
    d = tmp_path / "s"
    d.mkdir()  # same loss after a 0.25 start row: cap 0.75, not hit
    fx = Fx(d).start(S25).start(S10).many([-1.0] * 4).write(cal=cal_dict(h_w=100.0, h_a=200.0, winsor_lo=-1.0, winsor_hi=1.0))
    res = run_json(fx, capsys)[1]
    assert res["loss_cap_lamports"] == 750_000_000 and not res["loss_cap_hit"]


def test_only_named_bot_rows_counted(tmp_path, capsys):
    fx = Fx(tmp_path).start(S10).start(S10, bot=OTHER)
    fx.many([0.01] * 3)
    fx.many([-0.9] * 10, S10, bot=OTHER)  # another book's heavy losses must not count
    fx.row("fee", "Zfee", bot=OTHER, sol_out_lamports=1000)
    fx.write()
    code, res = run_json(fx, capsys)
    assert res["n_closed_book"] == 3 and res["n_population"] == 3 and res["book_realized_lamports"] == 3 * round(0.01 * S10)
    assert not res["warning"] and code == 0
    # a bot with no start row of its own: error
    assert M.main(["--bot", "rot-nobody-1", "--ledger-file", str(fx.p), "--calibration", str(fx.c)]) == 2


def test_torn_last_line_ignored(tmp_path, capsys):
    fx = Fx(tmp_path).start(S10).many([0.01, 0.01])
    complete = json.dumps({"schema": "x", "bot_id": BOT, "row_id": "t", "ev": "send_intent", "ts_ms": 9, "mint": "T", "stake_lamports": S10}).encode()
    for tail in (b'{"schema": "x", "bot_id": "' + BOT.encode() + b'", "ev": "ex', complete):  # a partial line, and a complete JSON without its newline: both a write in progress
        fx.write(tail=tail)
        code, res = run_json(fx, capsys)
        assert code == 0 and res["n_closed_book"] == 2 and res["n_open"] == 0


def test_bad_complete_line_exit_2(tmp_path, capsys):
    fx = Fx(tmp_path).start(S10).many([0.01])
    fx.write()
    fx.p.write_bytes(fx.p.read_bytes() + b"{garbage\n" + json.dumps(fx.rows[0]).encode() + b"\n")
    assert M.main(fx.args()) == 2
    assert "unreadable" in capsys.readouterr().err
    fx.p.write_bytes(b"[1, 2]\n")
    assert M.main(fx.args()) == 2
    fx.p.write_bytes(b'{"ev": 5}\n')
    assert M.main(fx.args()) == 2
    # a row of the bot with a non-integer ts_ms fails closed
    fx.p.write_bytes(json.dumps({"bot_id": BOT, "ev": "send_intent", "mint": "m", "ts_ms": "x", "stake_lamports": S10}).encode() + b"\n")
    assert M.main(fx.args()) == 2
    assert M.main(["--bot", BOT, "--ledger-file", str(tmp_path / "missing.jsonl"), "--calibration", str(fx.c)]) == 2


def test_stake_change_starts_new_population(tmp_path, capsys):
    """Losses at the old stake do not carry into the new population: S resets at a stake change."""
    fx = Fx(tmp_path).start(S25).many([-0.10] * 5, S25).start(S125).many([0.0] * 2, S125).write(cal=cal_dict(stake=S125))
    code, res = run_json(fx, capsys)
    assert res["S"] == pytest.approx(0.0) and not res["warning"]
    assert res["n_population"] == 2 and res["n_closed_other_stake_or_before_start"] == 5 and code == 0
    # a restart at the same stake continues the population
    d = tmp_path / "r"
    d.mkdir()
    fx = Fx(d).start(S10).many([-0.10], S10).start(S10).many([-0.10], S10).write()
    res = run_json(fx, capsys)[1]
    assert res["n_population"] == 2 and res["alarm"]
    # the calibration must be for the current rung
    fx.write(cal=cal_dict(stake=S25))
    assert M.main(fx.args()) == 2


def test_since_ms_per_close_and_stray_fee(tmp_path, capsys):
    fx = Fx(tmp_path).start(S10)
    fx.many([-0.10, -0.10])  # S 0.1 then 0.2: warning, alarm at the 2nd close
    t_after_alarm = fx.t
    fx.many([0.5] * 10)  # recovery: S back to 0 at the latest state
    fx.row("fee", "Zstray", sol_out_lamports=5000)  # a cost, not a trade
    fx.write()
    code, res = run_json(fx, capsys)
    assert code == 0 and res["n_closed_book"] == 12 and res["S"] == 0.0
    code, res = run_json(fx, capsys, "--since-ms", "0")
    assert code == 10 and res["exit_code_latest"] == 0
    assert [c["exit_code"] for c in res["fired_at_close"]] == [10, 10] and res["fired_at_close"][1]["flags"] == ["warning", "alarm"]
    code, res = run_json(fx, capsys, "--since-ms", str(t_after_alarm + 1))
    assert code == 0 and res["fired_at_close"] == []
    assert M.main(fx.args("--since-ms", "0")) == 10
    out = capsys.readouterr().out
    assert "fired_at_close" in out and "worst exit 10" in out


def test_read_only_and_text_output(tmp_path, capsys):
    fx = Fx(tmp_path).start(S10).many([0.0] * 3).write()
    before = (fx.p.read_bytes(), fx.c.read_bytes(), sorted(p.name for p in tmp_path.iterdir()))
    assert M.main(fx.args()) == 0
    out = capsys.readouterr().out
    assert "CUSUM S" in out and "not a profit claim" in out
    assert before == (fx.p.read_bytes(), fx.c.read_bytes(), sorted(p.name for p in tmp_path.iterdir()))


def test_default_ledger_is_the_rotation_constant_and_bot_id_checked(tmp_path):
    from tools.forge import rotation_lane_executor as RX
    assert M.RX.ROTATION_LEDGER_PATH == RX.ROTATION_LEDGER_PATH == "/var/lib/mal-live/rotation-lane/lane-ledger.jsonl"
    fx = Fx(tmp_path).start(S10).write()
    assert M.main(["--bot", "floor-lane-1", "--ledger-file", str(fx.p), "--calibration", str(fx.c)]) == 2
