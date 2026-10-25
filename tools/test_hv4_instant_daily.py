"""scripts/ops/hv4-instant-daily.sh: dry-run plan, guard refusals, and END-TO-END runs with the REAL tools/hv4_read_instant.py (no stub) on a synthetic tmp ledger and
synthetic FINAL hour tables written by the real arena_hour_health.render (hour lines plus the {"type":"sha256"} last line), via .tmp then rename like D3.
Time is faked with a `date`/`sleep` shim on PATH and a time.time patch around the real tool; the run date is never the wall clock. Never touches
/data/mal/arena. PAPER ONLY."""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from tools import arena_hour_health as H  # noqa: E402
from tools.test_hv4_read_instant import write_ledger  # noqa: E402

SCRIPT = REPO / "scripts" / "ops" / "hv4-instant-daily.sh"
BOTS = [f"hv4-{g}-{a}" for g, a in (("CON", "L"), ("CON", "F"), ("CON", "P"), ("CON", "X"), ("CON", "R"), ("BIG", "L"), ("BIG", "F"), ("BIG", "X"), ("BIG", "R"))]
CLOCK_MS = 1_792_886_400_000  # 2026-10-25T00:00:00Z
REAL_DATE = subprocess.run(["which", "date"], capture_output=True, text=True).stdout.strip()


def run(env_extra, tmp, cwd=REPO):
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp), **env_extra}
    return subprocess.run(["sh", str(SCRIPT)], cwd=cwd, env=env, capture_output=True, text=True, timeout=300)


