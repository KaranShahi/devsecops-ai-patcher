"""Tests for the deterministic patch templates in agent.py.

These exist specifically to catch the class of bug where a patch template
rewrites one line in isolation but leaves a related line (an import, a
sibling call site) inconsistent with it — a patched file must actually
run correctly, not just look fixed in a diff.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent import patch_file
from scanner import scan_directory

FIXTURE_DIR = Path(__file__).parent.parent / "vulnerable_app"


class PatchFileTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.target = Path(self.tmpdir) / "server.js"
        shutil.copy(FIXTURE_DIR / "server.js", self.target)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _patch(self):
        findings = scan_directory(self.tmpdir)
        by_file: dict[str, list[dict]] = {}
        for finding in findings:
            by_file.setdefault(finding["file"], []).append(finding)
        results = []
        for file_path, file_findings in by_file.items():
            results.extend(patch_file(file_path, file_findings))
        return results, self.target.read_text(encoding="utf-8")

    def _child_process_import_line(self, patched: str) -> str:
        for line in patched.splitlines():
            if "require(\"child_process\")" in line or "require('child_process')" in line:
                return line
        self.fail("no child_process import found in patched output")

    def test_all_seeded_findings_are_patched(self):
        results, _ = self._patch()
        self.assertTrue(results)
        for r in results:
            self.assertTrue(r["applied"], f"{r['rule_id']} at line {r['line']} was not applied")

    def test_hardcoded_secret_uses_env_var(self):
        _, patched = self._patch()
        self.assertIn("process.env.STRIPE_API_KEY", patched)
        self.assertNotIn("REPLACE_ME_WITH_A_REAL_SECRET_VALUE_0000", patched)

    def test_sql_injection_removes_concatenation_and_binds_parameter(self):
        _, patched = self._patch()
        self.assertNotIn('" + userId', patched)
        self.assertIn('"SELECT * FROM users WHERE id = ?"', patched)
        # The whole point of the fix: the bound value must actually reach
        # db.query(), not just disappear once the string is parameterized.
        self.assertIn("db.query(query, [userId]", patched)

    def test_command_injection_uses_execfile_with_matching_import(self):
        _, patched = self._patch()
        self.assertIn('execFile("ping", [host]', patched)
        self.assertNotIn('exec("ping " + host', patched)
        # execFile must be imported wherever it's used, or the patch just
        # trades a command-injection bug for a ReferenceError.
        self.assertIn("execFile", self._child_process_import_line(patched))

    def test_patched_file_is_valid_javascript(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed; skipping syntax validation")
        self._patch()
        subprocess.run([node, "--check", str(self.target)], check=True)

    def test_rerunning_the_patcher_is_a_no_op(self):
        _, first_patched = self._patch()
        second_findings = scan_directory(self.tmpdir)
        self.assertEqual(second_findings, [], "already-patched code should not raise new findings")
        self.assertEqual(self.target.read_text(encoding="utf-8"), first_patched)


if __name__ == "__main__":
    unittest.main()
