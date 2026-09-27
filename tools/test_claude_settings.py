"""Validate .claude/settings.json and the unattended allow/ask/deny intent.

Claude Code Bash rules: `*` matches any characters including spaces.
A trailing ` *` also matches end-of-string (word boundary). Evaluation is
deny, then ask, then allow. See https://code.claude.com/docs/en/permissions
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / ".claude" / "settings.json"
SCHEMA_URL = "https://json.schemastore.org/claude-code-settings.json"

FORBIDDEN_ALLOW = {
    "Bash(python3 *)",
    "Bash(python *)",
    "Bash(pytest *)",
    "Bash(gh *)",
    "Bash(git push *)",
    "Bash(scripts/mal-core/agent-ssh.sh *)",
}


def glob_to_regex(glob: str) -> re.Pattern[str]:
    if glob.endswith(":*"):
        glob = glob[:-2] + " *"
    optional_tail = False
    if glob.endswith(" *"):
        glob = glob[:-2]
        optional_tail = True
    body = ".*".join(re.escape(part) for part in glob.split("*"))
    if optional_tail:
        body += r"(?: .*)?"
    return re.compile("^" + body + "$")


def parse_bash_rule(rule: str) -> str | None:
    if rule == "Bash" or rule == "Bash(*)":
        return "*"
    if rule.startswith("Bash(") and rule.endswith(")"):
        return rule[len("Bash(") : -1]
    return None


def bash_decision(command: str, settings: dict) -> str:
    perms = settings["permissions"]
    for bucket in ("deny", "ask", "allow"):
        for rule in perms.get(bucket, []):
            pattern = parse_bash_rule(rule)
            if pattern is None:
                continue
            if glob_to_regex(pattern).fullmatch(command):
                return bucket
    return "prompt"


class ClaudeSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = SETTINGS_PATH.read_text(encoding="utf-8")
        cls.settings = json.loads(cls.raw)
        cls.allow = list(cls.settings["permissions"]["allow"])
        cls.ask = list(cls.settings["permissions"]["ask"])
        cls.deny = list(cls.settings["permissions"]["deny"])

    def test_json_is_strict_object(self) -> None:
        self.assertIsInstance(self.settings, dict)
        self.assertEqual(self.settings["$schema"], SCHEMA_URL)
        perms = self.settings["permissions"]
        for key in ("allow", "ask", "deny"):
            self.assertIsInstance(perms[key], list)
            for rule in perms[key]:
                self.assertIsInstance(rule, str)
                self.assertTrue(rule, "empty permission rule")

    def test_no_forbidden_allow_rules(self) -> None:
        found = FORBIDDEN_ALLOW.intersection(self.allow)
        self.assertEqual(found, set(), f"broad allow rules still present: {found}")

    def test_required_allow_rules(self) -> None:
        for rule in (
            "Bash(python3 -m unittest *)",
            "Bash(git push)",
            "Bash(git push origin claude/*)",
            "Bash(gh pr create *)",
            "Bash(gh pr view *)",
            "Bash(gh pr list *)",
            "Bash(gh pr diff *)",
            "Bash(gh pr checks *)",
            "Bash(gh run view *)",
            "Bash(gh run list *)",
            "Edit",
            "Write",
        ):
            self.assertIn(rule, self.allow)

    def test_required_ask_and_deny_rules(self) -> None:
        self.assertIn("Bash(scripts/mal-core/agent-ssh.sh *)", self.ask)
        for rule in (
            "Bash(git push * --force*)",
            "Bash(git push * -f *)",
            "Bash(git push * +*)",
            "Bash(git push * main)",
            "Bash(git reset --hard *)",
            "Bash(gh pr merge *)",
            "Bash(gh pr close *)",
            "Bash(gh repo *)",
            "Bash(gh api *)",
            "Bash(gh release *)",
            "Edit(.claude/settings.json)",
            "Edit(.claude/settings.local.json)",
            "Edit(*.env)",
            "Edit(**/*helius*.env)",
            "Bash(ufw *)",
            "Edit(/etc/ssh/**)",
            "Edit(/etc/cloudflared/**)",
        ):
            self.assertIn(rule, self.deny)

    def test_python_unittest_allowed_other_python_not(self) -> None:
        self.assertEqual(
            bash_decision("python3 -m unittest tools.test_agent_ssh", self.settings),
            "allow",
        )
        self.assertEqual(
            bash_decision("python3 -m unittest discover -s tools -p test_*.py", self.settings),
            "allow",
        )
        self.assertEqual(bash_decision("python3 -c 'print(1)'", self.settings), "ask")
        self.assertEqual(bash_decision("python3 tools/fast_helius_pre.py", self.settings), "prompt")

    def test_gh_read_and_pr_create_allowed_writes_denied(self) -> None:
        self.assertEqual(bash_decision("gh pr create --title x --body y", self.settings), "allow")
        self.assertEqual(bash_decision("gh pr view 113", self.settings), "allow")
        self.assertEqual(bash_decision("gh pr list", self.settings), "allow")
        self.assertEqual(bash_decision("gh pr diff 113", self.settings), "allow")
        self.assertEqual(bash_decision("gh pr checks 113", self.settings), "allow")
        self.assertEqual(bash_decision("gh run list", self.settings), "allow")
        self.assertEqual(bash_decision("gh run view 1", self.settings), "allow")
        self.assertEqual(bash_decision("gh pr merge 113", self.settings), "deny")
        self.assertEqual(bash_decision("gh pr close 113", self.settings), "deny")
        self.assertEqual(bash_decision("gh repo delete vaanai/MAL", self.settings), "deny")
        self.assertEqual(bash_decision("gh api -X POST /repos/vaanai/MAL", self.settings), "deny")
        self.assertEqual(bash_decision("gh release create v1", self.settings), "deny")

    def test_force_and_main_pushes_denied_claude_branch_allowed(self) -> None:
        self.assertEqual(
            bash_decision("git push origin claude/narrow-permissions", self.settings),
            "allow",
        )
        self.assertEqual(
            bash_decision("git push -u origin claude/narrow-permissions", self.settings),
            "allow",
        )
        self.assertEqual(bash_decision("git push", self.settings), "allow")
        for command in (
            "git push --force origin claude/x",
            "git push origin claude/x --force",
            "git push origin claude/x --force-with-lease",
            "git push origin claude/x --force-if-includes",
            "git push -f origin claude/x",
            "git push origin claude/x -f",
            "git push origin +claude/x",
            "git push origin main",
            "git push origin master",
            "git push origin HEAD:main",
            "git push origin claude/x:refs/heads/main",
            "git reset --hard origin/main",
        ):
            self.assertEqual(bash_decision(command, self.settings), "deny", command)

    def test_agent_ssh_asks(self) -> None:
        self.assertEqual(
            bash_decision("scripts/mal-core/agent-ssh.sh --host fast --dry-run", self.settings),
            "ask",
        )
        self.assertNotIn(
            "Bash(scripts/mal-core/agent-ssh.sh *)",
            self.allow,
        )

    def test_schema_url_resolves(self) -> None:
        try:
            with urlopen(SCHEMA_URL, timeout=20) as response:
                schema = json.loads(response.read().decode("utf-8"))
        except OSError as exc:
            self.skipTest(f"schema fetch failed: {exc}")
        self.assertIn("properties", schema)
        self.assertIn("permissions", schema.get("properties", {}))


if __name__ == "__main__":
    unittest.main()