# ------------------------------------------------------------------------------------------------------------------------------ fake world
def epoch(day: str, hm: str = "01:30") -> int:
    return int(datetime.strptime(f"{day}T{hm}:00", "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())


def make_table(tables: Path, day: str, excluded=()) -> Path:
    rows = [{"hour": f"{day}T{h:02d}:00:00Z", "excluded": h in excluded, "labels_failing": ["x"] if h in excluded else []} for h in range(24)]
    text = "\n".join(H.render(rows, "0" * 64)) + "\n"
    p = tables / f"hour-table-{day}-final.jsonl"
    tmp = Path(str(p) + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, p)
    return p


def days(first: str, last: str):
    out, t = [], epoch(first, "00:00")
    while t <= epoch(last, "00:00"):
        out.append(datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d"))
        t += 86400
    return out


class World:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.tables = tmp / "tables"
        self.rec = tmp / "rec"
        self.ledger = tmp / "ledger"
        self.tables.mkdir()
        self.rec.mkdir()
        self.restarts = tmp / "restarts.jsonl"
        self.restarts.write_text("")
        self.clock = tmp / "fake-now"
        self.late = tmp / "late-src"
        shim = tmp / "shim"
        shim.mkdir()
        (shim / "date").write_text(
            f'#!/bin/sh\ncase "$*" in\n"-u +%s") cat "{self.clock}";;\n"-u +%FT%TZ") exec {REAL_DATE} -u -d "@$(cat "{self.clock}")" +%FT%TZ;;\n*) exec {REAL_DATE} "$@";;\nesac\n')
        # sleep advances the fake clock 50x; it also lands the "late" table (if any) the first time anything sleeps
        (shim / "sleep").write_text(f'#!/bin/sh\necho $(( $(cat "{self.clock}") + $1 * 50 )) > "{self.clock}"\n'
                                    f'[ -f "{self.late}" ] && mv "{self.late}" "$LATE_DST"\nexit 0\n')
        for f in ("date", "sleep"):
            (shim / f).chmod(0o755)
        (tmp / "pyw.py").write_text(
            "import sys, runpy, time\n"
            f"sys.path.insert(0, {str(REPO)!r})\n"
            f"ft = float(open({str(self.clock)!r}).read())\n"
            "time.time = lambda: ft + 1\n"
            "sys.argv = [sys.argv[0]] + sys.argv[3:]\n"
            "runpy.run_module('tools.hv4_read_instant', run_name='__main__')\n")
        pyw = tmp / "pyw"
        pyw.write_text(f'#!/bin/sh\ncase "$1" in\n-m) [ -n "${{STUB_FAIL:-}}" ] && {{ echo "REFUSED: stub" >&2; exit 2; }}; exec {sys.executable} "{tmp}/pyw.py" "$@";;\n'
                       f'*) exec {sys.executable} "$@";;\nesac\n')
        pyw.chmod(0o755)
        self.shim, self.pyw = shim, pyw

    def ledger_for_conf(self):
        # 110 opens over 2026-10-25..29, 22 per day: the first boundary with margin >= 100 and 5 O dates is 2026-10-30T00:00Z
        opens = [CLOCK_MS + d * 86_400_000 + (1 + i) * 3_600_000 for d in range(5) for i in range(22)]
        write_ledger(self.ledger, opens, noise=False, bot="hv4-CON-F")

    def go(self, first, last, now=None, **extra):
        now = epoch(first) + 30 if now is None else now
        self.clock.write_text(str(now))
        env = {"PATH": f"{self.shim}:{os.environ['PATH']}", "FIRST": first, "LAST": last, "RESTARTS": str(self.restarts), "TABLE_DIR": str(self.tables),
               "RECORD_DIR": str(self.rec), "LEDGER": str(self.ledger), "PY": str(self.pyw), "TABLE_WAIT_S": "0", **extra}
        return run(env, self.tmp)


def fill(w: World, first="2026-10-25", last="2026-10-29"):
    for d in days(first, last):
        if not (w.tables / f"hour-table-{d}-final.jsonl").exists():
            make_table(w.tables, d)


# ------------------------------------------------------------------------------------------------------------------------------ plain tests
def test_script_is_posix_sh_and_lists_the_nine_bots():
    txt = SCRIPT.read_text()
    assert txt.startswith("#!/bin/sh")
    assert "[[" not in txt and "PIPESTATUS" not in txt and "\\<" not in txt
    for b in BOTS:
        assert b in txt
    assert subprocess.run(["sh", "-n", str(SCRIPT)]).returncode == 0
    assert "HM=${HM:-01:30}" in txt


def test_dry_run_plan(tmp_path):
    rec = tmp_path / "rec"
    rec.mkdir()
    (rec / "hv4-CON-L.instant.json").write_text("{}")
    restarts = tmp_path / "restarts.jsonl"
    restarts.write_text("")
    r = run({"DRY": "1", "RESTARTS": str(restarts), "RECORD_DIR": str(rec), "LEDGER": "/fake/ledger", "TABLE_DIR": "/fake/tables", "PY": "/fake/python"}, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert out.count("== plan run") == 10
    assert "== plan run 2026-10-30" in out and "== plan run 2026-11-08" in out
    assert out.count("SKIP hv4-CON-L: record exists") == 10
    cmds = [ln for ln in out.splitlines() if ln.startswith("/fake/python -m tools.hv4_read_instant")]
    assert len(cmds) == 10 * 8
    first = cmds[0]
    assert "--mode screen --bot hv4-CON-F --ledger-root /fake/ledger" in first
    assert "--health /fake/tables/hour-table-2026-10-25-final.jsonl --health-sha <recorded-tool-sha-2026-10-25>" in first
    assert first.count("--health ") == 5 and "hour-table-2026-10-29-final" in first and "hour-table-2026-10-30-final" not in first
    last = cmds[-1]
    assert last.count("--health ") == 14 and "hour-table-2026-11-07-final" in last
    assert f"--restarts {restarts} --as-of-ms <now-ms> --out {rec}/hv4-BIG-R.instant.json" in last
    assert not (rec / "hv4-CON-F.instant.json").exists()


def test_refuses_without_restarts(tmp_path):
    r = run({"DRY": "1"}, tmp_path)
    assert r.returncode == 2 and "RESTARTS" in r.stdout


def test_guard_refusals(tmp_path):
    rs = tmp_path / "r.jsonl"
    rs.write_text("")
    env = {"DRY": "1", "RESTARTS": str(rs)}
    # a repo without 9ed4c5ff (an empty one, and a depth-1 clone)
    empty = tmp_path / "empty"
    empty.mkdir()
    subprocess.run(["git", "init", "-q", str(empty)], check=True)
    r = run(env, tmp_path, cwd=empty)
    assert r.returncode == 2 and "9ed4c5ff" in r.stdout
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{REPO}", str(shallow)], check=True)
    r = run(env, tmp_path, cwd=shallow)
    assert r.returncode == 2 and ("9ed4c5ff" in r.stdout or "shallow" in r.stdout)
    # a full clone without Amendment 15 on the tree
    full = tmp_path / "full"
    subprocess.run(["git", "clone", "-q", "--shared", str(REPO), str(full)], check=True)
    dec = full / "DEC" / "DEC-031-forward-paper-arena.md"
    dec.write_text(dec.read_text().replace("### Amendment 15 ", "### Amendment 1 5 "))
    r = run(env, tmp_path, cwd=full)
    assert r.returncode == 2 and "Amendment 15" in r.stdout
    # and the same clone with the DEC untouched passes the guards
    dec.write_text(dec.read_text().replace("### Amendment 1 5 ", "### Amendment 15 "))
    assert run(env, tmp_path, cwd=full).returncode == 0


# ------------------------------------------------------------------------------------------------------------------------------ end to end, real tool
def test_e2e_not_ready_record_skip_and_edited_table(tmp_path):
    w = World(tmp_path)
    w.ledger_for_conf()
    # day 10-27: tables 25, 26 only; B cannot exist yet -> status only, no file
    fill(w, last="2026-10-26")
    r = w.go("2026-10-27", "2026-10-27")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("NOT_READY: no boundary yet") == 9
    assert "RECORD bot=" not in r.stdout and "O 1" not in r.stdout and "margin" not in r.stdout
    assert list(w.rec.glob("*.instant.json")) == []
    shas = (w.rec / "health-shas.txt").read_text().splitlines()
    assert [ln.split()[0] for ln in shas] == ["2026-10-25", "2026-10-26"]
    t25 = w.tables / "hour-table-2026-10-25-final.jsonl"
    last_line = t25.read_text().splitlines()[-1]
    tool_sha = __import__("json").loads(last_line)["sha256"]
    file_sha = hashlib.sha256(t25.read_bytes()).hexdigest()
    assert shas[0] == f"2026-10-25 {tool_sha} {file_sha}" and tool_sha != file_sha
    # day 10-30: B = 2026-10-30T00:00Z for hv4-CON-F; a record is written, printed without counts
    fill(w)
    r = w.go("2026-10-30", "2026-10-30")
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert "RECORD bot=hv4-CON-F READ_INSTANT 2026-10-30T00:00:00Z" in out and "record_sha256=" in out
    assert "O 110" not in out and "margin" not in out
    assert out.count("NOT_READY: no boundary yet") == 8
    recf = w.rec / "hv4-CON-F.instant.json"
    assert recf.exists() and [p.name for p in w.rec.glob("*.instant.json")] == ["hv4-CON-F.instant.json"]
    assert hashlib.sha256(recf.read_bytes()).hexdigest() in out
    # a later day: the record exists, skip (not a failure); the other eight are still NOT_READY
    make_table(w.tables, "2026-10-30")
    r = w.go("2026-10-31", "2026-10-31")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SKIP hv4-CON-F: record exists" in r.stdout and r.stdout.count("NOT_READY: no boundary yet") == 8
    # a table edited after first sight (still a valid table, so the pinned load_health accepts it) is refused
    make_table(w.tables, "2026-10-26", excluded=(3,))
    r = w.go("2026-10-31", "2026-10-31")
    assert r.returncode == 2 and "changed since first sight" in r.stdout
    assert "tool_exit" not in r.stdout and recf.exists()
    # and a table whose body no longer matches its last line is refused by load_health
    make_table(w.tables, "2026-10-26")
    p = w.tables / "hour-table-2026-10-26-final.jsonl"
    p.write_text(p.read_text().replace('"excluded":false', '"excluded":true', 1))
    r = w.go("2026-10-31", "2026-10-31")
    assert r.returncode == 2 and "refused by the pinned load_health" in r.stdout


def test_e2e_last_day_writes_not_decidable(tmp_path):
    w = World(tmp_path)
    w.ledger_for_conf()
    fill(w, last="2026-11-07")
    r = w.go("2026-11-08", "2026-11-08")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("RECORD bot=") == 9
    assert r.stdout.count(" NOT_DECIDABLE") == 8 and "RECORD bot=hv4-CON-F READ_INSTANT 2026-10-30T00:00:00Z" in r.stdout
    assert "margin 1" not in r.stdout and "O 110" not in r.stdout
    assert len(list(w.rec.glob("*.instant.json"))) == 9


def test_skip_already_past_day(tmp_path):
    w = World(tmp_path)
    fill(w)
    r = w.go("2026-10-30", "2026-10-30", now=epoch("2026-10-30") + 7200)
    assert r.returncode == 0 and "JOB skip 2026-10-30 (already past)" in r.stdout
    assert "RECORD" not in r.stdout and "NOT_READY" not in r.stdout


def test_missing_table_fails_that_day_only(tmp_path):
    w = World(tmp_path)
    w.ledger_for_conf()
    fill(w, last="2026-10-28")
    make_table(w.tables, "2026-10-30")
    late = w.tables / "hour-table-2026-10-29-final.jsonl"
    make_table(w.late.parent, "2026-10-29")  # build the table in tmp, then park it where the sleep shim lands it
    os.replace(w.late.parent / "hour-table-2026-10-29-final.jsonl", w.late)
    r = w.go("2026-10-30", "2026-10-31", LATE_DST=str(late))
    # 10-30: table 10-29 missing -> that day fails and is logged; 10-31: the table has landed, the day runs and the record is written
    assert "DAY FAILED 2026-10-30" in r.stdout and "== run 2026-10-31" in r.stdout
    assert "RECORD bot=hv4-CON-F READ_INSTANT 2026-10-30T00:00:00Z" in r.stdout
    assert "failed_days=1" in r.stdout and r.returncode == 3, r.stdout + r.stderr
    # a leftover .tmp counts as not complete
    (w.tables / "hour-table-2026-10-30-final.jsonl.tmp").write_text("x")
    r = w.go("2026-10-31", "2026-10-31")
    assert "DAY FAILED 2026-10-31" in r.stdout and r.returncode == 3


def test_tool_exit_2_fails_the_job(tmp_path):
    w = World(tmp_path)
    fill(w)
    r = w.go("2026-10-30", "2026-10-30", STUB_FAIL="1")
    assert r.returncode == 2 and "tool exit 2" in r.stdout


def test_shas_file_override(tmp_path):
    w = World(tmp_path)
    fill(w)
    alt = tmp_path / "alt-shas.txt"
    r = w.go("2026-10-30", "2026-10-30", SHAS_FILE=str(alt))
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(alt.read_text().splitlines()) == 5 and not (w.rec / "health-shas.txt").exists()
