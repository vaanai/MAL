"""The repo .claude/settings.json must keep the probe-key deny rules (DEC-019)."""
from __future__ import annotations

import json
import re
from pathlib import Path

SETTINGS = Path(__file__).resolve().parent.parent / ".claude/settings.json"
FILE_TOOLS = ("Read", "Edit", "Write")
_SINGLE_SLASH = re.compile(rf"^({'|'.join(FILE_TOOLS)})\(/[^/]")


def _perms() -> dict:
    return json.loads(SETTINGS.read_text())["permissions"]


def test_required_probe_key_rules_exist():
    deny = set(_perms()["deny"])
    required = {
        "Read(//etc/mal-probe/**)",
        "Edit(//etc/mal-probe/**)",
        "Write(//etc/mal-probe/**)",
        "Read(//var/lib/mal-live/probe-wallet.json)",
        "Edit(//var/lib/mal-live/probe-wallet.json)",
        "Write(//var/lib/mal-live/probe-wallet.json)",
        "Bash(*/etc/mal-probe*)",
        "Bash(sudo *mal-probe*)",
        "Bash(*probe-wallet.json*)",
        "Bash(systemd-creds *)",
        "Bash(sudo systemd-creds *)",
    }
    for cmd in ("cat", "cp", "head", "tail", "less", "more", "xxd", "base64", "od", "strings", "dd"):
        required.add(f"Bash({cmd} /etc/mal-probe*)")
        required.add(f"Bash(sudo {cmd} /etc/mal-probe*)")
    missing = sorted(required - deny)
    assert not missing, missing


def test_no_single_slash_absolute_file_rules():
    # "/x" in a Read/Edit/Write rule is relative to the settings file and protects nothing.
    perms = _perms()
    bad = [r for key in ("allow", "deny") for r in perms.get(key, []) if _SINGLE_SLASH.match(r)]
    assert bad == []
