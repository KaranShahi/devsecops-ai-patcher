"""Tests for the regex-based scanner rules in scanner.py."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scanner import scan_directory

FIXTURE_DIR = Path(__file__).parent.parent / "vulnerable_app"


class ScanDirectoryTests(unittest.TestCase):
    def test_finds_all_three_seeded_vulnerabilities(self):
        findings = scan_directory(str(FIXTURE_DIR))
        rule_ids = sorted(f["rule_id"] for f in findings)
        self.assertEqual(rule_ids, ["command-injection", "hardcoded-secret", "sql-injection"])

    def test_each_finding_has_a_cwe_id(self):
        findings = scan_directory(str(FIXTURE_DIR))
        self.assertTrue(findings)
        for f in findings:
            self.assertTrue(f["cwe"].startswith("CWE-"))

    def test_clean_directory_has_no_findings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clean_file = Path(tmpdir) / "clean.js"
            clean_file.write_text(
                'const express = require("express");\n'
                "const key = process.env.API_KEY;\n"
                'db.query("SELECT * FROM users WHERE id = ?", [userId]);\n',
                encoding="utf-8",
            )
            findings = scan_directory(tmpdir)
            self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
